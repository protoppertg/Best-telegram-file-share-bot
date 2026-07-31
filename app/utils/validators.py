"""Input validation and sanitisation utilities."""

from __future__ import annotations

import re
from typing import List, Optional

from aiogram.types import Document as TgDocument

from app.config import settings


def validate_pdf_document(doc: TgDocument) -> tuple[bool, str]:
    if not doc.mime_type or "pdf" not in doc.mime_type.lower():
        return False, "Only PDF files are accepted."
    if doc.file_size and doc.file_size > settings.max_file_size_bytes:
        return False, f"File too large. Maximum size is {settings.MAX_FILE_SIZE_MB} MB."
    return True, ""


def sanitise_text(text: str, max_length: int = 500) -> str:
    text = text.strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:max_length]


def parse_keywords(text: str) -> List[str]:
    if not text:
        return []
    parts = re.split(r"[,\n]", text)
    return [sanitise_text(p, 100) for p in parts if p.strip()][:20]


def parse_year(text: str) -> Optional[int]:
    text = text.strip()
    if not text:
        return None
    try:
        year = int(text)
        if 1900 <= year <= 2100:
            return year
    except ValueError:
        pass
    return None


def is_valid_search_query(query: str) -> bool:
    return bool(query and query.strip() and len(query.strip()) >= 2)
