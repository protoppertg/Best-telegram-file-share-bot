"""User-facing handlers: /start, /help, /search, file upload flow (FSM)."""

from __future__ import annotations

import uuid
import re
import random
from html import escape
from typing import Any, Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from app.config import settings
from app.database import get_session
from app.models import BotSetting, Bounty, User
from app.services import user as user_service
from app.services import document as doc_service
from app.services.search import search_documents
from app.services.telegram import forward_to_channel
from app.services.cache import get_cache
from app.utils.keyboards import after_file_keyboard, category_keyboard, search_results_keyboard, main_menu_kb
from app.utils.logger import logger
from app.utils.validators import is_valid_search_query, parse_keywords, parse_year, sanitise_text, validate_pdf_document

router = Router()

# Random Indian names for the Ghost AI
GHOST_NAMES = ["Rahul", "Priya", "Amit", "Sneha", "Rohan", "Anjali", "Vikram", "Pooja", "Arjun", "Kavya", "Sanjay", "Neha"]

class UploadStates(StatesGroup):
    waiting_file_name = State()
    waiting_subject = State()
    waiting_category = State()
    waiting_class = State()
    waiting_year = State()
    waiting_keywords = State()

async def _is_premium_enabled() -> bool:
    async with get_session() as session:
        prem_enabled = await session.execute(select(BotSetting).where(BotSetting.key == "premium_enabled"))
        prem_enabled = prem_enabled.scalar_one_or_none()
        return not (prem_enabled and prem_enabled.value == "false")

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    
    if command.args and command.args.startswith("ref_"):
        ref_id_str = command.args.replace("ref_", "")
        if ref_id_str.isdigit():
            ref_id = int(ref_id_str)
            if ref_id != message.from_user.id:
                async with get_session() as session:
                    existing_user = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
                    if not existing_user.scalar_one_or_none():
                        await user_service.add_referral(ref_id)
                        try:
                            await message.bot.send_message(ref_id, "🎉 <b>New Referral!</b>\nSomeone joined using your link. You earned 10 Aura!")
                        except Exception:
                            pass

    async with get_session() as session:
        text_setting = await session.execute(select(BotSetting).where(BotSetting.key == "start_text"))
        text_setting = text_setting.scalar_one_or_none()
        
    user_name = escape(message.from_user.first_name or "Student")
    
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
    
    show_prem = await _is_premium_enabled()
    await message.answer(text, reply_markup=main_menu_kb(show_premium=show_prem))

@router.message(Command("bounty"))
async def cmd_bounty(message: Message, command: CommandObject, db_user: User | None = None):
    if not db_user:
        await message.answer("Please send /start first to register.")
        return
    query = command.args
    if not query or len(query) < 3:
        await message.answer("Usage: <code>/bounty Need HC Verma Physics PDF</code>")
        return
        
    async with get_session() as session:
        bounty = Bounty(requester_id=db_user.telegram_id, query=query)
        session.add(bounty)
        
    await message.answer(
        "🎯 <b>Bounty Posted!</b>\n"
        f"<blockquote>{escape(query)}</blockquote>\n"
        "If someone uploads a file matching this request, they will instantly earn <b>50 Aura</b>!\n\n"
        "Use /leaderboard to see top contributors."
    )

@router.message(Command("studybuddy"))
async def cmd_studybuddy(message: Message, command: CommandObject, db_user: User | None = None):
    if not db_user:
        await message.answer("Please send /start first to register.")
        return
        
    subject = command.args
    if not subject:
        await message.answer("Usage: <code>/studybuddy Physics</code>")
        return
        
    safe_subject = escape(subject.capitalize())
    
    async with get_session() as session:
        me_res = await session.execute(select(User).where(User.telegram_id == db_user.telegram_id))
        me = me_res.scalar_one_or_none()
        
        if not me:
            await message.answer("Error: User profile not found.")
            return
            
        # 1. Try to find a real user first
        match_res = await session.execute(
            select(User).where(User.study_buddy_subject == subject.capitalize(), User.telegram_id != db_user.telegram_id).limit(1)
        )
        match = match_res.scalar_one_or_none()
        
        if match:
            # Connect them in-bot
            match.study_buddy_subject = None
            match.chat_partner_id = db_user.telegram_id
            
            me.study_buddy_subject = None
            me.chat_partner_id = match.telegram_id
            await session.flush()
            
            # Use real usernames for introduction
            await message.answer(f"👥 <b>Study Buddy Found!</b>\nYou are now connected with <b>@{escape(match.username or 'Buddy')}</b> for <b>{safe_subject}</b>.\n<blockquote>Say hi! Type /endchat to disconnect.</blockquote>")
            await message.bot.send_message(match.telegram_id, f"👥 <b>Study Buddy Found!</b>\nYou are now connected with <b>@{escape(message.from_user.username or 'Buddy')}</b> for <b>{safe_subject}</b>.\n<blockquote>Say hi! Type /endchat to disconnect.</blockquote>")
        else:
            # 2. GHOST AI FALLBACK
            ai_name = random.choice(GHOST_NAMES)
            me.study_buddy_subject = subject.capitalize()
            me.ai_name = ai_name
            await session.flush()
            
            # Introduce the AI with its fake name
            await message.answer(f"👥 <b>Study Buddy Found!</b>\nYou are now connected with <b>{ai_name}</b> for <b>{safe_subject}</b>.\n<blockquote>Say hi! Type /endchat to disconnect.</blockquote>")

@router.message(Command("endchat"))
async def cmd_endchat(message: Message, db_user: User | None = None):
    if not db_user or (not db_user.chat_partner_id and not db_user.study_buddy_subject):
        await message.answer("You are not currently in a chat.")
        return
        
    partner_id = db_user.chat_partner_id
    is_ai = bool(db_user.study_buddy_subject)
    
    async with get_session() as session:
        me = await session.execute(select(User).where(User.telegram_id == db_user.telegram_id))
        me = me.scalar_one_or_none()
        if me:
            me.chat_partner_id = None
            me.study_buddy_subject = None
            me.ai_name = None # Clear AI name
            
        if partner_id:
            partner = await session.execute(select(User).where(User.telegram_id == partner_id))
            partner = partner.scalar_one_or_none()
            if partner: partner.chat_partner_id = None
        await session.flush()
        
    await message.answer("👋 <b>Chat Ended.</b>\nYou have been disconnected.")
    if not is_ai and partner_id:
        try:
            await message.bot.send_message(partner_id, "👋 <b>Chat Ended.</b>\nYour Study Buddy has disconnected.")
        except Exception:
            pass

@router.message(Command("leaderboard"))
async def cmd_leaderboard(message: Message):
    top_users = await user_service.get_leaderboard()
    if not top_users:
        await message.answer("📊 <b>Leaderboard is empty!</b>\nBe the first to earn Aura by uploading files!")
        return
        
    text = "🏆 <b>Top Contributors</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for i, u in enumerate(top_users, 1):
        text += f"{i}. @{u.username or 'Unknown'} - <b>{u.aura} Aura</b>\n"
        
    await message.answer(text)

@router.message(F.text == "🤝 Referral")
@router.message(Command("referral"))
async def cmd_referral(message: Message, db_user: User | None = None):
    if not db_user:
        await message.answer("Please send /start first to register.")
        return
        
    bot_info = await message.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{db_user.telegram_id}"
    
    async with get_session() as session:
        res = await session.execute(select(BotSetting).where(BotSetting.key.in_(["referral_reward_type", "referral_reward_amount"])))
        s_dict = {r.key: r.value for r in res.scalars().all()}
        r_type = s_dict.get("referral_reward_type") or "searches"
        r_amount_val = s_dict.get("referral_reward_amount") or "1"
        r_amount = int(r_amount_val) if r_amount_val and r_amount_val.isdigit() else 1

    if r_type == "premium":
        reward_text = f"⭐ <b>{r_amount} Day(s) of Premium</b> & +10 Aura"
    elif r_type == "daily_bonus":
        reward_text = f"⚡ <b>+{r_amount} Bonus Searches Today</b> & +10 Aura"
    else:
        reward_text = f"🔍 <b>+{r_amount} Permanent Daily Searches</b> & +10 Aura"

    text = (
        "<b>Referral Dashboard</b> 🤝\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote><b>Reward:</b> {reward_text}</blockquote>\n"
        f"📊 <b>Your Statistics:</b>\n"
        f"👥 Total Invited: <b>{db_user.referral_count}</b> users\n"
        f"✨ Total Aura: <b>{db_user.aura}</b>\n\n"
        f"🔗 <b>Your Unique Link:</b>\n<code>{ref_link}</code>"
    )
    
    kb = InlineKeyboardBuilder()
    share_text = f"📚 Join PrepCore! The ultimate library for study materials."
    kb.button(text="📤 Share Link", url=f"https://t.me/share/url?url={ref_link}&text={share_text}")
    
    await message.answer(text, reply_markup=kb.as_markup())

@router.message(F.text == "❓ Help")
@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "<b>PrepCore Guide</b> 📖\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote><b>1. Basic Search</b>\nJust type your query.\n"
        "<i>Example:</i> <code>physics thermodynamics</code></blockquote>\n"
        "<blockquote><b>2. Advanced Filters</b>\nNarrow down results instantly.\n"
        "<i>Example:</i> <code>math subject:Algebra year:2023</code></blockquote>\n"
        "<blockquote><b>3. Bounty (</b><code>/bounty</code><b>)</b>\nRequest a file you can't find. If someone uploads it, they get 50 Aura!</blockquote>\n"
        "<blockquote><b>4. Study Buddy (</b><code>/studybuddy</code><b>)</b>\nFind a study partner instantly. If no one is available, PrepCore AI will help you!</blockquote>"
    )
    await message.answer(text)

@router.message(Command("about"))
async def cmd_about(message: Message):
    async with get_session() as session:
        text_setting = await session.execute(select(BotSetting).where(BotSetting.key == "about_text"))
        text_setting = text_setting.scalar_one_or_none()
        
    default_text = (
        "<b>About PrepCore</b> ℹ️\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>PrepCore is a searchable library of study materials. Search for PDFs, notes, and previous year questions.</blockquote>\n"
        "<i>Built with ❤️ using Python and FastAPI.</i>"
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
        "<b>Your Daily Usage</b> 📊\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>🔍 Searches: <b>{db_user.search_count} / {search_limit}</b>\n"
        f"📤 Uploads: <b>{db_user.upload_count} / {upload_limit}</b></blockquote>\n"
        f"✨ Aura: <b>{db_user.aura}</b>\n"
        "<i>Limits reset daily.</i>"
    )
    await message.answer(text)

@router.message(F.text == "🎟️ Premium")
@router.message(Command("premium"))
async def cmd_premium(message: Message, db_user: User | None = None):
    is_premium_enabled = await _is_premium_enabled()
    
    if not is_premium_enabled:
        await message.answer("🚫 <b>Premium is currently disabled by the admin.</b>")
        return

    async with get_session() as session:
        text_setting = await session.execute(select(BotSetting).where(BotSetting.key == "premium_text"))
        text_setting = text_setting.scalar_one_or_none()
        
    if db_user and db_user.is_premium and db_user.premium_expiry:
        status = f"✅ <b>Active</b> until {escape(db_user.premium_expiry.strftime('%Y-%m-%d %H:%M UTC'))}"
    else:
        status = "❌ <b>Not active</b>"

    default_text = (
        f"<b>Premium Status</b> 🎟️\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>Status: {status}</blockquote>\n"
        f"<b>Premium Benefits:</b>\n"
        f"• Unlimited searches per day\n"
        f"• No ads/short links when downloading files\n\n"
        f"<b>How to get Premium:</b>\n"
        f"Send a Rs. 100 gift card to the admin. Once verified, the admin will grant you premium status manually."
    )
        
    text = text_setting.value if text_setting and text_setting.value else default_text
    await message.answer(text)

@router.message(F.text == "🔍 Search")
async def btn_search(message: Message):
    await message.answer("🔍 <i>Please type your search query now (e.g., <code>physics notes</code>):</i>")

@router.message(Command("search"))
async def cmd_search(message: Message, command: CommandObject, db_user: User | None = None):
    query = command.args or ""
    if not is_valid_search_query(query):
        await message.answer("🔍 Please provide a search query.\nExample: <code>/search physics notes</code>")
        return
    await _perform_search(message, query, db_user, page=1)

# Intercept messages for Chat Relay or Ghost AI
@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, db_user: User | None = None):
    query = message.text.strip()
    
    # 1. Check if in a real Study Buddy Chat Relay
    if db_user and db_user.chat_partner_id:
        # CHAT MODERATION: Block links, usernames, and phone numbers
        if re.search(r'http[s]?://|t\.me/|@|(\+?\d{10,})', query, re.I):
            await message.answer("🚫 <b>Warning!</b>\nSharing external links, usernames, or phone numbers is not allowed in Study Buddy chat to prevent spam.")
            return
            
        try:
            await message.bot.send_message(db_user.chat_partner_id, f"👤 <b>Buddy:</b> {escape(query)}")
            await message.answer("✅ <i>Sent.</i>")
        except Exception:
            await message.answer("❌ Failed to send message. Your buddy may have left.")
        return
        
    # 2. Check if in Ghost AI Mode
    if db_user and db_user.study_buddy_subject and db_user.ai_name:
        await message.bot.send_chat_action(message.chat.id, "typing")
        from app.services.ai import get_ghost_ai_response
        # Pass the AI's name so it remembers who it is!
        ai_reply = await get_ghost_ai_response(db_user.ai_name, db_user.study_buddy_subject, query)
        await message.answer(ai_reply) # Send raw text, no "AI:" prefix
        return

    # 3. Normal Search
    if query in ["🔍 Search", "📤 Upload", "🎟️ Premium", "🤝 Referral", "❓ Help"]:
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
    await message.bot.send_chat_action(message.chat.id, "typing")
    
    safe_query = escape(sanitise_text(query, 50))
    status_msg = await message.answer(f"⚙️ <i>Scanning database for <b>{safe_query}</b>...</i>")

    try:
        async with get_session() as session:
            setting = await session.execute(select(BotSetting).where(BotSetting.key == "search_enabled"))
            setting = setting.scalar_one_or_none()
            if setting and setting.value == "false":
                await status_msg.delete()
                await message.answer("🚫 <b>Search is temporarily disabled by the admin.</b>\nPlease try again later.")
                return

            if db_user:
                if not await user_service.check_search_limit(db_user):
                    limit = await user_service.get_user_search_limit(db_user)
                    await status_msg.delete()
                    await message.answer(f"⛔ <b>Daily search limit reached ({limit}/{limit})</b>")
                    return

            clean_q, subject_filter, class_filter, year_filter = _parse_advanced_search(query)
            
            results, total = await search_documents(
                session, clean_q, page=page, 
                subject=subject_filter, class_name=class_filter, year=year_filter
            )

            if db_user:
                await user_service.increment_search_count(db_user.telegram_id)
                await user_service.log_search(session, db_user.id, query, total)

        await status_msg.delete()

        if not results:
            await message.answer(f"🔍 No results found for <b>{escape(sanitise_text(query, 100))}</b>.\nTry different keywords or use /bounty to request it!")
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

        text = (
            f"<b>Search Results</b> 🔍\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>Query: <code>{escape(sanitise_text(query, 100))}</code>\n"
            f"Found: <b>{total}</b> result(s) — Page {page}/{total_pages}</blockquote>\n"
            f"<i>Select a file to download:</i>"
        )
        await message.answer(text, reply_markup=search_results_keyboard(results, query_key, page, total_pages))
        
    except Exception as e:
        logger.error("search_error", error=str(e), exc_info=True)
        try:
            await status_msg.edit_text("❌ An error occurred while searching. Please try again.")
        except Exception:
            pass

@router.message(F.text == "📤 Upload")
async def btn_upload(message: Message):
    await message.answer("📤 <i>Please send the PDF file you want to upload to the library.</i>")

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
    
    tags = user_service.auto_tag_file(original_name)
    await state.update_data(
        subject=tags["subject"],
        category=tags["category"],
        class_name=tags["class_name"],
        year=tags["year"]
    )
    
    await message.answer(
        f"📤 <b>Upload Started</b>\n\n"
        f"📁 File: <code>{escape(sanitise_text(original_name, 100))}</code>\n\n"
        f"🤖 <b>AI Auto-Tagged:</b>\n"
        f"Subject: {tags['subject'] or 'N/A'}\n"
        f"Category: {tags['category'] or 'N/A'}\n"
        f"Class: {tags['class_name'] or 'N/A'}\n"
        f"Year: {tags['year'] or 'N/A'}\n\n"
        f"Send /skip to accept these tags, or type a new <b>file name</b>:"
    )

@router.message(UploadStates.waiting_file_name, F.text)
async def upload_file_name(message: Message, state: FSMContext):
    if message.text.strip().lower() == "/skip":
        data = await state.get_data()
        file_name = data.get("original_file_name", "document.pdf")
    else:
        file_name = sanitise_text(message.text, 500)
        tags = user_service.auto_tag_file(file_name)
        await state.update_data(
            subject=tags["subject"],
            category=tags["category"],
            class_name=tags["class_name"],
            year=tags["year"]
        )
        
    await state.update_data(file_name=file_name)
    data = await state.get_data()
    if data.get("subject") and data.get("category"):
        await state.set_state(UploadStates.waiting_keywords)
        await message.answer(
            f"✅ <b>Auto-Tagging Complete!</b>\n"
            f"Subject: {data.get('subject')}\n"
            f"Category: {data.get('category')}\n"
            f"Class: {data.get('class_name') or 'N/A'}\n"
            f"Year: {data.get('year') or 'N/A'}\n\n"
            f"Enter <b>keywords</b> separated by commas (or /skip):"
        )
    else:
        await state.set_state(UploadStates.waiting_subject)
        await message.answer("Couldn't auto-detect subject. Enter the <b>subject</b> (or /skip):")

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
    await callback.message.edit_text(f"✅ Category: {escape(category)}")
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

    status_msg = await message.answer("⏳ <i>Processing your upload...</i>")
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
        
        matched_bounty = await user_service.check_bounty_match(session, doc.file_name, db_user.telegram_id if db_user else 0)
        if matched_bounty:
            matched_bounty.fulfilled = True
            await user_service.add_aura(db_user.telegram_id, 50)
            await session.flush()
            
            try:
                await bot.send_message(matched_bounty.requester_id, f"🎯 <b>Bounty Fulfilled!</b>\nSomeone uploaded a file matching your request: <i>{escape(matched_bounty.query)}</i>\n\nFile: <code>{escape(doc.file_name)}</code>")
            except Exception:
                pass

        if db_user: await user_service.increment_upload_count(db_user.telegram_id)

    if approved:
        reward_text = f"✅ <b>Upload Successful!</b>\n\n📁 {escape(sanitise_text(doc.file_name, 100))}\n\nThank you for supporting the library! 🙏"
        if matched_bounty:
            reward_text += "\n\n🎉 <b>BONUS:</b> You fulfilled a bounty and earned <b>50 Aura</b>!"
        await status_msg.edit_text(reward_text)
    else:
        await status_msg.edit_text(f"⏳ <b>Upload Received — Pending Approval</b>\n\n📁 {escape(sanitise_text(doc.file_name, 100))}\n\nYour file is awaiting admin approval.")

@router.message(Command("cancel"), StateFilter(None))
async def cancel_idle(message: Message):
    await message.answer("Nothing to cancel.")

@router.message(Command("cancel"))
async def cancel_fsm(message: Message, state: FSMContext):
    await state.clear()
    show_prem = await _is_premium_enabled()
    await message.answer("❌ Operation cancelled. What would you like to do next?", reply_markup=main_menu_kb(show_premium=show_prem))

@router.message(F.text.startswith("/"))
async def unknown_command(message: Message):
    await message.answer("⚠️ I don't recognize this command. Please use the menu below!")
