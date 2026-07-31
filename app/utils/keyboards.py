"""Inline keyboard builders for the bot UI."""

from __future__ import annotations

from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings

def search_results_keyboard(results: List, query_key: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for doc in results:
        name = doc.file_name[:55] + ("…" if len(doc.file_name) > 55 else "")
        meta_parts = []
        if doc.subject: meta_parts.append(doc.subject[:20])
        if doc.category: meta_parts.append(doc.category[:15])
        meta = f" ({' · '.join(meta_parts)})" if meta_parts else ""
        kb.button(text=f"📄 {name}{meta}", callback_data=f"getfile:{doc.id}:{query_key}:{page}")
        kb.adjust(1)

    nav_buttons = []
    if page > 1: nav_buttons.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"search:{query_key}:{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages: nav_buttons.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"search:{query_key}:{page + 1}"))
    if len(nav_buttons) > 1: kb.row(*nav_buttons)

    kb.row(InlineKeyboardButton(text="🔍 New Search", callback_data="search_again"))
    return kb.as_markup()

def after_file_keyboard(query_key: str, page: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Back to results", callback_data=f"search:{query_key}:{page}")
    kb.button(text="🔍 New Search", callback_data="search_again")
    kb.adjust(1)
    return kb.as_markup()

CATEGORIES = ["Notes", "PYQ", "Book", "Assignment", "Lab Manual", "Other"]
def category_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for cat in CATEGORIES: kb.button(text=cat, callback_data=f"upload_cat:{cat}")
    kb.adjust(3)
    return kb.as_markup()

SEMESTERS = [str(i) for i in range(1, 9)] + ["Other"]
def semester_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for sem in SEMESTERS: kb.button(text=f"Sem {sem}", callback_data=f"upload_sem:{sem}")
    kb.adjust(4)
    return kb.as_markup()
