from app.handlers.user import router as user_router
from app.handlers.callback import router as callback_router
from app.handlers.admin import router as admin_router
from app.handlers.premium import router as premium_router

__all__ = ["user_router", "callback_router", "admin_router", "premium_router"]
