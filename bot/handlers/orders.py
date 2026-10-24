from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import Settings
from bot.db import Database
from bot.handlers.common import parse_id, user_id_of
from bot.keyboards.inline import order_back_keyboard, orders_keyboard
from bot.keyboards.menu import BTN_ORDERS
from bot.render import render_order, render_orders
from bot.yookassa_api import sync_pending

router = Router(name="orders")


async def send_orders(message: Message, db: Database, settings: Settings, user_id: int) -> None:
    await sync_pending(db, settings, user_id)
    orders = db.list_orders(user_id)
    await message.answer(render_orders(orders), reply_markup=orders_keyboard(orders))


@router.message(F.text == BTN_ORDERS)
async def orders_message(message: Message, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(message)
    if user_id is None:
        return
    await send_orders(message, db, settings, user_id)


@router.callback_query(F.data == "ord")
async def orders_callback(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(callback)
    if user_id is None or callback.message is None:
        await callback.answer("Не удалось открыть заказы.", show_alert=True)
        return
    await callback.answer()
    await send_orders(callback.message, db, settings, user_id)


@router.callback_query(F.data.startswith("o:"))
async def order_card(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    from bot.stock import expire
    expire(db)
    order_id = parse_id(callback.data or "")
    user_id = user_id_of(callback)
    if order_id is None or user_id is None or callback.message is None:
        await callback.answer("Не удалось открыть заказ.", show_alert=True)
        return
    order = db.get_order_for_user(user_id, order_id)
    if order is None:
        await callback.answer("Этот заказ вам недоступен.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(render_order(order), reply_markup=order_back_keyboard())
    if order.reservation_state=='active':
        from bot.keyboards.inline import reservation_keyboard
        await callback.message.answer('Срок проверяется сервером. При сбое безопасно повторите прежнюю кнопку.',reply_markup=reservation_keyboard(order.id))


@router.callback_query(F.data.startswith('cancel:'))
async def cancel_order(callback:CallbackQuery,db:Database):
    from bot.stock import cancel
    try:
        oid=int(callback.data.split(':',1)[1])
        changed=cancel(db,callback.from_user.id,oid)
    except (ValueError,PermissionError) as e:
        await callback.answer(str(e),show_alert=True);return
    await callback.answer('Резерв освобождён' if changed else 'Резерв уже завершён')
    await callback.message.answer(render_order(db.get_order_for_user(callback.from_user.id,oid)))
