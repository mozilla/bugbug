"""The bug an autowebcompat agent works on: a Bugzilla id or inline report text."""

from dataclasses import dataclass
from typing import Literal


@dataclass
class BugIdInput:
    bug_id: int
    type: Literal["bug_id"] = "bug_id"

    def subject(self) -> str:
        return f"bug {self.bug_id}"


@dataclass
class BugDataInput:
    bug_data: str
    type: Literal["bug_data"] = "bug_data"

    def subject(self) -> str:
        return self.bug_data


AutoWebcompatInput = BugIdInput | BugDataInput
