"""Loopback-only isolated acceptance server. Never import these routes into the real bot."""
import argparse
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode
from aiohttp import web
from bot.config import ROOT,Settings
from bot.db import Database
from bot import stock,finance,refunds
from bot.web import create_app,ACTOR

LAB_ROOT=ROOT/'data'/'stock-tests'
LAB_TOKEN='123456:synthetic-stock-lab-token'
CONTROL='local-stock-acceptance-only'
MANAGER=900
SETTINGS=Settings(LAB_TOKEN,frozenset({MANAGER}),'','','')


def signed(uid):
    values={'user':json.dumps({'id':uid}),'auth_date':str(int(time.time()))}
    secret=hmac.new(b'WebAppData',LAB_TOKEN.encode(),hashlib.sha256).digest()
    values['hash']=hmac.new(secret,'\n'.join(f'{k}={v}' for k,v in sorted(values.items())).encode(),hashlib.sha256).hexdigest()
    return {'X-Telegram-Init-Data':urlencode(values)}


def safe_path(value):
    p=Path(value).resolve()
    if not p.is_relative_to(LAB_ROOT.resolve()) or p.suffix!='.sqlite3':
        raise ValueError('Lab database must be an explicit .sqlite3 file inside data/stock-tests')
    return p


class SimRefunds:
    """Provider state is in a separate file and survives application termination."""
    def __init__(self,path):
        self.path=path
        c=sqlite3.connect(path)
        c.execute('CREATE TABLE IF NOT EXISTS refunds (operation_key TEXT PRIMARY KEY,id TEXT UNIQUE,payment_id TEXT UNIQUE,amount INTEGER,status TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS controls (id INTEGER PRIMARY KEY CHECK(id=1),fail INTEGER,lost INTEGER)')
        c.execute('INSERT OR IGNORE INTO controls VALUES (1,0,0)');c.commit();c.close()

    def configure(self,fail=None,lost=None):
        c=sqlite3.connect(self.path)
        if fail is not None:
            c.execute('UPDATE controls SET fail=? WHERE id=1',(fail,))
        if lost is not None:
            c.execute('UPDATE controls SET lost=? WHERE id=1',(lost,))
        c.commit();c.close()

    def reconcile(self,job,p):
        c=sqlite3.connect(self.path,isolation_level=None)
        try:
            c.execute('BEGIN IMMEDIATE')
            control=c.execute('SELECT fail,lost FROM controls WHERE id=1').fetchone()
            if control[0]>0:
                c.execute('UPDATE controls SET fail=fail-1');c.execute('COMMIT')
                raise ConnectionError('Учебная временная ошибка провайдера')
            old=c.execute('SELECT id,status FROM refunds WHERE payment_id=?',(p['id'],)).fetchone()
            if old:
                c.execute('COMMIT');return {'id':old[0],'status':old[1]}
            rid='sim-refund-'+hashlib.sha256(job['operation_key'].encode()).hexdigest()[:24]
            c.execute("INSERT INTO refunds VALUES (?,?,?,?,'succeeded')",(job['operation_key'],rid,p['id'],p['amount']))
            if control[1]>0:
                c.execute('UPDATE controls SET lost=lost-1');c.execute('COMMIT')
                raise ConnectionError('Учебный успешный ответ возврата потерян')
            c.execute('COMMIT')
            return {'id':rid,'status':'succeeded'}
        finally:
            c.close()


def control_clock(path):
    c=sqlite3.connect(path)
    try:
        return datetime.fromisoformat(c.execute('SELECT utc FROM lab_controls WHERE id=1').fetchone()[0])
    finally:
        c.close()


async def serve(args):
    path=safe_path(args.db)
    db=Database(path,reservation_seconds=args.ttl)
    with db._transaction() as c:
        c.execute('CREATE TABLE IF NOT EXISTS lab_controls (id INTEGER PRIMARY KEY CHECK(id=1),utc TEXT,crash TEXT)')
        c.execute("INSERT OR IGNORE INTO lab_controls VALUES (1,?,'none')",(stock.stamp(datetime.now(timezone.utc)),))
        c.execute('UPDATE products SET price_kopecks=120000,cost_kopecks=70000 WHERE id IN (1,2)')
    db.clock=lambda:control_clock(path)
    def fault(point):
        c=db._connect()
        try:
            crash=c.execute('SELECT crash FROM lab_controls WHERE id=1').fetchone()[0]
        finally:
            c.close()
        if crash==point:
            os._exit(77)
    db.fault_hook=fault
    sim=SimRefunds(path.with_suffix('.provider.sqlite3'))

    @web.middleware
    async def lab_guard(request,handler):
        if request.path.startswith('/test/') and (request.remote not in {'127.0.0.1','::1'} or request.headers.get('X-Test-Control')!=CONTROL):
            return web.json_response({'error':'Lab control is private'},status=403)
        response=await handler(request)
        if request.path=='/shop/checkout' and response.status==200:
            data=json.loads(response.body)
            p=finance.ensure_payment(db,data['order_id'],'demo',request[ACTOR])
            data['payment_id']=p['id']
            return web.json_response(data)
        return response

    app=create_app(db,SETTINGS)
    app.middlewares.append(lab_guard)

    async def control(request):
        body=await request.json()
        with db._transaction() as c:
            if 'advance' in body:
                at=db.clock()+timedelta(seconds=body['advance'])
                c.execute('UPDATE lab_controls SET utc=? WHERE id=1',(stock.stamp(at),))
            if 'crash' in body:
                c.execute('UPDATE lab_controls SET crash=? WHERE id=1',(body['crash'],))
        sim.configure(body.get('fail'),body.get('lost'))
        return web.json_response({'ok':True})

    async def pay(request):
        body=await request.json();p=finance.payment(db,body['payment_id'])
        status=body.get('status','succeeded')
        finance.apply_event(db,p['id'],status=status,amount=body.get('amount',p['amount']),currency='RUB',
            order_id=p['order_id'],external_id=f'lab:{p["id"]}:{status}',provider='demo',provider_id=None,author='test-provider')
        if db.get_order(p['order_id']).stock_state=='payment_conflict':
            stock.fault(db,'after_late_commit')
        return web.json_response({'payment':finance.payment(db,p['id'])})

    async def refund(request):
        body=await request.json()
        p=refunds.request(db,body['payment_id'],MANAGER)
        return web.json_response(p)

    async def tick(request):
        await refunds.tick(db,SETTINGS,sim)
        return web.json_response({'ok':True})

    app.router.add_post('/test/control',control)
    app.router.add_post('/test/payment',pay)
    app.router.add_post('/test/refund',refund)
    app.router.add_post('/test/tick',tick)
    await refunds.tick(db,SETTINGS,sim)
    async def worker():
        while True:
            await asyncio.sleep(5)
            await refunds.tick(db,SETTINGS,sim)
    task=asyncio.create_task(worker())
    runner=web.AppRunner(app,access_log=None);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',args.port);await site.start()
    port=site._server.sockets[0].getsockname()[1]
    Path(args.ready).write_text(json.dumps({'port':port,'pid':os.getpid()}),encoding='utf-8')
    try:
        await asyncio.Event().wait()
    finally:
        task.cancel();await runner.cleanup()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True);parser.add_argument('--ready',required=True)
    parser.add_argument('--port',type=int,default=0);parser.add_argument('--ttl',type=int,default=120)
    args=parser.parse_args()
    if not Path(args.ready).resolve().is_relative_to(LAB_ROOT.resolve()):
        raise ValueError('Ready file must be inside the isolated lab directory')
    asyncio.run(serve(args))


if __name__=='__main__':
    main()
