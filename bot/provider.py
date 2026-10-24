"""Verified YooKassa snapshots -> the same state machine used by the simulator."""
import asyncio
from decimal import Decimal, InvalidOperation
from bot import finance
from bot.yookassa_api import YooKassaError, amount_value


def configure(settings):
    if not settings.yookassa_enabled:
        raise ValueError("ЮKassa не настроена")
    from yookassa import Configuration
    Configuration.account_id=settings.yookassa_shop_id
    Configuration.secret_key=settings.yookassa_secret_key


def snapshot(settings,provider_id,refund=False):
    configure(settings)
    from yookassa import Payment,Refund
    try:
        return (Refund if refund else Payment).find_one(provider_id)
    except Exception as exc:
        raise ValueError("Не удалось подтвердить результат у ЮKassa. Финансовая сводка не изменена") from exc


def kopecks(value):
    try:
        n=Decimal(str(value))*100
        if not n.is_finite() or n!=n.to_integral_value() or n<=0:
            raise ValueError("Некорректная сумма провайдера")
        return int(n)
    except InvalidOperation as exc:
        raise ValueError("Некорректная сумма провайдера") from exc


async def sync_payment(db,settings,p):
    if not p['provider_id']:
        return
    refund=p['status']=='refund_pending' and bool(p['refund_id'])
    if p['status'] not in {'pending','refund_pending'} or (p['status']=='refund_pending' and not refund):
        return
    obj=await asyncio.to_thread(snapshot,settings,p['refund_id'] if refund else p['provider_id'],refund)
    if str(obj.id)!=(p['refund_id'] if refund else p['provider_id']):
        raise ValueError("ID ответа ЮKassa не совпал")
    if refund:
        if str(obj.payment_id)!=p['provider_id']:
            raise ValueError("Возврат относится к другому платежу")
        order_id=p['order_id']
    else:
        order_id=int((obj.metadata or {}).get('order_id',0))
    amount=kopecks(obj.amount.value); currency=str(obj.amount.currency)
    if amount!=p['amount'] or currency!='RUB' or order_id!=p['order_id']:
        raise ValueError("Сумма, валюта или заказ ЮKassa не совпали")
    if obj.status=='canceled' and refund:
        raise ValueError("ЮKassa отменила возврат. Исходная финансовая сводка сохранена")
    if obj.status not in {'succeeded','canceled'}:
        return
    status='refund_succeeded' if refund else obj.status
    finance.apply_event(db,p['id'],status=status,amount=amount,currency=currency,
        order_id=order_id,external_id=f"yookassa:{obj.id}:{status}",provider='yookassa',
        provider_id=p['provider_id'],author='yookassa')


async def start_refund(db,settings,p,actor):
    from bot.refunds import request,process_one
    configure(settings)
    request(db,p['id'],actor)
    await process_one(db,settings,force=True)
    c=db._connect()
    try:
        job=c.execute('SELECT * FROM refund_jobs WHERE payment_id=?',(p['id'],)).fetchone()
    finally:
        c.close()
    if job and job['state'] in {'error','manual_review'}:
        raise ValueError(job['last_error'])
