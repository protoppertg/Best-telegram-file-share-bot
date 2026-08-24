"""Input validation and sanitisation utilities."""

from __future__ import annotations

import re
from typing import List, Optional

from aiogram.types import Document as TgDocument

from app.config import settings


def validate_pdf_document(doc: TgDocument) -> tuple[bool, str]:
    """Validates that the file is a PDF. No size restrictions."""
    # Check if it's a PDF based on mime type or file extension
    is_pdf = False
    if doc.mime_type and "pdf" in doc.mime_type.lower():
        is_pdf = True
    elif doc.file_name and doc.file_name.lower().endswith(".pdf"):
        is_pdf = True
        
    if not is_pdf:
        return False, "Only PDF files are accepted."
        
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
