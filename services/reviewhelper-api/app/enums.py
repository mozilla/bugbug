from enum import Enum


class Platform(str, Enum):
    PHABRICATOR = "Phabricator"
    GITHUB = "GitHub"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_PENDING = "retry_pending"
    PUBLISHED = "published"
    FAILED = "failed"
    # The revision was intentionally not reviewed (gated out by risk/complexity
    # thresholds, reviewer-group rules, etc.). Terminal, like PUBLISHED/FAILED.
    SKIPPED = "skipped"

    @property
    def is_final(self) -> bool:
        return self in (
            ReviewStatus.PUBLISHED,
            ReviewStatus.FAILED,
            ReviewStatus.SKIPPED,
        )


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
