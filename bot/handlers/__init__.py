from aiogram import Router

from bot.handlers.cart import router as cart_router
from bot.handlers.catalog import router as catalog_router
from bot.handlers.checkout import router as checkout_router
from bot.handlers.fallback import router as fallback_router
from bot.handlers.manager import router as manager_router
from bot.handlers.orders import router as orders_router
from bot.handlers.start import router as start_router
from bot.handlers.yookassa import router as yookassa_router
from bot.middlewares import EnsureUserMiddleware
from bot.handlers.finance import router as finance_router


def setup_routers() -> Router:
    root = Router()
    root.message.middleware(EnsureUserMiddleware())
    root.callback_query.middleware(EnsureUserMiddleware())
    root.include_router(start_router)
    root.include_router(finance_router)
    root.include_router(catalog_router)
    root.include_router(cart_router)
    root.include_router(orders_router)
    root.include_router(manager_router)
    root.include_router(checkout_router)
    root.include_router(yookassa_router)
    root.include_router(fallback_router)
    return root
