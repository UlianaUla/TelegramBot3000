"""Single-warehouse state machine. Every mutation uses the caller's SQLite transaction."""
import hashlib
import json
from datetime import datetime,timezone,timedelta


class StockError(ValueError):
    pass


def moment(db):
    value=db.clock() if db.clock else datetime.now(timezone.utc)
    return value.astimezone(timezone.utc)


def stamp(value):
    return value.isoformat(timespec='microseconds')


def migrate(db):
    with db._transaction() as c:
        additions=[('orders','stock_state',"TEXT NOT NULL DEFAULT 'legacy' CHECK(stock_state IN ('legacy','awaiting_payment','paid','canceled','expired','payment_conflict','refunded'))"),
                   ('checkout_tokens','cart_json','TEXT')]
        for table,name,definition in additions:
            if name not in {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        for sql in [
            """CREATE TABLE IF NOT EXISTS inventory (
                product_id INTEGER PRIMARY KEY REFERENCES products(id),
                on_hand INTEGER NOT NULL DEFAULT 0 CHECK(typeof(on_hand)='integer' AND on_hand>=0),
                reserved INTEGER NOT NULL DEFAULT 0 CHECK(typeof(reserved)='integer' AND reserved>=0 AND reserved<=on_hand))""",
            """CREATE TABLE IF NOT EXISTS reservations (
                order_id INTEGER PRIMARY KEY REFERENCES orders(id),
                state TEXT NOT NULL CHECK(state IN ('active','consumed','released')),
                expires_at TEXT NOT NULL, release_reason TEXT)""",
            """CREATE TABLE IF NOT EXISTS reservation_items (
                order_id INTEGER NOT NULL REFERENCES reservations(order_id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>0),
                PRIMARY KEY(order_id,product_id))""",
            """CREATE TABLE IF NOT EXISTS inventory_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL
                CHECK(kind IN ('receipt','reserve','sale','release','return')),
                product_id INTEGER NOT NULL REFERENCES products(id),
                delta_on_hand INTEGER NOT NULL,delta_reserved INTEGER NOT NULL,
                order_id INTEGER REFERENCES orders(id),created_at TEXT NOT NULL,
                author TEXT NOT NULL,operation_key TEXT NOT NULL UNIQUE)""",
            """CREATE TABLE IF NOT EXISTS checkout_requests (
                user_id INTEGER NOT NULL REFERENCES users(telegram_id),request_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,cart_json TEXT NOT NULL,result_json TEXT NOT NULL,
                PRIMARY KEY(user_id,request_key))""",
            """CREATE TABLE IF NOT EXISTS refund_jobs (
                payment_id TEXT PRIMARY KEY REFERENCES payments(id),state TEXT NOT NULL
                CHECK(state IN ('pending','running','error','succeeded','manual_review')),
                operation_key TEXT NOT NULL UNIQUE,attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,lease_until TEXT,last_error TEXT,
                reason TEXT NOT NULL,created_at TEXT NOT NULL,provider_refund_id TEXT)""",
            'CREATE INDEX IF NOT EXISTS reservations_due ON reservations(state,expires_at)',
            'CREATE INDEX IF NOT EXISTS refund_jobs_due ON refund_jobs(state,next_attempt_at)',
        ]:
            c.execute(sql)
        c.execute('INSERT OR IGNORE INTO inventory(product_id) SELECT id FROM products')
        c.execute('''INSERT OR IGNORE INTO refund_jobs
            (payment_id,state,operation_key,attempts,next_attempt_at,reason,created_at,provider_refund_id)
            SELECT id,'pending','foliant-refund-'||id,1,updated_at,'legacy_refund_recovery',updated_at,refund_id
            FROM payments WHERE status='refund_pending' ''')


def canonical(items):
    if not isinstance(items,list) or not 1<=len(items)<=100:
        raise StockError('Нужен непустой состав заказа')
    result={}
    for line in items:
        if not isinstance(line,dict) or set(line)!={'product_id','quantity'}:
            raise StockError('Состав содержит только product_id и quantity; цену определяет сервер')
        pid=line['product_id']; qty=line['quantity']
        if type(pid) is not int or pid<=0 or type(qty) is not int or not 1<=qty<=99 or pid in result:
            raise StockError('Некорректный товар или количество')
        result[pid]=qty
    return [{'product_id':pid,'quantity':qty} for pid,qty in sorted(result.items())]


def fingerprint(items):
    return hashlib.sha256(json.dumps(canonical(items),sort_keys=True).encode()).hexdigest()


def fault(db,point):
    if db.fault_hook:
        db.fault_hook(point)


def movement(c,kind,pid,hand,reserved,order,author,key,at):
    c.execute('''INSERT INTO inventory_movements
        (kind,product_id,delta_on_hand,delta_reserved,order_id,created_at,author,operation_key)
        VALUES (?,?,?,?,?,?,?,?)''',(kind,pid,hand,reserved,order,at,str(author),key))


def receive(db,pid,quantity,author,key):
    if type(pid) is not int or type(quantity) is not int or not 0<quantity<=1000000 or not isinstance(key,str) or not 8<=len(key)<=120:
        raise StockError('Поступление: товар, положительное целое количество и ключ 8–120 символов')
    op='receipt:'+key
    with db._transaction() as c:
        old=c.execute('SELECT * FROM inventory_movements WHERE operation_key=?',(op,)).fetchone()
        if old:
            if old['product_id']!=pid or old['delta_on_hand']!=quantity or old['author']!=str(author):
                raise StockError('Ключ поступления уже использован для другой операции')
            return old['id']
        if not c.execute('SELECT 1 FROM products WHERE id=?',(pid,)).fetchone():
            raise StockError('Товар не найден')
        c.execute('INSERT OR IGNORE INTO inventory(product_id) VALUES (?)',(pid,))
        c.execute('UPDATE inventory SET on_hand=on_hand+? WHERE product_id=?',(quantity,pid))
        movement(c,'receipt',pid,quantity,0,None,author,op,stamp(moment(db)))
        return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def release(c,oid,reason,at,author='system'):
    r=c.execute('SELECT * FROM reservations WHERE order_id=?',(oid,)).fetchone()
    if r is None or r['state']!='active':
        return False
    for item in c.execute('SELECT * FROM reservation_items WHERE order_id=? ORDER BY product_id',(oid,)).fetchall():
        c.execute('UPDATE inventory SET reserved=reserved-? WHERE product_id=?',(item['quantity'],item['product_id']))
        movement(c,'release',item['product_id'],0,-item['quantity'],oid,author,
                 f'release:{oid}:{item["product_id"]}',at)
    c.execute("UPDATE reservations SET state='released',release_reason=? WHERE order_id=?",(reason,oid))
    c.execute("UPDATE orders SET stock_state=?,status='cancelled' WHERE id=?",('expired' if reason=='expired' else 'canceled',oid))
    return True


def expire_in(c,at):
    ids=[r['order_id'] for r in c.execute("SELECT order_id FROM reservations WHERE state='active' AND expires_at<=? ORDER BY order_id",(at,))]
    return sum(release(c,oid,'expired',at) for oid in ids)


def expire(db):
    with db._transaction() as c:
        return expire_in(c,stamp(moment(db)))


def checkout(db,uid,key,items,name,*,online=True):
    items=canonical(items)
    if type(uid) is not int or not isinstance(key,str) or not 1<=len(key)<=120 or not isinstance(name,str) or not 1<=len(name.strip())<=40:
        raise StockError('Некорректный покупатель, ключ или имя')
    digest=fingerprint(items);encoded=json.dumps(items,sort_keys=True)
    db.ensure_user(uid)
    with db._transaction() as c:
        at=stamp(moment(db));expire_in(c,at)
        old=c.execute('SELECT * FROM checkout_requests WHERE user_id=? AND request_key=?',(uid,key)).fetchone()
        if old:
            if old['fingerprint']!=digest:
                raise StockError('Тот же ключ нельзя использовать с другим составом')
            result=json.loads(old['result_json']);result['duplicate']=True
            return result
        cart=[dict(r) for r in c.execute('SELECT product_id,quantity FROM cart_items WHERE user_id=? ORDER BY product_id',(uid,))]
        if cart!=items:
            raise StockError('Состав не совпадает с вашей корзиной. Обновите заказ')
        products=[]; missing=[]
        for item in items:
            row=c.execute('''SELECT p.*,COALESCE(i.on_hand-i.reserved,0) AS available
                FROM products p LEFT JOIN inventory i ON i.product_id=p.id WHERE p.id=?''',(item['product_id'],)).fetchone()
            if row is None or not row['is_available'] or row['available']<item['quantity']:
                missing.append(item['product_id'])
            else:
                products.append((row,item['quantity']))
        if missing:
            result={'kind':'unavailable','missing':missing,'message':'Сейчас нет в наличии: весь заказ не оформлен'}
        else:
            from bot.format import FULFILLMENT,PAYMENT_YOOKASSA,PAYMENT_METHOD
            total=sum(p['price_kopecks']*qty for p,qty in products)
            cursor=c.execute('''INSERT INTO orders (user_id,customer_name,fulfillment,payment_method,
                total_kopecks,status,created_at,confirm_token,payment_status,stock_state)
                VALUES (?,?,?,?,?,'new',?,?,?,'awaiting_payment')''',
                (uid,name.strip(),FULFILLMENT,PAYMENT_YOOKASSA if online else PAYMENT_METHOD,total,at,
                 f'stock:{uid}:{key}','pending' if online else 'on_receipt'))
            oid=cursor.lastrowid
            fault(db,'checkout_after_order')
            expires=stamp(datetime.fromisoformat(at)+timedelta(seconds=db.reservation_seconds))
            c.execute("INSERT INTO reservations VALUES (?,'active',?,NULL)",(oid,expires))
            for p,qty in products:
                c.execute('''INSERT INTO order_items (order_id,product_id,title,price_kopecks,quantity,cost_kopecks)
                    VALUES (?,?,?,?,?,?)''',(oid,p['id'],p['title'],p['price_kopecks'],qty,p['cost_kopecks']))
                # Conditional update is an additional guard beneath BEGIN IMMEDIATE.
                updated=c.execute('UPDATE inventory SET reserved=reserved+? WHERE product_id=? AND on_hand-reserved>=?',(qty,p['id'],qty))
                if updated.rowcount!=1:
                    raise StockError('Остаток изменился. Повторите запрос с тем же ключом')
                c.execute('INSERT INTO reservation_items VALUES (?,?,?)',(oid,p['id'],qty))
                movement(c,'reserve',p['id'],0,qty,oid,uid,f'reserve:{oid}:{p["id"]}',at)
            result={'kind':'created','order_id':oid,'total_kopecks':total,'expires_at':expires,
                    'stock_state':'awaiting_payment','status':'new','payment_status':'pending' if online else 'on_receipt'}
            c.execute('DELETE FROM cart_items WHERE user_id=?',(uid,))
            fault(db,'checkout_before_commit')
        c.execute('INSERT INTO checkout_requests VALUES (?,?,?,?,?)',(uid,key,digest,encoded,json.dumps(result)))
    return result


def cancel(db,uid,oid):
    with db._transaction() as c:
        order=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
        if order is None or order['user_id']!=uid:
            raise PermissionError('Этот заказ вам недоступен')
        if order['stock_state'] in {'paid','refunded','payment_conflict'}:
            raise StockError('Оплаченный заказ нельзя отменить: нужен полный возврат')
        return release(c,oid,'canceled',stamp(moment(db)),uid)


def queue_refund(c,pid,reason,at):
    c.execute('''INSERT OR IGNORE INTO refund_jobs
        (payment_id,state,operation_key,next_attempt_at,reason,created_at)
        VALUES (?,'pending',?,?,?,?)''',(pid,f'foliant-refund-{pid}',at,reason,at))


def accept_payment(c,oid,pid,at):
    r=c.execute('SELECT * FROM reservations WHERE order_id=?',(oid,)).fetchone()
    if r is None:
        return 'legacy'
    if r['state']=='active' and at<r['expires_at']:
        for item in c.execute('SELECT * FROM reservation_items WHERE order_id=? ORDER BY product_id',(oid,)).fetchall():
            qty=item['quantity'];product=item['product_id']
            c.execute('UPDATE inventory SET on_hand=on_hand-?,reserved=reserved-? WHERE product_id=?',(qty,qty,product))
            movement(c,'sale',product,-qty,-qty,oid,'payment',f'sale:{oid}:{product}',at)
        c.execute("UPDATE reservations SET state='consumed' WHERE order_id=?",(oid,))
        c.execute("UPDATE orders SET stock_state='paid' WHERE id=?",(oid,))
        return 'paid'
    release(c,oid,'expired',at)
    c.execute("UPDATE orders SET stock_state='payment_conflict',status='cancelled' WHERE id=?",(oid,))
    queue_refund(c,pid,'late_payment',at)
    return 'payment_conflict'


def returned(c,oid,at):
    r=c.execute('SELECT * FROM reservations WHERE order_id=?',(oid,)).fetchone()
    if r and r['state']=='consumed':
        for item in c.execute('SELECT * FROM reservation_items WHERE order_id=? ORDER BY product_id',(oid,)).fetchall():
            c.execute('UPDATE inventory SET on_hand=on_hand+? WHERE product_id=?',(item['quantity'],item['product_id']))
            movement(c,'return',item['product_id'],item['quantity'],0,oid,'refund',f'return:{oid}:{item["product_id"]}',at)
    if r:
        c.execute("UPDATE orders SET stock_state='refunded' WHERE id=?",(oid,))


def view(db):
    expire(db)
    c=db._connect()
    try:
        c.execute('BEGIN')
        result={
            'server_time':stamp(moment(db)),
            'inventory':[dict(r) for r in c.execute('''SELECT p.id AS product_id,p.title,i.on_hand,i.reserved,
                i.on_hand-i.reserved AS available FROM inventory i JOIN products p ON p.id=i.product_id ORDER BY p.id''')],
            'reservations':[dict(r) for r in c.execute('''SELECT r.*,o.user_id,o.stock_state FROM reservations r
                JOIN orders o ON o.id=r.order_id WHERE r.state='active' OR r.order_id IN
                (SELECT order_id FROM reservations ORDER BY order_id DESC LIMIT 200)
                ORDER BY CASE WHEN r.state='active' THEN 0 ELSE 1 END,r.order_id DESC''')],
            'movements':[dict(r) for r in c.execute('SELECT * FROM inventory_movements ORDER BY id DESC LIMIT 500')],
            'refund_jobs':[dict(r) for r in c.execute('''SELECT j.*,p.order_id,p.amount,p.provider FROM refund_jobs j
                JOIN payments p ON p.id=j.payment_id WHERE j.state!='succeeded' OR j.payment_id IN
                (SELECT payment_id FROM refund_jobs ORDER BY created_at DESC LIMIT 200)
                ORDER BY j.created_at DESC''')],
            'conflicts':[dict(r) for r in c.execute("SELECT id,stock_state,payment_status FROM orders WHERE stock_state='payment_conflict'")],
        }
        c.execute('COMMIT')
        return result
    finally:
        c.close()


def check_invariants(db):
    c=db._connect()
    try:
        c.execute('BEGIN')
        for r in c.execute('''SELECT i.*,COALESCE((SELECT sum(ri.quantity) FROM reservation_items ri
            JOIN reservations re ON re.order_id=ri.order_id WHERE ri.product_id=i.product_id AND re.state='active'),0) AS expected
            FROM inventory i'''):
            if not 0<=r['reserved']<=r['on_hand'] or r['expected']!=r['reserved']:
                raise AssertionError(f'Inventory invariant failed for product {r["product_id"]}')
            sums=c.execute('SELECT COALESCE(sum(delta_on_hand),0),COALESCE(sum(delta_reserved),0) FROM inventory_movements WHERE product_id=?',(r['product_id'],)).fetchone()
            if tuple(sums)!=(r['on_hand'],r['reserved']):
                raise AssertionError('Warehouse journal does not reconcile')
        for reservation in c.execute('SELECT r.*,o.stock_state FROM reservations r JOIN orders o ON o.id=r.order_id'):
            if reservation['state']=='active' and reservation['stock_state']!='awaiting_payment':
                raise AssertionError('Active reserve has an incompatible order state')
            sale_count=c.execute("SELECT count(*) FROM inventory_movements WHERE order_id=? AND kind='sale'",(reservation['order_id'],)).fetchone()[0]
            item_count=c.execute('SELECT count(*) FROM reservation_items WHERE order_id=?',(reservation['order_id'],)).fetchone()[0]
            if (reservation['state']=='consumed' and sale_count!=item_count) or (reservation['state']!='consumed' and sale_count):
                raise AssertionError('Sale does not match the consumed reservation')
            returned_count=c.execute("SELECT count(*) FROM inventory_movements WHERE order_id=? AND kind='return'",(reservation['order_id'],)).fetchone()[0]
            expected=item_count if reservation['state']=='consumed' and reservation['stock_state']=='refunded' else 0
            if returned_count!=expected:
                raise AssertionError('Stock return does not match an accepted sale')
        return True
    finally:
        c.close()
