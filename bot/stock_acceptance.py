"""Actual HTTP/process/SQLite acceptance suite. No production settings or payments."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime,timedelta
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import aiohttp
from bot.config import ROOT
from bot.stock_lab import LAB_ROOT,CONTROL,MANAGER,signed,safe_path
from bot import stock
from bot.db import Database

DEFAULT_TTL=120


def require(condition,message):
    if not condition:
        raise AssertionError(message)


class Server:
    def __init__(self,path,ttl):
        self.path=path;self.ttl=ttl;self.process=None;self.log=None

    async def start(self):
        ready=self.path.parent/(uuid.uuid4().hex+'.ready.json')
        self.log=(self.path.parent/(uuid.uuid4().hex+'.server.log')).open('w',encoding='utf-8')
        self.process=subprocess.Popen([sys.executable,'-m','bot.stock_lab','--db',str(self.path),
            '--ready',str(ready),'--ttl',str(self.ttl)],cwd=ROOT,stdout=self.log,stderr=self.log)
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            if ready.exists():
                self.url='http://127.0.0.1:'+str(json.loads(ready.read_text())['port']);return
            if self.process.poll() is not None:
                raise AssertionError('Lab server failed; see '+str(self.log.name))
            await asyncio.sleep(.05)
        raise AssertionError('Lab server startup timed out')

    async def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            await asyncio.to_thread(self.process.wait,10)
        if self.log:
            self.log.close()


class Case:
    def __init__(self,path,ttl=120):
        self.path=safe_path(path);self.ttl=ttl;self.servers=[]

    async def start(self,n=2):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))
        for _ in range(n):
            server=Server(self.path,self.ttl);self.servers.append(server);await server.start()
        return self

    async def close(self):
        for s in self.servers:
            await s.stop()
        await self.session.close()

    async def api(self,path,body=None,uid=MANAGER,server=0,expected=(200,)):
        headers=signed(uid)
        if path.startswith('/test/'):
            headers['X-Test-Control']=CONTROL
        async with self.session.request('POST' if body is not None else 'GET',self.servers[server].url+path,
                                        json=body,headers=headers) as r:
            result=await r.json()
            require(r.status in expected,f'{path}: HTTP {r.status}: {result}')
            return result

    async def receive(self,pid,qty):
        return await self.api('/api/stock/receipts',{'product_id':pid,'quantity':qty,'key':uuid.uuid4().hex})

    async def cart(self,uid,items):
        return await self.api('/shop/cart',{'items':items},uid)

    async def checkout(self,uid,items,key=None,server=0,expected=(200,)):
        return await self.api('/shop/checkout',{'items':items,'key':key or uuid.uuid4().hex,'name':f'Учебный {uid}'},uid,server,expected)

    async def buy(self,uid,qty=1,pid=1):
        items=[{'product_id':pid,'quantity':qty}]
        await self.cart(uid,items)
        return await self.checkout(uid,items)

    async def pay(self,order,status='succeeded',**extra):
        return await self.api('/test/payment',{'payment_id':order['payment_id'],'status':status,**extra})

    async def advance(self,seconds,tick=True):
        await self.api('/test/control',{'advance':seconds})
        if tick:
            await self.api('/test/tick',{})

    async def triple(self,pid=1):
        view=await self.api('/api/stock')
        row=next(x for x in view['inventory'] if x['product_id']==pid)
        return [row['on_hand'],row['reserved'],row['available']]

    def rows(self,sql,args=()):
        c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row
        try:
            return [dict(r) for r in c.execute(sql,args)]
        finally:
            c.close()

    def counts(self):
        return {t:self.rows('SELECT count(*) AS n FROM '+t)[0]['n'] for t in ['orders','reservations','finance_operations']}

    def invariant(self):
        stock.check_invariants(Database(self.path))

    async def restart(self):
        for s in self.servers:
            await s.stop()
        self.servers=[]
        for _ in range(2):
            server=Server(self.path,self.ttl);self.servers.append(server);await server.start()


@asynccontextmanager
async def case(root,name,ttl=None):
    c=Case(root/name/'case.sqlite3',DEFAULT_TTL if ttl is None else ttl)
    try:
        await c.start();yield c
        c.invariant()
    finally:
        await c.close()


async def chain(root):
    async with case(root,'01-chain') as c:
        await c.receive(1,10); triples=[await c.triple()]
        a=await c.buy(1,3);triples.append(await c.triple())
        await c.pay(a);triples.append(await c.triple())
        await c.buy(2,2);triples.append(await c.triple())
        await c.advance(c.ttl);triples.append(await c.triple())
        await c.api('/test/refund',{'payment_id':a['payment_id']})
        require(await c.triple()==[7,0,7],'Request alone must not restock')
        await c.api('/test/tick',{});triples.append(await c.triple())
        expected=[[10,0,10],[10,3,7],[7,0,7],[7,2,5],[7,0,7],[10,0,10]]
        require(triples==expected,f'Chain mismatch {triples}')
        await c.pay(a,'refund_succeeded')
        require(await c.triple()==[10,0,10],'Repeated refund restocked twice')
        return {'triples':triples,'expected':expected}


async def competition(root,stock_count,buyers,repeats,label):
    results=[]
    require(buyers>=stock_count,'Buyers must cover stock in competition')
    for rep in range(repeats):
        async with case(root,f'{label}-r{rep+1}',ttl=3600) as c:
            await c.receive(1,stock_count)
            items=[{'product_id':1,'quantity':1}]
            for uid in range(1000,1000+buyers):
                await c.cart(uid,items)
            signal=asyncio.Event()
            async def attempt(index):
                await signal.wait()
                return await c.checkout(1000+index,items,key=f'buyer-{index}-request',server=index%2,expected=(200,409))
            tasks=[asyncio.create_task(attempt(i)) for i in range(buyers)]
            await asyncio.sleep(0);signal.set()
            responses=await asyncio.gather(*tasks)
            wins=sum(r['kind']=='created' for r in responses)
            rejects=sum(r['kind']=='unavailable' for r in responses)
            triple=await c.triple();counts=c.counts()
            require((wins,rejects)==(stock_count,buyers-stock_count),f'Unexpected responses {wins}/{rejects}')
            require(triple==[stock_count,stock_count,0],f'Unexpected stock {triple}')
            require(counts['orders']==counts['reservations']==stock_count,'Partial or duplicate orders')
            results.append({'repeat':rep+1,'success':wins,'unavailable':rejects,'technical_errors':0,'triple':triple})
    return results


async def multi_rollback(root):
    async with case(root,'04-multi') as c:
        await c.receive(1,3)
        items=[{'product_id':1,'quantity':1},{'product_id':2,'quantity':1}]
        await c.cart(1,items)
        r=await c.checkout(1,items,expected=(409,))
        require(await c.triple()==[3,0,3],'Partial reserve left behind')
        require(c.counts()['orders']==c.counts()['reservations']==0,'Partial order left behind')
        return {'result':r['kind'],'triple':await c.triple(),'orders':0}


async def repeated_checkout(root):
    async with case(root,'05-repeat') as c:
        await c.receive(1,5)
        items=[{'product_id':1,'quantity':1}];await c.cart(1,items)
        start=asyncio.Event()
        async def repeat(i):
            await start.wait()
            return await c.checkout(1,items,'same-key',i%2)
        tasks=[asyncio.create_task(repeat(i)) for i in range(20)];start.set()
        results=await asyncio.gather(*tasks)
        ids={r['order_id'] for r in results}
        require(len(ids)==1 and c.counts()['orders']==1,'Idempotent checkout created duplicate')
        changed=await c.checkout(1,[{'product_id':1,'quantity':2}],'same-key',expected=(400,))
        require(await c.triple()==[5,1,4],'Changed key corrupted reserve')
        return {'requests':20,'unique_orders':len(ids),'changed_rejected':bool(changed.get('error'))}


async def repeat_pay(root):
    async with case(root,'06-payment') as c:
        await c.receive(1,1);a=await c.buy(1)
        for _ in range(5):
            await c.pay(a)
        await c.pay(a,'pending');await c.pay(a,'canceled')
        ops=c.rows('SELECT type,amount,cost_amount FROM finance_operations')
        require(len(ops)==1 and ops[0]['amount']==120000,'Repeated receipt or wrong amount')
        require(await c.triple()==[0,0,0],'Repeated sale')
        require(c.rows("SELECT count(*) AS n FROM inventory_movements WHERE kind='sale'")[0]['n']==1,'Duplicate warehouse sale')
        return {'operations':ops,'triple':await c.triple()}


async def cancel_expire(root):
    async with case(root,'07-release') as c:
        await c.receive(1,2);a=await c.buy(1)
        for _ in range(2):
            await c.api('/shop/cancel',{'order_id':a['order_id']},uid=1)
        await c.buy(2);await c.advance(c.ttl)
        require(await c.triple()==[2,0,2] and c.counts()['finance_operations']==0,'Release or finance mismatch')
        count=c.rows("SELECT count(*) AS n FROM inventory_movements WHERE kind='release'")[0]['n']
        require(count==2,'Repeated release')
        return {'releases':count,'triple':await c.triple(),'receipts':0}


async def races(root,repeats):
    outcomes=[]
    for rep in range(repeats):
        async with case(root,f'08-race-{rep}') as c:
            await c.receive(1,1);a=await c.buy(1)
            start=asyncio.Event()
            async def payment():
                await start.wait();return await c.pay(a)
            async def cancel():
                await start.wait();return await c.api('/shop/cancel',{'order_id':a['order_id']},1,1,expected=(200,400))
            tasks=[asyncio.create_task(payment()),asyncio.create_task(cancel())];start.set();await asyncio.gather(*tasks)
            await c.api('/test/tick',{})
            state=c.rows('SELECT stock_state FROM orders')[0]['stock_state'];triple=await c.triple()
            require((state,triple) in [('paid',[0,0,0]),('refunded',[1,0,1])],f'Impossible race outcome {state}/{triple}')
            outcomes.append({'winner':'payment' if state=='paid' else 'release_then_refund','state':state,'triple':triple})
    bounds=[]
    for seconds in [DEFAULT_TTL-1,DEFAULT_TTL,DEFAULT_TTL+1]:
        async with case(root,f'08-boundary-{seconds}') as c:
            await c.receive(1,1);a=await c.buy(1);await c.advance(seconds,tick=False)
            start=asyncio.Event()
            async def payment():
                await start.wait();return await c.pay(a)
            async def expiry():
                await start.wait();return await c.api('/test/tick',{},server=1)
            tasks=[asyncio.create_task(payment()),asyncio.create_task(expiry())];start.set();await asyncio.gather(*tasks)
            await c.api('/test/tick',{})
            state=c.rows('SELECT stock_state FROM orders')[0]['stock_state']
            require(state==('paid' if seconds<c.ttl else 'refunded'),f'Boundary violated at {seconds}: {state}')
            bounds.append({'seconds':seconds,'state':state,'triple':await c.triple()})
    return {'races':outcomes,'strict_boundary':bounds}


async def late(root):
    async with case(root,'09-late') as c:
        await c.receive(1,1);a=await c.buy(1)
        await c.api('/shop/cancel',{'order_id':a['order_id']},1)
        b=await c.buy(2);await c.pay(b);await c.pay(a)
        require(c.rows('SELECT stock_state FROM orders WHERE id=?',(a['order_id'],))[0]['stock_state']=='payment_conflict','Late payment not recorded')
        require(await c.triple()==[0,0,0],'Late payment stole inventory')
        await c.api('/test/tick',{})
        at=c.rows('SELECT utc FROM lab_controls')[0]['utc'][:10]
        summary=await c.api(f'/api/summary?start={at}&end={at}')
        require((summary['receipt'],summary['refund'],summary['sold_cost'],summary['cash_flow'],summary['educational_result'])==(240000,120000,70000,120000,50000),'Wrong late-payment financials')
        for _ in range(3):
            await c.pay(a);await c.pay(b);await c.api('/test/tick',{})
        require(await c.triple()==[0,0,0] and c.counts()['finance_operations']==3,'Late replay changed state')
        return {'receipt':summary['receipt'],'refund':summary['refund'],'cost':summary['sold_cost'],
                'cash_flow':summary['cash_flow'],'result':summary['educational_result'],'triple':await c.triple()}


async def restart_expiry(root):
    async with case(root,'10-restart') as c:
        await c.receive(1,1);await c.buy(1)
        # Move only the isolated test clock; do not manually release a reservation.
        await c.advance(c.ttl+1,tick=False)
        for s in c.servers:
            await s.stop()
        begin=time.monotonic();await c.restart()
        elapsed=time.monotonic()-begin
        require(c.rows('SELECT state FROM reservations')[0]['state']=='released','Startup worker did not release before the first API read')
        require(await c.triple()==[1,0,1],'Startup did not release expired reserve')
        await c.restart();require(await c.triple()==[1,0,1],'Restart repeated release')
        releases=c.rows("SELECT count(*) AS n FROM inventory_movements WHERE kind='release'")[0]['n']
        require(releases==1,'Release duplicated')
        return {'triple':await c.triple(),'releases':releases,'startup_seconds':round(elapsed,3)}


async def crash_refund(root):
    async with case(root,'11-refund-crash') as c:
        await c.receive(1,1);a=await c.buy(1)
        await c.api('/shop/cancel',{'order_id':a['order_id']},1)
        # Stop the second worker so it cannot finish the job during the forced crash.
        await c.servers[1].stop()
        await c.api('/test/control',{'fail':1,'lost':1,'crash':'after_late_commit'})
        try:
            await c.pay(a)
            raise AssertionError('Crash point did not terminate server')
        except (aiohttp.ClientError,asyncio.TimeoutError):
            pass
        require(c.counts()['finance_operations']==1,'Late receipt was not durably committed')
        jobs=c.rows('SELECT * FROM refund_jobs');require(len(jobs)==1 and jobs[0]['state']=='pending','Refund outbox missing after crash')
        await c.restart()
        require(c.rows('SELECT state FROM refund_jobs')[0]['state']=='error','Temporary failure not visible')
        await c.api('/test/control',{'crash':'none'})
        await c.advance(5)
        require('потерян' in c.rows('SELECT last_error FROM refund_jobs')[0]['last_error'],'Lost reply not simulated')
        await c.advance(10)
        require(c.rows('SELECT state FROM refund_jobs')[0]['state']=='succeeded','Refund not recovered')
        provider=sqlite3.connect(c.path.with_suffix('.provider.sqlite3'))
        external_count=provider.execute('SELECT count(*) FROM refunds').fetchone()[0];provider.close()
        require(external_count==1 and c.counts()['finance_operations']==2,'Duplicate external or financial refund')
        require(await c.triple()==[1,0,1],'Late refund incorrectly restocked')
        return {'receipt_rows':1,'refund_rows':1,'provider_refunds':external_count,'attempts':c.rows('SELECT attempts FROM refund_jobs')[0]['attempts'],'triple':await c.triple()}


async def crash_checkout(root):
    async with case(root,'12-transaction-crash') as c:
        await c.receive(1,1);items=[{'product_id':1,'quantity':1}];await c.cart(1,items)
        await c.servers[1].stop()
        await c.api('/test/control',{'crash':'checkout_after_order'})
        try:
            await c.checkout(1,items,'crash-safe-key')
            raise AssertionError('Checkout crash did not happen')
        except (aiohttp.ClientError,asyncio.TimeoutError):
            pass
        require(c.counts()['orders']==c.counts()['reservations']==0,'Uncommitted order survived process death')
        await c.restart();await c.api('/test/control',{'crash':'none'})
        result=await c.checkout(1,items,'crash-safe-key')
        require(result['kind']=='created' and c.counts()['orders']==1,'Safe retry failed')
        require(await c.triple()==[1,1,0],'Wrong inventory after crash retry')
        return {'orders_after_crash':0,'orders_after_retry':1,'triple':await c.triple()}


async def security(root):
    async with case(root,'13-access') as c:
        await c.receive(1,2);a=await c.buy(1)
        before=c.counts();triple=await c.triple()
        for path,body in [('/api/stock',None),('/api/stock/receipts',{'product_id':1,'quantity':10,'key':'attack-key'}),('/api/stock/refunds/unknown/retry',{})]:
            await c.api(path,body,uid=2,expected=(403,))
        await c.api('/shop/cancel',{'order_id':a['order_id']},2,expected=(403,))
        await c.api('/shop/orders/'+str(a['order_id']),uid=2,expected=(403,))
        items=[{'product_id':1,'quantity':1}];await c.cart(2,items)
        for extra in [{'amount':1},{'expires_at':'2099-01-01'},{'user_id':1}]:
            await c.api('/shop/checkout',{'items':items,'key':'attack','name':'Учебный',**extra},2,expected=(400,))
        await c.api('/test/payment',{'payment_id':a['payment_id'],'amount':1},expected=(400,))
        async with c.session.post(c.servers[0].url+'/test/payment',headers=signed(2),json={'payment_id':a['payment_id']}) as r:
            require(r.status==403,'Customer could call lab provider controls')
        require(c.counts()==before and await c.triple()==triple,'Rejected access changed records')
        return {'unauthorized_stock':'403','foreign_order':'403','amount_and_TTL':'400','data_unchanged':True}


async def run(args):
    global DEFAULT_TTL
    DEFAULT_TTL=args.ttl
    LAB_ROOT.mkdir(parents=True,exist_ok=True)
    run_dir=LAB_ROOT/('run-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    run_dir.mkdir()
    scenarios=[('01 ручная цепочка',lambda:chain(run_dir)),
        (f'02 1 единица / 20 покупателей ×{args.repeats}',lambda:competition(run_dir,1,20,args.repeats,'02')),
        (f'03 10 единиц / 50 покупателей ×{args.repeats}',lambda:competition(run_dir,10,50,args.repeats,'03')),
        ('04 многотоварный откат',lambda:multi_rollback(run_dir)),
        ('05 повторы оформления',lambda:repeated_checkout(run_dir)),
        ('06 пять оплат и старые события',lambda:repeat_pay(run_dir)),
        ('07 отмена и срок',lambda:cancel_expire(run_dir)),
        ('08 гонки и строгая граница срока',lambda:races(run_dir,args.repeats)),
        ('09 поздняя оплата',lambda:late(run_dir)),
        ('10 перезапуск с резервом',lambda:restart_expiry(run_dir)),
        ('11 авария и потерянный ответ возврата',lambda:crash_refund(run_dir)),
        ('12 авария до commit',lambda:crash_checkout(run_dir)),
        ('13 доступ и подмена',lambda:security(run_dir))]
    if args.buyers is not None:
        scenarios.append(('14 параметрическая конкуренция',lambda:competition(run_dir,args.stock,args.buyers,args.repeats,'14')))
    results=[]
    for name,operation in scenarios:
        start=time.monotonic()
        try:
            actual=await operation();row={'scenario':name,'passed':True,'actual':actual}
            print('PASS',name,flush=True)
        except Exception as exc:
            row={'scenario':name,'passed':False,'error':repr(exc)}
            print('FAIL',name,repr(exc),flush=True)
        row['seconds']=round(time.monotonic()-start,3);results.append(row)
    report={'command':'python -m bot.stock_acceptance '+' '.join(sys.argv[1:]),'isolated_run':str(run_dir.relative_to(ROOT)),
            'repetitions':args.repeats,'separate_server_processes':2,'results':results}
    (run_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Фактический отчёт складской приёмки','',f'Команда: `{report["command"]}`',
        f'Изолированные данные: `{report["isolated_run"]}`',f'Повторов конкурентных сценариев: {args.repeats}. Два HTTP-сервера с общей SQLite.','',
        'Это настоящие HTTP-запросы и остановки процессов на изолированном стенде; провайдер учебный, реальная ЮKassa и Telegram-видео не запускались.','']
    for row in results:
        lines+=['## '+row['scenario'],'','Ожидалось: инварианты и точные результаты раздела 10 документа store-stock.md.',
            'Результат: '+('ПРОЙДЕНО' if row['passed'] else 'ОШИБКА'),f'Длительность: {row["seconds"]} сек.','',
            '```json',json.dumps(row.get('actual',row.get('error')),ensure_ascii=False,indent=2),'```','']
    lines+=['## Ручная сверка поздней оплаты','',
        'Два подтверждённых платежа по 1200 ₽ = 2400 ₽ поступлений; один полный поздний возврат 1200 ₽. Денежный поток = 2400 − 1200 = 1200 ₽. Себестоимость только принятой продажи Б = 700 ₽; результат = 2400 − 1200 − 700 = 500 ₽. Поздняя оплата А и её возврат имеют нулевую себестоимость и не меняют склад.','',
        'Незавершённое вне автоматической приёмки: запись реального Telegram/Mini App, оплата/возврат в вашем тестовом кабинете ЮKassa и публикация репозитория не выполнялись этим запуском.']
    (ROOT/'STOCK_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print('Report:',run_dir/'report.json',flush=True)
    return 0 if all(r['passed'] for r in results) else 1


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8',errors='replace')
        sys.stderr.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser()
    parser.add_argument('--repeats',type=int,default=5)
    parser.add_argument('--stock',type=int,default=1);parser.add_argument('--buyers',type=int)
    parser.add_argument('--ttl',type=int,default=120)
    parser.add_argument('--reset',help='Remove only an explicitly named previous isolated run directory')
    args=parser.parse_args()
    if args.reset:
        target=Path(args.reset).resolve()
        if target.parent!=LAB_ROOT.resolve() or not target.name.startswith('run-'):
            parser.error('Reset is allowed only for one data/stock-tests/run-* directory')
        shutil.rmtree(target);print('Removed isolated test run:',target);return
    if args.repeats<5 or not 1<=args.ttl<=86400 or args.stock<1 or (args.buyers is not None and not args.stock<=args.buyers<=200):
        parser.error('Use at least five repeats, positive stock, stock <= buyers <= 200')
    sys.exit(asyncio.run(run(args)))


if __name__=='__main__':
    main()
