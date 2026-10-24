from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import Settings
from bot.db import Database
from bot.handlers.common import is_manager, parse_id, user_id_of
from bot.keyboards.inline import manager_keyboard, status_keyboard
from bot.keyboards.menu import BTN_MANAGER
from bot.render import render_manager_list, render_order
from bot.yookassa_api import sync_pending

router = Router(name="manager")


async def send_new_orders(message: Message, db: Database, settings: Settings) -> None:
    await sync_pending(db, settings)
    orders = db.list_new_orders()
    await message.answer(render_manager_list(orders), reply_markup=manager_keyboard(orders))


@router.message(F.text == BTN_MANAGER)
async def manager_message(message: Message, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(message)
    if user_id is None or not is_manager(user_id, settings):
        await message.answer("Список новых заказов доступен только менеджеру.")
        return
    await send_new_orders(message, db, settings)


@router.callback_query(F.data == "mgr")
async def manager_callback(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(callback)
    if user_id is None or not is_manager(user_id, settings) or callback.message is None:
        await callback.answer("Список новых заказов доступен только менеджеру.", show_alert=True)
        return
    await callback.answer()
    await send_new_orders(callback.message, db, settings)


@router.callback_query(F.data.startswith("m:"))
async def manager_order(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(callback)
    order_id = parse_id(callback.data or "")
    if user_id is None or not is_manager(user_id, settings):
        await callback.answer("Список новых заказов доступен только менеджеру.", show_alert=True)
        return
    if order_id is None or callback.message is None:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    order = db.get_order(order_id)
    if order is None:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(render_order(order), reply_markup=status_keyboard(order.id))


@router.callback_query(F.data.startswith("s:"))
async def change_status(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(callback)
    if user_id is None or not is_manager(user_id, settings):
        await callback.answer("Менять статус может только менеджер.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in {"ready", "done", "cancelled"}:
        await callback.answer("Неизвестный статус.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    order = db.set_status(int(parts[1]), parts[2])
    if order is None:
        await callback.answer("Переход недоступен: оплаченный заказ требует возврата, неоплаченный нельзя выдать.", show_alert=True)
        return
    await callback.answer("Статус обновлён")
    await callback.message.answer(render_order(order), reply_markup=status_keyboard(order.id))
