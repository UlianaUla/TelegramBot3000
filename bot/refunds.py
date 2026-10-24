"""Durable outbox. External IO never runs while a SQLite transaction is open."""
import asyncio
from datetime import datetime,timedelta
from bot import stock,finance


def request(db,pid,actor):
    p=finance.payment(db,pid)
    if p['status'] not in {'succeeded','refund_pending'}:
        raise ValueError('Полный возврат уже завершён или платёж не оплачен')
    if p['status']=='succeeded':
        finance.apply_event(db,pid,status='refund_pending',amount=p['amount'],currency=p['currency'],
            order_id=p['order_id'],external_id='refund-request:'+pid,provider=p['provider'],
            provider_id=p['provider_id'],author=actor)
    else:
        with db._transaction() as c:
            stock.queue_refund(c,pid,'manager_request',stock.stamp(stock.moment(db)))
    return finance.payment(db,pid)


class YooRefunds:
    def __init__(self,settings):
        self.settings=settings

    def reconcile(self,job,p):
        from bot.provider import configure,kopecks
        from bot.yookassa_api import amount_value,_receipt
        from yookassa import Refund
        configure(self.settings)
        rid=job['provider_refund_id'] or p['refund_id']
        if rid:
            obj=Refund.find_one(rid)
        else:
            # First resolve a response lost after the provider committed the refund.
            params={'payment_id':p['provider_id'],'limit':100}
            candidates=[]
            while True:
                page=Refund.list(params)
                candidates.extend(page.items or [])
                cursor=getattr(page,'next_cursor',None)
                if not cursor:
                    break
                params['cursor']=cursor
            matches=[x for x in candidates if str(x.payment_id)==p['provider_id']
                     and kopecks(x.amount.value)==p['amount'] and str(x.amount.currency)==p['currency']]
            if len(matches)>1 or (candidates and not matches):
                raise RuntimeError('Возвраты провайдера требуют ручной сверки: новый возврат не отправлен')
            if matches:
                obj=matches[0]
            else:
                age=stock.moment(self.db)-datetime.fromisoformat(job['created_at'])
                if job['attempts']>1 and age.total_seconds()>23*3600:
                    raise RuntimeError('Срок идемпотентности близок к окончанию: нужна ручная сверка, новый возврат заблокирован')
                payload={'payment_id':p['provider_id'],'amount':{'value':amount_value(p['amount']),'currency':p['currency']}}
                if self.settings.yookassa_receipt_email:
                    payload['receipt']=_receipt(self.db.get_order(p['order_id']),self.settings.yookassa_receipt_email)
                obj=Refund.create(payload,job['operation_key'])
        if (str(obj.payment_id)!=p['provider_id'] or kopecks(obj.amount.value)!=p['amount']
            or str(obj.amount.currency)!=p['currency']):
            raise ValueError('Ответ возврата не совпал с сохранённым платежом')
        return {'id':str(obj.id),'status':str(obj.status)}


def claim(db,provider=None,force=False):
    with db._transaction() as c:
        at=stock.stamp(stock.moment(db))
        row=c.execute('''SELECT j.* FROM refund_jobs j JOIN payments p ON p.id=j.payment_id
            WHERE j.state IN ('pending','error','running') AND (j.lease_until IS NULL OR j.lease_until<=?)
            AND (? OR j.next_attempt_at<=?) AND (? IS NULL OR p.provider=?) ORDER BY j.created_at LIMIT 1''',
            (at,force,at,provider,provider)).fetchone()
        if row is None:
            return None
        lease=stock.stamp(stock.moment(db)+timedelta(seconds=30))
        c.execute("UPDATE refund_jobs SET state='running',attempts=attempts+1,lease_until=? WHERE payment_id=?",(lease,row['payment_id']))
        return dict(c.execute('SELECT * FROM refund_jobs WHERE payment_id=?',(row['payment_id'],)).fetchone())


async def process_one(db,settings,adapter=None,*,force=False):
    provider='demo' if adapter is not None else 'yookassa'
    if adapter is None and not settings.yookassa_enabled:
        return False
    job=claim(db,provider,force)
    if not job:
        return False
    if adapter is None:
        adapter=YooRefunds(settings);adapter.db=db
    p=finance.payment(db,job['payment_id'])
    try:
        if p['status']=='succeeded':
            request(db,p['id'],'refund-worker')
            p=finance.payment(db,p['id'])
        obj=await asyncio.to_thread(adapter.reconcile,job,p)
        if not isinstance(obj.get('id'),str) or not obj['id']:
            raise ValueError('Провайдер не вернул ID возврата')
        with db._transaction() as c:
            c.execute('UPDATE refund_jobs SET provider_refund_id=? WHERE payment_id=?',(obj['id'],p['id']))
            c.execute('UPDATE payments SET refund_id=? WHERE id=?',(obj['id'],p['id']))
        if obj['status']=='succeeded':
            finance.apply_event(db,p['id'],status='refund_succeeded',amount=p['amount'],currency=p['currency'],
                order_id=p['order_id'],external_id=f'{p["provider"]}:{obj["id"]}:refund_succeeded',
                provider=p['provider'],provider_id=p['provider_id'],author='refund-worker')
        elif obj['status']=='canceled':
            raise RuntimeError('Провайдер отменил возврат: нужна ручная сверка, деньги и склад не возвращены')
        else:
            with db._transaction() as c:
                c.execute("UPDATE refund_jobs SET state='pending',lease_until=NULL,next_attempt_at=? WHERE payment_id=?",(stock.stamp(stock.moment(db)+timedelta(seconds=5)),p['id']))
    except Exception as exc:
        # Avoid persisting SDK descriptions that may contain request details/credentials.
        message=str(exc) if isinstance(exc,(ValueError,RuntimeError,ConnectionError,TimeoutError)) else 'Ошибка сети или провайдера; будет повторная сверка'
        with db._transaction() as c:
            c.execute('''UPDATE refund_jobs SET state=?,last_error=?,lease_until=NULL,next_attempt_at=?
                WHERE payment_id=? AND state!='succeeded' ''',
                ('manual_review' if isinstance(exc,RuntimeError) else 'error',message[:300],
                 stock.stamp(stock.moment(db)+timedelta(seconds=min(60,5*job['attempts']))),p['id']))
    return True


async def tick(db,settings,adapter=None):
    stock.expire(db)
    # Bound each tick, while lease and UNIQUE protect competing worker processes.
    for _ in range(20):
        if not await process_one(db,settings,adapter):
            break


async def run(db,settings):
    import logging
    async def expiry_loop():
        while True:
            try:
                await asyncio.to_thread(stock.expire,db)
            except Exception:
                logging.warning('Склад: освобождение резерва не завершилось; повтор через 5 секунд')
            await asyncio.sleep(5)
    async def refund_loop():
        while True:
            try:
                for _ in range(20):
                    if not await process_one(db,settings):
                        break
            except Exception:
                logging.warning('Склад: сверка возврата не завершилась; задание сохранено')
            await asyncio.sleep(5)
    await asyncio.gather(expiry_loop(),refund_loop())
