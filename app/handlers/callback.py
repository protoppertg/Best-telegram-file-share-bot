"""Inline button callback handlers: get file, pagination, new search, auto-delete, protect content."""

from __future__ import annotations

import asyncio
from html import escape
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from app.config import settings
from app.database import get_session
from app.models import BotSetting, User
from app.services import document as doc_service
from app.services.cache import get_cache
from app.services.search import search_documents
from app.services.shortlink import get_shortlink
from app.utils.keyboards import after_file_keyboard, search_results_keyboard
from app.utils.logger import logger
from app.utils.validators import sanitise_text

router = Router()


async def _get_settings(session) -> dict:
    """Fetch all bot settings in one go."""
    result = await session.execute(select(BotSetting))
    settings_rows = result.scalars().all()
    
    data = {
        "auto_delete_enabled": False,
        "auto_delete_seconds": 3600,
        "protect_forwarding": False,
        "post_file_message": ""
    }
    
    for row in settings_rows:
        if row.key == "auto_delete_enabled" and row.value == "true":
            data["auto_delete_enabled"] = True
        elif row.key == "auto_delete_seconds" and row.value.isdigit():
            data["auto_delete_seconds"] = int(row.value)
        elif row.key == "protect_forwarding" and row.value == "true":
            data["protect_forwarding"] = True
        elif row.key == "post_file_message":
            data["post_file_message"] = row.value or ""
            
    return data


async def _schedule_auto_delete(bot: Bot, chat_id: int, message_id: int, delay: int):
    """Background task to delete a message after a specified delay."""
    try:
        await asyncio.sleep(delay)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info("auto_delete_success", chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning("auto_delete_failed", chat_id=chat_id, error=str(e))


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    await callback.answer("If you joined, please try your request again.", show_alert=True)


@router.callback_query(F.data == "search_again")
async def search_again(callback: CallbackQuery):
    await callback.message.edit_text("🔍 <b>New Search</b>\n\nType your search query or use <code>/search \"query\"</code>")
    await callback.answer()


async def _send_file_to_user(bot: Bot, callback: CallbackQuery, doc, bot_settings: dict, query_key: str, page: int):
    """Helper to send file, send post-file text, and apply auto-delete to both."""
    
    is_ad_enabled = bot_settings["auto_delete_enabled"]
    ad_seconds = bot_settings["auto_delete_seconds"]
    protect = bot_settings["protect_forwarding"]
    post_file_msg = bot_settings["post_file_message"]
    
    # 1. Send the File
    sent_file_msg = await bot.send_document(
        chat_id=callback.from_user.id, 
        document=doc.file_id, 
        protect_content=protect, 
        caption=f"📄 <b>{escape(sanitise_text(doc.file_name, 100))}</b>\n📚 {escape(doc.subject or 'N/A')} | 🏷️ {escape(doc.category or 'N/A')}"
    )

    # Keep track of all message IDs sent so we can delete them all if Auto-Delete is on
    msg_ids_to_delete = [sent_file_msg.message_id]

    # 2. Send the Custom Text Message (if configured)
    if post_file_msg:
        try:
            sent_text_msg = await bot.send_message(
                chat_id=callback.from_user.id, 
                text=post_file_msg, 
                protect_content=protect
            )
            msg_ids_to_delete.append(sent_text_msg.message_id)
        except Exception as e:
            logger.error("post_file_message_send_failed", error=str(e))

    # 3. Schedule Auto-Delete for both the file and the text message
    if is_ad_enabled and ad_seconds > 0:
        for msg_id in msg_ids_to_delete:
            asyncio.create_task(_schedule_auto_delete(bot, callback.from_user.id, msg_id, ad_seconds))

    try:
        await callback.message.edit_text(f"✅ File sent: <b>{escape(sanitise_text(doc.file_name, 100))}</b>", reply_markup=after_file_keyboard(query_key, page))
    except TelegramBadRequest: pass


@router.callback_query(F.data.startswith("getfile:"))
async def get_file_callback(callback: CallbackQuery, bot: Bot, db_user: User | None = None):
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Invalid request.", show_alert=True)
        return
    doc_id = int(parts[1])
    query_key = parts[2] if len(parts) > 2 else ""
    page = int(parts[3]) if len(parts) > 3 else 1

    async with get_session() as session:
        doc = await doc_service.get_document_by_id(session, doc_id)
        bot_settings = await _get_settings(session)

    if not doc:
        await callback.answer("File not found.", show_alert=True)
        return
    if not doc.approved:
        await callback.answer("This file is pending approval.", show_alert=True)
        return

    if settings.SHORTLINK_ENABLED and (not db_user or not db_user.is_premium):
        original_url = "https://google.com" 
        short_url = await get_shortlink(original_url)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="📢 Visit Sponsor", url=short_url)
        kb.button(text="📥 Download File", callback_data=f"dlfile:{doc_id}:{query_key}:{page}")
        kb.adjust(1)
        
        await callback.message.edit_text(
            f"⚠️ <b>Free User Download</b>\n\n"
            f"To download this file, please support us by visiting the sponsor link below.\n"
            f"<i>Premium users download directly without ads. Use /premium to upgrade.</i>",
            reply_markup=kb.as_markup()
        )
        await callback.answer()
        return

    await callback.answer("📥 Sending file...")
    await _send_file_to_user(bot, callback, doc, bot_settings, query_key, page)


@router.callback_query(F.data.startswith("dlfile:"))
async def dl_file_callback(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    doc_id = int(parts[1])
    query_key = parts[2] if len(parts) > 2 else ""
    page = int(parts[3]) if len(parts) > 3 else 1

    async with get_session() as session:
        doc = await doc_service.get_document_by_id(session, doc_id)
        bot_settings = await _get_settings(session)

    if not doc:
        await callback.answer("File not found.", show_alert=True)
        return

    await callback.answer("📥 Sending file...")
    await _send_file_to_user(bot, callback, doc, bot_settings, query_key, page)


@router.callback_query(F.data.startswith("search:"))
async def search_pagination(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Invalid pagination request.", show_alert=True)
        return
    query_key = parts[1]
    page = int(parts[2])

    cache = await get_cache()
    query = await cache.get(f"searchq:{query_key}")

    if not query:
        await callback.answer("Search session expired. Please search again.", show_alert=True)
        await callback.message.edit_text("🔍 Your search session has expired.\nType a new query or use /search.")
        return

    async with get_session() as session:
        results, total = await search_documents(session, query, page=page)

    if not results:
        await callback.answer("No more results.", show_alert=True)
        return

    per_page = settings.SEARCH_RESULTS_PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)
    text = f"🔍 <b>Search: {escape(sanitise_text(query, 100))}</b>\n📊 Found <b>{total}</b> result(s) — Page {page}/{total_pages}\n\nTap a file to download:"

    try:
        await callback.message.edit_text(text, reply_markup=search_results_keyboard(results, query_key, page, total_pages))
    except TelegramBadRequest: pass
    await callback.answer()
