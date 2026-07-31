"""Premium-feature gate middleware."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class PremiumMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        db_user = data.get("db_user")
        if db_user is None: return await handler(event, data)
        if not db_user.is_premium:
            if hasattr(event, "answer"):
                await event.answer("⛔ This feature is only available for premium users.\nUse /premium to learn how to upgrade.")
            return
        return await handler(event, data)
