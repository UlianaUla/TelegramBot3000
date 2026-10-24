from aiogram.types import CallbackQuery, Message

from bot.config import Settings


def user_id_of(event: Message | CallbackQuery) -> int | None:
    user = event.from_user
    return None if user is None else user.id


def is_manager(user_id: int, settings: Settings) -> bool:
    return user_id in settings.manager_ids


def parse_id(data: str) -> int | None:
    _, _, raw = data.partition(":")
    if not raw.isdigit():
        return None
    return int(raw)
