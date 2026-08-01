"""Interactive Telegram Admin Panel, Auto-Indexer, Broadcaster & DMs."""

from __future__ import annotations

import asyncio
import json
from html import escape
from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.config import settings
from app.database import get_session
from app.models import BotSetting, Document, User
from app.services import document as doc_service
from app.services import user as user_service
from app.utils.logger import logger
from app.utils.validators import sanitise_text, parse_keywords, parse_year

router = Router()


class AdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if message.chat.type == "channel":
            return True
        return message.from_user and message.from_user.id in settings.admin_ids_list

router.message.filter(AdminFilter())
router.callback_query.filter(F.data.startswith("adm:"))


class ForceSubStates(StatesGroup):
    waiting_channel_id = State()
    waiting_invite_link = State()

class BroadcastStates(StatesGroup):
    waiting_message = State()

class DirectMessageStates(StatesGroup):
    waiting_message = State()


# ── Helpers for Force Sub JSON ──────────────────

async def get_force_sub_channels(session) -> list[dict]:
    res = await session.execute(select(BotSetting).where(BotSetting.key == "force_sub_channels"))
    s = res.scalar_one_or_none()
    if s and s.value:
        try: return json.loads(s.value)
        except: return []
    return []

async def save_force_sub_channels(session, channels_list: list[dict]):
    res = await session.execute(select(BotSetting).where(BotSetting.key == "force_sub_channels"))
    s = res.scalar_one_or_none()
    val = json.dumps(channels_list)
    if not s:
        session.add(BotSetting(key="force_sub_channels", value=val))
    else:
        s.value = val


# ── Keyboards ───────────────────────────────────

def admin_menu_kb():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Statistics", callback_data="adm:stats")
    kb.button(text="👥 Users", callback_data="adm:users:1")
    kb.button(text="📄 Documents", callback_data="adm:docs:1")
    kb.button(text="⏳ Pending", callback_data="adm:pend:1")
    kb.button(text="⚙️ Force Sub", callback_data="adm:fs")
    kb.button(text="📢 Broadcast", callback_data="adm:bcast")
    kb.adjust(2)
    return kb.as_markup()

def admin_stats_kb():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Back to Menu", callback_data="adm:menu")
    return kb.as_markup()

def admin_users_kb(users: list[User], page: int, total_pages: int):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    kb = InlineKeyboardBuilder()
    for u in users:
        prem = "⭐" if u.is_premium else "🆓"
        kb.button(text=f"{prem} {u.first_name or 'User'} (@{u.username or 'NA'})", callback_data=f"adm:u:{u.telegram_id}")
    kb.adjust(1)
    nav = []
    if page > 1: nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"adm:users:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"adm:users:{page + 1}"))
    if len(nav) > 1: kb.row(*nav)
    kb.button(text="🔙 Back to Menu", callback_data="adm:menu")
    return kb.as_markup()

def admin_user_actions_kb(telegram_id: int, is_premium: bool, is_banned: bool):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    if is_premium: kb.button(text="❌ Revoke Premium", callback_data=f"adm:u:{telegram_id}:revoke")
    else: kb.button(text="⭐ Grant Premium (30d)", callback_data=f"adm:u:{telegram_id}:grant")
    kb.button(text="🔄 Reset Search Count", callback_data=f"adm:u:{telegram_id}:reset")
    if is_banned: kb.button(text="✅ Unban User", callback_data=f"adm:u:{telegram_id}:unban")
    else: kb.button(text="🚫 Ban User", callback_data=f"adm:u:{telegram_id}:ban")
    kb.button(text="✉️ Send Message", callback_data=f"adm:u:{telegram_id}:msg")
    kb.button(text="🔙 Back to Users", callback_data="adm:users:1")
    kb.adjust(1)
    return kb.as_markup()

def admin_docs_kb(docs: list[Document], page: int, total_pages: int, prefix: str):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    kb = InlineKeyboardBuilder()
    for d in docs:
        status = "⏳" if not d.approved else "✅"
        safe_name = d.file_name[:40] if d.file_name else "Untitled"
        kb.button(text=f"{status} {safe_name}...", callback_data=f"adm:doc:{d.id}")
    kb.adjust(1)
    nav = []
    if page > 1: nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"{prefix}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"{prefix}:{page + 1}"))
    if len(nav) > 1: kb.row(*nav)
    kb.button(text="🔙 Back to Menu", callback_data="adm:menu")
    return kb.as_markup()

def admin_doc_actions_kb(doc_id: int, approved: bool):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    if not approved: kb.button(text="✅ Approve", callback_data=f"adm:doc:{doc_id}:approve")
    kb.button(text="🗑 Delete", callback_data=f"adm:doc:{doc_id}:delete")
    kb.button(text="🔙 Back to List", callback_data="adm:docs:1")
    kb.adjust(1)
    return kb.as_markup()


# ── Main Menu & Stats ────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🔧 <b>Admin Panel</b>\n\nWelcome to the control center. Select an option below:", reply_markup=admin_menu_kb())

@router.callback_query(F.data == "adm:menu")
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🔧 <b>Admin Panel</b>\n\nSelect an option below:", reply_markup=admin_menu_kb())
    await callback.answer()

@router.callback_query(F.data == "adm:stats")
async def cb_admin_stats(callback: CallbackQuery):
    async with get_session() as session:
        stats = await user_service.get_stats(session)
    text = (
        "📊 <b>PrepCore Statistics</b>\n\n"
        f"📄 Total Documents: <b>{stats['total_documents']}</b>\n"
        f"👥 Total Users: <b>{stats['total_users']}</b>\n"
        f"⭐ Premium Users: <b>{stats['premium_users']}</b>\n"
        f"🔍 Searches Today: <b>{stats['searches_today']}</b>\n"
        f"📤 Uploads Today: <b>{stats['uploads_today']}</b>\n"
        f"⏳ Pending Documents: <b>{stats['pending_documents']}</b>"
    )
    await callback.message.edit_text(text, reply_markup=admin_stats_kb())
    await callback.answer()

# ── Broadcast Feature ─────────────────────────────

@router.callback_query(F.data == "adm:bcast")
async def cb_admin_bcast(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastStates.waiting_message)
    await callback.message.edit_text(
        "📢 <b>Broadcast Message</b>\n\n"
        "Send the message you want to broadcast to all users.\n"
        "You can send text, photos, videos, or documents.\n\n"
        "Send /cancel to abort."
    )
    await callback.answer()

@router.message(BroadcastStates.waiting_message, Command("cancel"))
async def cancel_bcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Broadcast cancelled.", reply_markup=admin_menu_kb())

@router.message(BroadcastStates.waiting_message)
async def perform_bcast(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    
    async with get_session() as session:
        result = await session.execute(select(User.telegram_id))
        user_ids = result.scalars().all()

    total_users = len(user_ids)
    await message.answer(f"⏳ <b>Broadcasting to {total_users} users...</b>\nThis may take a few minutes. I will notify you when it's done.")
    
    asyncio.create_task(_background_bcast(bot, message.chat.id, message.message_id, user_ids))

async def _background_bcast(bot: Bot, admin_chat_id: int, message_id: int, user_ids: list[int]):
    sent_count = 0
    failed_count = 0

    for uid in user_ids:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=admin_chat_id, message_id=message_id)
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed_count += 1
            
    await bot.send_message(
        admin_chat_id,
        f"✅ <b>Broadcast Finished!</b>\n\n"
        f"👥 Total Users: {len(user_ids)}\n"
        f"✅ Sent Successfully: {sent_count}\n"
        f"❌ Failed (blocked bot): {failed_count}",
        reply_markup=admin_menu_kb()
    )

# ── Force Sub Settings (Multiple Channels) ────────

@router.callback_query(F.data == "adm:fs")
async def cb_admin_fs(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    async with get_session() as session:
        channels = await get_force_sub_channels(session)
        
    if not channels:
        text = "⚙️ <b>Force Sub Settings</b>\n\nNo channels added yet.\n\n1. Send /cancel to abort.\n2. Send a new Channel ID (e.g., -1001234567890) to add."
    else:
        text = "⚙️ <b>Force Sub Settings</b>\n\nCurrent Channels:\n"
        for ch in channels:
            text += f"• <code>{ch['id']}</code> - {ch['link']}\n"
        text += "\n1. Send /cancel to abort.\n2. Send a new Channel ID to add.\n3. Send /clear to remove all channels."

    await state.set_state(ForceSubStates.waiting_channel_id)
    await callback.message.edit_text(text)
    await callback.answer()

@router.message(ForceSubStates.waiting_channel_id, F.text & ~F.text.startswith("/"))
async def fs_channel_id(message: Message, state: FSMContext):
    channel_id = message.text.strip()
    if not channel_id.startswith("-100"):
        await message.answer("Invalid ID. It must start with -100. Try again or /cancel:")
        return
    await state.update_data(channel_id=channel_id)
    await state.set_state(ForceSubStates.waiting_invite_link)
    await message.answer("Now send the Invite Link (e.g., https://t.me/+abc123):")

@router.message(ForceSubStates.waiting_invite_link, F.text & ~F.text.startswith("/"))
async def fs_invite_link(message: Message, state: FSMContext):
    link = message.text.strip()
    if not link.startswith("https://t.me/"):
        await message.answer("Invalid link. Must start with https://t.me/. Try again or /cancel:")
        return
    data = await state.get_data()
    channel_id = data["channel_id"]
    
    async with get_session() as session:
        channels = await get_force_sub_channels(session)
        channels.append({"id": channel_id, "link": link})
        await save_force_sub_channels(session, channels)

    await state.clear()
    await message.answer("✅ Force Sub channel added!", reply_markup=admin_menu_kb())

@router.message(ForceSubStates.waiting_channel_id, Command("clear"))
async def fs_clear(message: Message, state: FSMContext):
    await state.clear()
    async with get_session() as session:
        await save_force_sub_channels(session, [])
    await message.answer("🗑 All Force Sub channels cleared.", reply_markup=admin_menu_kb())

# ── Auto-Index Channel Posts ─────────────────────

@router.channel_post(F.document)
async def auto_index_channel_post(message: Message, bot: Bot):
    try:
        target_channel = int(settings.CHANNEL_ID)
    except (ValueError, TypeError):
        return 

    if message.chat.id != target_channel:
        return

    if message.caption and message.caption.startswith("📤 Uploaded by:"):
        return

    file_id = message.document.file_id
    file_name = message.document.file_name or "Untitled.pdf"
    message_id = message.message_id

    async with get_session() as session:
        result = await session.execute(select(Document).where(Document.file_id == file_id))
        if result.scalar_one_or_none():
            return

        doc = await doc_service.create_document(
            session, file_id=file_id, message_id=message_id, file_name=file_name,
            subject="Uncategorized", category="Uncategorized", approved=True
        )

    for admin_id in settings.admin_ids_list:
        try:
            await bot.send_message(
                admin_id,
                f"📥 <b>Auto-Indexed File</b>\n\n"
                f"📁 Name: <code>{escape(file_name)}</code>\n"
                f"🆔 ID: {doc.id}\n\n"
                f"Use the command below to update its metadata so users can find it:\n"
                f"<code>/edit_doc {doc.id} subject=Physics category=PYQ year=2023</code>"
            )
        except Exception:
            pass

# ── Edit Document Metadata ───────────────────────

@router.message(Command("edit_doc"))
async def cmd_edit_doc(message: Message, command: CommandObject):
    if not command.args:
        await message.answer(
            "Usage: <code>/edit_doc [id] [field]=[value]</code>\n\n"
            "Fields: file_name, subject, category, class_name, year, keywords\n\n"
            "Example: <code>/edit_doc 42 subject=Physics class_name=Class 10 year=2023</code>"
        )
        return

    parts = command.args.split(maxsplit=1)
    try:
        doc_id = int(parts[0])
    except ValueError:
        await message.answer("Invalid document ID.")
        return

    if len(parts) < 2:
        await message.answer("No fields to update. Example: <code>/edit_doc 42 subject=Physics</code>")
        return

    updates = {}
    for pair in parts[1].split():
        if "=" not in pair: continue
        field, value = pair.split("=", 1)
        field = field.strip().lower()
        if field in ("file_name", "subject", "category", "class_name"):
            updates[field] = sanitise_text(value, 255)
        elif field == "year":
            updates[field] = parse_year(value)
        elif field == "keywords":
            updates[field] = parse_keywords(value)

    if not updates:
        await message.answer("No valid fields to update.")
        return

    async with get_session() as session:
        doc = await doc_service.update_document(session, doc_id, **updates)

    if doc:
        await message.answer(f"✅ Updated document {doc_id}\nFields: {', '.join(updates.keys())}")
    else:
        await message.answer(f"Document {doc_id} not found.")


# ── User Management & Direct Message ───────────────

@router.callback_query(F.data.startswith("adm:users:"))
async def cb_admin_users(callback: CallbackQuery):
    page = int(callback.data.split(":")[2])
    per_page = 5
    async with get_session() as session:
        total = (await session.execute(select(func.count(User.id)))).scalar() or 0
        result = await session.execute(select(User).order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
        users = result.scalars().all()
    total_pages = max(1, (total + per_page - 1) // per_page)
    await callback.message.edit_text(f"👥 <b>Users Management</b> ({total} total)\nSelect a user:", reply_markup=admin_users_kb(users, page, total_pages))
    await callback.answer()

@router.callback_query(F.data.startswith("adm:u:"))
async def cb_admin_user_actions(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    telegram_id = int(parts[2])
    
    if len(parts) == 3:
        async with get_session() as session:
            user = await session.execute(select(User).where(User.telegram_id == telegram_id))
            user = user.scalar_one_or_none()
        if not user:
            await callback.answer("User not found.", show_alert=True)
            return
        prem_status = "⭐ ACTIVE" if user.is_premium else "❌ INACTIVE"
        if user.premium_expiry: prem_status += f" (until {user.premium_expiry.strftime('%Y-%m-%d')})"
        
        ban_status = "🚫 BANNED" if user.is_banned else "✅ ACTIVE"
        
        text = (
            f"👤 <b>User Profile</b>\n\n"
            f"🆔 ID: <code>{user.telegram_id}</code>\n"
            f"👤 Name: {escape(user.first_name or 'N/A')}\n"
            f" USERNAME: @{escape(user.username or 'N/A')}\n\n"
            f"📊 Search Count: {user.search_count}\n"
            f"📤 Upload Count: {user.upload_count}\n"
            f"🎟️ Premium: {prem_status}\n"
            f"🚫 Status: {ban_status}"
        )
        await callback.message.edit_text(text, reply_markup=admin_user_actions_kb(telegram_id, user.is_premium, user.is_banned))
        await callback.answer()
    elif len(parts) == 4:
        action = parts[3]
        if action == "grant":
            await user_service.activate_premium(telegram_id, 30)
            await callback.answer("Premium granted for 30 days!", show_alert=True)
        elif action == "revoke":
            await user_service.revoke_premium(telegram_id)
            await callback.answer("Premium revoked!", show_alert=True)
        elif action == "reset":
            await user_service.reset_search_count(telegram_id)
            await callback.answer("Search count reset!", show_alert=True)
        elif action == "ban":
            await user_service.ban_user(telegram_id)
            await callback.answer("User banned!", show_alert=True)
        elif action == "unban":
            await user_service.unban_user(telegram_id)
            await callback.answer("User unbanned!", show_alert=True)
        elif action == "msg":
            await state.set_state(DirectMessageStates.waiting_message)
            await state.update_data(target_id=telegram_id)
            await callback.message.answer(
                f"✉️ <b>Direct Message to User {telegram_id}</b>\n\n"
                "Send the message you want to send to this user. You can send text, photos, videos, or documents.\n\n"
                "Send /cancel to abort."
            )
            await callback.answer()
        await cb_admin_user_actions(callback)

@router.message(DirectMessageStates.waiting_message, Command("cancel"))
async def cancel_dm(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Direct message cancelled.", reply_markup=admin_menu_kb())

@router.message(DirectMessageStates.waiting_message)
async def perform_dm(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    target_id = data.get("target_id")
    await state.clear()

    if not target_id:
        await message.answer("❌ Error: Target user not found. Please try again.")
        return

    try:
        await bot.copy_message(chat_id=target_id, from_chat_id=message.chat.id, message_id=message.message_id)
        await message.answer(f"✅ Message sent successfully to user <code>{target_id}</code>.")
    except Exception as e:
        await message.answer(f"❌ Failed to send message to user <code>{target_id}</code>. They may have blocked the bot.\nError: {escape(str(e))}")

# ── Document Management ───────────────────────────

@router.callback_query(F.data.startswith("adm:docs:"))
async def cb_admin_docs(callback: CallbackQuery):
    page = int(callback.data.split(":")[2])
    per_page = 5
    async with get_session() as session:
        total = (await session.execute(select(func.count(Document.id)))).scalar() or 0
        result = await session.execute(select(Document).order_by(Document.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
        docs = result.scalars().all()
    total_pages = max(1, (total + per_page - 1) // per_page)
    await callback.message.edit_text(f"📄 <b>Documents Management</b> ({total} total)\nSelect a document:", reply_markup=admin_docs_kb(docs, page, total_pages, "adm:docs"))
    await callback.answer()

@router.callback_query(F.data.startswith("adm:pend:"))
async def cb_admin_pending(callback: CallbackQuery):
    page = int(callback.data.split(":")[2])
    per_page = 5
    async with get_session() as session:
        total = (await session.execute(select(func.count(Document.id)).where(Document.approved == False))).scalar() or 0
        result = await session.execute(select(Document).where(Document.approved == False).order_by(Document.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
        docs = result.scalars().all()
    if not docs:
        await callback.answer("No pending documents!", show_alert=True)
        return
    total_pages = max(1, (total + per_page - 1) // per_page)
    await callback.message.edit_text(f"⏳ <b>Pending Approval</b> ({total} total)\nSelect a document:", reply_markup=admin_docs_kb(docs, page, total_pages, "adm:pend"))
    await callback.answer()

@router.callback_query(F.data.startswith("adm:doc:"))
async def cb_admin_doc_actions(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    doc_id = int(parts[2])
    if len(parts) == 3:
        async with get_session() as session:
            doc = await doc_service.get_document_by_id(session, doc_id)
        if not doc:
            await callback.answer("Document not found.", show_alert=True)
            return
        text = (
            f"📄 <b>Document Details</b>\n\n"
            f"🆔 ID: <code>{doc.id}</code>\n"
            f"📁 Name: {escape(sanitise_text(doc.file_name, 100))}\n"
            f"📚 Subject: {escape(doc.subject or 'N/A')}\n"
            f"🏷️ Category: {escape(doc.category or 'N/A')}\n"
            f"✅ Approved: {'Yes' if doc.approved else 'No'}\n"
        )
        await callback.message.edit_text(text, reply_markup=admin_doc_actions_kb(doc.id, doc.approved))
        await callback.answer()
    elif len(parts) == 4:
        action = parts[3]
        if action == "approve":
            async with get_session() as session: await doc_service.approve_document(session, doc_id)
            await callback.answer("Document approved!", show_alert=True)
        elif action == "delete":
            async with get_session() as session: await doc_service.delete_document(session, doc_id)
            await callback.answer("Document deleted!", show_alert=True)
            callback.data = "adm:docs:1"
            await cb_admin_docs(callback)
            return
        await cb_admin_doc_actions(callback, bot)
