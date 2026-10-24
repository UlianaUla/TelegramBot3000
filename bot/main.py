import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import load_settings
from bot.db import DB_PATH, Database
from bot.handlers import setup_routers
from bot.web import start_web
from bot.finance_worker import run as finance_worker
from bot.refunds import run as stock_worker


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    db = Database(DB_PATH,reservation_seconds=settings.reservation_seconds)
    from bot.stock import expire
    expire(db)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(setup_routers())
    logging.info("База: %s. Книг в каталоге: %s.", db.path, db.product_count())
    logging.info("Бот «Фолиант» запущен. Остановка: Ctrl+C.")
    runner = await start_web(db, settings)
    worker = asyncio.create_task(finance_worker(db, settings, bot))
    warehouse_worker = asyncio.create_task(stock_worker(db, settings))
    try:
        await dispatcher.start_polling(bot, settings=settings, db=db, close_bot_session=False)
    finally:
        worker.cancel()
        warehouse_worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        with suppress(asyncio.CancelledError):
            await warehouse_worker
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
