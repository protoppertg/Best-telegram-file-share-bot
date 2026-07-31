"""Premium info and activation placeholder handlers."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import settings
from app.models import User
from app.services.user import activate_premium

router = Router()


@router.message(Command("premium"))
async def premium_info(message: Message, db_user: User | None = None):
    if db_user and db_user.is_premium and db_user.premium_expiry:
        status = f"✅ <b>Active</b> until {db_user.premium_expiry.strftime('%Y-%m-%d %H:%M UTC')}"
    else:
        status = "❌ <b>Not active</b>"

    text = (
        f"🎟️ <b>Premium Status</b>\n\n"
        f"Status: {status}\n\n"
        f"<b>Premium Benefits:</b>\n"
        f"• {settings.PREMIUM_SEARCH_LIMIT} searches per day (vs {settings.FREE_SEARCH_LIMIT} free)\n"
        f"• {settings.PREMIUM_UPLOAD_LIMIT} uploads per day (vs {settings.FREE_UPLOAD_LIMIT} free)\n"
        f"• No ads/short links when downloading files\n"
        f"• Advanced search filters\n\n"
        f"<b>How to get Premium:</b>\n"
        f"Premium can be activated manually by the admin.\n"
        f"Send a Rs. 100 gift card to the admin. Once verified, the admin will grant you premium status.\n\n"
        f"Use /advanced_search for filtered search (premium only)."
    )
    await message.answer(text)


@router.message(Command("advanced_search"))
async def advanced_search_help(message: Message, db_user: User | None = None):
    if not db_user or not db_user.is_premium:
        await message.answer("⛔ Advanced search is a premium-only feature.\nUse /premium to learn how to upgrade.")
        return
    await message.answer("🔍 Advanced search is active! Use filters like --subject=Physics --year=2023")


@router.message(Command("activate_premium"))
async def admin_activate_premium(message: Message):
    if message.from_user.id not in settings.admin_ids_list: return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /activate_premium <user_id> [days]")
        return
    try:
        target_id = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else settings.PREMIUM_DURATION_DAYS
    except ValueError:
        await message.answer("Invalid arguments.")
        return
    success = await activate_premium(target_id, days)
    if success: await message.answer(f"✅ Premium activated for user {target_id} ({days} days).")
    else: await message.answer(f"❌ User {target_id} not found.")
