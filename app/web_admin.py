"""FastAPI Web Admin Panel Router."""

from __future__ import annotations

from typing import Optional
from pathlib import Path
import asyncio
import json
import os
import asyncpg

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, text

from app.config import settings
from app.database import get_session
from app.bot import bot
from app.models import AdminUser, AuditLog, BotSetting, User, Document
from app.services import user as user_service
from app.services import document as doc_service
from app.utils.logger import logger

router = APIRouter(prefix="/admin")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Super Admin Permissions (always allowed)
SUPER_PERMS = ["stats", "users", "documents", "broadcast", "forcesub", "admins"]

async def verify_admin(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    request.state.perms = request.session.get("perms", [])
    return True

async def log_admin_action(request: Request, action: str, target: str = ""):
    """Helper to safely log admin actions"""
    try:
        admin_id = request.session.get("is_admin", "unknown")
        admin_name = "Super Admin" if admin_id == "super" else "Sub-Admin"
        async with get_session() as session:
            log = AuditLog(admin_id=str(admin_id), admin_name=admin_name, action=action, target=target)
            session.add(log)
    except Exception as e:
        logger.error("audit_log_error", error=str(e))

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
    if not s: session.add(BotSetting(key="force_sub_channels", value=val))
    else: s.value = val

async def get_setting(session, key: str, default: str = "") -> str:
    res = await session.execute(select(BotSetting).where(BotSetting.key == key))
    s = res.scalar_one_or_none()
    return s.value if s and s.value else default

async def repair_database():
    async with get_session() as session:
        await session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0"))
        await session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT false"))
        await session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_expiry TIMESTAMP WITH TIME ZONE"))
        await session.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS class_name VARCHAR(100)"))
        await session.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS keywords TEXT[]"))
        await session.execute(text("ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS password VARCHAR(255)"))

@router.get("/login", response_class=templates.TemplateResponse)
async def admin_login(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})

@router.post("/login")
async def admin_login_post(request: Request, password: str = Form(...)):
    if password == settings.WEB_ADMIN_PASSWORD:
        request.session["is_admin"] = "super"
        request.session["perms"] = SUPER_PERMS
        return RedirectResponse(url="/admin/", status_code=303)
        
    try:
        async with get_session() as session:
            res = await session.execute(select(AdminUser).where(AdminUser.password == password))
            admin = res.scalar_one_or_none()
            if admin and admin.permissions:
                request.session["is_admin"] = str(admin.telegram_id)
                request.session["perms"] = [p.strip() for p in admin.permissions.split(",")]
                return RedirectResponse(url="/admin/", status_code=303)
    except Exception:
        await repair_database()
        
    return templates.TemplateResponse(request, "login.html", {"error": "Invalid password"})

@router.get("/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)

@router.get("/", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_dashboard(request: Request):
    try:
        async with get_session() as session:
            stats = await user_service.get_stats(session)
            top_ref_res = await session.execute(
                select(User.username, User.referral_count)
                .where(User.referral_count > 0)
                .order_by(User.referral_count.desc())
                .limit(5)
            )
            top_referrers = top_ref_res.all()
        return templates.TemplateResponse(request, "dashboard.html", {"stats": stats, "top_referrers": top_referrers, "active": "dashboard"})
    except Exception:
        await repair_database()
        return RedirectResponse(url="/admin/", status_code=303)

@router.get("/settings", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_settings(request: Request):
    if "forcesub" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    async with get_session() as session:
        search_enabled = await get_setting(session, "search_enabled", "true")
        ad_enabled = await get_setting(session, "auto_delete_enabled", "false")
        ad_seconds = await get_setting(session, "auto_delete_seconds", "3600")
        protect_fwd = await get_setting(session, "protect_forwarding", "false")
        post_file_msg = await get_setting(session, "post_file_message", "")
        start_text = await get_setting(session, "start_text", "")
        about_text = await get_setting(session, "about_text", "")
        premium_text = await get_setting(session, "premium_text", "")
        premium_enabled = await get_setting(session, "premium_enabled", "true")
        free_search_limit = await get_setting(session, "free_search_limit", str(settings.FREE_SEARCH_LIMIT))
        prem_search_limit = await get_setting(session, "premium_search_limit", str(settings.PREMIUM_SEARCH_LIMIT))
        shortlink_enabled = await get_setting(session, "shortlink_enabled", "false")
        shortlink_api_url = await get_setting(session, "shortlink_api_url", "")
        shortlink_api_key = await get_setting(session, "shortlink_api_key", "")
        referral_reward_type = await get_setting(session, "referral_reward_type", "searches")
        referral_reward_amount = await get_setting(session, "referral_reward_amount", "1")
        channels = await get_force_sub_channels(session)
        
    return templates.TemplateResponse(request, "settings.html", {
        "search_enabled": search_enabled == "true", "ad_enabled": ad_enabled == "true",
        "ad_seconds": ad_seconds, "protect_forwarding": protect_fwd == "true",
        "post_file_message": post_file_msg, "start_text": start_text, "about_text": about_text,
        "premium_text": premium_text, "premium_enabled": premium_enabled == "true",
        "free_search_limit": free_search_limit, "prem_search_limit": prem_search_limit,
        "shortlink_enabled": shortlink_enabled == "true", "shortlink_api_url": shortlink_api_url,
        "shortlink_api_key": shortlink_api_key, "referral_reward_type": referral_reward_type,
        "referral_reward_amount": referral_reward_amount, "channels": channels, "active": "settings"
    })

@router.post("/settings", dependencies=[Depends(verify_admin)])
async def admin_settings_post(request: Request, search_enabled: str = Form("off"), auto_delete_enabled: str = Form("off"), auto_delete_seconds: str = Form("3600"), protect_forwarding: str = Form("off"), post_file_message: str = Form(""), start_text: str = Form(""), about_text: str = Form(""), premium_text: str = Form(""), premium_enabled: str = Form("off"), free_search_limit: str = Form("5"), prem_search_limit: str = Form("100"), shortlink_enabled: str = Form("off"), shortlink_api_url: str = Form(""), shortlink_api_key: str = Form(""), referral_reward_type: str = Form("searches"), referral_reward_amount: str = Form("1")):
    if "forcesub" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    try:
        async with get_session() as session:
            async def save_setting(key: str, value: str):
                s_setting = await session.execute(select(BotSetting).where(BotSetting.key == key))
                s_setting = s_setting.scalar_one_or_none()
                if not s_setting: session.add(BotSetting(key=key, value=value))
                else: s_setting.value = value

            await save_setting("search_enabled", "true" if search_enabled == "on" else "false")
            await save_setting("auto_delete_enabled", "true" if auto_delete_enabled == "on" else "false")
            await save_setting("auto_delete_seconds", auto_delete_seconds if auto_delete_seconds and auto_delete_seconds.isdigit() else "3600")
            await save_setting("protect_forwarding", "true" if protect_forwarding == "on" else "false")
            await save_setting("post_file_message", post_file_message)
            await save_setting("start_text", start_text)
            await save_setting("about_text", about_text)
            await save_setting("premium_text", premium_text)
            await save_setting("premium_enabled", "true" if premium_enabled == "on" else "false")
            await save_setting("free_search_limit", free_search_limit if free_search_limit and free_search_limit.isdigit() else "5")
            await save_setting("premium_search_limit", prem_search_limit if prem_search_limit and prem_search_limit.isdigit() else "100")
            await save_setting("shortlink_enabled", "true" if shortlink_enabled == "on" else "false")
            await save_setting("shortlink_api_url", shortlink_api_url)
            await save_setting("shortlink_api_key", shortlink_api_key)
            await save_setting("referral_reward_type", referral_reward_type or "searches")
            await save_setting("referral_reward_amount", referral_reward_amount if referral_reward_amount and referral_reward_amount.isdigit() else "1")
        await log_admin_action(request, "Updated Bot Settings")
        return RedirectResponse(url="/admin/settings", status_code=303)
    except Exception as e:
        logger.error("web_settings_save_failed", error=str(e), exc_info=True)
        return RedirectResponse(url="/admin/settings?status=error", status_code=303)

@router.post("/settings/fs_add", dependencies=[Depends(verify_admin)])
async def admin_settings_fs_add(request: Request, channel_id: str = Form(...), invite_link: str = Form(...)):
    async with get_session() as session:
        channels = await get_force_sub_channels(session)
        channels.append({"id": channel_id.strip(), "link": invite_link.strip()})
        await save_force_sub_channels(session, channels)
    await log_admin_action(request, "Added Force Sub Channel", channel_id)
    return RedirectResponse(url="/admin/settings", status_code=303)

@router.post("/settings/fs_delete/{index}", dependencies=[Depends(verify_admin)])
async def admin_settings_fs_delete(request: Request, index: int):
    async with get_session() as session:
        channels = await get_force_sub_channels(session)
        if 0 <= index < len(channels):
            channels.pop(index)
            await save_force_sub_channels(session, channels)
    await log_admin_action(request, "Removed Force Sub Channel")
    return RedirectResponse(url="/admin/settings", status_code=303)

@router.get("/broadcast", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_broadcast(request: Request):
    if "broadcast" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    return templates.TemplateResponse(request, "broadcast.html", {"active": "broadcast"})

@router.post("/broadcast", dependencies=[Depends(verify_admin)])
async def admin_broadcast_post(request: Request, message: str = Form(...)):
    async with get_session() as session:
        result = await session.execute(select(User.telegram_id).where(User.is_banned == False))
        user_ids = result.scalars().all()
    asyncio.create_task(_web_background_bcast(message, user_ids))
    await log_admin_action(request, "Sent Broadcast", f"Msg: {message[:20]}...")
    return RedirectResponse(url="/admin/broadcast?status=started", status_code=303)

async def _web_background_bcast(message: str, user_ids: list[int]):
    for uid in user_ids:
        try:
            await bot.send_message(uid, message)
            await asyncio.sleep(0.05)
        except Exception: pass

@router.get("/documents", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_documents(request: Request, page: int = 1, q: Optional[str] = None):
    if "documents" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    per_page = 50
    try:
        async with get_session() as session:
            if q: 
                stmt = select(Document).where(Document.file_name.ilike(f"%{q}%"))
                count_stmt = select(func.count(Document.id)).where(Document.file_name.ilike(f"%{q}%"))
            else: 
                stmt = select(Document)
                count_stmt = select(func.count(Document.id))
            result = await session.execute(stmt.order_by(Document.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
            docs = result.scalars().all()
            total = (await session.execute(count_stmt)).scalar() or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        return templates.TemplateResponse(request, "documents.html", {"docs": docs, "page": page, "total_pages": total_pages, "q": q, "active": "documents"})
    except Exception:
        await repair_database()
        return templates.TemplateResponse(request, "documents.html", {"docs": [], "page": 1, "total_pages": 1, "q": q, "active": "documents"})

@router.get("/documents/edit/{doc_id}", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_edit_doc(request: Request, doc_id: int):
    if "documents" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    try:
        async with get_session() as session:
            doc = await doc_service.get_document_by_id(session, doc_id)
        if not doc: return RedirectResponse(url="/admin/documents?status=notfound", status_code=303)
        return templates.TemplateResponse(request, "edit_document.html", {"doc": doc, "active": "documents"})
    except Exception:
        await repair_database()
        return RedirectResponse(url="/admin/documents?status=error", status_code=303)

@router.post("/documents/edit/{doc_id}", dependencies=[Depends(verify_admin)])
async def admin_edit_doc_post(request: Request, doc_id: int, file_name: str = Form(...), subject: str = Form(""), category: str = Form(""), class_name: str = Form(""), year: str = Form(""), keywords: str = Form(""), description: str = Form("")):
    updates = {"file_name": file_name, "subject": subject or None, "category": category or None, "class_name": class_name or None, "year": int(year) if year and year.isdigit() else None, "keywords": [k.strip() for k in keywords.split(",") if k.strip()], "description": description or None}
    try:
        async with get_session() as session:
            await doc_service.update_document(session, doc_id, **updates)
        await log_admin_action(request, "Edited Document", str(doc_id))
        return RedirectResponse(url="/admin/documents?status=updated", status_code=303)
    except Exception:
        return RedirectResponse(url=f"/admin/documents/edit/{doc_id}?status=error", status_code=303)

@router.post("/documents/{doc_id}/approve", dependencies=[Depends(verify_admin)])
async def admin_approve_doc(request: Request, doc_id: int):
    async with get_session() as session: await doc_service.approve_document(session, doc_id)
    await log_admin_action(request, "Approved Document", str(doc_id))
    return Response(status_code=200)

@router.post("/documents/{doc_id}/delete", dependencies=[Depends(verify_admin)])
async def admin_delete_doc(request: Request, doc_id: int):
    async with get_session() as session: await doc_service.delete_document(session, doc_id)
    await log_admin_action(request, "Deleted Document", str(doc_id))
    return Response(status_code=200)

@router.post("/documents/delete_duplicates", dependencies=[Depends(verify_admin)])
async def admin_delete_duplicates(request: Request):
    async with get_session() as session:
        deleted_count = await doc_service.delete_duplicates(session)
    await log_admin_action(request, "Deleted Duplicates", f"Count: {deleted_count}")
    return RedirectResponse(url=f"/admin/documents?status=deduped&count={deleted_count}", status_code=303)

@router.get("/users", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_users(request: Request, page: int = 1, q: Optional[str] = None):
    if "users" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    per_page = 15
    try:
        async with get_session() as session:
            if q: 
                if q.isdigit():
                    stmt = select(User).where((User.username.ilike(f"%{q}%")) | (User.telegram_id == int(q)) | (User.first_name.ilike(f"%{q}%")) | (User.last_name.ilike(f"%{q}%")))
                    count_stmt = select(func.count(User.id)).where((User.username.ilike(f"%{q}%")) | (User.telegram_id == int(q)) | (User.first_name.ilike(f"%{q}%")) | (User.last_name.ilike(f"%{q}%")))
                else:
                    stmt = select(User).where((User.username.ilike(f"%{q}%")) | (User.first_name.ilike(f"%{q}%")) | (User.last_name.ilike(f"%{q}%")))
                    count_stmt = select(func.count(User.id)).where((User.username.ilike(f"%{q}%")) | (User.first_name.ilike(f"%{q}%")) | (User.last_name.ilike(f"%{q}%")))
            else: 
                stmt = select(User)
                count_stmt = select(func.count(User.id))
            result = await session.execute(stmt.order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
            users = result.scalars().all()
            total = (await session.execute(count_stmt)).scalar() or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        return templates.TemplateResponse(request, "users.html", {"users": users, "page": page, "total_pages": total_pages, "q": q, "active": "users"})
    except Exception:
        await repair_database()
        return templates.TemplateResponse(request, "users.html", {"users": [], "page": 1, "total_pages": 1, "q": q, "active": "users"})

@router.get("/users/{telegram_id}", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_user_profile(request: Request, telegram_id: int):
    if "users" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    try:
        async with get_session() as session:
            user = await session.execute(select(User).where(User.telegram_id == telegram_id))
            user = user.scalar_one_or_none()
        if not user: return RedirectResponse(url="/admin/users?status=notfound", status_code=303)
        return templates.TemplateResponse(request, "user_profile.html", {"u": user, "active": "users"})
    except Exception:
        await repair_database()
        return RedirectResponse(url="/admin/users?status=error", status_code=303)

@router.post("/users/{telegram_id}/send_message", dependencies=[Depends(verify_admin)])
async def admin_send_dm(request: Request, telegram_id: int, message: str = Form(...)):
    try: await bot.send_message(telegram_id, message)
    except Exception as e: logger.error("web_dm_failed", user_id=telegram_id, error=str(e))
    await log_admin_action(request, "Sent DM", str(telegram_id))
    return RedirectResponse(url=f"/admin/users/{telegram_id}?status=sent", status_code=303)

@router.post("/users/{telegram_id}/grant_premium", dependencies=[Depends(verify_admin)])
async def admin_grant_premium(request: Request, telegram_id: int, days: int = Form(30)):
    await user_service.activate_premium(telegram_id, days)
    await log_admin_action(request, "Granted Premium", f"User: {telegram_id}, Days: {days}")
    return RedirectResponse(url=f"/admin/users/{telegram_id}", status_code=303)

@router.post("/users/{telegram_id}/revoke_premium", dependencies=[Depends(verify_admin)])
async def admin_revoke_premium(request: Request, telegram_id: int):
    await user_service.revoke_premium(telegram_id)
    await log_admin_action(request, "Revoked Premium", str(telegram_id))
    return RedirectResponse(url=f"/admin/users/{telegram_id}", status_code=303)

@router.post("/users/{telegram_id}/ban", dependencies=[Depends(verify_admin)])
async def admin_ban_user(request: Request, telegram_id: int):
    await user_service.ban_user(telegram_id)
    await log_admin_action(request, "Banned User", str(telegram_id))
    return RedirectResponse(url=f"/admin/users/{telegram_id}", status_code=303)

@router.post("/users/{telegram_id}/unban", dependencies=[Depends(verify_admin)])
async def admin_unban_user(request: Request, telegram_id: int):
    await user_service.unban_user(telegram_id)
    await log_admin_action(request, "Unbanned User", str(telegram_id))
    return RedirectResponse(url=f"/admin/users/{telegram_id}", status_code=303)

@router.post("/users/{telegram_id}/reset_search", dependencies=[Depends(verify_admin)])
async def admin_reset_search(request: Request, telegram_id: int):
    await user_service.reset_search_count(telegram_id)
    await log_admin_action(request, "Reset Search Count", str(telegram_id))
    return RedirectResponse(url=f"/admin/users/{telegram_id}", status_code=303)

# ── Admin Management (Super Admin Only) ─────────

@router.get("/admins", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_list(request: Request):
    if "admins" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    try:
        async with get_session() as session:
            res = await session.execute(select(AdminUser).order_by(AdminUser.created_at.desc()))
            admins = res.scalars().all()
        return templates.TemplateResponse(request, "admins.html", {"admins": admins, "active": "admins"})
    except Exception:
        await repair_database()
        return RedirectResponse(url="/admin/admins", status_code=303)

@router.post("/admins/add", dependencies=[Depends(verify_admin)])
async def admin_add(request: Request, telegram_id: str = Form(...), name: str = Form(""), password: str = Form(...), permissions: list[str] = Form([])):
    if "admins" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    try:
        tid = int(telegram_id)
        perms = ",".join(permissions)
        async with get_session() as session:
            existing = await session.execute(select(AdminUser).where(AdminUser.telegram_id == tid))
            if not existing.scalar_one_or_none():
                session.add(AdminUser(telegram_id=tid, name=name, password=password, permissions=perms))
        await log_admin_action(request, "Added Admin", str(tid))
    except Exception as e:
        logger.error("admin_add_error", error=str(e))
        await repair_database()
    return RedirectResponse(url="/admin/admins", status_code=303)

@router.get("/admins/edit/{admin_id}", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_edit(request: Request, admin_id: int):
    if "admins" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    try:
        async with get_session() as session:
            admin = await session.get(AdminUser, admin_id)
        if not admin: return RedirectResponse(url="/admin/admins", status_code=303)
        return templates.TemplateResponse(request, "admin_edit.html", {"admin": admin, "active": "admins"})
    except Exception:
        await repair_database()
        return RedirectResponse(url="/admin/admins", status_code=303)

@router.post("/admins/edit/{admin_id}", dependencies=[Depends(verify_admin)])
async def admin_edit_post(request: Request, admin_id: int, name: str = Form(""), password: str = Form(""), permissions: list[str] = Form([])):
    if "admins" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    try:
        async with get_session() as session:
            admin = await session.get(AdminUser, admin_id)
            if admin:
                admin.name = name
                if password: admin.password = password
                admin.permissions = ",".join(permissions)
        await log_admin_action(request, "Edited Admin", str(admin_id))
    except Exception as e:
        logger.error("admin_edit_error", error=str(e))
    return RedirectResponse(url="/admin/admins", status_code=303)

@router.post("/admins/delete/{admin_id}", dependencies=[Depends(verify_admin)])
async def admin_delete(request: Request, admin_id: int):
    if "admins" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    async with get_session() as session:
        admin = await session.get(AdminUser, admin_id)
        if admin: await session.delete(admin)
    await log_admin_action(request, "Deleted Admin", str(admin_id))
    return RedirectResponse(url="/admin/admins", status_code=303)

# ── Activity Logs (Super Admin Only) ─────────

@router.get("/logs", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_logs(request: Request):
    if "admins" not in request.state.perms:
        return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    async with get_session() as session:
        res = await session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100))
        logs = res.scalars().all()
    return templates.TemplateResponse(request, "logs.html", {"logs": logs, "active": "logs"})

# ── Automated Cleanup & Maintenance Tools ─────────

@router.get("/deep_clean", dependencies=[Depends(verify_admin)])
async def deep_clean(request: Request):
    if "admins" not in request.state.perms: return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    async with get_session() as session:
        await session.execute(text("DELETE FROM documents WHERE file_id IS NULL OR file_name IS NULL OR file_name = ''"))
        await session.execute(text("DELETE FROM documents WHERE id NOT IN (SELECT MIN(id) FROM documents GROUP BY file_id)"))
    await log_admin_action(request, "Ran Deep Clean")
    return "✅ Deep Clean Complete!"

@router.get("/fix_sequence", dependencies=[Depends(verify_admin)])
async def fix_sequence(request: Request):
    if "admins" not in request.state.perms: return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    async with get_session() as session:
        await session.execute(text("SELECT setval(pg_get_serial_sequence('documents', 'id'), (SELECT MAX(id) FROM documents));"))
    await log_admin_action(request, "Reset ID Sequence")
    return "✅ Success! The ID counter has been reset."

@router.get("/update_keyboards", dependencies=[Depends(verify_admin)])
async def update_keyboards(request: Request):
    if "admins" not in request.state.perms: return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    from app.utils.keyboards import main_menu_kb
    async with get_session() as session:
        prem_res = await session.execute(select(BotSetting).where(BotSetting.key == "premium_enabled"))
        prem_setting = prem_res.scalar_one_or_none()
        show_prem = not (prem_setting and prem_setting.value == "false")
        result = await session.execute(select(User.telegram_id).where(User.is_banned == False))
        user_ids = result.scalars().all()
    sent_count = 0
    failed_count = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, "✨ <b>PrepCore just got an update!</b>\n\nWe've added a new <b>Referral Program</b>! Check out the new menu button below to invite your friends and earn extra daily searches. 🎁", reply_markup=main_menu_kb(show_premium=show_prem))
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception: failed_count += 1
    await log_admin_action(request, "Updated All Keyboards", f"Sent: {sent_count}")
    return f"✅ Success! Sent the new keyboard to {sent_count} users. ({failed_count} failed/blocked)."

# ── Database Migration Tool ─────────────────────

@router.get("/migrate", dependencies=[Depends(verify_admin)])
async def migrate_data(request: Request):
    if "admins" not in request.state.perms: return RedirectResponse(url="/admin/?status=unauthorized", status_code=303)
    old_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    new_url = os.environ.get("NEW_DATABASE_URL")
    if not new_url: return "Error: NEW_DATABASE_URL is not set."
    if "sslmode" not in new_url: new_url += "?sslmode=require"

    try:
        old_conn = await asyncpg.connect(old_url)
        new_conn = await asyncpg.connect(new_url)
    except Exception as e: return f"Connection failed: {e}"

    await new_conn.execute("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, telegram_id BIGINT UNIQUE NOT NULL, username VARCHAR(255), first_name VARCHAR(255), last_name VARCHAR(255), is_premium BOOLEAN DEFAULT false, premium_expiry TIMESTAMP WITH TIME ZONE, is_banned BOOLEAN DEFAULT false, search_count INTEGER DEFAULT 0, upload_count INTEGER DEFAULT 0, referral_count INTEGER DEFAULT 0, last_reset_date DATE DEFAULT CURRENT_DATE, created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(), updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW());")
    await new_conn.execute("CREATE TABLE IF NOT EXISTS documents (id SERIAL PRIMARY KEY, file_id TEXT NOT NULL, message_id BIGINT, file_name TEXT NOT NULL, subject VARCHAR(255), category VARCHAR(100), class_name VARCHAR(100), year INTEGER, keywords TEXT[], description TEXT, uploaded_by BIGINT, approved BOOLEAN DEFAULT true, created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(), updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW());")
    await new_conn.execute("CREATE TABLE IF NOT EXISTS bot_settings (key VARCHAR(50) PRIMARY KEY, value TEXT);")

    users = await old_conn.fetch("SELECT telegram_id, username, first_name, last_name, is_premium, premium_expiry, is_banned, search_count, upload_count, last_reset_date FROM users")
    if users:
        await new_conn.executemany("INSERT INTO users (telegram_id, username, first_name, last_name, is_premium, premium_expiry, is_banned, search_count, upload_count, last_reset_date) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT DO NOTHING", [(u['telegram_id'], u['username'], u['first_name'], u['last_name'], u['is_premium'], u['premium_expiry'], u['is_banned'], u['search_count'], u['upload_count'], u['last_reset_date']) for u in users])

    docs = await old_conn.fetch("SELECT file_id, message_id, file_name, subject, category, class_name, year, keywords, description, uploaded_by, approved FROM documents")
    if docs:
        await new_conn.executemany("INSERT INTO documents (file_id, message_id, file_name, subject, category, class_name, year, keywords, description, uploaded_by, approved) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) ON CONFLICT DO NOTHING", [(d['file_id'], d['message_id'], d['file_name'], d['subject'], d['category'], d['class_name'], d['year'], d['keywords'], d['description'], d['uploaded_by'], d['approved']) for d in docs])

    settings_row = await old_conn.fetch("SELECT key, value FROM bot_settings")
    if settings_row:
        await new_conn.executemany("INSERT INTO bot_settings (key, value) VALUES ($1, $2) ON CONFLICT DO NOTHING", [(s['key'], s['value']) for s in settings_row])

    await old_conn.close()
    await new_conn.close()
    return f"✅ Success! Copied {len(users)} users, {len(docs)} documents, and {len(settings_row)} settings to the new database."
