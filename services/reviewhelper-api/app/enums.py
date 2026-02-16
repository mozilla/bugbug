from enum import Enum


class Platform(str, Enum):
    PHABRICATOR = "phabricator"
    GITHUB = "github"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PUBLISHED = "published"
    FAILED = "failed"

    @property
    def is_final(self) -> bool:
        return self in {ReviewStatus.PUBLISHED, ReviewStatus.FAILED}


class FeedbackType(str, Enum):
    UP = "up"
    DOWN = "down"


class ActingCapacity(str, Enum):
    AUTHOR = "author"
    REVIEWER = "reviewer"
    PARTICIPANT = "participant"
