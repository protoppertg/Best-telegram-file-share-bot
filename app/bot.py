"""Bot instance, Dispatcher setup, router and middleware registration."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent, Update

from app.config import settings
from app.utils.logger import logger

bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


def setup_dispatcher(dispatcher: Dispatcher = dp) -> None:
    from app.handlers.user import router as user_router
    from app.handlers.callback import router as callback_router
    from app.handlers.admin import router as admin_router
    from app.handlers.premium import router as premium_router

    from app.middleware.logging import LoggingMiddleware
    from app.middleware.auth import AuthMiddleware
    from app.middleware.rate_limit import RateLimitMiddleware

    dispatcher.include_router(admin_router)
    dispatcher.include_router(premium_router)
    dispatcher.include_router(callback_router)
    dispatcher.include_router(user_router)

    dispatcher.update.outer_middleware(LoggingMiddleware())
    dispatcher.update.outer_middleware(AuthMiddleware())
    user_router.message.outer_middleware(RateLimitMiddleware())

    @dispatcher.errors()
    async def on_error(event: ErrorEvent, bot: Bot = bot):
        logger.error("unhandled_exception", error=str(event.exception), update_id=event.update.update_id if event.update else None, exc_info=True)
        if event.update and event.update.message:
            try: await bot.send_message(event.update.message.chat.id, "⚠️ An error occurred. Please try again.")
            except Exception: pass
    logger.info("dispatcher_configured")


async def on_startup() -> None:
    if settings.USE_POLLING:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("polling_mode_started")
    else:
        await bot.set_webhook(url=settings.webhook_full_url, secret_token=settings.WEBHOOK_SECRET or None, drop_pending_updates=True)
        logger.info("webhook_set", url=settings.webhook_full_url)


async def on_shutdown() -> None:
    if not settings.USE_POLLING: await bot.delete_webhook()
    await bot.session.close()
    await dp.storage.close()
    logger.info("bot_shutdown_complete")
