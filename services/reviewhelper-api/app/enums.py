from enum import Enum


class Platform(str, Enum):
    PHABRICATOR = "Phabricator"
    GITHUB = "GitHub"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PUBLISHED = "published"
    FAILED = "failed"

    @property
    def is_final(self) -> bool:
        return self is ReviewStatus.PUBLISHED or self is ReviewStatus.FAILED


class FeedbackType(str, Enum):
    UP = "up"
    DOWN = "down"


class ActingCapacity(str, Enum):
    AUTHOR = "author"
    REVIEWER = "reviewer"
    PARTICIPANT = "participant"


class ReviewerStatus(str, Enum):
    BLOCKING = "blocking"
    ADDED = "added"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMMENTED = "commented"
    ACCEPTED_OLDER = "accepted-older"
    REJECTED_OLDER = "rejected-older"
    RESIGNED = "resigned"
