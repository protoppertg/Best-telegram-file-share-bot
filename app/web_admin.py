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
from app.models import BotSetting, User, Document
from app.services import user as user_service
from app.services import document as doc_service
from app.utils.logger import logger

router = APIRouter(prefix="/admin")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


async def verify_admin(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return True

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

@router.get("/login", response_class=templates.TemplateResponse)
async def admin_login(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})

@router.post("/login")
async def admin_login_post(request: Request, password: str = Form(...)):
    if password == settings.WEB_ADMIN_PASSWORD:
        request.session["is_admin"] = True
        return RedirectResponse(url="/admin/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": "Invalid password"})

@router.get("/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)

@router.get("/", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_dashboard(request: Request):
    async with get_session() as session:
        stats = await user_service.get_stats(session)
        # Fetch top 5 referrers for the chart
        top_ref_res = await session.execute(
            select(User.username, User.referral_count)
            .where(User.referral_count > 0)
            .order_by(User.referral_count.desc())
            .limit(5)
        )
        top_referrers = top_ref_res.all()
    return templates.TemplateResponse(request, "dashboard.html", {"stats": stats, "top_referrers": top_referrers, "active": "dashboard"})
    
@router.get("/settings", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_settings(request: Request):
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
        channels = await get_force_sub_channels(session)
        referral_reward_type = await get_setting(session, "referral_reward_type", "searches")
        referral_reward_amount = await get_setting(session, "referral_reward_amount", "1")
        
    return templates.TemplateResponse(request, "settings.html", {
        "search_enabled": search_enabled == "true", 
        "ad_enabled": ad_enabled == "true",
        "ad_seconds": ad_seconds,
        "protect_forwarding": protect_fwd == "true",
        "post_file_message": post_file_msg,
        "start_text": start_text,
        "about_text": about_text,
        "premium_text": premium_text,
        "premium_enabled": premium_enabled == "true",
        "free_search_limit": free_search_limit,
        "prem_search_limit": prem_search_limit,
        "shortlink_enabled": shortlink_enabled == "true",
        "shortlink_api_url": shortlink_api_url,
        "shortlink_api_key": shortlink_api_key,
        "channels": channels,
        "active": "settings"
        "referral_reward_type": referral_reward_type,
        "referral_reward_amount": referral_reward_amount,
    })

@router.post("/settings", dependencies=[Depends(verify_admin)])
async def admin_settings_post(
    request: Request, 
    search_enabled: str = Form("off"), 
    auto_delete_enabled: str = Form("off"),
    auto_delete_seconds: str = Form("3600"),
    protect_forwarding: str = Form("off"),
    post_file_message: str = Form(""),
    start_text: str = Form(""),
    about_text: str = Form(""),
    premium_text: str = Form(""),
    premium_enabled: str = Form("off"),
    free_search_limit: str = Form("5"),
    prem_search_limit: str = Form("100"),
    shortlink_enabled: str = Form("off"),
    shortlink_api_url: str = Form(""),
    shortlink_api_key: str = Form("")
):
    try:
        async with get_session() as session:
            async def save_setting(key: str, value: str):
                s_setting = await session.execute(select(BotSetting).where(BotSetting.key == key))
                s_setting = s_setting.scalar_one_or_none()
                if not s_setting: session.add(BotSetting(key=key, value=value))
                else: s_setting.value = value

            await save_setting("search_enabled", "true" if search_enabled == "on" else "false")
            await save_setting("auto_delete_enabled", "true" if auto_delete_enabled == "on" else "false")
            await save_setting("auto_delete_seconds", auto_delete_seconds if auto_delete_seconds.isdigit() else "3600")
            await save_setting("protect_forwarding", "true" if protect_forwarding == "on" else "false")
            await save_setting("post_file_message", post_file_message)
            await save_setting("start_text", start_text)
            await save_setting("about_text", about_text)
            await save_setting("premium_text", premium_text)
            await save_setting("premium_enabled", "true" if premium_enabled == "on" else "false")
            await save_setting("free_search_limit", free_search_limit if free_search_limit.isdigit() else "5")
            await save_setting("premium_search_limit", prem_search_limit if prem_search_limit.isdigit() else "100")
            await save_setting("shortlink_enabled", "true" if shortlink_enabled == "on" else "false")
            await save_setting("shortlink_api_url", shortlink_api_url)
            await save_setting("shortlink_api_key", shortlink_api_key)
                
        return RedirectResponse(url="/admin/settings", status_code=303)
    except Exception as e:
        logger.error("web_settings_save_failed", error=str(e), exc_info=True)
        return RedirectResponse(url="/admin/settings?status=error", status_code=303)

@router.post("/settings/fs_add", dependencies=[Depends(verify_admin)])
async def admin_settings_fs_add(channel_id: str = Form(...), invite_link: str = Form(...)):
    async with get_session() as session:
        channels = await get_force_sub_channels(session)
        channels.append({"id": channel_id.strip(), "link": invite_link.strip()})
        await save_force_sub_channels(session, channels)
    return RedirectResponse(url="/admin/settings", status_code=303)

@router.post("/settings/fs_delete/{index}", dependencies=[Depends(verify_admin)])
async def admin_settings_fs_delete(index: int):
    async with get_session() as session:
        channels = await get_force_sub_channels(session)
        if 0 <= index < len(channels):
            channels.pop(index)
            await save_force_sub_channels(session, channels)
    return RedirectResponse(url="/admin/settings", status_code=303)

@router.get("/broadcast", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_broadcast(request: Request):
    return templates.TemplateResponse(request, "broadcast.html", {"active": "broadcast"})

@router.post("/broadcast", dependencies=[Depends(verify_admin)])
async def admin_broadcast_post(message: str = Form(...)):
    async with get_session() as session:
        result = await session.execute(select(User.telegram_id).where(User.is_banned == False))
        user_ids = result.scalars().all()
    asyncio.create_task(_web_background_bcast(message, user_ids))
    return RedirectResponse(url="/admin/broadcast?status=started", status_code=303)

async def _web_background_bcast(message: str, user_ids: list[int]):
    for uid in user_ids:
        try:
            await bot.send_message(uid, message)
            await asyncio.sleep(0.05)
        except Exception: pass

# ── Documents (Fixed Counting Logic) ─────────────

@router.get("/documents", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_documents(request: Request, page: int = 1, q: Optional[str] = None):
    per_page = 50
    async with get_session() as session:
        if q: 
            stmt = select(Document).where(Document.file_name.ilike(f"%{q}%"))
            count_stmt = select(func.count(Document.id)).where(Document.file_name.ilike(f"%{q}%"))
        else: 
            stmt = select(Document)
            count_stmt = select(func.count(Document.id))
            
        result = await session.execute(stmt.order_by(Document.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
        docs = result.scalars().all()
        
        # Use SQL Count instead of fetching all rows into memory
        total = (await session.execute(count_stmt)).scalar() or 0
        
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(request, "documents.html", {"docs": docs, "page": page, "total_pages": total_pages, "q": q, "active": "documents"})

@router.get("/documents/edit/{doc_id}", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_edit_doc(request: Request, doc_id: int):
    async with get_session() as session:
        doc = await doc_service.get_document_by_id(session, doc_id)
    if not doc: return RedirectResponse(url="/admin/documents", status_code=303)
    return templates.TemplateResponse(request, "edit_document.html", {"doc": doc, "active": "documents"})

@router.post("/documents/edit/{doc_id}", dependencies=[Depends(verify_admin)])
async def admin_edit_doc_post(doc_id: int, file_name: str = Form(...), subject: str = Form(""), category: str = Form(""), class_name: str = Form(""), year: str = Form(""), keywords: str = Form(""), description: str = Form("")):
    updates = {
        "file_name": file_name, "subject": subject or None, "category": category or None, 
        "class_name": class_name or None, "year": int(year) if year.isdigit() else None,
        "keywords": [k.strip() for k in keywords.split(",") if k.strip()], "description": description or None
    }
    async with get_session() as session:
        await doc_service.update_document(session, doc_id, **updates)
    return RedirectResponse(url="/admin/documents", status_code=303)

@router.post("/documents/{doc_id}/approve", dependencies=[Depends(verify_admin)])
async def admin_approve_doc(doc_id: int):
    async with get_session() as session: await doc_service.approve_document(session, doc_id)
    return Response(status_code=200)

@router.post("/documents/{doc_id}/delete", dependencies=[Depends(verify_admin)])
async def admin_delete_doc(doc_id: int):
    async with get_session() as session: await doc_service.delete_document(session, doc_id)
    return Response(status_code=200)

@router.post("/documents/delete_duplicates", dependencies=[Depends(verify_admin)])
async def admin_delete_duplicates():
    async with get_session() as session:
        deleted_count = await doc_service.delete_duplicates(session)
    return RedirectResponse(url=f"/admin/documents?status=deduped&count={deleted_count}", status_code=303)

# ── Users ─────────────────────────────────────────

@router.get("/users", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_users(request: Request, page: int = 1, q: Optional[str] = None):
    per_page = 15
    async with get_session() as session:
        if q: 
            stmt = select(User).where((User.username.ilike(f"%{q}%")) | (User.telegram_id == q))
            count_stmt = select(func.count(User.id)).where((User.username.ilike(f"%{q}%")) | (User.telegram_id == q))
        else: 
            stmt = select(User)
            count_stmt = select(func.count(User.id))
            
        result = await session.execute(stmt.order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
        users = result.scalars().all()
        total = (await session.execute(count_stmt)).scalar() or 0
        
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(request, "users.html", {"users": users, "page": page, "total_pages": total_pages, "q": q, "active": "users"})

@router.get("/users/{telegram_id}", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_user_profile(request: Request, telegram_id: int):
    async with get_session() as session:
        user = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = user.scalar_one_or_none()
    if not user: return RedirectResponse(url="/admin/users", status_code=303)
    return templates.TemplateResponse(request, "user_profile.html", {"u": user, "active": "users"})

@router.post("/users/{telegram_id}/send_message", dependencies=[Depends(verify_admin)])
async def admin_send_dm(telegram_id: int, message: str = Form(...)):
    try: await bot.send_message(telegram_id, message)
    except Exception as e: logger.error("web_dm_failed", user_id=telegram_id, error=str(e))
    return RedirectResponse(url=f"/admin/users/{telegram_id}?status=sent", status_code=303)

@router.post("/users/{telegram_id}/grant_premium", dependencies=[Depends(verify_admin)])
async def admin_grant_premium(telegram_id: int, days: int = Form(30)):
    await user_service.activate_premium(telegram_id, days)
    return RedirectResponse(url=f"/admin/users/{telegram_id}", status_code=303)

@router.post("/users/{telegram_id}/revoke_premium", dependencies=[Depends(verify_admin)])
async def admin_revoke_premium(telegram_id: int):
    await user_service.revoke_premium(telegram_id)
    return RedirectResponse(url=f"/admin/users/{telegram_id}", status_code=303)

@router.post("/users/{telegram_id}/ban", dependencies=[Depends(verify_admin)])
async def admin_ban_user(telegram_id: int):
    await user_service.ban_user(telegram_id)
    return RedirectResponse(url=f"/admin/users/{telegram_id}", status_code=303)

@router.post("/users/{telegram_id}/unban", dependencies=[Depends(verify_admin)])
async def admin_unban_user(telegram_id: int):
    await user_service.unban_user(telegram_id)
    return RedirectResponse(url=f"/admin/users/{telegram_id}", status_code=303)

@router.post("/users/{telegram_id}/reset_search", dependencies=[Depends(verify_admin)])
async def admin_reset_search(telegram_id: int):
    await user_service.reset_search_count(telegram_id)
    return RedirectResponse(url=f"/admin/users/{telegram_id}", status_code=303)

# ── Automated Cleanup Tools ─────────────────────

@router.get("/deep_clean", dependencies=[Depends(verify_admin)])
async def deep_clean():
    """Deletes broken rows, empty rows, and exact duplicate file_ids."""
    async with get_session() as session:
        await session.execute(text("DELETE FROM documents WHERE file_id IS NULL OR file_name IS NULL OR file_name = ''"))
        await session.execute(text("""
            DELETE FROM documents
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM documents
                GROUP BY file_id
            )
        """))
        
    return "✅ Deep Clean Complete! Broken files and duplicates removed. Please hard-refresh your browser (Ctrl+F5)."

@router.get("/fix_sequence", dependencies=[Depends(verify_admin)])
async def fix_sequence():
    """Resets the database ID counter so the next upload is exactly +1 from the current max ID."""
    from sqlalchemy import text
    async with get_session() as session:
        await session.execute(text("SELECT setval(pg_get_serial_sequence('documents', 'id'), (SELECT MAX(id) FROM documents));"))
        
    return "✅ Success! The ID counter has been reset. The next file you upload will be exactly +1 from your highest current ID."

# ── Database Migration Tool ─────────────────────

@router.get("/migrate", dependencies=[Depends(verify_admin)])
async def migrate_data():
    """Temporary route to copy data from Render to Supabase (Fast Bulk Version)."""
    old_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    new_url = os.environ.get("NEW_DATABASE_URL")
    
    if not new_url:
        return "Error: NEW_DATABASE_URL is not set in Render environment."
        
    if "sslmode" not in new_url:
        new_url += "?sslmode=require"

    try:
        old_conn = await asyncpg.connect(old_url)
        new_conn = await asyncpg.connect(new_url)
    except Exception as e:
        return f"Connection failed: {e}"

    await new_conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, telegram_id BIGINT UNIQUE NOT NULL, username VARCHAR(255),
            first_name VARCHAR(255), last_name VARCHAR(255), is_premium BOOLEAN DEFAULT false,
            premium_expiry TIMESTAMP WITH TIME ZONE, is_banned BOOLEAN DEFAULT false,
            search_count INTEGER DEFAULT 0, upload_count INTEGER DEFAULT 0,
            last_reset_date DATE DEFAULT CURRENT_DATE, created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
    """)
    await new_conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY, file_id TEXT NOT NULL, message_id BIGINT, file_name TEXT NOT NULL,
            subject VARCHAR(255), category VARCHAR(100), class_name VARCHAR(100), year INTEGER,
            keywords TEXT[], description TEXT, uploaded_by BIGINT, approved BOOLEAN DEFAULT true,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(), updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
    """)
    await new_conn.execute("""CREATE TABLE IF NOT EXISTS bot_settings (key VARCHAR(50) PRIMARY KEY, value TEXT);""")

    users = await old_conn.fetch("SELECT telegram_id, username, first_name, last_name, is_premium, premium_expiry, is_banned, search_count, upload_count, last_reset_date FROM users")
    if users:
        await new_conn.executemany(
            "INSERT INTO users (telegram_id, username, first_name, last_name, is_premium, premium_expiry, is_banned, search_count, upload_count, last_reset_date) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT DO NOTHING",
            [(u['telegram_id'], u['username'], u['first_name'], u['last_name'], u['is_premium'], u['premium_expiry'], u['is_banned'], u['search_count'], u['upload_count'], u['last_reset_date']) for u in users]
        )

    docs = await old_conn.fetch("SELECT file_id, message_id, file_name, subject, category, class_name, year, keywords, description, uploaded_by, approved FROM documents")
    if docs:
        await new_conn.executemany(
            "INSERT INTO documents (file_id, message_id, file_name, subject, category, class_name, year, keywords, description, uploaded_by, approved) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) ON CONFLICT DO NOTHING",
            [(d['file_id'], d['message_id'], d['file_name'], d['subject'], d['category'], d['class_name'], d['year'], d['keywords'], d['description'], d['uploaded_by'], d['approved']) for d in docs]
        )

    settings_row = await old_conn.fetch("SELECT key, value FROM bot_settings")
    if settings_row:
        await new_conn.executemany(
            "INSERT INTO bot_settings (key, value) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            [(s['key'], s['value']) for s in settings_row]
        )

    await old_conn.close()
    await new_conn.close()
    
    return f"✅ Success! Copied {len(users)} users, {len(docs)} documents, and {len(settings_row)} settings to the new database."
