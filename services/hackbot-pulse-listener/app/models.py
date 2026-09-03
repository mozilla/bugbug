from dataclasses import dataclass, field


@dataclass
class RunContext:
    """What the notifier needs about a triggered agent run."""

    run_id: str
    repo: str
    git_commit: str
    hg_revision: str
    task_id: str
    developer_email: str | None
    # Which agent produced the run, and (for test-repair) every failing test group
    # the run covers. These drive the notifier's recipient/body routing.
    agent: str = "build-repair"
    test_groups: list[str] = field(default_factory=list)
