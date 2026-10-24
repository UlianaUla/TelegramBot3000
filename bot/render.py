"""Тексты экранов. Цены подставляются из объектов базы, не из сообщения покупателя."""

from html import escape

from bot.db import CartLine, Order, Product
from bot.format import FULFILLMENT, PICKUP_PLACE, money, status_label


def render_catalog(products: list[Product]) -> str:
    if not products:
        return "Каталог пуст. Если файл базы только что создан, перезапустите бота: книги добавляются при пустой таблице products."
    lines = ["<b>Каталог «Фолиант»</b>", ""]
    for product in products:
        lines.append(f"<b>{escape(product.title)}</b> — {money(product.price_kopecks)}")
        lines.append(escape(product.description))
        lines.append(f'Доступно: {product.available} шт.' if product.available else 'Нет в наличии')
        lines.append("")
    lines.append("Нажмите книгу, чтобы открыть карточку.")
    return "\n".join(lines)


def render_product(product: Product, quantity: int | None = None) -> str:
    text = (
        f"<b>{escape(product.title)}</b>\n"
        f"{escape(product.description)}\n\n"
        f"Цена: {money(product.price_kopecks)}"
    )
    text+=f'\nДоступно: {product.available} шт.' if product.available else '\nНет в наличии'
    if quantity:
        text += f"\nВ вашей корзине: {quantity} шт."
    return text


def render_cart(lines: list[CartLine], total: int) -> str:
    if not lines:
        return "Корзина пуста. Заказ из пустой корзины не оформляется. Откройте каталог и выберите книгу."
    parts = ["<b>Корзина</b>", ""]
    for line in lines:
        parts.append(
            f"<b>{escape(line.title)}</b>\n"
            f"{money(line.price_kopecks)} × {line.quantity} = {money(line.line_total)}"
        )
        parts.append("")
    parts.append(f"<b>Итого: {money(total)}</b>")
    parts.append("Кнопки «−» и «+» меняют количество. «Удалить» убирает позицию.")
    return "\n".join(parts)


def render_review(name: str, lines: list[CartLine], total: int) -> str:
    parts = [
        "<b>Проверьте заказ</b>",
        "",
        f"Имя: {escape(name)}",
        f"Получение: {escape(FULFILLMENT)}, {escape(PICKUP_PLACE)}",
        "Дальше выберите расчёт: при получении или ЮKassa.",
        "«Оплачено» появится только после успешного платежа ЮKassa.",
        "",
    ]
    for line in lines:
        parts.append(
            f"{escape(line.title)} — {money(line.price_kopecks)} × {line.quantity} = {money(line.line_total)}"
        )
    parts.append("")
    parts.append(f"<b>Итого: {money(total)}</b>")
    parts.append("После подтверждения статус будет «Новый».")
    parts.append('После оформления действует ограниченный резерв (точный срок будет в заказе). Подтверждение оплаты после срока означает полный возврат без продажи.')
    return "\n".join(parts)


def render_order(order: Order) -> str:
    parts = [
        f"<b>Заказ № {order.id}</b>",
        f"Статус: {escape(status_label(order.status))}",
        f"Имя: {escape(order.customer_name)}",
        f"Получение: {escape(order.fulfillment)}, {escape(PICKUP_PLACE)}",
        _payment_line(order),
        "",
    ]
    if order.yookassa_payment_id:
        parts.insert(5, f'ID платежа ЮKassa: <code>{escape(order.yookassa_payment_id)}</code>')
    if order.stock_state!='legacy':
        labels={'awaiting_payment':'Ожидает оплаты','paid':'Оплачен: товар списан','canceled':'Отменён',
                'expired':'Срок резерва истёк','payment_conflict':'Резерв закончился, оформляется возврат',
                'refunded':'Полный возврат подтверждён'}
        parts.insert(2,'Склад: '+labels.get(order.stock_state,order.stock_state))
        if order.expires_at:
            from datetime import datetime,timezone
            seconds=max(0,int((datetime.fromisoformat(order.expires_at)-datetime.now(timezone.utc)).total_seconds()))
            parts.insert(3,f'Резерв до {escape(order.expires_at)} · осталось {seconds} сек.' if order.reservation_state=='active' else f'Резерв: {order.reservation_state}')
    for item in order.items:
        line_total = item.price_kopecks * item.quantity
        parts.append(
            f"{escape(item.title)} — {money(item.price_kopecks)} × {item.quantity} = {money(line_total)}"
        )
    parts.append("")
    parts.append(f"<b>Итого: {money(order.total_kopecks)}</b>")
    parts.append(f"Дата: {escape(order.created_at)}")
    return "\n".join(parts)


def _payment_hint(order: Order) -> str:
    if order.payment_status == "succeeded":
        return " — Оплачено"
    if order.payment_status == "pending":
        return " — ожидает оплаты"
    return ""


def _payment_line(order: Order) -> str:
    if order.payment_status == "succeeded":
        return "Оплата: Оплачено"
    if order.payment_status == "pending":
        return f"Оплата: ожидает подтверждения. {escape(order.payment_method)}"
    if order.payment_status == "refunded":
        return "Оплата: полный возврат подтверждён"
    if order.payment_status == "canceled":
        return "Оплата: отменена"
    return f"Расчёт: {escape(order.payment_method)}"


def render_orders(orders: list[Order]) -> str:
    if not orders:
        return "У вас пока нет заказов."
    lines = ["<b>Мои заказы</b>", ""]
    for order in orders:
        lines.append(
            f"№ {order.id} — {money(order.total_kopecks)} — {escape(status_label(order.status))}"
            f"{_payment_hint(order)} — {escape(order.created_at)}"
        )
    lines.append("")
    lines.append("Нажмите номер, чтобы открыть состав.")
    return "\n".join(lines)


def render_manager_list(orders: list[Order]) -> str:
    if not orders:
        return "Новых заказов нет."
    lines = ["<b>Новые заказы</b>", ""]
    for order in orders:
        lines.append(
            f"№ {order.id}, {escape(order.customer_name)} — {money(order.total_kopecks)} — {escape(order.created_at)}"
        )
    lines.append("")
    lines.append("Откройте заказ, чтобы сменить статус.")
    return "\n".join(lines)
