"""Inline button callback handlers: get file, pagination, new search."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.database import get_session
from app.models import User
from app.services import document as doc_service
from app.services.cache import get_cache
from app.services.search import search_documents
from app.services.telegram import send_document_to_user
from app.services.shortlink import get_shortlink
from app.utils.keyboards import after_file_keyboard, search_results_keyboard
from app.utils.logger import logger
from app.utils.validators import sanitise_text

router = Router()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    await callback.answer("If you joined, please try your request again.", show_alert=True)


@router.callback_query(F.data == "search_again")
async def search_again(callback: CallbackQuery):
    await callback.message.edit_text("🔍 <b>New Search</b>\n\nType your search query or use <code>/search &lt;query&gt;</code>")
    await callback.answer()


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
    success = await send_document_to_user(bot, chat_id=callback.from_user.id, file_id=doc.file_id, caption=f"📄 <b>{sanitise_text(doc.file_name, 100)}</b>\n📚 {doc.subject or 'N/A'} | 🏷️ {doc.category or 'N/A'}")

    if not success:
        await callback.message.answer("❌ Failed to send the file. It may have been removed from storage.")
        return

    try:
        await callback.message.edit_text(f"✅ File sent: <b>{sanitise_text(doc.file_name, 100)}</b>", reply_markup=after_file_keyboard(query_key, page))
    except TelegramBadRequest: pass


@router.callback_query(F.data.startswith("dlfile:"))
async def dl_file_callback(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    doc_id = int(parts[1])
    query_key = parts[2] if len(parts) > 2 else ""
    page = int(parts[3]) if len(parts) > 3 else 1

    async with get_session() as session:
        doc = await doc_service.get_document_by_id(session, doc_id)

    await callback.answer("📥 Sending file...")
    success = await send_document_to_user(bot, chat_id=callback.from_user.id, file_id=doc.file_id, caption=f"📄 <b>{sanitise_text(doc.file_name, 100)}</b>\n📚 {doc.subject or 'N/A'} | 🏷️ {doc.category or 'N/A'}")

    if not success:
        await callback.message.answer("❌ Failed to send the file. It may have been removed from storage.")
        return

    try:
        await callback.message.edit_text(f"✅ File sent: <b>{sanitise_text(doc.file_name, 100)}</b>", reply_markup=after_file_keyboard(query_key, page))
    except TelegramBadRequest: pass


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
    text = f"🔍 <b>Search: {sanitise_text(query, 100)}</b>\n📊 Found <b>{total}</b> result(s) — Page {page}/{total_pages}\n\nTap a file to download:"

    try:
        await callback.message.edit_text(text, reply_markup=search_results_keyboard(results, query_key, page, total_pages))
    except TelegramBadRequest: pass
    await callback.answer()
