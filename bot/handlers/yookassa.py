import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.config import Settings
from bot.format import money
from bot.db import Database, Order
from bot.handlers.common import parse_id, user_id_of
from bot.keyboards.inline import yookassa_keyboard
from bot.render import render_order
from bot.yookassa_api import YooKassaError, create_payment, sync_order

router = Router(name="yookassa")


@router.callback_query(F.data.startswith("yk:"))
async def pay_online(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(callback)
    token = (callback.data or "").split(":", 1)[1] if callback.data else ""
    if user_id is None or not token or callback.message is None:
        await callback.answer("Не удалось начать оплату.", show_alert=True)
        return
    if not settings.yookassa_enabled or not _email_ok(settings.yookassa_receipt_email):
        await callback.answer(
            "Онлайн-оплата не настроена. Заполните YOOKASSA_SHOP_ID, "
            "YOOKASSA_SECRET_KEY и YOOKASSA_RECEIPT_EMAIL в .env. "
            "Пока можно нажать «Подтвердить заказ» и оплатить при получении.",
            show_alert=True,
        )
        return
    try:
        result = db.confirm(user_id, token, online=True)
    except ValueError as e:
        await callback.answer(str(e)[:190],show_alert=True)
        return
    await callback.answer()
    if result.kind=='unavailable':
        await callback.message.answer('Сейчас нет в наличии. Заказ и частичный резерв не созданы. Обновите корзину и начните новое оформление.')
        return
    if result.kind == "empty":
        await callback.message.answer("Корзина пуста. Заказ не оформлен.")
        return
    if result.kind == "forbidden":
        await callback.message.answer("Это подтверждение относится к другому покупателю.")
        return
    if result.kind == "stale":
        await callback.message.answer(
            "Это подтверждение уже не действует. Откройте корзину и проверьте заказ ещё раз."
        )
        return
    if result.order_id is None:
        await callback.message.answer("Не удалось создать заказ для оплаты.")
        return
    if result.kind == "duplicate" and result.payment_status not in {"pending", None}:
        await callback.message.answer(
            f"Заказ № {result.order_id} уже оформлен со способом «{result.payment_method}». "
            "Второй заказ не создан."
        )
        return
    order = db.get_order_for_user(user_id, result.order_id)
    if order is None:
        await callback.message.answer("Заказ не найден.")
        return
    await _send_payment(callback, db, settings, order)
    from bot.keyboards.inline import reservation_keyboard
    await callback.message.answer(render_order(order),reply_markup=reservation_keyboard(order.id))


@router.callback_query(F.data.startswith("chk:"))
async def check_payment(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    from bot.stock import expire
    expire(db)
    user_id = user_id_of(callback)
    order_id = parse_id(callback.data or "")
    if user_id is None or order_id is None or callback.message is None:
        await callback.answer("Не удалось проверить оплату.", show_alert=True)
        return
    order = db.get_order_for_user(user_id, order_id)
    if order is None:
        await callback.answer("Этот заказ вам недоступен.", show_alert=True)
        return
    if order.payment_status == "on_receipt":
        await callback.answer("Этот заказ оплачивается при получении.", show_alert=True)
        return
    await callback.answer()
    if order.payment_status == "pending" and order.yookassa_payment_id:
        order = await sync_order(db, settings, order)
    if order.payment_status in {"succeeded","refunded"}:
        await callback.message.answer(render_order(order))
        return
    if order.payment_status == "canceled":
        await callback.message.answer(render_order(order))
        return
    if not order.yookassa_confirmation_url:
        await _send_payment(callback, db, settings, order)
        return
    await callback.message.answer(
        f"Заказ № {order.id}: оплата в ЮKassa ещё не прошла.",
        reply_markup=yookassa_keyboard(order.id, order.yookassa_confirmation_url),
    )


async def _send_payment(callback: CallbackQuery, db: Database, settings: Settings, order: Order) -> None:
    if callback.message is None:
        return
    if order.stock_state in {'expired','canceled','payment_conflict','refunded'} and not order.yookassa_payment_id:
        await callback.message.answer('Резерв уже завершён. Новая платёжная ссылка для этого заказа не создаётся. Начните новую покупку; уже полученная поздняя оплата будет возвращена.')
        return
    from bot.finance import ensure_payment
    try:
        ensure_payment(db, order.id, 'yookassa', order.user_id, order.yookassa_payment_id)
    except ValueError:
        await callback.message.answer('Для этого заказа уже выбран другой режим платежа. Откройте «Мои заказы».')
        return
    if order.yookassa_payment_id and order.yookassa_confirmation_url:
        await callback.message.answer(
            f"Заказ № {order.id} на {money(order.total_kopecks)} ждёт оплату в ЮKassa.\n"
            "После оплаты нажмите «Проверить оплату». Статус «Оплачено» появится только если платёж прошёл.",
            reply_markup=yookassa_keyboard(order.id, order.yookassa_confirmation_url),
        )
        return
    me = await callback.bot.get_me()
    username = me.username or ""
    return_url = f"https://t.me/{username}" if username else "https://t.me"
    try:
        payment_id, url = await asyncio.to_thread(create_payment, settings, order, return_url)
    except YooKassaError as exc:
        await callback.message.answer(
            f"Заказ № {order.id} создан, но ссылка на оплату не получена.\n{exc}\n"
            "Нажмите «Проверить оплату», чтобы запросить ссылку ещё раз. Второй заказ не создаётся."
        )
        return
    db.attach_payment(order.id, payment_id, url)
    from bot.finance import ensure_payment
    ensure_payment(db, order.id, 'yookassa', order.user_id, payment_id)
    await callback.message.answer(
        f"Заказ № {order.id} создан.\n"
        "Откройте страницу ЮKassa и оплатите тестовой картой. "
        "«Оплачено» бот напишет только после успешного ответа ЮKassa.",
        reply_markup=yookassa_keyboard(order.id, url),
    )


def _email_ok(email: str) -> bool:
    if "@" not in email:
        return False
    name, _, domain = email.partition("@")
    return bool(name and "." in domain)
