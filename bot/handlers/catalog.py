from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import Database
from bot.handlers.common import parse_id, user_id_of
from bot.keyboards.inline import catalog_keyboard, product_keyboard
from bot.keyboards.menu import BTN_CATALOG
from bot.render import render_catalog, render_product

router = Router(name="catalog")


async def send_catalog(message: Message, db: Database) -> None:
    products = db.list_products()
    markup = catalog_keyboard(products) if products else None
    await message.answer(render_catalog(products), reply_markup=markup)


@router.message(F.text == BTN_CATALOG)
async def catalog_message(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    await send_catalog(message, db)


@router.callback_query(F.data == "cat")
async def catalog_callback(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if callback.message is not None:
        await send_catalog(callback.message, db)


@router.callback_query(F.data.startswith("p:"))
async def product_card(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    product_id = parse_id(callback.data or "")
    user_id = user_id_of(callback)
    if product_id is None or user_id is None or callback.message is None:
        await callback.answer("Откройте каталог ещё раз.", show_alert=True)
        return
    product = db.get_product(product_id)
    if product is None:
        await callback.answer("Книга не найдена. Откройте каталог ещё раз.", show_alert=True)
        return
    lines, _total = db.get_cart(user_id)
    quantity = next((line.quantity for line in lines if line.product_id == product_id), None)
    await callback.answer()
    await callback.message.answer(
        render_product(product, quantity),
        reply_markup=product_keyboard(product_id),
    )


@router.callback_query(F.data.startswith("a:"))
async def add_product(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    product_id = parse_id(callback.data or "")
    user_id = user_id_of(callback)
    if product_id is None or user_id is None or callback.message is None:
        await callback.answer("Не удалось добавить книгу.", show_alert=True)
        return
    ok, error, quantity = db.add_to_cart(user_id, product_id)
    if not ok:
        await callback.answer(error, show_alert=True)
        return
    product = db.get_product(product_id)
    await callback.answer("Книга добавлена в корзину")
    if product is not None:
        await callback.message.answer(
            render_product(product, quantity),
            reply_markup=product_keyboard(product_id),
        )
