"""Wrapper service for Telegram API operations."""

from __future__ import annotations

from typing import Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from app.config import settings
from app.utils.logger import logger

async def send_document_to_user(bot: Bot, chat_id: int, file_id: str, caption: Optional[str] = None) -> bool:
    try:
        await bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
        return True
    except Exception as exc:
        logger.error("send_document_failed", chat_id=chat_id, error=str(exc))
        return False

async def forward_to_channel(bot: Bot, file_id: str, caption: Optional[str] = None) -> Tuple[str, int]:
    channel_id = int(settings.CHANNEL_ID)
    try:
        msg = await bot.send_document(chat_id=channel_id, document=file_id, caption=caption)
        if not msg.document: raise RuntimeError("Channel message has no document attachment")
        return msg.document.file_id, msg.message_id
    except Exception as exc:
        logger.error("forward_to_channel_failed", error=str(exc), exc_info=True)
        raise
