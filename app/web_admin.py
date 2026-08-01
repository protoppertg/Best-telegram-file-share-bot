"""FastAPI Web Admin Panel Router."""
from __future__ import annotations
from typing import Optional
from pathlib import Path
import asyncio
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

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
    return templates.TemplateResponse(request, "dashboard.html", {"stats": stats, "active": "dashboard"})

@router.get("/settings", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_settings(request: Request):
    async with get_session() as session:
        search_enabled = await get_setting(session, "search_enabled", "true")
        ad_enabled = await get_setting(session, "auto_delete_enabled", "false")
        ad_seconds = await get_setting(session, "auto_delete_seconds", "3600")
        protect_fwd = await get_setting(session, "protect_forwarding", "false")
        post_file_msg = await get_setting(session, "post_file_message", "")
        channels = await get_force_sub_channels(session)
        
    return templates.TemplateResponse(request, "settings.html", {
        "search_enabled": search_enabled == "true", 
        "ad_enabled": ad_enabled == "true",
        "ad_seconds": ad_seconds,
        "protect_forwarding": protect_fwd == "true",
        "post_file_message": post_file_msg,
        "channels": channels,
        "active": "settings"
    })

@router.post("/settings", dependencies=[Depends(verify_admin)])
async def admin_settings_post(
    request: Request, 
    search_enabled: str = Form("off"), 
    auto_delete_enabled: str = Form("off"),
    auto_delete_seconds: str = Form("3600"),
    protect_forwarding: str = Form("off"),
    post_file_message: str = Form("")
):
    async with get_session() as session:
        s_val = "true" if search_enabled == "on" else "false"
        s_setting = await session.execute(select(BotSetting).where(BotSetting.key == "search_enabled"))
        s_setting = s_setting.scalar_one_or_none()
        if not s_setting: session.add(BotSetting(key="search_enabled", value=s_val))
        else: s_setting.value = s_val
            
        ad_val = "true" if auto_delete_enabled == "on" else "false"
        ad_setting = await session.execute(select(BotSetting).where(BotSetting.key == "auto_delete_enabled"))
        ad_setting = ad_setting.scalar_one_or_none()
        if not ad_setting: session.add(BotSetting(key="auto_delete_enabled", value=ad_val))
        else: ad_setting.value = ad_val
            
        seconds_val = auto_delete_seconds if auto_delete_seconds.isdigit() else "3600"
        secs_setting = await session.execute(select(BotSetting).where(BotSetting.key == "auto_delete_seconds"))
        secs_setting = secs_setting.scalar_one_or_none()
        if not secs_setting: session.add(BotSetting(key="auto_delete_seconds", value=seconds_val))
        else: secs_setting.value = seconds_val
            
        pf_val = "true" if protect_forwarding == "on" else "false"
        pf_setting = await session.execute(select(BotSetting).where(BotSetting.key == "protect_forwarding"))
        pf_setting = pf_setting.scalar_one_or_none()
        if not pf_setting: session.add(BotSetting(key="protect_forwarding", value=pf_val))
        else: pf_setting.value = pf_val
            
        pfm_setting = await session.execute(select(BotSetting).where(BotSetting.key == "post_file_message"))
        pfm_setting = pfm_setting.scalar_one_or_none()
        if not pfm_setting: session.add(BotSetting(key="post_file_message", value=post_file_message))
        else: pfm_setting.value = post_file_message
            
    return RedirectResponse(url="/admin/settings", status_code=303)

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

@router.get("/documents", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_documents(request: Request, page: int = 1, q: Optional[str] = None):
    per_page = 15
    async with get_session() as session:
        if q: stmt = select(Document).where(Document.file_name.ilike(f"%{q}%"))
        else: stmt = select(Document)
        result = await session.execute(stmt.order_by(Document.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
        docs = result.scalars().all()
        total = (await session.execute(select(Document).where(Document.file_name.ilike(f"%{q}%")) if q else select(Document))).scalars().all()
        total = len(total)
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

@router.get("/users", dependencies=[Depends(verify_admin)], response_class=templates.TemplateResponse)
async def admin_users(request: Request, page: int = 1, q: Optional[str] = None):
    per_page = 15
    async with get_session() as session:
        if q: stmt = select(User).where((User.username.ilike(f"%{q}%")) | (User.telegram_id == q))
        else: stmt = select(User)
        result = await session.execute(stmt.order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page))
        users = result.scalars().all()
        all_users = (await session.execute(stmt)).scalars().all()
        total = len(all_users)
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
