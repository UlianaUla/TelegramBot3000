from aiogram import Router
from aiogram.types import CallbackQuery, Message

router = Router(name="fallback")


@router.message()
async def unknown_message(message: Message) -> None:
    await message.answer("Выберите кнопку меню: Каталог, Корзина или Мои заказы.")


@router.callback_query()
async def unknown_button(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка устарела. Откройте нужный раздел в меню.", show_alert=True)
