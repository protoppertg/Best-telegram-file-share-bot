"""Ghost AI Service for Study Buddy Fallback."""

from __future__ import annotations
import httpx
from app.config import settings
from app.utils.logger import logger

async def get_ghost_ai_response(ai_name: str, subject: str, user_message: str) -> str:
    """Generates a response acting like a student, saving tokens."""
    if not settings.AI_API_KEY:
        return "Hey! I'm a bit busy right now, what did you ask?"
        
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.AI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {settings.AI_API_KEY}"},
                json={
                    "model": settings.AI_MODEL,
                    "max_tokens": 60,
                    "temperature": 0.8,
                    "messages": [
                        {
                            "role": "system", 
                            "content": f"Your name is {ai_name}. You are an Indian student studying {subject}. You are chatting informally on Telegram with another student. Be friendly, concise (1-2 sentences), and do NOT use markdown formatting. Never say you are an AI or a language model. If they say 'hi', say hi back and mention your name."
                        },
                        {
                            "role": "user", 
                            "content": user_message
                        }
                    ]
                }
            )
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "Hmm, I'm not sure about that. Let me check the book.").strip()
    except Exception as e:
        logger.error("ghost_ai_error", error=str(e))
        return "Wait, I'm getting distracted. What did you ask?"
