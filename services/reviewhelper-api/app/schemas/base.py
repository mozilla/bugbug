from pydantic import BaseModel, model_validator

from app.enums import ActingCapacity, ReviewerStatus


class UserActionBase(BaseModel):
    """Base model for schemas that include user + acting_capacity fields."""

    user_id: int
    user_name: str
    acting_capacity: ActingCapacity | None = None
    reviewer_status: ReviewerStatus | None = None

    @model_validator(mode="before")
    @classmethod
    def split_compound_acting_capacity(cls, data):
        if isinstance(data, dict):
            raw = data.get("acting_capacity")
            if isinstance(raw, str) and ":" in raw:
                base, sub = raw.split(":", 1)
                data["acting_capacity"] = base
                data["reviewer_status"] = sub
        return data

    @model_validator(mode="after")
    def validate_reviewer_status(self):
        if (
            self.reviewer_status is not None
            and self.acting_capacity != ActingCapacity.REVIEWER
        ):
            raise ValueError(
                "reviewer_status can only be set when acting_capacity is 'reviewer'"
            )
        return self
