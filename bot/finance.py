"""Payment state machine and immutable money journal. All values are integer kopecks."""
from datetime import datetime, timezone, date, timedelta
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def migrate(db):
    with db._transaction() as c:
        for table, column, definition in [
            ("products", "cost_kopecks", "INTEGER CHECK(cost_kopecks >= 0)"),
            ("order_items", "cost_kopecks", "INTEGER CHECK(cost_kopecks >= 0)"),
        ]:
            if column not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        # Current teaching costs, never backfilled into historic order items.
        for pid, cost in [(1,32000),(2,45000),(3,36000),(4,28000),(5,24000),(6,30000)]:
            c.execute("UPDATE products SET cost_kopecks=? WHERE id=? AND cost_kopecks IS NULL", (cost,pid))
        statements = [
            """CREATE TABLE IF NOT EXISTS payments (
                id TEXT PRIMARY KEY, order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
                provider TEXT NOT NULL CHECK(provider IN ('demo','yookassa')),
                provider_id TEXT UNIQUE, amount INTEGER NOT NULL CHECK(amount>0),
                currency TEXT NOT NULL CHECK(currency='RUB'),
                status TEXT NOT NULL CHECK(status IN ('pending','succeeded','canceled','refund_pending','refund_succeeded')),
                refund_id TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS finance_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('receipt','refund','expense')),
                amount INTEGER NOT NULL CHECK(amount>0), confirmed_at TEXT NOT NULL,
                order_id INTEGER REFERENCES orders(id), external_id TEXT UNIQUE,
                author TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                cost_amount INTEGER CHECK(cost_amount>=0), request_key TEXT UNIQUE,
                CHECK ((type='expense' AND order_id IS NULL) OR
                       (type IN ('receipt','refund') AND order_id IS NOT NULL)),
                UNIQUE(order_id,type))""",
            """CREATE TABLE IF NOT EXISTS finance_notifications (
                operation_id INTEGER NOT NULL REFERENCES finance_operations(id),
                manager_id INTEGER NOT NULL, PRIMARY KEY(operation_id,manager_id))""",
            """CREATE TABLE IF NOT EXISTS order_notifications (
                order_id INTEGER NOT NULL REFERENCES orders(id),
                manager_id INTEGER NOT NULL, PRIMARY KEY(order_id,manager_id))""",
        ]
        for statement in statements:
            c.execute(statement)


def _order(c, order_id):
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if row is None:
        raise ValueError("Заказ не найден")
    items = c.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    if not items or sum(i['price_kopecks']*i['quantity'] for i in items) != row['total_kopecks']:
        raise ValueError("Состав заказа не совпадает с итогом")
    cost = None if any(i['cost_kopecks'] is None for i in items) else sum(i['cost_kopecks']*i['quantity'] for i in items)
    return row, cost


def ensure_payment(db, order_id, provider, actor, provider_id=None):
    if provider not in {'demo','yookassa'}:
        raise ValueError("Неизвестный режим")
    with db._transaction() as c:
        order, _ = _order(c, order_id)
        if actor != order['user_id']:
            raise PermissionError("Чужой заказ")
        existing = c.execute("SELECT * FROM payments WHERE order_id=?", (order_id,)).fetchone()
        if existing:
            if existing['provider'] != provider or (provider_id and existing['provider_id'] not in {None,provider_id}):
                raise ValueError("Заказ уже связан с другим платежом")
            if provider_id and existing['provider_id'] is None:
                c.execute('UPDATE payments SET provider_id=? WHERE id=?',(provider_id,existing['id']))
                return dict(c.execute('SELECT * FROM payments WHERE id=?',(existing['id'],)).fetchone())
            return dict(existing)
        if order['payment_status'] != 'pending' or order['status'] == 'cancelled':
            raise ValueError("Заказ не ожидает онлайн-оплату")
        pid = uuid4().hex
        c.execute("INSERT INTO payments VALUES (?,?,?,?,?,'RUB','pending',NULL,?,?)",
                  (pid,order_id,provider,provider_id,order['total_kopecks'],now(),now()))
        if provider == 'demo':
            c.execute("UPDATE orders SET payment_method='Демонстрация без платёжного провайдера' WHERE id=?", (order_id,))
        return dict(c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone())


def payment(db, payment_id):
    c=db._connect()
    try:
        row=c.execute("SELECT * FROM payments WHERE id=?",(payment_id,)).fetchone()
        if row is None:
            raise ValueError("Платёж не найден")
        return dict(row)
    finally:
        c.close()


def apply_event(db, pid, *, status, amount, currency, order_id, external_id, provider, provider_id, author):
    """Only trusted adapters call this; status and journal are committed together."""
    with db._transaction() as c:
        p=c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        if p is None:
            raise ValueError("Платёж не найден")
        order,cost=_order(c,p['order_id'])
        if (type(amount) is not int or amount!=p['amount'] or amount!=order['total_kopecks']
            or currency!=p['currency'] or order_id!=p['order_id'] or provider!=p['provider']
            or provider_id!=p['provider_id'] or not external_id):
            raise ValueError("Не совпали сумма, валюта, заказ или ID платежа")
        if status == p['status']:
            return False
        if status=='succeeded' and p['status'] in {'refund_pending','refund_succeeded'}:
            return False
        if status in {'pending','canceled'} and p['status'] in {'succeeded','refund_pending','refund_succeeded'}:
            return False
        allowed={'pending':{'succeeded','canceled'},'canceled':{'succeeded'},'succeeded':{'refund_pending'},
                 'refund_pending':{'refund_succeeded'}}
        if status not in allowed.get(p['status'],set()):
            raise ValueError("Недопустимый переход состояния платежа")
        from bot import stock
        at=stock.stamp(stock.moment(db))
        event_time=at if db.clock else now()
        conflict=False
        if status=='succeeded':
            conflict=stock.accept_payment(c,order_id,pid,at)=='payment_conflict'
            if conflict:
                cost=0
        elif status=='canceled':
            stock.release(c,order_id,'canceled',at)
        elif status=='refund_pending':
            stock.queue_refund(c,pid,'manager_request',at)
        elif status=='refund_succeeded':
            stock.returned(c,order_id,at)
            receipt=c.execute("SELECT cost_amount FROM finance_operations WHERE order_id=? AND type='receipt'",(order_id,)).fetchone()
            if receipt:
                cost=receipt['cost_amount']
            c.execute("UPDATE refund_jobs SET state='succeeded',last_error=NULL,lease_until=NULL WHERE payment_id=?",(pid,))
        if status in {'succeeded','refund_succeeded'}:
            kind='receipt' if status=='succeeded' else 'refund'
            c.execute("""INSERT INTO finance_operations
                (type,amount,confirmed_at,order_id,external_id,author,cost_amount)
                VALUES (?,?,?,?,?,?,?)""",(kind,amount,event_time,order_id,external_id,str(author),cost))
            if conflict:
                c.execute("UPDATE finance_operations SET note='Поздняя оплата: резерв закончился, продажа не принята; ожидается полный возврат' WHERE order_id=? AND type='receipt'",(order_id,))
        c.execute("UPDATE payments SET status=?,updated_at=? WHERE id=?", (status,now(),pid))
        order_state={'succeeded':'succeeded','canceled':'canceled',
                     'refund_pending':'succeeded','refund_succeeded':'refunded'}[status]
        c.execute("UPDATE orders SET payment_status=? WHERE id=?", (order_state,order_id))
        if status in {'canceled','refund_succeeded'}:
            c.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
        return True


def demo_event(db,pid,status,actor,*,manager=False,fail=False):
    p=payment(db,pid)
    order=db.get_order(p['order_id'])
    if p['provider']!='demo' or (not manager and order.user_id!=actor):
        raise PermissionError("Платёж недоступен")
    if status.startswith('refund_') and not manager:
        raise PermissionError("Возврат доступен менеджеру")
    if fail:
        raise ValueError("Учебная техническая ошибка: возврат не подтверждён, сводка сохранена")
    return apply_event(db,pid,status=status,amount=p['amount'],currency=p['currency'],
        order_id=p['order_id'],external_id=f"demo:{pid}:{status}",provider='demo',
        provider_id=None,author=actor)


def expense(db,amount,note,actor,key):
    if type(amount) is not int or not 0<amount<=100000000 or not isinstance(note,str) or not 1<=len(note.strip())<=200:
        raise ValueError("Нужны положительная сумма в копейках и описание до 200 символов")
    if not isinstance(key,str) or not 8<=len(key)<=80:
        raise ValueError("Нужен ключ операции")
    with db._transaction() as c:
        old=c.execute("SELECT * FROM finance_operations WHERE request_key=?",(key,)).fetchone()
        if old:
            if old['amount']!=amount or old['note']!=note.strip() or old['author']!=str(actor):
                raise ValueError("Ключ уже использован для другого расхода")
            return old['id']
        return c.execute("""INSERT INTO finance_operations
            (type,amount,confirmed_at,author,note,request_key) VALUES ('expense',?,?,?,?,?)""",
            (amount,now(),str(actor),note.strip(),key)).lastrowid


def report(db,start,end):
    # Dates are UTC calendar dates, end day included.
    a=date.fromisoformat(start); b=date.fromisoformat(end)
    if a>b or (b-a).days>3660:
        raise ValueError("Некорректный период (максимум 10 лет)")
    lower=datetime.combine(a,datetime.min.time(),timezone.utc).isoformat()
    upper=datetime.combine(b+timedelta(days=1),datetime.min.time(),timezone.utc).isoformat()
    c=db._connect()
    try:
        c.execute('BEGIN')
        ops=[dict(r) for r in c.execute("SELECT * FROM finance_operations WHERE confirmed_at>=? AND confirmed_at<? ORDER BY id DESC",(lower,upper))]
        pays=[dict(r) for r in c.execute("SELECT id,order_id,provider,amount,currency,status FROM payments ORDER BY created_at DESC")]
    finally:
        c.close()
    totals={t:sum(o['amount'] for o in ops if o['type']==t) for t in ['receipt','refund','expense']}
    cash=totals['receipt']-totals['refund']-totals['expense']
    missing=sum(o['cost_amount'] is None for o in ops if o['type']!='expense')
    sold=sum(o['cost_amount'] or 0 for o in ops if o['type']=='receipt')
    restored=sum(o['cost_amount'] or 0 for o in ops if o['type']=='refund')
    return dict(start=start,end=end,timezone='UTC',**totals,cash_flow=cash,
                educational_result=None if missing else cash-sold+restored,
                sold_cost=sold,returned_cost=restored,missing_cost_operations=missing,
                operations=ops,payments=pays)
