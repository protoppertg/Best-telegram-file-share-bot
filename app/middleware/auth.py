"""Authentication, user-registration, Force Sub, and Ban middleware."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject
from sqlalchemy import select

from app.bot import bot
from app.database import get_session
from app.models import BotSetting, User
from app.services.user import get_or_create_user, reset_daily_counts_if_needed
from app.utils.logger import logger


async def get_force_sub_channels(session) -> list[dict]:
    res = await session.execute(select(BotSetting).where(BotSetting.key == "force_sub_channels"))
    s = res.scalar_one_or_none()
    if s and s.value:
        try: return json.loads(s.value)
        except: return []
    return []


class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        try:
            async with get_session() as session:
                user = await get_or_create_user(session, telegram_id=tg_user.id, username=tg_user.username, first_name=tg_user.first_name, last_name=tg_user.last_name)
                await reset_daily_counts_if_needed(session, user)
                
                # ── Ban Check ──────────────────────────
                if user.is_banned:
                    if isinstance(event, CallbackQuery):
                        await event.answer("You are banned from using this bot.", show_alert=True)
                    elif isinstance(event, Message):
                        await bot.send_message(tg_user.id, "🚫 You have been banned from using this bot.")
                    return
                
                # ── Force Sub Check ──────────────────────
                channels = await get_force_sub_channels(session)
                if channels:
                    missing_channels = []
                    for ch in channels:
                        try:
                            # Force casting to int is required for Aiogram
                            chat_id = int(ch['id'])
                            member = await bot.get_chat_member(chat_id=chat_id, user_id=tg_user.id)
                            if member.status in ["left", "kicked"]:
                                missing_channels.append(ch)
                        except ValueError:
                            logger.error("force_sub_invalid_id", channel_id=ch.get('id'))
                        except Exception as e:
                            # If bot isn't admin, it will error here. We skip to avoid blocking users accidentally.
                            logger.error("force_sub_check_error", channel=ch.get('id'), error=str(e))
                    
                    if missing_channels:
                        from aiogram.utils.keyboard import InlineKeyboardBuilder
                        kb = InlineKeyboardBuilder()
                        for ch in missing_channels:
                            kb.button(text=f"📢 Join Channel", url=ch['link'])
                        kb.button(text="✅ I Joined", callback_data="check_sub")
                        kb.adjust(1)
                        
                        if isinstance(event, CallbackQuery):
                            await event.answer("You must join the channels first!", show_alert=True)
                        await bot.send_message(tg_user.id, "⚠️ You must join our channels to use this bot!", reply_markup=kb.as_markup())
                        return

            data["db_user"] = user
        except Exception as exc:
            logger.error("auth_middleware_error", error=str(exc), exc_info=True)
            
        return await handler(event, data)
