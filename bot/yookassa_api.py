"""Платёж ЮKassa. Секрет магазина читается из настроек и в сообщения не попадает."""

from __future__ import annotations

import logging

from bot.config import Settings
from bot.db import Order

log = logging.getLogger(__name__)


class YooKassaError(Exception):
    pass


def amount_value(kopecks: int) -> str:
    rubles, kop = divmod(int(kopecks), 100)
    return f"{rubles}.{kop:02d}"


def create_payment(settings: Settings, order: Order, return_url: str) -> tuple[str, str]:
    """Возвращает (id платежа, ссылка на страницу оплаты)."""
    Configuration, Payment = _sdk()
    Configuration.account_id = settings.yookassa_shop_id
    Configuration.secret_key = settings.yookassa_secret_key
    payload: dict = {
        "amount": {"value": amount_value(order.total_kopecks), "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": f"Заказ №{order.id}, книжный магазин Фолиант"[:128],
        "metadata": {"order_id": str(order.id)},
    }
    if settings.yookassa_receipt_email:
        payload["receipt"] = _receipt(order, settings.yookassa_receipt_email)
    try:
        payment = Payment.create(payload, f"foliant-order-{order.id}")
    except Exception as exc:
        raise YooKassaError(_explain(exc)) from exc
    confirmation = getattr(payment, "confirmation", None)
    url = ""
    if confirmation is not None:
        url = str(getattr(confirmation, "confirmation_url", "") or "")
    payment_id = str(getattr(payment, "id", "") or "")
    if not payment_id:
        raise YooKassaError("ЮKassa не вернула номер платежа.")
    return payment_id, url


def fetch_status(settings: Settings, payment_id: str) -> str:
    Configuration, Payment = _sdk()
    Configuration.account_id = settings.yookassa_shop_id
    Configuration.secret_key = settings.yookassa_secret_key
    try:
        payment = Payment.find_one(payment_id)
    except Exception as exc:
        raise YooKassaError(_explain(exc)) from exc
    return str(getattr(payment, "status", "") or "")


async def sync_order(db, settings: Settings, order: Order) -> Order:
    """Сверяет ожидающий платёж с ЮKassa и возвращает свежую запись заказа."""
    import asyncio

    if order.payment_status != "pending" or not order.yookassa_payment_id:
        return order
    if not settings.yookassa_enabled:
        return order
    try:
        from bot.finance import ensure_payment
        from bot.provider import sync_payment
        p = ensure_payment(db, order.id, 'yookassa', order.user_id, order.yookassa_payment_id)
        await sync_payment(db, settings, p)
    except (YooKassaError, ValueError):
        log.warning("Не удалось проверить платёж заказа %s", order.id)
        return order
    fresh = db.get_order(order.id)
    return fresh if fresh is not None else order


async def sync_pending(db, settings: Settings, user_id: int | None = None) -> None:
    pending = db.list_pending_online(user_id)
    for order in pending:
        await sync_order(db, settings, order)


def _sdk():
    try:
        from yookassa import Configuration, Payment
    except ImportError as exc:
        raise YooKassaError(
            "Пакет yookassa не установлен. Выполните: python -m pip install -r requirements.txt"
        ) from exc
    return Configuration, Payment


def _receipt(order: Order, email: str) -> dict:
    items = []
    for item in order.items:
        items.append(
            {
                "description": item.title[:128],
                "quantity": f"{item.quantity}.00",
                "amount": {"value": amount_value(item.price_kopecks), "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_prepayment",
                "payment_subject": "commodity",
            }
        )
    return {"customer": {"email": email}, "items": items}


def _explain(exc: Exception) -> str:
    description = getattr(exc, "description", None)
    if description:
        return f"ЮKassa отклонила запрос: {description}"
    return "ЮKassa не приняла запрос. Проверьте shopId и секретный ключ тестового магазина."
