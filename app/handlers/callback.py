"""Inline button callback handlers: get file, pagination, new search, auto-delete, protect content, force sub verify."""

from __future__ import annotations

import asyncio
import json
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
from app.utils.keyboards import after_file_keyboard, search_results_keyboard, main_menu_kb
from app.utils.logger import logger
from app.utils.validators import sanitise_text

router = Router()

async def _get_settings(session) -> dict:
    result = await session.execute(select(BotSetting))
    settings_rows = result.scalars().all()
    data = {
        "auto_delete_enabled": False, "auto_delete_seconds": 3600,
        "protect_forwarding": False, "post_file_message": "", "shortlink_enabled": False
    }
    for row in settings_rows:
        if row.key == "auto_delete_enabled" and row.value == "true": data["auto_delete_enabled"] = True
        elif row.key == "auto_delete_seconds" and row.value.isdigit(): data["auto_delete_seconds"] = int(row.value)
        elif row.key == "protect_forwarding" and row.value == "true": data["protect_forwarding"] = True
        elif row.key == "post_file_message": data["post_file_message"] = row.value or ""
        elif row.key == "shortlink_enabled" and row.value == "true": data["shortlink_enabled"] = True
    return data

async def _schedule_auto_delete(bot: Bot, chat_id: int, message_id: int, delay: int):
    try:
        await asyncio.sleep(delay)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning("auto_delete_failed", chat_id=chat_id, error=str(e))

@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()

@router.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery, bot: Bot):
    async with get_session() as session:
        res = await session.execute(select(BotSetting).where(BotSetting.key == "force_sub_channels"))
        s = res.scalar_one_or_none()
        channels = []
        if s and s.value:
            try: channels = json.loads(s.value)
            except: pass
            
        missing_channels = []
        for ch in channels:
            try:
                chat_id = int(ch['id'])
                member = await bot.get_chat_member(chat_id=chat_id, user_id=callback.from_user.id)
                if member.status in ["left", "kicked"]:
                    missing_channels.append(ch)
            except Exception as e:
                logger.error("force_sub_verify_error", error=str(e))

        if missing_channels:
            await callback.answer("You haven't joined all channels yet!", show_alert=True)
        else:
            await callback.answer("Verification successful! Welcome aboard. 🎉", show_alert=False)
            text_setting = await session.execute(select(BotSetting).where(BotSetting.key == "start_text"))
            text_setting = text_setting.scalar_one_or_none()
            
            default_text = (
                "✨ <b>Welcome to PrepCore!</b> ✨\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📚 Your ultimate library for study materials.\n"
                "Find notes, PYQs, and books in seconds!\n\n"
                "🛠 <b>How to use me:</b>\n"
                "┣👉 <b>Search:</b> Type keywords or use advanced filters.\n"
                "┣👉 <b>Upload:</b> Send a PDF to support the community.\n"
                "┗👉 <b>Premium:</b> Unlock unlimited searches & ad-free downloads.\n\n"
                "💡 <b>Advanced Search Tip:</b>\n"
                "You can filter your search using tags!\n"
                "<code>physics subject:Math class:10 year:2023</code>\n\n"
                "<i>Ready to dive in? Just type a keyword below!</i>"
            )
            text = text_setting.value if text_setting and text_setting.value else default_text
            try: await callback.message.delete()
            except: pass
            await bot.send_message(callback.from_user.id, text, reply_markup=main_menu_kb())

@router.callback_query(F.data == "search_again")
async def search_again(callback: CallbackQuery):
    await callback.message.edit_text("🔍 <b>New Search</b>\n\nType your search query or use <code>/search \"query\"</code>")
    await callback.answer()

async def _send_file_to_user(bot: Bot, callback: CallbackQuery, doc, bot_settings: dict, query_key: str, page: int):
    is_ad_enabled = bot_settings["auto_delete_enabled"]
    ad_seconds = bot_settings["auto_delete_seconds"]
    protect = bot_settings["protect_forwarding"]
    post_file_msg = bot_settings["post_file_message"]
    
    caption_parts = []
    if doc.subject: caption_parts.append(f"📚 {escape(doc.subject)}")
    if doc.category: caption_parts.append(f"🏷️ {escape(doc.category)}")
    caption = " | ".join(caption_parts) if caption_parts else None
    
    sent_file_msg = await bot.send_document(
        chat_id=callback.from_user.id, document=doc.file_id, protect_content=protect, caption=caption
    )

    msg_ids_to_delete = [sent_file_msg.message_id]

    if post_file_msg:
        try:
            sent_text_msg = await bot.send_message(chat_id=callback.from_user.id, text=post_file_msg, protect_content=protect)
            msg_ids_to_delete.append(sent_text_msg.message_id)
        except Exception as e:
            logger.error("post_file_message_send_failed", error=str(e))

    if is_ad_enabled and ad_seconds > 0:
        for msg_id in msg_ids_to_delete:
            asyncio.create_task(_schedule_auto_delete(bot, callback.from_user.id, msg_id, ad_seconds))

    try:
        await callback.message.edit_text(f"✅ File sent successfully.", reply_markup=after_file_keyboard(query_key, page))
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

    if bot_settings["shortlink_enabled"] and (not db_user or not db_user.is_premium):
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
    cache_data = await cache.get(f"searchq:{query_key}")

    if not cache_data:
        await callback.answer("Search session expired. Please search again.", show_alert=True)
        await callback.message.edit_text("🔍 Your search session has expired.\nType a new query or use /search.")
        return

    query = cache_data.get("query", " ")
    subject = cache_data.get("subject")
    class_name = cache_data.get("class_name")
    year = cache_data.get("year")

    async with get_session() as session:
        results, total = await search_documents(session, query, page=page, subject=subject, class_name=class_name, year=year)

    if not results:
        await callback.answer("No more results.", show_alert=True)
        return

    per_page = settings.SEARCH_RESULTS_PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)
    
    display_q = "All files" if query.strip() == " " else query
    header_text = f"🔍 <b>Search: {escape(sanitise_text(display_q, 100))}</b>"
    if subject: header_text += f"\n📚 Subject: {escape(subject)}"
    if class_name: header_text += f"\n🎓 Class: {escape(class_name)}"
    if year: header_text += f"\n📅 Year: {year}"
    
    text = f"{header_text}\n📊 Found <b>{total}</b> result(s) — Page {page}/{total_pages}\n\nTap a file to download:"

    try:
        await callback.message.edit_text(text, reply_markup=search_results_keyboard(results, query_key, page, total_pages))
    except TelegramBadRequest: pass
    await callback.answer()
