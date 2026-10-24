import asyncio
import logging
from bot.format import money
from bot.provider import sync_payment

log=logging.getLogger(__name__)


async def tick(db,settings,bot):
    c=db._connect()
    try:
        pending=[dict(r) for r in c.execute("SELECT * FROM payments WHERE provider='yookassa' AND status IN ('pending','refund_pending')")]
    finally:
        c.close()
    if settings.yookassa_enabled:
        for p in pending:
            try:
                await sync_payment(db,settings,p)
            except (ValueError,RuntimeError):
                log.warning('Не удалось подтвердить платёж или возврат заказа %s',p['order_id'])
    for manager in settings.manager_ids:
        for order in db.list_new_orders():
            c=db._connect()
            try:
                sent=c.execute('SELECT 1 FROM order_notifications WHERE order_id=? AND manager_id=?',(order.id,manager)).fetchone()
            finally:
                c.close()
            if sent:
                continue
            try:
                await bot.send_message(manager,f'Новый заказ №{order.id}: {money(order.total_kopecks)}. Способ: {order.payment_method}. Откройте «Новые заказы».')
            except Exception:
                log.warning('Уведомление о заказе менеджеру %s не доставлено',manager)
                break
            with db._transaction() as c:
                c.execute('INSERT OR IGNORE INTO order_notifications VALUES (?,?)',(order.id,manager))
        c=db._connect()
        try:
            ops=[dict(r) for r in c.execute("""SELECT * FROM finance_operations o WHERE NOT EXISTS
                (SELECT 1 FROM finance_notifications n WHERE n.operation_id=o.id AND n.manager_id=?) ORDER BY o.id LIMIT 25""",(manager,))]
        finally:
            c.close()
        for op in ops:
            label={'receipt':'Подтверждено поступление','refund':'Подтверждён полный возврат','expense':'Записан расход'}[op['type']]
            try:
                await bot.send_message(manager,f'{label}: {money(op["amount"])}. Заказ: {op["order_id"] or "—"}. {op["note"]} /summary')
            except Exception:
                log.warning('Уведомление менеджеру %s не доставлено',manager)
                break
            with db._transaction() as c:
                c.execute('INSERT OR IGNORE INTO finance_notifications VALUES (?,?)',(op['id'],manager))


async def run(db,settings,bot):
    while True:
        try:
            await tick(db,settings,bot)
        except Exception:
            log.warning('Ошибка фоновой сверки финансов; повтор через 15 секунд')
        await asyncio.sleep(15)
