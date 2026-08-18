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

def detect_language(text: str) -> str:
    if re.search(r'[\u0900-\u097F]', text):
        return "Hindi"
    hinglish_words = ['hai', 'kya', 'kaise', 'mera', 'tera', 'aaj', 'kal', 'padhai', 'exam', 'bhai', 'yaar', 'nahi', 'haan']
    words = text.lower().split()
    if any(word in hinglish_words for word in words):
        return "Hinglish (Hindi in English script)"
    return "English"

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

@router.message(F.text == "✨ Aura Store")
@router.message(Command("aura"))
async def cmd_aura(message: Message, db_user: User | None = None):
    """Aura Wallet & Store Dashboard"""
    if not db_user:
        await message.answer("Please send /start first to register.")
        return
        
    text = (
        "<b>✨ Aura Dashboard</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Your Balance: <b>{db_user.aura} Aura</b>\n\n"
        "<b>📈 How to Earn Aura:</b>\n"
        "• Upload a file: <b>+10 Aura</b>\n"
        "• Fulfill a Bounty: <b>+50 Aura</b>\n"
        "• Invite a Friend: <b>+10 Aura</b>\n"
        "• Receive Kudos (Thanks): <b>+1 Aura</b>\n\n"
        "<b>🛒 Aura Store</b>\n"
        "Spend your Aura points below!"
    )
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 Buy +5 Searches Today (20 Aura)", callback_data="buy:searches")
    kb.button(text="⭐ Buy 1 Day Premium (100 Aura)", callback_data="buy:premium")
    kb.adjust(1)
    
    await message.answer(text, reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith("buy:"))
async def process_purchase(callback: CallbackQuery, db_user: User | None = None):
    """Handles Aura Store purchases."""
    if not db_user:
        await callback.answer("User not found. Please send /start first.", show_alert=True)
        return
        
    item = callback.data.split(":")[1]
    
    if item == "searches":
        cost = 20
        if db_user.aura < cost:
            await callback.answer(f"Insufficient Aura! You need {cost} but have {db_user.aura}.", show_alert=True)
            return
            
        success = await user_service.deduct_aura(db_user.telegram_id, cost)
        if success:
            await user_service.grant_bonus_searches(db_user.telegram_id, 5)
            await callback.answer("✅ Purchased +5 Searches for today!", show_alert=True)
            await callback.message.edit_text(f"✅ <b>Purchase Successful!</b>\n\nYou spent {cost} Aura and gained +5 searches for today.\nRemaining Aura: <b>{db_user.aura - cost}</b>")
        else:
            await callback.answer("Transaction failed.", show_alert=True)
            
    elif item == "premium":
        cost = 100
        if db_user.aura < cost:
            await callback.answer(f"Insufficient Aura! You need {cost} but have {db_user.aura}.", show_alert=True)
            return
            
        success = await user_service.deduct_aura(db_user.telegram_id, cost)
        if success:
            await user_service.activate_premium(db_user.telegram_id, 1)
            await callback.answer("✅ Purchased 1 Day Premium!", show_alert=True)
            await callback.message.edit_text(f"✅ <b>Purchase Successful!</b>\n\nYou spent {cost} Aura and gained 1 Day of Premium!\nRemaining Aura: <b>{db_user.aura - cost}</b>")
        else:
            await callback.answer("Transaction failed.", show_alert=True)

@router.message(Command("bounty"))
async def cmd_bounty(message: Message, command: CommandObject, db_user: User | None = None):
    if not db_user:
        await message.answer("Please send /start first to register.")
        return
        
    query = command.args
    
    if not query or len(query) < 3:
        async with get_session() as session:
            res = await session.execute(select(Bounty).where(Bounty.fulfilled == False).order_by(Bounty.created_at.desc()).limit(10))
            active_bounties = res.scalars().all()
            
        text = "🎯 <b>Bounty Board</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        text += "<i>Can't find a file? Request it here! If someone uploads it, they earn +50 Aura.</i>\n\n"
        text += "<b>How to use:</b>\n<code>/bounty [file name]</code>\n<i>Example:</i> <code>/bounty HC Verma Physics PDF</code>\n\n"
        
        if active_bounties:
            text += "<b>Active Requests:</b>\n"
            for b in active_bounties:
                text += f"• {escape(b.query)}\n"
        else:
            text += "<b>Active Requests:</b>\nNo active bounties right now. Be the first to request one!"
            
        await message.answer(text)
        return
        
    async with get_session() as session:
        bounty = Bounty(requester_id=db_user.telegram_id, query=query)
        session.add(bounty)
        
    await message.answer(
        "🎯 <b>Bounty Posted!</b>\n"
        f"<blockquote>{escape(query)}</blockquote>\n"
        "If someone uploads a file matching this request, they will instantly earn <b>50 Aura</b>!\n\n"
        "Type /bounty to see what others are looking for."
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
            
        ai_enabled_res = await session.execute(select(BotSetting).where(BotSetting.key == "ai_study_buddy_enabled"))
        ai_enabled_setting = ai_enabled_res.scalar_one_or_none()
        ai_enabled = not (ai_enabled_setting and ai_enabled_setting.value == "false")
            
        match_res = await session.execute(
            select(User).where(User.study_buddy_subject == subject.capitalize(), User.telegram_id != db_user.telegram_id).limit(1)
        )
        match = match_res.scalar_one_or_none()
        
        if match:
            match.study_buddy_subject = None
            match.chat_partner_id = db_user.telegram_id
            
            me.study_buddy_subject = None
            me.chat_partner_id = match.telegram_id
            await session.flush()
            
            await message.answer(f"👥 <b>Study Buddy Found!</b>\nYou are now connected with <b>@{escape(match.username or 'Buddy')}</b> for <b>{safe_subject}</b>.\n<blockquote>Say hi! Type /endchat to disconnect.</blockquote>")
            await message.bot.send_message(match.telegram_id, f"👥 <b>Study Buddy Found!</b>\nYou are now connected with <b>@{escape(message.from_user.username or 'Buddy')}</b> for <b>{safe_subject}</b>.\n<blockquote>Say hi! Type /endchat to disconnect.</blockquote>")
        else:
            if ai_enabled:
                ai_name = random.choice(GHOST_NAMES)
                me.study_buddy_subject = subject.capitalize()
                me.ai_name = ai_name
                me.ai_language = "English"
                await session.flush()
                
                cache = await get_cache()
                await cache.delete(f"ai_chat_history:{db_user.telegram_id}")
                
                await message.answer(f"👥 <b>Study Buddy Found!</b>\nYou are now connected with <b>{ai_name}</b> for <b>{safe_subject}</b>.\n<blockquote>Say hi! Type /endchat to disconnect.</blockquote>")
            else:
                me.study_buddy_subject = subject.capitalize()
                me.ai_name = None
                await session.flush()
                await message.answer(f"⏳ <b>Searching for a Study Buddy...</b>\nYou are now in the queue for <b>{safe_subject}</b>.\n<blockquote>We will notify you the moment someone else joins! Type /endchat to leave the queue.</blockquote>")

@router.message(Command("endchat"))
async def cmd_endchat(message: Message, db_user: User | None = None):
    if not db_user or (not db_user.chat_partner_id and not db_user.study_buddy_subject):
        await message.answer("You are not currently in a chat.")
        return
        
    partner_id = db_user.chat_partner_id
    is_ai = bool(db_user.study_buddy_subject and db_user.ai_name)
    
    async with get_session() as session:
        me = await session.execute(select(User).where(User.telegram_id == db_user.telegram_id))
        me = me.scalar_one_or_none()
        if me:
            me.chat_partner_id = None
            me.study_buddy_subject = None
            me.ai_name = None 
            me.ai_language = None
            
        if partner_id:
            partner = await session.execute(select(User).where(User.telegram_id == partner_id))
            partner = partner.scalar_one_or_none()
            if partner: partner.chat_partner_id = None
        await session.flush()
        
    cache = await get_cache()
    await cache.delete(f"ai_chat_history:{db_user.telegram_id}")
        
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
        s_dict
