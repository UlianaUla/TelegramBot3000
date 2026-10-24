"""Настройки из локального .env."""

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    bot_token: str
    manager_ids: frozenset[int]
    yookassa_shop_id: str
    yookassa_secret_key: str
    yookassa_receipt_email: str
    payment_mode: str = "yookassa"
    mini_app_url: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    init_data_ttl: int = 3600
    reservation_seconds: int = 120

    @property
    def yookassa_enabled(self) -> bool:
        return bool(self.yookassa_shop_id and self.yookassa_secret_key)


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "В .env нет BOT_TOKEN. Скопируйте .env.example в .env и впишите токен из BotFather."
        )
    mode = 'yookassa'

    raw_ids = os.getenv("MANAGER_IDS", "")
    manager_ids: set[int] = set()
    for part in raw_ids.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise SystemExit(
                "MANAGER_IDS должен содержать числовые Telegram ID через запятую."
            )
        manager_ids.add(int(part))

    return Settings(
        bot_token=token,
        manager_ids=frozenset(manager_ids),
        yookassa_shop_id=os.getenv("YOOKASSA_SHOP_ID", "").strip(),
        yookassa_secret_key=os.getenv("YOOKASSA_SECRET_KEY", "").strip(),
        yookassa_receipt_email=os.getenv("YOOKASSA_RECEIPT_EMAIL", "").strip(),
        payment_mode=mode,
        mini_app_url=os.getenv("MINI_APP_URL", "").strip(),
        web_host=os.getenv("WEB_HOST", "127.0.0.1"),
        web_port=int(os.getenv("WEB_PORT", "8080")),
        init_data_ttl=int(os.getenv("INIT_DATA_TTL", "3600")),
        reservation_seconds=int(os.getenv('RESERVATION_SECONDS','120')),
    )
