"""Authentication, user-registration, and Force Sub middleware."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject
from sqlalchemy import select

from app.bot import bot
from app.database import get_session
from app.models import BotSetting, User
from app.services.user import get_or_create_user, reset_daily_counts_if_needed
from app.utils.logger import logger


class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        try:
            async with get_session() as session:
                user = await get_or_create_user(session, telegram_id=tg_user.id, username=tg_user.username, first_name=tg_user.first_name, last_name=tg_user.last_name)
                await reset_daily_counts_if_needed(session, user)
                
                fs_id_setting = await session.execute(select(BotSetting).where(BotSetting.key == "force_sub_channel_id"))
                fs_id_setting = fs_id_setting.scalar_one_or_none()
                
                if fs_id_setting and fs_id_setting.value:
                    try:
                        chat_id = int(fs_id_setting.value)
                        member = await bot.get_chat_member(chat_id=chat_id, user_id=tg_user.id)
                        if member.status in ["left", "kicked"]:
                            fs_link_setting = await session.execute(select(BotSetting).where(BotSetting.key == "force_sub_invite_link"))
                            fs_link_setting = fs_link_setting.scalar_one_or_none()
                            invite_link = fs_link_setting.value if fs_link_setting and fs_link_setting.value else "https://t.me"
                            
                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="📢 Join Channel", url=invite_link)],
                                [InlineKeyboardButton(text="✅ I Joined", callback_data="check_sub")]
                            ])
                            
                            if isinstance(event, CallbackQuery):
                                await event.answer("You must join the channel first!", show_alert=True)
                            await bot.send_message(tg_user.id, "⚠️ You must join our channel to use this bot!", reply_markup=kb)
                            return
                    except Exception as e:
                        logger.error("force_sub_check_error", error=str(e))

            data["db_user"] = user
        except Exception as exc:
            logger.error("auth_middleware_error", error=str(exc), exc_info=True)
            
        return await handler(event, data)
