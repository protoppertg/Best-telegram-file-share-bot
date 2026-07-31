"""Rate-limiting middleware for /search commands and document uploads."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, TelegramObject

from app.config import settings
from app.models import User
from app.services.user import get_user_search_limit, get_user_upload_limit
from app.utils.logger import logger


class RateLimitMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        if not isinstance(event, Message): return await handler(event, data)
        db_user: User | None = data.get("db_user")
        if db_user is None: return await handler(event, data)

        is_search = False
        is_upload = False

        if event.text and event.text.startswith("/search"):
            is_search = True
        elif event.document:
            state: FSMContext | None = data.get("state")
            if state:
                current = await state.get_state()
                if current is None: is_upload = True
            else: is_upload = True
        elif event.text and not event.text.startswith("/"):
            state: FSMContext | None = data.get("state")
            if state:
                current = await state.get_state()
                if current is None: is_search = True

        if is_search:
            limit = await get_user_search_limit(db_user)
            if db_user.search_count >= limit:
                await event.answer(f"⛔ <b>Daily search limit reached ({limit}/{limit})</b>\n\nUpgrade to premium with /premium for more searches.")
                return

        if is_upload:
            limit = await get_user_upload_limit(db_user)
            if db_user.upload_count >= limit:
                await event.answer(f"⛔ <b>Daily upload limit reached ({limit}/{limit})</b>\n\nPlease try again tomorrow.")
                return

        return await handler(event, data)
