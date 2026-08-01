"""User-facing handlers: /start, /help, /search, file upload flow (FSM)."""

from __future__ import annotations

import uuid
import re
from html import escape
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select

from app.config import settings
from app.database import get_session
from app.models import BotSetting, User
from app.services import user as user_service
from app.services import document as doc_service
from app.services.search import search_documents
from app.services.telegram import forward_to_channel
from app.services.cache import get_cache
from app.utils.keyboards import after_file_keyboard, category_keyboard, search_results_keyboard, main_menu_kb
from app.utils.logger import logger
from app.utils.validators import is_valid_search_query, parse_keywords, parse_year, sanitise_text, validate_pdf_document

router = Router()


class UploadStates(StatesGroup):
    waiting_file_name = State()
    waiting_subject = State()
    waiting_category = State()
    waiting_class = State()
    waiting_year = State()
    waiting_keywords = State()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    async with get_session() as session:
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
    await message.answer(text, reply_markup=main_menu_kb())


@router.message(F.text == "❓ Help")
@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📖 <b>Help & Guide</b>\n\n"
        "🔍 <b>Basic Search:</b>\n"
        "Just type what you're looking for (e.g., <code>physics notes</code>).\n\n"
        "🚀 <b>Advanced Search:</b>\n"
        "Use filters to narrow down results instantly!\n"
        "<code>math subject:Physics class:Class 10 year:2023</code>\n\n"
        "📤 <b>Upload:</b> Send a PDF to support the library.\n"
        "🎟️ <b>Premium:</b> Get unlimited searches and ad-free downloads."
    )
    await message.answer(text)


@router.message(Command("about"))
async def cmd_about(message: Message):
    async with get_session() as session:
        text_setting = await session.execute(select(BotSetting).where(BotSetting.key == "about_text"))
        text_setting = text_setting.scalar_one_or_none()
        
    default_text = (
        "ℹ️ <b>About PrepCore</b>\n\n"
        "PrepCore is a searchable library of study materials.\n"
        "Search for PDFs, notes, and previous year questions.\n\n"
        "Built with ❤️ using Python and FastAPI."
    )
    text = text_setting.value if text_setting and text_setting.value else default_text
    await message.answer(text)


@router.message(Command("usage"))
async def cmd_usage(message: Message, db_user: User | None = None):
    if not db_user:
        await message.answer("Please send /start first to register.")
        return
    search_limit = await user_service.get_user_search_limit(db_user)
    upload_limit = await user_service.get_user_upload_limit(db_user)
    text = (
        "📊 <b>Your Daily Usage</b>\n\n"
        f"🔍 Searches: {db_user.search_count} / {search_limit}\n"
        f"📤 Uploads: {db_user.upload_count} / {upload_limit}\n\n"
        "Limits reset daily."
    )
    await message.answer(text)


@router.message(F.text == "🎟️ Premium")
@router.message(Command("premium"))
async def cmd_premium(message: Message, db_user: User | None = None):
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
        f"• No ads/short links when downloading files\n\n"
        f"<b>How to get Premium:</b>\n"
        f"Send a Rs. 100 gift card to the admin. Once verified, the admin will grant you premium status manually."
    )
    await message.answer(text)


@router.message(F.text == "🔍 Search")
async def btn_search(message: Message):
    await message.answer("🔍 Please type your search query now (e.g., <code>physics notes</code>):")


@router.message(Command("search"))
async def cmd_search(message: Message, command: CommandObject, db_user: User | None = None):
    query = command.args or ""
    if not is_valid_search_query(query):
        await message.answer("🔍 Please provide a search query.\nExample: <code>/search physics notes</code>")
        return
    await _perform_search(message, query, db_user, page=1)


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def text_search(message: Message, db_user: User | None = None):
    query = message.text.strip()
    if query in ["🔍 Search", "📤 Upload", "🎟️ Premium", "❓ Help"]:
        return
    if not is_valid_search_query(query):
        await message.answer("🔍 Your query is too short. Please enter at least 2 characters.")
        return
    await _perform_search(message, query, db_user, page=1)


def _parse_advanced_search(raw_query: str) -> tuple[str, Optional[str], Optional[str], Optional[int]]:
    subject = None
    class_name = None
    year = None
    clean_query = raw_query

    sub_match = re.search(r'(?:subject|sub):\s*([^\s]+)', raw_query, re.IGNORECASE)
    if sub_match:
        subject = sub_match.group(1)
        clean_query = clean_query.replace(sub_match.group(0), "").strip()

    class_match = re.search(r'(?:class|cls):\s*([^\s]+)', raw_query, re.IGNORECASE)
    if class_match:
        class_name = class_match.group(1)
        clean_query = clean_query.replace(class_match.group(0), "").strip()

    year_match = re.search(r'(?:year|yr):\s*(\d{4})', raw_query, re.IGNORECASE)
    if year_match:
        year = int(year_match.group(1))
        clean_query = clean_query.replace(year_match.group(0), "").strip()

    if not clean_query:
        clean_query = " "

    return clean_query, subject, class_name, year


async def _perform_search(message: Message, query: str, db_user: User | None, page: int) -> None:
    async with get_session() as session:
        setting = await session.execute(select(BotSetting).where(BotSetting.key == "search_enabled"))
        setting = setting.scalar_one_or_none()
        if setting and setting.value == "false":
            await message.answer("🚫 <b>Search is temporarily disabled by the admin.</b>\nPlease try again later.")
            return

        if db_user:
            if not await user_service.check_search_limit(db_user):
                limit = await user_service.get_user_search_limit(db_user)
                await message.answer(f"⛔ <b>Daily search limit reached ({limit}/{limit})</b>")
                return

        clean_q, subject_filter, class_filter, year_filter = _parse_advanced_search(query)
        
        results, total = await search_documents(
            session, clean_q, page=page, 
            subject=subject_filter, class_name=class_filter, year=year_filter
        )

        if db_user:
            await user_service.increment_search_count(session, db_user.telegram_id)
            await user_service.log_search(session, db_user.id, query, total)

    if not results:
        await message.answer(f"🔍 No results found for <b>{escape(sanitise_text(query, 100))}</b>.\nTry different keywords or remove some filters.")
        return

    query_key = uuid.uuid4().hex[:8]
    cache = await get_cache()
    cache_data = {
        "query": clean_q, 
        "subject": subject_filter, 
        "class_name": class_filter, 
        "year": year_filter
    }
    await cache.set(f"searchq:{query_key}", cache_data, ttl=1800)

    per_page = settings.SEARCH_RESULTS_PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)

    text = f"🔍 <b>Search: {escape(sanitise_text(query, 100))}</b>\n📊 Found <b>{total}</b> result(s) — Page {page}/{total_pages}\n\nTap a file to download:"
    await message.answer(text, reply_markup=search_results_keyboard(results, query_key, page, total_pages))


@router.message(F.text == "📤 Upload")
async def btn_upload(message: Message):
    await message.answer("📤 Please send the PDF file you want to upload to the library.")


@router.message(F.document, StateFilter(None))
async def handle_document_upload(message: Message, state: FSMContext, db_user: User | None = None):
    if not message.document: return
    ok, error = validate_pdf_document(message.document)
    if not ok:
        await message.answer(f"❌ {error}")
        return

    if db_user:
        if not await user_service.check_upload_limit(db_user):
            limit = await user_service.get_user_upload_limit(db_user)
            await message.answer(f"⛔ Daily upload limit reached ({limit}/{limit}). Try again tomorrow.")
            return

    original_name = message.document.file_name or "document.pdf"
    await state.update_data(original_file_id=message.document.file_id, original_file_name=original_name, file_size=message.document.file_size)
    await state.set_state(UploadStates.waiting_file_name)
    await message.answer(f"📤 <b>Upload Started</b>\n\nFile: <code>{escape(sanitise_text(original_name, 100))}</code>\n\nEnter a <b>file name</b> (or send /skip to use the original name):")


@router.message(UploadStates.waiting_file_name, F.text)
async def upload_file_name(message: Message, state: FSMContext):
    if message.text.strip().lower() == "/skip":
        data = await state.get_data()
        file_name = data.get("original_file_name", "document.pdf")
    else:
        file_name = sanitise_text(message.text, 500)
    await state.update_data(file_name=file_name)
    await state.set_state(UploadStates.waiting_subject)
    await message.answer("Enter the <b>subject</b> (or /skip):")


@router.message(UploadStates.waiting_subject, F.text)
async def upload_subject(message: Message, state: FSMContext):
    subject = None
    if message.text and message.text.strip().lower() != "/skip": subject = sanitise_text(message.text, 255)
    await state.update_data(subject=subject)
    await state.set_state(UploadStates.waiting_category)
    await message.answer("Select a <b>category</b>:", reply_markup=category_keyboard())


@router.message(UploadStates.waiting_category, F.text)
async def upload_category_text(message: Message, state: FSMContext):
    if message.text and not message.text.startswith("/"):
        await state.update_data(category=sanitise_text(message.text, 100))
        await state.set_state(UploadStates.waiting_class)
        await message.answer("Enter the <b>Class</b> (e.g., Class 10, B.Sc 1st Year) or /skip:")


@router.callback_query(F.data.startswith("upload_cat:"), UploadStates.waiting_category)
async def upload_category_callback(callback, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await state.set_state(UploadStates.waiting_class)
    await callback.message.edit_text(f"✅ Category: {category}")
    await callback.message.answer("Enter the <b>Class</b> (e.g., Class 10, B.Sc 1st Year) or /skip:")
    await callback.answer()


@router.message(UploadStates.waiting_class, F.text)
async def upload_class_name(message: Message, state: FSMContext):
    class_name = None
    if message.text and message.text.strip().lower() != "/skip": class_name = sanitise_text(message.text, 100)
    await state.update_data(class_name=class_name)
    await state.set_state(UploadStates.waiting_year)
    await message.answer("Enter the <b>year</b> (e.g. 2023) or /skip:")


@router.message(UploadStates.waiting_year, F.text)
async def upload_year(message: Message, state: FSMContext):
    year = None
    if message.text and message.text.strip().lower() != "/skip":
        year = parse_year(message.text)
        if year is None:
            await message.answer("⚠️ Invalid year. Please enter a 4-digit year (e.g. 2023) or /skip:")
            return
    await state.update_data(year=year)
    await state.set_state(UploadStates.waiting_keywords)
    await message.answer("Enter <b>keywords</b> separated by commas (or /skip):\nExample: <code>thermodynamics, entropy, exam</code>")


@router.message(UploadStates.waiting_keywords, F.text)
async def upload_keywords(message: Message, state: FSMContext, bot: Bot, db_user: User | None = None):
    keywords = []
    if message.text and message.text.strip().lower() != "/skip": keywords = parse_keywords(message.text)
    await state.update_data(keywords=keywords)
    data = await state.get_data()
    await state.clear()

    status_msg = await message.answer("⏳ Processing your upload...")
    try:
        new_file_id, channel_msg_id = await forward_to_channel(bot, file_id=data["original_file_id"], caption=f"📤 Uploaded by: @{message.from_user.username or message.from_user.id}\n📁 {data.get('file_name', 'document.pdf')}")
    except Exception as exc:
        logger.error("upload_forward_failed", error=str(exc), exc_info=True)
        await status_msg.edit_text("❌ Failed to process your upload. Please try again later.")
        return

    approved = not settings.MODERATION_ENABLED
    async with get_session() as session:
        doc = await doc_service.create_document(
            session, file_id=new_file_id, message_id=channel_msg_id,
            file_name=data.get("file_name", data.get("original_file_name", "document.pdf")),
            subject=data.get("subject"), category=data.get("category"), class_name=data.get("class_name"),
            year=data.get("year"), keywords=keywords, description=None,
            uploaded_by=db_user.telegram_id if db_user else None, approved=approved
        )
        if db_user: await user_service.increment_upload_count(session, db_user.telegram_id)

    if approved:
        await status_msg.edit_text(f"✅ <b>Upload Successful!</b>\n\n📁 {escape(sanitise_text(doc.file_name, 100))}\n\nThank you for supporting the library! 🙏")
    else:
        await status_msg.edit_text(f"⏳ <b>Upload Received — Pending Approval</b>\n\n📁 {escape(sanitise_text(doc.file_name, 100))}\n\nYour file is awaiting admin approval.")
        for admin_id in settings.admin_ids_list:
            try: await bot.send_message(admin_id, f"⏳ <b>New pending upload</b>\nDoc ID: {doc.id}\nUse /admin to approve.")
            except Exception: pass


@router.message(Command("cancel"), StateFilter(None))
async def cancel_idle(message: Message):
    await message.answer("Nothing to cancel.")


@router.message(Command("cancel"))
async def cancel_fsm(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Operation cancelled. What would you like to do next?", reply_markup=main_menu_kb())
