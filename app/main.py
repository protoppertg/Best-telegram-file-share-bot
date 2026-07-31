"""FastAPI application — webhook entrypoint for PrepCore bot."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.bot import bot, dp, on_shutdown, on_startup, setup_dispatcher
from app.config import settings
from app.database import engine
from app.services.cache import close_cache, get_cache
from app.utils.logger import logger, setup_logging
from app.web_admin import router as web_admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    setup_dispatcher(dp)
    await get_cache()

    if settings.USE_POLLING:
        await on_startup()
        polling_task = asyncio.create_task(_polling_loop())
        logger.info("app_started_polling")
    else:
        await on_startup()
        logger.info("app_started_webhook")

    yield

    if settings.USE_POLLING:
        polling_task.cancel()
        try: await polling_task
        except asyncio.CancelledError: pass

    await on_shutdown()
    await close_cache()
    await engine.dispose()
    logger.info("app_stopped")


app = FastAPI(title="PrepCore Bot", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.WEB_ADMIN_SECRET_KEY,
    session_cookie="prepcore_admin_session",
    max_age=14 * 24 * 60 * 60,
)

app.include_router(web_admin_router)


@app.post(settings.WEBHOOK_PATH)
async def webhook(request: Request):
    if settings.WEBHOOK_SECRET:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret != settings.WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Forbidden")

    try:
        update_data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad Request")

    try:
        from aiogram.types import Update
        update = Update.model_validate(update_data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as exc:
        logger.error("webhook_processing_error", error=str(exc), exc_info=True)

    return JSONResponse({"ok": True})


@app.get("/health")
async def health():
    return {"status": "ok", "bot": settings.BOT_USERNAME or "PrepCore"}


async def _polling_loop():
    from aiogram.types import Update
    logger.info("polling_loop_started")
    offset = 0
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30)
            for update in updates:
                offset = update.update_id + 1
                asyncio.create_task(dp.feed_update(bot, update))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("polling_error", error=str(exc), exc_info=True)
            await asyncio.sleep(5)


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
