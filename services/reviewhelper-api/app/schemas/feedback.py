from pydantic import BaseModel

from app.enums import ActingCapacity, FeedbackType


class FeedbackCreate(BaseModel):
    """Request body for submitting feedback."""

    comment_id: int
    feedback_type: FeedbackType
    user_id: int
    user_name: str
    acting_capacity: ActingCapacity | None


class FeedbackResponse(BaseModel):
    """Response schema for feedback."""

    message: str
