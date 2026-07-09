from dataclasses import dataclass


@dataclass
class RunContext:
    """What the notifier needs about a triggered build-repair run."""

    run_id: str
    repo: str
    git_commit: str
    hg_revision: str
    task_id: str
    developer_email: str | None
