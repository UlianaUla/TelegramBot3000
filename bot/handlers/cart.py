from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import Database
from bot.handlers.common import parse_id, user_id_of
from bot.keyboards.inline import cart_keyboard
from bot.keyboards.menu import BTN_CART
from bot.render import render_cart

router = Router(name="cart")


async def send_cart(message: Message, db: Database, user_id: int) -> None:
    db.cancel_checkout(user_id)
    lines, total = db.get_cart(user_id)
    await message.answer(render_cart(lines, total), reply_markup=cart_keyboard(lines))


@router.message(F.text == BTN_CART)
async def cart_message(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(message)
    if user_id is None:
        return
    await send_cart(message, db, user_id)


@router.callback_query(F.data == "cart")
async def cart_callback(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(callback)
    if user_id is None or callback.message is None:
        await callback.answer("Не удалось открыть корзину.", show_alert=True)
        return
    await callback.answer()
    await send_cart(callback.message, db, user_id)


@router.callback_query(F.data.startswith("i:"))
async def increase(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await _change(callback, db, state, delta=1)


@router.callback_query(F.data.startswith("d:"))
async def decrease(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await _change(callback, db, state, delta=-1)


async def _change(callback: CallbackQuery, db: Database, state: FSMContext, delta: int) -> None:
    await state.clear()
    product_id = parse_id(callback.data or "")
    user_id = user_id_of(callback)
    if product_id is None or user_id is None or callback.message is None:
        await callback.answer("Не удалось изменить количество.", show_alert=True)
        return
    ok, error = db.change_quantity(user_id, product_id, delta)
    if not ok:
        await callback.answer(error, show_alert=True)
        return
    await callback.answer("Количество обновлено")
    await send_cart(callback.message, db, user_id)


@router.callback_query(F.data.startswith("r:"))
async def remove_item(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    product_id = parse_id(callback.data or "")
    user_id = user_id_of(callback)
    if product_id is None or user_id is None or callback.message is None:
        await callback.answer("Не удалось удалить позицию.", show_alert=True)
        return
    removed = db.remove_from_cart(user_id, product_id)
    if not removed:
        await callback.answer("Этой книги уже нет в корзине.", show_alert=True)
        return
    await callback.answer("Позиция удалена")
    await send_cart(callback.message, db, user_id)
