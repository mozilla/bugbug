from pydantic import BaseModel

from app.enums import FeedbackType
from app.schemas.base import UserActionBase


class FeedbackCreate(UserActionBase):
    """Request body for submitting feedback."""

    comment_id: int
    feedback_type: FeedbackType


class FeedbackResponse(BaseModel):
    """Response schema for feedback."""

    message: str
