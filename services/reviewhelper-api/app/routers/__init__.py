from app.routers.feedback import router as feedback_router
from app.routers.internal import router as internal_router
from app.routers.request import router as request_router

__all__ = ["request_router", "feedback_router", "internal_router"]
