from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import Settings
from bot.handlers.common import is_manager, user_id_of
from bot.keyboards.menu import BTN_START, main_menu

router = Router(name="start")

WELCOME = (
    "<b>Книжный магазин «Фолиант»</b>\n\n"
    "Здесь можно выбрать книгу, собрать корзину и оформить заказ "
    "с самовывозом и расчётом при получении.\n\n"
    "Пункт выдачи: г. Учебный, ул. Книжная, д. 7, ежедневно 10:00–19:00.\n\n"
    "Кнопки меню: Каталог, Корзина, Мои заказы."
)


@router.message(CommandStart())
@router.message(F.text == BTN_START)
async def show_start(message: Message, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    user_id = user_id_of(message)
    if user_id is None:
        return
    await message.answer(WELCOME, reply_markup=main_menu(is_manager=is_manager(user_id, settings)))
    if is_manager(user_id, settings):
        await message.answer('Финансы: /finance — Mini App, /summary — сводка за месяц UTC.')
