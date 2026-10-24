"""SQLite: каталог, корзины, заказы. Сумма считается здесь"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import secrets
import sqlite3
import json
import time
from typing import Iterator

from bot.config import ROOT
from bot.format import FULFILLMENT, PAYMENT_METHOD, PAYMENT_YOOKASSA

DB_PATH = ROOT / "data" / "foliant.db"
MAX_QUANTITY = 99

# Начальный учебный ассортимент. Попадает в базу при первом запуске, если каталог пуст.
CATALOG = (
    (1, "Тихий переулок", "А. Лесная. Повесть о городском дворе.", 64000),
    (2, "Схемы для начинающих", "М. Чертёж. Учебное пособие по схемам.", 89000),
    (3, "Остров карманных карт", "И. Компас. Приключенческая повесть.", 72000),
    (4, "Каталог невозможных вещей", "С. Архив. Сборник коротких рассказов.", 56000),
    (5, "Практикум по вниманию", "Е. Ясная. Упражнения, учебное издание.", 48000),
    (6, "Письма с чердака", "Н. Чернила. Переписка вымышленных героев.", 61000),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'telegram'
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    price_kopecks INTEGER NOT NULL CHECK (price_kopecks > 0),
    is_available INTEGER NOT NULL DEFAULT 1 CHECK (is_available IN (0, 1))
);

CREATE TABLE IF NOT EXISTS cart_items (
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (user_id, product_id),
    FOREIGN KEY (user_id) REFERENCES users (telegram_id),
    FOREIGN KEY (product_id) REFERENCES products (id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    fulfillment TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    total_kopecks INTEGER NOT NULL CHECK (total_kopecks > 0),
    status TEXT NOT NULL CHECK (status IN ('new', 'ready', 'done', 'cancelled')),
    created_at TEXT NOT NULL,
    confirm_token TEXT NOT NULL UNIQUE,
    payment_status TEXT NOT NULL DEFAULT 'on_receipt',
    yookassa_payment_id TEXT,
    yookassa_confirmation_url TEXT,
    FOREIGN KEY (user_id) REFERENCES users (telegram_id)
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    product_id INTEGER,
    title TEXT NOT NULL,
    price_kopecks INTEGER NOT NULL CHECK (price_kopecks > 0),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    FOREIGN KEY (order_id) REFERENCES orders (id),
    FOREIGN KEY (product_id) REFERENCES products (id)
);

CREATE TABLE IF NOT EXISTS checkout_tokens (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    fulfillment TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (telegram_id)
);
"""


@dataclass
class Product:
    id: int
    title: str
    description: str
    price_kopecks: int
    is_available: bool
    available: int = 0


@dataclass
class CartLine:
    product_id: int
    title: str
    price_kopecks: int
    quantity: int

    @property
    def line_total(self) -> int:
        return self.price_kopecks * self.quantity


@dataclass
class OrderItem:
    title: str
    price_kopecks: int
    quantity: int
    product_id: int | None
    cost_kopecks: int | None = None


@dataclass
class Order:
    id: int
    user_id: int
    customer_name: str
    fulfillment: str
    payment_method: str
    total_kopecks: int
    status: str
    created_at: str
    payment_status: str = "on_receipt"
    yookassa_payment_id: str | None = None
    yookassa_confirmation_url: str | None = None
    items: list[OrderItem] = field(default_factory=list)
    stock_state: str = 'legacy'
    reservation_state: str | None = None
    expires_at: str | None = None


@dataclass
class CheckoutDraft:
    token: str
    lines: list[CartLine]
    total_kopecks: int
    customer_name: str
    fulfillment: str
    payment_method: str


@dataclass
class ConfirmResult:
    kind: str
    order_id: int | None = None
    total_kopecks: int | None = None
    status: str | None = None
    payment_status: str | None = None
    payment_method: str | None = None
    confirmation_url: str | None = None


class Database:
    def __init__(self, path: Path = DB_PATH, *, clock=None, reservation_seconds=120, fault_hook=None) -> None:
        self.path = Path(path)
        self.clock = clock
        self.reservation_seconds = reservation_seconds
        self.fault_hook = fault_hook
        if type(reservation_seconds) is not int or not 1<=reservation_seconds<=86400:
            raise ValueError('Reservation TTL must be 1–86400 seconds')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            for attempt in range(3):
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as exc:
                    if 'locked' not in str(exc).lower() and 'busy' not in str(exc).lower():
                        raise
                    if attempt == 2:
                        raise ValueError('База занята. Безопасно повторите запрос с тем же ключом') from exc
                    time.sleep(.05*(attempt+1))
            yield connection
            connection.execute("COMMIT")
        except Exception:
            _safe_rollback(connection)
            raise
        finally:
            connection.close()

    def _prepare(self) -> None:
        connection = self._connect()
        try:
            for statement in SCHEMA.split(";"):
                sql = statement.strip()
                if sql:
                    connection.execute(sql)
            _migrate_orders(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                count = connection.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
                if count == 0:
                    connection.executemany(
                        """
                        INSERT INTO products (id, title, description, price_kopecks, is_available)
                        VALUES (?, ?, ?, ?, 1)
                        """,
                        CATALOG,
                    )
                connection.execute("COMMIT")
                from bot.finance import migrate
                migrate(self)
                from bot.stock import migrate as migrate_stock
                migrate_stock(self)
            except Exception:
                _safe_rollback(connection)
                raise
        finally:
            connection.close()

    def product_count(self) -> int:
        connection = self._connect()
        try:
            return int(connection.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"])
        finally:
            connection.close()

    def ensure_user(self, telegram_id: int) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT OR IGNORE INTO users (telegram_id, platform) VALUES (?, 'telegram')",
                (telegram_id,),
            )
        finally:
            connection.close()

    def list_products(self) -> list[Product]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT id, title, description, price_kopecks, is_available,
                       COALESCE((SELECT on_hand-reserved FROM inventory WHERE product_id=products.id),0) AS available
                FROM products
                WHERE is_available = 1
                ORDER BY id
                """
            ).fetchall()
        finally:
            connection.close()
        return [_product(row) for row in rows]

    def get_product(self, product_id: int) -> Product | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT id, title, description, price_kopecks, is_available,
                       COALESCE((SELECT on_hand-reserved FROM inventory WHERE product_id=products.id),0) AS available
                FROM products
                WHERE id = ? AND is_available = 1
                """,
                (product_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return _product(row)

    def add_to_cart(self, user_id: int, product_id: int) -> tuple[bool, str, int]:
        self.ensure_user(user_id)
        if self.get_product(product_id) is None:
            return False, "Этой книги нет в каталоге.", 0
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT quantity FROM cart_items WHERE user_id = ? AND product_id = ?",
                (user_id, product_id),
            ).fetchone()
            quantity = 1 if current is None else int(current["quantity"]) + 1
            if quantity > MAX_QUANTITY:
                return False, f"Больше {MAX_QUANTITY} экземпляров одной книги добавить нельзя.", int(current["quantity"])
            if current is None:
                connection.execute(
                    "INSERT INTO cart_items (user_id, product_id, quantity) VALUES (?, ?, 1)",
                    (user_id, product_id),
                )
            else:
                connection.execute(
                    "UPDATE cart_items SET quantity = ? WHERE user_id = ? AND product_id = ?",
                    (quantity, user_id, product_id),
                )
            _drop_tokens(connection, user_id)
        return True, "", quantity

    def change_quantity(self, user_id: int, product_id: int, delta: int) -> tuple[bool, str]:
        if delta == 0:
            return False, "Количество не изменилось."
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT quantity FROM cart_items WHERE user_id = ? AND product_id = ?",
                (user_id, product_id),
            ).fetchone()
            if current is None:
                return False, "Этой книги уже нет в корзине."
            new_quantity = int(current["quantity"]) + delta
            if new_quantity <= 0:
                return False, "Количество не может быть нулевым или отрицательным. Удалите позицию кнопкой «Удалить»."
            if new_quantity > MAX_QUANTITY:
                return False, f"Больше {MAX_QUANTITY} экземпляров одной книги добавить нельзя."
            connection.execute(
                "UPDATE cart_items SET quantity = ? WHERE user_id = ? AND product_id = ?",
                (new_quantity, user_id, product_id),
            )
            _drop_tokens(connection, user_id)
        return True, ""

    def remove_from_cart(self, user_id: int, product_id: int) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM cart_items WHERE user_id = ? AND product_id = ?",
                (user_id, product_id),
            )
            _drop_tokens(connection, user_id)
            return cursor.rowcount > 0

    def get_cart(self, user_id: int) -> tuple[list[CartLine], int]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT c.product_id, p.title, p.price_kopecks, c.quantity
                FROM cart_items AS c
                JOIN products AS p ON p.id = c.product_id
                WHERE c.user_id = ? AND p.is_available = 1
                ORDER BY p.id
                """,
                (user_id,),
            ).fetchall()
        finally:
            connection.close()
        lines = [
            CartLine(
                product_id=int(row["product_id"]),
                title=str(row["title"]),
                price_kopecks=int(row["price_kopecks"]),
                quantity=int(row["quantity"]),
            )
            for row in rows
        ]
        total = sum(line.line_total for line in lines)
        return lines, total

    def cancel_checkout(self, user_id: int) -> None:
        connection = self._connect()
        try:
            connection.execute("DELETE FROM checkout_tokens WHERE user_id = ?", (user_id,))
        finally:
            connection.close()

    def create_checkout(self, user_id: int, customer_name: str) -> CheckoutDraft | None:
        name = customer_name.strip()
        if not name:
            return None
        self.ensure_user(user_id)
        token = secrets.token_hex(8)
        with self._transaction() as connection:
            rows = _cart_rows(connection, user_id)
            if not rows:
                return None
            connection.execute("DELETE FROM checkout_tokens WHERE user_id = ?", (user_id,))
            connection.execute(
                """
                INSERT INTO checkout_tokens (token, user_id, customer_name, fulfillment, payment_method)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token, user_id, name, FULFILLMENT, PAYMENT_METHOD),
            )
            connection.execute('UPDATE checkout_tokens SET cart_json=? WHERE token=?',(json.dumps([{'product_id':int(r['product_id']),'quantity':int(r['quantity'])} for r in rows]),token))
        lines = _lines(rows)
        return CheckoutDraft(
            token=token,
            lines=lines,
            total_kopecks=sum(line.line_total for line in lines),
            customer_name=name,
            fulfillment=FULFILLMENT,
            payment_method=PAYMENT_METHOD,
        )

    def confirm(self, user_id: int, token: str, *, online: bool = False) -> ConfirmResult:
        from bot import stock
        c=self._connect()
        try:
            row=c.execute('SELECT * FROM checkout_tokens WHERE token=?',(token,)).fetchone()
            old=c.execute('SELECT * FROM checkout_requests WHERE user_id=? AND request_key=?',(user_id,token)).fetchone()
            if row is None:
                if old is None:
                    return ConfirmResult('stale')
                items=json.loads(old['cart_json']);name='Повтор заказа'
            elif row['user_id']!=user_id:
                return ConfirmResult('forbidden')
            else:
                items=json.loads(row['cart_json']) if row['cart_json'] else [dict(x) for x in c.execute('SELECT product_id,quantity FROM cart_items WHERE user_id=? ORDER BY product_id',(user_id,))]
                name=row['customer_name']
        finally:
            c.close()
        if not items:
            return ConfirmResult('empty')
        result=stock.checkout(self,user_id,token,items,name,online=online)
        if result['kind']=='unavailable':
            return ConfirmResult('unavailable')
        order=self.get_order(result['order_id'])
        return ConfirmResult('duplicate' if result.get('duplicate') else 'created',
            order_id=order.id,total_kopecks=order.total_kopecks,status=order.status,
            payment_status=order.payment_status,payment_method=order.payment_method,
            confirmation_url=order.yookassa_confirmation_url)

    def list_orders(self, user_id: int) -> list[Order]:
        return self._select_orders("WHERE o.user_id = ? ORDER BY o.id DESC", (user_id,))

    def list_new_orders(self) -> list[Order]:
        return self._select_orders(
            """
            WHERE o.status = 'new'
              AND o.payment_status IN ('on_receipt', 'succeeded')
            ORDER BY o.id
            """,
            (),
        )

    def list_pending_online(self, user_id: int | None = None) -> list[Order]:
        if user_id is None:
            return self._select_orders(
                "WHERE o.payment_status = 'pending' AND o.yookassa_payment_id IS NOT NULL ORDER BY o.id",
                (),
            )
        return self._select_orders(
            """
            WHERE o.user_id = ? AND o.payment_status = 'pending'
              AND o.yookassa_payment_id IS NOT NULL
            ORDER BY o.id
            """,
            (user_id,),
        )

    def attach_payment(self, order_id: int, payment_id: str, confirmation_url: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE orders
                SET yookassa_payment_id = ?, yookassa_confirmation_url = ?
                WHERE id = ? AND payment_status = 'pending'
                """,
                (payment_id, confirmation_url, order_id),
            )

    def mark_paid(self, order_id: int) -> None:
        raise ValueError("Используйте проверенное событие платежа finance.apply_event")

    def mark_payment_canceled(self, order_id: int) -> None:
        raise ValueError("Используйте проверенное событие платежа finance.apply_event")

    def list_all_orders(self) -> list[Order]:
        return self._select_orders("ORDER BY o.id", ())

    def get_order_for_user(self, user_id: int, order_id: int) -> Order | None:
        found = self._select_orders("WHERE o.user_id = ? AND o.id = ?", (user_id, order_id))
        return found[0] if found else None

    def get_order(self, order_id: int) -> Order | None:
        found = self._select_orders("WHERE o.id = ?", (order_id,))
        return found[0] if found else None

    def set_status(self, order_id: int, status: str) -> Order | None:
        if status not in {"new", "ready", "done", "cancelled"}:
            return None
        order=self.get_order(order_id)
        if order and status=='done' and order.stock_state not in {'legacy','paid'}:
            return None
        if order and status=='cancelled' and order.stock_state!='legacy':
            from bot.stock import cancel
            try:
                cancel(self,order.user_id,order_id)
            except ValueError:
                return None
            return self.get_order(order_id)
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                (status, order_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_order(order_id)

    def _select_orders(self, clause: str, params: tuple[object, ...]) -> list[Order]:
        connection = self._connect()
        try:
            connection.execute('BEGIN')
            rows = connection.execute(
                f"""
                SELECT o.id, o.user_id, o.customer_name, o.fulfillment, o.payment_method,
                       o.total_kopecks, o.status, o.created_at, o.payment_status,
                       o.yookassa_payment_id, o.yookassa_confirmation_url, o.stock_state
                FROM orders AS o
                {clause}
                """,
                params,
            ).fetchall()
            orders = [_order(row) for row in rows]
            for order in orders:
                reserve=connection.execute('SELECT * FROM reservations WHERE order_id=?',(order.id,)).fetchone()
                if reserve:
                    order.reservation_state=reserve['state'];order.expires_at=reserve['expires_at']
                item_rows = connection.execute(
                    """
                    SELECT title, price_kopecks, quantity, product_id, cost_kopecks
                    FROM order_items
                    WHERE order_id = ?
                    ORDER BY id
                    """,
                    (order.id,),
                ).fetchall()
                order.items = [
                    OrderItem(
                        title=str(item["title"]),
                        price_kopecks=int(item["price_kopecks"]),
                        quantity=int(item["quantity"]),
                        product_id=None if item["product_id"] is None else int(item["product_id"]),
                        cost_kopecks=item['cost_kopecks'],
                    )
                    for item in item_rows
                ]
            return orders
        finally:
            connection.close()


def _product(row: sqlite3.Row) -> Product:
    return Product(
        id=int(row["id"]),
        title=str(row["title"]),
        description=str(row["description"]),
        price_kopecks=int(row["price_kopecks"]),
        is_available=bool(row["is_available"]),
        available=int(row["available"]),
    )


def _order(row: sqlite3.Row) -> Order:
    return Order(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        customer_name=str(row["customer_name"]),
        fulfillment=str(row["fulfillment"]),
        payment_method=str(row["payment_method"]),
        total_kopecks=int(row["total_kopecks"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        stock_state=str(row["stock_state"]),
        payment_status=str(row["payment_status"]),
        yookassa_payment_id=None if row["yookassa_payment_id"] is None else str(row["yookassa_payment_id"]),
        yookassa_confirmation_url=(
            None if row["yookassa_confirmation_url"] is None else str(row["yookassa_confirmation_url"])
        ),
    )


def _cart_rows(connection: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT c.product_id, p.title, p.price_kopecks, c.quantity
        FROM cart_items AS c
        JOIN products AS p ON p.id = c.product_id
        WHERE c.user_id = ? AND p.is_available = 1
        ORDER BY p.id
        """,
        (user_id,),
    ).fetchall()


def _lines(rows: list[sqlite3.Row]) -> list[CartLine]:
    return [
        CartLine(
            product_id=int(row["product_id"]),
            title=str(row["title"]),
            price_kopecks=int(row["price_kopecks"]),
            quantity=int(row["quantity"]),
        )
        for row in rows
    ]


def _migrate_orders(connection: sqlite3.Connection) -> None:
    exists = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
    ).fetchone()
    if exists is None:
        return
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(orders)")}
    if "payment_status" not in columns:
        connection.execute(
            "ALTER TABLE orders ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'on_receipt'"
        )
    if "yookassa_payment_id" not in columns:
        connection.execute("ALTER TABLE orders ADD COLUMN yookassa_payment_id TEXT")
    if "yookassa_confirmation_url" not in columns:
        connection.execute("ALTER TABLE orders ADD COLUMN yookassa_confirmation_url TEXT")


def _drop_tokens(connection: sqlite3.Connection, user_id: int) -> None:
    connection.execute("DELETE FROM checkout_tokens WHERE user_id = ?", (user_id,))


def _safe_rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass
