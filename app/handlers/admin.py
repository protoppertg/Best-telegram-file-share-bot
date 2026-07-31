"""Interactive Telegram Admin Panel."""

from __future__ import annotations

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
from app.utils.validators import sanitise_text

router = Router()


class AdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user and message.from_user.id in settings.admin_ids_list

router.message.filter(AdminFilter())
router.callback_query.filter(F.data.startswith("adm:"))


class ForceSubStates(StatesGroup):
    waiting_channel_id = State()
    waiting_invite_link = State()


def admin_menu_kb():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Statistics", callback_data="adm:stats")
    kb.button(text="👥 Users", callback_data="adm:users:1")
    kb.button(text="📄 Documents", callback_data="adm:docs:1")
    kb.button(text="⏳ Pending", callback_data="adm:pend:1")
    kb.button(text="⚙️ Force Sub", callback_data="adm:fs")
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

def admin_user_actions_kb(telegram_id: int, is_premium: bool):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    if is_premium: kb.button(text="❌ Revoke Premium", callback_data=f"adm:u:{telegram_id}:revoke")
    else: kb.button(text="⭐ Grant Premium (30d)", callback_data=f"adm:u:{telegram_id}:grant")
    kb.button(text="🔄 Reset Search Count", callback_data=f"adm:u:{telegram_id}:reset")
    kb.button(text="🔙 Back to Users", callback_data="adm:users:1")
    kb.adjust(1)
    return kb.as_markup()

def admin_docs_kb(docs: list[Document], page: int, total_pages: int, prefix: str):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    kb = InlineKeyboardBuilder()
    for d in docs:
        status = "⏳" if not d.approved else "✅"
        kb.button(text=f"{status} {d.file_name[:40]}...", callback_data=f"adm:doc:{d.id}")
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

@router.callback_query(F.data == "adm:fs")
async def cb_admin_fs(callback: CallbackQuery, state: FSMContext):
    async with get_session() as session:
        id_setting = await session.execute(select(BotSetting).where(BotSetting.key == "force_sub_channel_id"))
        id_setting = id_setting.scalar_one_or_none()
        link_setting = await session.execute(select(BotSetting).where(BotSetting.key == "force_sub_invite_link"))
        link_setting = link_setting.scalar_one_or_none()
        
    current_id = id_setting.value if id_setting and id_setting.value else "Not set"
    current_link = link_setting.value if link_setting and link_setting.value else "Not set"
    
    text = (
        "⚙️ <b>Force Sub Settings</b>\n\n"
        f"Current Channel ID: <code>{current_id}</code>\n"
        f"Current Invite Link: {current_link}\n\n"
        "1. Send /cancel to abort.\n"
        "2. Send the new Channel ID (e.g., -1001234567890) to update."
    )
    await state.set_state(ForceSubStates.waiting_channel_id)
    await callback.message.edit_text(text)
    await callback.answer()

@router.message(ForceSubStates.waiting_channel_id)
async def fs_channel_id(message: Message, state: FSMContext):
    channel_id = message.text.strip()
    if not channel_id.startswith("-100"):
        await message.answer("Invalid ID. It must start with -100. Try again or /cancel:")
        return
    await state.update_data(channel_id=channel_id)
    await state.set_state(ForceSubStates.waiting_invite_link)
    await message.answer("Now send the Invite Link (e.g., https://t.me/+abc123):")

@router.message(ForceSubStates.waiting_invite_link)
async def fs_invite_link(message: Message, state: FSMContext):
    link = message.text.strip()
    if not link.startswith("https://t.me/"):
        await message.answer("Invalid link. Must start with https://t.me/. Try again or /cancel:")
        return
    data = await state.get_data()
    channel_id = data["channel_id"]
    
    async with get_session() as session:
        id_setting = await session.execute(select(BotSetting).where(BotSetting.key == "force_sub_channel_id"))
        id_setting = id_setting.scalar_one_or_none()
        if not id_setting:
            session.add(BotSetting(key="force_sub_channel_id", value=channel_id))
        else:
            id_setting.value = channel_id
            
        link_setting = await session.execute(select(BotSetting).where(BotSetting.key == "force_sub_invite_link"))
        link_setting = link_setting.scalar_one_or_none()
        if not link_setting:
            session.add(BotSetting(key="force_sub_invite_link", value=link))
        else:
            link_setting.value = link

    await state.clear()
    await message.answer("✅ Force Sub settings updated!", reply_markup=admin_menu_kb())

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
async def cb_admin_user_actions(callback: CallbackQuery):
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
        text = (
            f"👤 <b>User Profile</b>\n\n"
            f"🆔 ID: <code>{user.telegram_id}</code>\n"
            f"👤 Name: {user.first_name or 'N/A'}\n"
            f" USERNAME: @{user.username or 'N/A'}\n\n"
            f"📊 Search Count: {user.search_count}\n"
            f"📤 Upload Count: {user.upload_count}\n"
            f"🎟️ Premium: {prem_status}"
        )
        await callback.message.edit_text(text, reply_markup=admin_user_actions_kb(telegram_id, user.is_premium))
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
        await cb_admin_user_actions(callback)

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
            f"📁 Name: {sanitise_text(doc.file_name, 100)}\n"
            f"📚 Subject: {doc.subject or 'N/A'}\n"
            f"🏷️ Category: {doc.category or 'N/A'}\n"
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
