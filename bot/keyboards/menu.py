from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_START = "Начать"
BTN_CATALOG = "Каталог"
BTN_CART = "Корзина"
BTN_ORDERS = "Мои заказы"
BTN_MANAGER = "Новые заказы"
BTN_FINANCE = "Финансы · Mini App"


def main_menu(*, is_manager: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_START)],
        [KeyboardButton(text=BTN_CATALOG), KeyboardButton(text=BTN_CART)],
        [KeyboardButton(text=BTN_ORDERS)],
    ]
    if is_manager:
        rows.append([KeyboardButton(text=BTN_MANAGER)])
    rows.append([KeyboardButton(text=BTN_FINANCE)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
