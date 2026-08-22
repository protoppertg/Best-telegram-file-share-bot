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
from app.services.user import add_aura
from app.utils.keyboards import after_file_keyboard, search_results_keyboard, main_menu_kb
from app.utils.logger import logger
from app.utils.validators import sanitise_text

router = Router()

async def _get_settings(session) -> dict:
    result = await session.execute(select(BotSetting))
    settings_rows = result.scalars().all()
    data = {
        "auto_delete_enabled": False, "auto_delete_seconds": 3600,
        "protect_forwarding": False, "post_file_message": "", "shortlink_enabled": False,
        "premium_enabled": True
    }
    for row in settings_rows:
        val = row.value or ""
        if row.key == "auto_delete_enabled" and val == "true": data["auto_delete_enabled"] = True
        elif row.key == "auto_delete_seconds" and val.isdigit(): data["auto_delete_seconds"] = int(val)
        elif row.key == "protect_forwarding" and val == "true": data["protect_forwarding"] = True
        elif row.key == "post_file_message": data["post_file_message"] = val
        elif row.key == "shortlink_enabled" and val == "true": data["shortlink_enabled"] = True
        elif row.key == "premium_enabled" and val == "false": data["premium_enabled"] = False
    return data

async def _schedule_auto_delete(bot: Bot, chat_id: int, message_ids: list[int], delay: int):
    """Lightweight background task to delete messages after a delay."""
    try:
        await asyncio.sleep(delay)
        for msg_id in message_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except TelegramBadRequest:
                pass # Message already deleted or older than 48h
            except Exception as e:
                logger.warning("auto_delete_single_failed", chat_id=chat_id, msg_id=msg_id, error=str(e))
    except Exception as e:
        logger.error("auto_delete_task_failed", error=str(e))

@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()

@router.callback_query(F.data.startswith("kudos:"))
async def give_kudos(callback: CallbackQuery, bot: Bot):
    try:
        uploader_id = int(callback.data.split(":")[1])
        await add_aura(uploader_id, 1)
        
        try:
            await bot.send_message(uploader_id, f"🙏 <b>Someone thanked you!</b>\nYour file was downloaded and a user said thanks. You earned <b>1 Aura</b>.")
        except Exception:
            pass
            
        await callback.answer("🙏 Thanks sent! The uploader earned 1 Aura.", show_alert=True)
    except Exception:
        await callback.answer("Error sending kudos.", show_alert=True)

@router.callback_query(F.data.startswith("btydl:"))
async def bounty_download_callback(callback: CallbackQuery, bot: Bot):
    """Handles the private bounty download button."""
    try:
        doc_id = int(callback.data.split(":")[1])
        
        async with get_session() as session:
            doc = await doc_service.get_document_by_id(session, doc_id)

        if not doc:
            await callback.answer("File not found.", show_alert=True)
            return
            
        await callback.answer("📥 Sending file...")
        
        safe_name = escape(sanitise_text(doc.file_name, 80))
        safe_subject = escape(doc.subject or 'N/A')
        
        try:
            await bot.send_document(
                chat_id=callback.from_user.id, 
                document=doc.file_id, 
                caption=f"📄 <b>{safe_name}</b>\n📚 {safe_subject}\n\nHere is your requested file! [{doc.doc_code}]"
            )
        except Exception as e:
            logger.error("bounty_send_failed", error=str(e), doc_id=doc.id)
            await callback.message.answer("❌ Failed to send the file. The file may be corrupted or removed from storage.")
            return
        
        try:
            await callback.message.delete()
        except Exception:
            pass
            
    except Exception as e:
        logger.error("bounty_download_error", error=str(e))
        await callback.answer("Error downloading file.", show_alert=True)

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
            
            prem_enabled_setting = await session.execute(select(BotSetting).where(BotSetting.key == "premium_enabled"))
            prem_enabled_setting = prem_enabled_setting.scalar_one_or_none()
            show_prem = not (prem_enabled_setting and prem_enabled_setting.value == "false")
            
            user_name = escape(callback.from_user.first_name or "Student")
            default_text = (
                f"<b>PrepCore Hub</b> ✨\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Welcome, <b>{user_name}</b>!\n"
                f"<blockquote>Your ultimate library for study materials. Find notes, PYQs, and books in seconds.</blockquote>\n"
                f"┌ 📚 <b>Library</b>: Thousands of files available.\n"
                f"├ 🔍 <b>Smart Search</b>: Use filters like <code>class:10</code>.\n"
                f"├ 🎯 <b>Bounty</b>: Request files via /bounty\n"
                f"├ 👥 <b>Study Buddy</b>: Find a partner via /studybuddy\n"
                f"└ 🎟️ <b>Premium</b>: Unlock unlimited power.\n\n"
                f"<i>What are we studying today?</i>"
            )
            text = text_setting.value if text_setting and text_setting.value else default_text
            try: await callback.message.delete()
            except: pass
            await bot.send_message(callback.from_user.id, text, reply_markup=main_menu_kb(show_premium=show_prem))

@router.callback_query(F.data == "search_again")
async def search_again(callback: CallbackQuery):
    try:
        await callback.message.edit_text("🔍 <b>New Search</b>\n\nType your search query or use <code>/search \"query\"</code>")
    except Exception:
        pass
    await callback.answer()

async def _send_file_to_user(bot: Bot, callback: CallbackQuery, doc, bot_settings: dict, query_key: str, page: int):
    is_ad_enabled = bot_settings["auto_delete_enabled"]
    ad_seconds = bot_settings["auto_delete_seconds"]
    protect = bot_settings["protect_forwarding"]
    post_file_msg = bot_settings["post_file_message"]
    
    safe_name = escape(sanitise_text(doc.file_name, 80))
    safe_subject = escape(doc.subject or 'N/A')
    safe_category = escape(doc.category or 'N/A')
    
    # 1. Send a premium "Receipt" message while the file is being fetched
    receipt_text = (
        f"<b>Preparing Document</b> 📥\n"
        f"<blockquote><b>File:</b> {safe_name} [{doc.doc_code}]\n"
        f"<b>Subject:</b> {safe_subject}</blockquote>\n"
        f"<i>Fetching from secure storage...</i>"
    )
    
    try:
        receipt_msg = await bot.send_message(chat_id=callback.from_user.id, text=receipt_text)
    except Exception as e:
        logger.error("send_receipt_failed", error=str(e))
        await callback.answer("❌ Error: Could not initiate download. Have you started the bot?", show_alert=True)
        return
        
    # 2. Send the actual file
    caption_parts = []
    if doc.subject: caption_parts.append(f"📚 {safe_subject}")
    if doc.category: caption_parts.append(f"🏷️ {safe_category}")
    caption = " | ".join(caption_parts) if caption_parts else None
    
    try:
        sent_file_msg = await bot.send_document(
            chat_id=callback.from_user.id, 
            document=doc.file_id, 
            protect_content=protect, 
            caption=caption
        )
    except TelegramBadRequest as e:
        logger.error("send_document_bad_request", error=str(e), doc_id=doc.id, file_id=doc.file_id)
        try: await receipt_msg.edit_text("❌ <b>Error:</b> This file is corrupted or has been deleted from the storage channel. Please report this to the admin.")
        except Exception: pass
        return
    except Exception as e:
        logger.error("send_document_unknown_error", error=str(e), doc_id=doc.id)
        try: await receipt_msg.edit_text("❌ An unexpected error occurred while fetching the file.")
        except Exception: pass
        return

    # Keep track of all message IDs sent so we can delete them all if Auto-Delete is on
    msg_ids_to_delete = [sent_file_msg.message_id, receipt_msg.message_id]

    try:
        await receipt_msg.delete()
    except Exception:
        pass

    if post_file_msg:
        try:
            sent_text_msg = await bot.send_message(chat_id=callback.from_user.id, text=post_file_msg, protect_content=protect)
            msg_ids_to_delete.append(sent_text_msg.message_id)
        except Exception as e:
            logger.error("post_file_message_send_failed", error=str(e))

    # 3. Schedule Auto-Delete for ALL messages in one go (Lightweight)
    if is_ad_enabled and ad_seconds > 0:
        asyncio.create_task(_schedule_auto_delete(bot, callback.from_user.id, msg_ids_to_delete, ad_seconds))

    # 4. Update the search results message to show it was sent
    kb = InlineKeyboardBuilder()
    
    if doc.uploaded_by:
        kb.button(text="🙏 Say Thanks", callback_data=f"kudos:{doc.uploaded_by}")
        
    kb.button(text="⬅️ Back to results", callback_data=f"search:{query_key}:{page}")
    kb.button(text="🔍 New Search", callback_data="search_again")
    kb.adjust(1)

    success_text = f"✅ <b>File sent successfully.</b>\n\n<i>Did this file help you? Say thanks to the uploader!</i>"
    try:
        await callback.message.edit_text(success_text, reply_markup=kb.as_markup())
    except TelegramBadRequest:
        # If the message is too old to edit (>48h), just send a new one
        try:
            await bot.send_message(chat_id=callback.from_user.id, text=success_text, reply_markup=kb.as_markup())
        except Exception:
            pass
    except Exception as e:
        logger.error("edit_message_after_send_failed", error=str(e))

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

    is_prem_enabled = bot_settings["premium_enabled"]
    user_is_premium = is_prem_enabled and db_user and db_user.is_premium

    if bot_settings["shortlink_enabled"] and not user_is_premium:
        original_url = "https://google.com" 
        short_url = await get_shortlink(original_url)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="📢 Visit Sponsor", url=short_url)
        kb.button(text="📥 Download File", callback_data=f"dlfile:{doc_id}:{query_key}:{page}")
        kb.adjust(1)
        
        await callback.message.edit_text(
            f"⚠️ <b>Free User Download</b>\n\n"
            f"To download this file, please support us by visiting the sponsor link below.\n"
            f"<i>Premium users download directly without ads.</i>",
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

    await callback.answer("Loading page...")
    
    async with get_session() as session:
        results, total = await search_documents(session, query, page=page, subject=subject, class_name=class_name, year=year)

    if not results:
        await callback.answer("No more results.", show_alert=True)
        return

    per_page = settings.SEARCH_RESULTS_PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)
    
    display_q = "All files" if query.strip() == " " else escape(sanitise_text(query, 100))
    header_text = f"🔍 <b>Search: {display_q}</b>"
    if subject: header_text += f"\n📚 Subject: {escape(subject)}"
    if class_name: header_text += f"\n🎓 Class: {escape(class_name)}"
    if year: header_text += f"\n📅 Year: {year}"
    
    text = f"{header_text}\n📊 Found <b>{total}</b> result(s) — Page {page}/{total_pages}\n\nTap a file to download:"

    try:
        await callback.message.edit_text(text, reply_markup=search_results_keyboard(results, query_key, page, total_pages))
    except TelegramBadRequest: pass
