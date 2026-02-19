from pydantic import BaseModel

from app.enums import ActingCapacity, ReviewStatus


class ReviewRequestCreate(BaseModel):
    """Request body for creating a new review request (Phabricator)."""

    revision_id: int
    diff_id: int
    user_id: int
    user_name: str
    acting_capacity: ActingCapacity | None = None


class ReviewRequestResponse(BaseModel):
    """Response for review request status."""

    status: ReviewStatus
    message: str
