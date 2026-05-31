from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class AgentResult:
    """Outcome reported by an agent's main() to the runtime.

    The runtime serialises this into the summary.json artifact the orchestrator
    reads. `status` drives the run's terminal state in hackbot-api; `findings`
    is opaque to the platform and surfaced verbatim. Recorded actions are not
    carried here — the runtime reads them from `Context.actions`.
    """

    status: Literal["ok", "error"] = "ok"
    error: str | None = None
    findings: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0
