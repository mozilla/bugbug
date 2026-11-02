"""Base result model for hackbot agents.

An agent's ``main()`` may return a subclass of :class:`HackbotAgentResult`; the
runtime serializes it into ``summary.json``'s ``findings``. Framework-neutral —
plain pydantic, no claude-agent-sdk dependency.
"""

from pydantic import BaseModel


class HackbotAgentResult(BaseModel):
    num_turns: int
    total_cost_usd: float | None = None
