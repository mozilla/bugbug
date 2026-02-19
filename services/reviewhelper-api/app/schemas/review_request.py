from pydantic import BaseModel

from app.enums import ReviewStatus
from app.schemas.base import UserActionBase


class ReviewRequestCreate(UserActionBase):
    """Request body for creating a new review request (Phabricator)."""

    revision_id: int
    diff_id: int


class ReviewRequestResponse(BaseModel):
    """Response for review request status."""

    status: ReviewStatus
    message: str
