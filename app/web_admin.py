"""FastAPI Web Admin Panel Router."""

from __future__ import annotations

from typing import Optional
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.config import settings
from app.database import get_session
from app.models import User, Document
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


@router.get("/login")
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


@router.get("/", dependencies=[Depends(verify_admin)])
async def admin_dashboard(request: Request):
    async with get_session() as session:
        stats = await user_service.get_stats(session)
    return templates.TemplateResponse(request, "dashboard.html", {"stats": stats, "active": "dashboard"})


@router.get("/documents", dependencies=[Depends(verify_admin)])
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


@router.post("/documents/{doc_id}/approve", dependencies=[Depends(verify_admin)])
async def admin_approve_doc(doc_id: int):
    async with get_session() as session: await doc_service.approve_document(session, doc_id)
    return Response(status_code=200)


@router.post("/documents/{doc_id}/delete", dependencies=[Depends(verify_admin)])
async def admin_delete_doc(doc_id: int):
    async with get_session() as session: await doc_service.delete_document(session, doc_id)
    return Response(status_code=200)


@router.get("/users", dependencies=[Depends(verify_admin)])
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


@router.post("/users/{telegram_id}/grant_premium", dependencies=[Depends(verify_admin)])
async def admin_grant_premium(telegram_id: int, days: int = Form(30)):
    await user_service.activate_premium(telegram_id, days)
    return Response(status_code=200)


@router.post("/users/{telegram_id}/revoke_premium", dependencies=[Depends(verify_admin)])
async def admin_revoke_premium(telegram_id: int):
    await user_service.revoke_premium(telegram_id)
    return Response(status_code=200)


@router.post("/users/{telegram_id}/reset_search", dependencies=[Depends(verify_admin)])
async def admin_reset_search(telegram_id: int):
    await user_service.reset_search_count(telegram_id)
    return Response(status_code=200)
