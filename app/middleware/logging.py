"""Structured logging middleware for all updates."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.utils.logger import logger


class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        user_id = user.id if user else None
        if isinstance(event, Message):
            logger.info("message_received", user_id=user_id, text=event.text[:100] if event.text else None, has_document=bool(event.document))
        elif isinstance(event, CallbackQuery):
            logger.info("callback_received", user_id=user_id, callback_data=event.data)
        try:
            return await handler(event, data)
        except Exception as exc:
            logger.error("handler_exception", user_id=user_id, error=str(exc), exc_info=True)
            raise
