from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db import CartLine, Order, Product
from bot.format import money, status_label


def catalog_keyboard(products: list[Product]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=_clip(f"{product.title} — {money(product.price_kopecks)}"),
                callback_data=f"p:{product.id}",
            )
        ]
        for product in products
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="В корзину", callback_data=f"a:{product_id}")],
            [
                InlineKeyboardButton(text="В каталог", callback_data="cat"),
                InlineKeyboardButton(text="Открыть корзину", callback_data="cart"),
            ],
        ]
    )


def cart_keyboard(lines: list[CartLine]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for line in lines:
        rows.append(
            [
                InlineKeyboardButton(text=_clip(f"− {line.title}"), callback_data=f"d:{line.product_id}"),
                InlineKeyboardButton(text=_clip(f"+ {line.title}"), callback_data=f"i:{line.product_id}"),
            ]
        )
        rows.append(
            [InlineKeyboardButton(text=_clip(f"Удалить: {line.title}"), callback_data=f"r:{line.product_id}")]
        )
    if lines:
        rows.append([InlineKeyboardButton(text="Оформить заказ", callback_data="buy")])
    rows.append([InlineKeyboardButton(text="В каталог", callback_data="cat")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_cart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад в корзину", callback_data="cart")]]
    )


def pickup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Самовывоз", callback_data="pick")],
            [InlineKeyboardButton(text="Назад в корзину", callback_data="cart")],
        ]
    )


def review_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить заказ", callback_data=f"ok:{token}")],
            [InlineKeyboardButton(text="Оплатить через ЮKassa", callback_data=f"yk:{token}")],
            [InlineKeyboardButton(text="Назад в корзину", callback_data="cart")],
        ]
    )


def yookassa_keyboard(order_id: int, url: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if url:
        rows.append([InlineKeyboardButton(text="Перейти к оплате", url=url)])
    rows.append([InlineKeyboardButton(text="Проверить оплату", callback_data=f"chk:{order_id}")])
    rows.append([InlineKeyboardButton(text="Мои заказы", callback_data="ord")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_order_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Мои заказы", callback_data="ord")]]
    )


def reservation_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Обновить заказ и срок',callback_data=f'o:{order_id}')],
        [InlineKeyboardButton(text='Отменить неоплаченный заказ',callback_data=f'cancel:{order_id}')],
    ])


def orders_keyboard(orders: list[Order]) -> InlineKeyboardMarkup | None:
    if not orders:
        return None
    rows = [
        [
            InlineKeyboardButton(
                text=_clip(f"№ {order.id} — {status_label(order.status)}"),
                callback_data=f"o:{order.id}",
            )
        ]
        for order in orders
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="К списку заказов", callback_data="ord")]]
    )


def manager_keyboard(orders: list[Order]) -> InlineKeyboardMarkup | None:
    if not orders:
        return None
    rows = [
        [
            InlineKeyboardButton(
                text=_clip(f"№ {order.id}, {order.customer_name}, {money(order.total_kopecks)}"),
                callback_data=f"m:{order.id}",
            )
        ]
        for order in orders
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def status_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Готов к выдаче", callback_data=f"s:{order_id}:ready")],
            [InlineKeyboardButton(text="Выдан", callback_data=f"s:{order_id}:done")],
            [InlineKeyboardButton(text="Отменён", callback_data=f"s:{order_id}:cancelled")],
            [InlineKeyboardButton(text="К новым заказам", callback_data="mgr")],
        ]
    )


def _clip(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
