from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.db import Database
from bot.format import money, status_label
from bot.handlers.common import user_id_of
from bot.keyboards.inline import (
    after_order_keyboard,
    back_to_cart_keyboard,
    pickup_keyboard,
    review_keyboard,
)
from bot.keyboards.menu import BTN_CART, BTN_CATALOG, BTN_MANAGER, BTN_ORDERS, BTN_START, BTN_FINANCE
from bot.render import render_review

router = Router(name="checkout")

MENU_TEXTS = {BTN_START, BTN_CATALOG, BTN_CART, BTN_ORDERS, BTN_MANAGER, BTN_FINANCE}


class Checkout(StatesGroup):
    waiting_name = State()


@router.callback_query(F.data == "buy")
async def ask_name(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    user_id = user_id_of(callback)
    if user_id is None or callback.message is None:
        await callback.answer("Не удалось начать оформление.", show_alert=True)
        return
    lines, _total = db.get_cart(user_id)
    if not lines:
        await callback.answer("Корзина пуста. Заказ не оформлен.", show_alert=True)
        return
    await state.set_state(Checkout.waiting_name)
    await callback.answer()
    await callback.message.answer(
        "Напишите имя или псевдоним для заказа.\n"
        "Телефон и адрес не нужны: получение только самовывозом, расчёт при получении.",
        reply_markup=back_to_cart_keyboard(),
    )


@router.message(Checkout.waiting_name, F.text)
async def take_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if name in MENU_TEXTS or name.startswith("/"):
        await state.clear()
        await message.answer("Это команда или кнопка меню, а не имя. Нажмите её ещё раз либо напишите имя.")
        return
    if not name or len(name) > 40:
        await message.answer("Нужно имя или псевдоним до 40 символов.")
        return
    await state.update_data(customer_name=name)
    await state.set_state(None)
    await message.answer(
        f"Имя: {escape(name)}\nВыберите способ получения.",
        reply_markup=pickup_keyboard(),
    )


@router.callback_query(F.data == "pick")
async def choose_pickup(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    user_id = user_id_of(callback)
    if user_id is None or callback.message is None:
        await callback.answer("Не удалось выбрать получение.", show_alert=True)
        return
    data = await state.get_data()
    name = str(data.get("customer_name") or "").strip()
    await state.clear()
    if not name:
        await callback.answer("Сначала напишите имя сообщением.", show_alert=True)
        return
    draft = db.create_checkout(user_id, name)
    if draft is None:
        await callback.answer("Корзина пуста. Заказ не оформлен.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        render_review(draft.customer_name, draft.lines, draft.total_kopecks),
        reply_markup=review_keyboard(draft.token),
    )


@router.callback_query(F.data.startswith("ok:"))
async def confirm_order(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(callback)
    token = (callback.data or "").split(":", 1)[1] if callback.data else ""
    if user_id is None or not token or callback.message is None:
        await callback.answer("Не удалось подтвердить заказ.", show_alert=True)
        return
    try:
        result = db.confirm(user_id, token)
    except ValueError as e:
        await callback.answer(str(e)[:190],show_alert=True)
        return
    await callback.answer()
    if result.kind=='unavailable':
        await callback.message.answer('Сейчас нет в наличии одной из книг. Весь заказ не оформлен, резерв не создан. Проверьте корзину; для новой попытки откройте оформление заново.')
        return
    if result.kind == "created":
        text = (
            f"Заказ № {result.order_id} оформлен.\n"
            f"Сумма: {money(result.total_kopecks or 0)}\n"
            "Статус: Новый\n"
            "Получение: Самовывоз\n"
            "Расчёт: при получении."
        )
        await callback.message.answer(text, reply_markup=after_order_keyboard())
        from bot.render import render_order
        from bot.keyboards.inline import reservation_keyboard
        order=db.get_order(result.order_id)
        await callback.message.answer(render_order(order),reply_markup=reservation_keyboard(order.id))
        return
    if result.kind == "duplicate":
        if result.payment_status == "pending":
            text = (
                f"Заказ № {result.order_id} уже создан и ждёт оплаты в ЮKassa. "
                "Новый заказ не создан."
            )
        elif result.payment_status == "succeeded":
            text = (
                f"Заказ № {result.order_id} уже оплачен. "
                "Повторное подтверждение не создало новый заказ."
            )
        else:
            text = (
                f"Заказ № {result.order_id} уже оформлен. "
                "Повторное подтверждение не создало новый заказ.\n"
                f"Сумма: {money(result.total_kopecks or 0)}\n"
                f"Статус: {status_label(result.status or '')}"
            )
        await callback.message.answer(text, reply_markup=after_order_keyboard())
        return
    if result.kind == "empty":
        await callback.message.answer("Корзина пуста. Заказ не оформлен.")
        return
    if result.kind == "forbidden":
        await callback.message.answer("Это подтверждение относится к другому покупателю.")
        return
    await callback.message.answer(
        "Это подтверждение уже не действует. Откройте корзину и проверьте заказ ещё раз."
    )
