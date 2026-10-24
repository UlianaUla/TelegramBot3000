"""Same-origin Mini App API, Telegram authorization on every request."""
import hashlib
import hmac
import json
import time
import asyncio
from dataclasses import asdict
from urllib.parse import parse_qsl
from pathlib import Path
from aiohttp import web
from bot import finance
from bot.db import Database
from bot.config import Settings

DB = web.AppKey('db', Database)
CONFIG = web.AppKey('settings', Settings)
ACTOR = web.RequestKey('actor', int)


def validate_init_data(raw, settings, clock=None, *, manager=True):
    if not raw or len(raw)>16000:
        raise PermissionError("Откройте Mini App из бота")
    try:
        pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True)
        data=dict(pairs)
        if len(pairs)!=len(data):
            raise ValueError("duplicate fields")
        supplied=data.pop('hash')
        check='\n'.join(f'{k}={v}' for k,v in sorted(data.items()))
        secret=hmac.new(b'WebAppData',settings.bot_token.encode(),hashlib.sha256).digest()
        expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,supplied):
            raise ValueError("signature")
        age=(time.time() if clock is None else clock)-int(data['auth_date'])
        if age < -30 or age>settings.init_data_ttl:
            raise ValueError("expired")
        user=json.loads(data['user'])
        uid=user['id']
        if type(uid) is not int or (manager and uid not in settings.manager_ids):
            raise PermissionError("Финансы доступны только менеджеру")
        return uid
    except (ValueError,KeyError,TypeError) as exc:
        raise PermissionError("Неверная или просроченная авторизация Telegram. Откройте Mini App заново") from exc


@web.middleware
async def guard(request,handler):
    try:
        if request.path.startswith('/api/'):
            request[ACTOR]=validate_init_data(request.headers.get('X-Telegram-Init-Data',''), request.app[CONFIG])
        elif request.path.startswith('/shop/'):
            request[ACTOR]=validate_init_data(request.headers.get('X-Telegram-Init-Data',''),request.app[CONFIG],manager=False)
        response=await handler(request)
    except PermissionError as exc:
        response=web.json_response({'error':str(exc)},status=403)
    except (ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        response=web.json_response({'error':str(exc)},status=400)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    return response


async def summary(request):
    return web.json_response(finance.report(request.app[DB],request.query['start'],request.query['end']))


async def add_expense(request):
    body=await request.json()
    ident=finance.expense(request.app[DB],body['amount'],body['note'],request[ACTOR],body['key'])
    return web.json_response({'id':ident})


async def payment_action(request):
    body=await request.json()
    action=body['action']; pid=request.match_info['pid']
    if action not in {'succeeded','canceled','refund_pending','refund_succeeded','refund_error','check'}:
        raise ValueError("Неизвестное действие")
    db=request.app[DB]; p=finance.payment(db,pid)
    if p['provider']=='demo':
        raise ValueError('Имитатор отключён. Архивные учебные платежи доступны только для просмотра.')
    else:
        if action not in {'refund_pending','check'}:
            raise ValueError("Платёж ЮKassa проверяется только у провайдера")
        from bot.provider import sync_payment, start_refund
        if action=='refund_pending':
            await start_refund(db,request.app[CONFIG],p,request[ACTOR])
        else:
            await sync_payment(db,request.app[CONFIG],p)
    return web.json_response(finance.payment(db,pid))


async def index(request):
    return web.FileResponse(Path(__file__).parent/'static'/'index.html')


async def stock_view(request):
    from bot.stock import view
    return web.json_response(await asyncio.to_thread(view,request.app[DB]))


async def stock_receipt(request):
    from bot.stock import receive
    body=await request.json()
    if set(body)!={'product_id','quantity','key'}:
        raise ValueError('Нужны product_id, quantity и key; прямое изменение остатков запрещено')
    ident=await asyncio.to_thread(receive,request.app[DB],body['product_id'],body['quantity'],request[ACTOR],body['key'])
    return web.json_response({'id':ident})


async def retry_refund(request):
    from bot import stock
    db=request.app[DB]
    with db._transaction() as c:
        job=c.execute('SELECT * FROM refund_jobs WHERE payment_id=?',(request.match_info['pid'],)).fetchone()
        if job is None or job['state']=='succeeded':
            raise ValueError('Нет незавершённого возврата')
        if job['state']=='running' and job['lease_until'] and job['lease_until']>stock.stamp(stock.moment(db)):
            raise ValueError('Возврат сейчас проверяется; дождитесь результата')
        c.execute("UPDATE refund_jobs SET state='pending',next_attempt_at=?,lease_until=NULL WHERE payment_id=?",(stock.stamp(stock.moment(db)),job['payment_id']))
    return web.json_response({'queued':True})


async def shop_cart(request):
    from bot.stock import canonical
    body=await request.json()
    if set(body)!={'items'}:
        raise ValueError('Корзина принимает только состав items')
    items=canonical(body['items']);db=request.app[DB];uid=request[ACTOR]
    def update():
        db.ensure_user(uid)
        with db._transaction() as c:
            for item in items:
                if not c.execute('SELECT 1 FROM products WHERE id=? AND is_available=1',(item['product_id'],)).fetchone():
                    raise ValueError('Товар недоступен')
            c.execute('DELETE FROM cart_items WHERE user_id=?',(uid,))
            c.executemany('INSERT INTO cart_items VALUES (?,?,?)',[(uid,i['product_id'],i['quantity']) for i in items])
    await asyncio.to_thread(update)
    return web.json_response({'items':items})


async def shop_checkout(request):
    from bot.stock import checkout
    body=await request.json()
    if set(body)!={'key','items','name'}:
        raise ValueError('Нужны key, items, name. Покупатель не задаёт цену или срок')
    result=await asyncio.to_thread(checkout,request.app[DB],request[ACTOR],body['key'],body['items'],body['name'])
    return web.json_response(result,status=409 if result['kind']=='unavailable' else 200)


async def shop_cancel(request):
    from bot.stock import cancel
    body=await request.json()
    if set(body)!={'order_id'} or type(body['order_id']) is not int:
        raise ValueError('Нужен order_id')
    changed=await asyncio.to_thread(cancel,request.app[DB],request[ACTOR],body['order_id'])
    return web.json_response({'released':changed})


async def shop_order(request):
    from bot.stock import expire
    db=request.app[DB]
    await asyncio.to_thread(expire,db)
    order=db.get_order_for_user(request[ACTOR],int(request.match_info['oid']))
    if order is None:
        raise PermissionError('Чужой заказ недоступен')
    return web.json_response(asdict(order))


def create_app(db,settings):
    app=web.Application(middlewares=[guard],client_max_size=20000)
    app[DB]=db; app[CONFIG]=settings
    app.router.add_get('/',index)
    app.router.add_get('/api/summary',summary)
    app.router.add_post('/api/expenses',add_expense)
    app.router.add_post('/api/payments/{pid}',payment_action)
    app.router.add_get('/api/stock',stock_view)
    app.router.add_post('/api/stock/receipts',stock_receipt)
    app.router.add_post('/api/stock/refunds/{pid}/retry',retry_refund)
    app.router.add_post('/shop/cart',shop_cart)
    app.router.add_post('/shop/checkout',shop_checkout)
    app.router.add_post('/shop/cancel',shop_cancel)
    app.router.add_get('/shop/orders/{oid}',shop_order)
    return app


async def start_web(db,settings):
    runner=web.AppRunner(create_app(db,settings),access_log=None)
    await runner.setup()
    await web.TCPSite(runner,settings.web_host,settings.web_port).start()
    return runner
