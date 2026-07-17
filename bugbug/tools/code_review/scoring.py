# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Risk + complexity scoring via a cheap Claude model.

One call per fresh diff, returning two independent 0-10 integer scores plus the
factors behind them. The result gates whether the (expensive) review agent runs
at all, so this deliberately uses a small model. Token usage is captured so the
dashboard can show cost.

Ported from qreviews (qreviews/scoring.py); the raw Anthropic SDK call is
replaced with LangChain structured output to match the rest of bugbug.
"""

from dataclasses import dataclass, field
from logging import getLogger

from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from bugbug.tools.core.exceptions import ModelResultError
from bugbug.tools.core.llms import DEFAULT_SCORING_MODEL, usage_from_messages

logger = getLogger(__name__)


SCORING_SYSTEM_PROMPT = """\
You are a senior Mozilla Firefox reviewer. Rate an incoming Phabricator
revision on two integer 0-10 axes. You MUST use the full 0-10 range —
trivial changes deserve scores of 0 or 1, not 2 or 3.

  - RISK: blast radius if this change is wrong. Considers sensitive
    components (security, IPC, prefs, permissions, crypto, sandbox,
    network, auth), irreversibility (data migrations, schema changes),
    breadth (number of touched modules), and dangerous patterns (eval,
    raw HTML injection, regex on untrusted input, concurrency / async
    races, file/network I/O, telemetry definitions, mots.yaml /
    build / CI files).

  - COMPLEXITY: how hard the change is for a human reviewer to
    understand and verify. Considers LOC added/removed, number of
    files, control-flow density, new abstractions, refactors / renames,
    presence vs absence of tests, and clarity of the diff.

Both axes are independent. A simple 5-line patch to a security-critical
file is HIGH risk but LOW complexity.

The user message includes a `<test_signals>` block computed
deterministically from the diff and from a searchfox lookup over
mozilla-central. Treat these as hints, not absolutes:

  - `in_diff_test_signal=absent` means the patch adds no test
    changes. On non-trivial non-test code, this is mildly
    risk-increasing; on docs/CSS/string changes it does not matter.
  - `in_diff_test_signal=tests_only` means the patch is test-only.
    This is risk-decreasing — broken tests fail loudly without
    affecting production.
  - `coverage_signal=uncovered` means searchfox found no existing
    tests in mozilla-central that reference the changed non-test
    files. Combined with `in_diff_test_signal=absent` on a
    sensitive area, this materially raises risk.
  - `coverage_signal=covered` or `partial` lowers risk slightly —
    the touched code at least has some automated check around it.
  - `coverage_signal=skipped_*` means the lookup wasn't run
    (large diff or searchfox unavailable). Do not let it affect
    your score in either direction.

Continue to use the full 0-10 scale; do not let the test signals
overwhelm other risk factors (security, IPC, etc.) — they are one
input among many.

Score anchors (use these as a calibration reference, do not be afraid
to score 0 or 1):

  RISK
    0  = pure docs / comments / strings, no executable code change
    1  = CSS-only, dead-code removal, isolated UI tweak in a leaf component,
         a localization string addition
    2  = small JS/HTML change in a non-sensitive UI component, no network
         or storage touched
    3-4 = moderate change to UI logic, or any touch of a moderately
         sensitive area (telemetry, prefs reads)
    5-6 = multi-file change with cross-cutting effects, or touches
         moderately sensitive subsystems
    7-8 = security-relevant code, IPC, sandbox, auth, crypto, network
         protocols, build / CI / signing
    9-10 = data migrations, irreversible schema changes, security boundary
         changes

  COMPLEXITY
    0  = whitespace, single-line value change, single-line string change
    1  = <10 LOC, single file, no control flow added
    2  = ~10-30 LOC, 1-2 files, simple straight-line additions
    3-4 = 30-100 LOC, new function/method, simple control flow
    5-6 = 100-300 LOC OR 5+ files OR new abstraction
    7-8 = significant refactor, renames, signature changes across many
         callers, or non-trivial concurrency/async logic
    9-10 = large refactors with subtle invariants, new subsystems

Provide 1-5 factors per axis. Each factor MUST be ONE short sentence,
not a paragraph. Cite specific file paths in `path:line` form when useful.\
"""


class Scores(BaseModel):
    """Structured risk/complexity assessment of a revision."""

    risk: int = Field(ge=0, le=10, description="Blast radius if the change is wrong.")
    complexity: int = Field(
        ge=0, le=10, description="How hard the change is to review and verify."
    )
    risk_factors: list[str] = Field(
        default_factory=list,
        description="1-5 short sentences justifying the risk score.",
    )
    complexity_factors: list[str] = Field(
        default_factory=list,
        description="1-5 short sentences justifying the complexity score.",
    )


@dataclass
class ScoringResult:
    scores: Scores
    model: str
    usage: dict[str, int] = field(default_factory=dict)


def _build_user_message(
    *,
    title: str,
    summary: str | None,
    revision_id: int,
    author: str,
    bug_id: str | int | None,
    raw_diff: str,
    test_signals_block: str | None = None,
) -> str:
    header = (
        f"Revision: D{revision_id}\n"
        f"Title: {title}\n"
        f"Author: {author}\n"
        f"Bug: {bug_id or '(none)'}\n"
        f"\nSummary:\n{summary or '(no summary provided)'}\n"
    )
    signals = f"\n{test_signals_block}\n" if test_signals_block else ""
    return (
        f"{header}{signals}\n----- BEGIN DIFF -----\n{raw_diff}\n----- END DIFF -----\n"
    )


class RiskComplexityScorer:
    """Scores a revision's risk and complexity with a single cheap LLM call."""

    def __init__(self, llm, model_name: str) -> None:
        self._model_name = model_name
        self._structured = llm.with_structured_output(Scores, include_raw=True)

    @classmethod
    def create(
        cls,
        model: str = DEFAULT_SCORING_MODEL,
        max_tokens: int = 1024,
    ) -> "RiskComplexityScorer":
        llm = init_chat_model(model, max_tokens=max_tokens, temperature=0)
        return cls(llm, model)

    async def run(
        self,
        *,
        title: str,
        summary: str | None,
        revision_id: int,
        author: str,
        bug_id: str | int | None,
        raw_diff: str,
        test_signals_block: str | None = None,
    ) -> ScoringResult:
        """Call the model and return parsed scores plus token usage."""
        user_msg = _build_user_message(
            title=title,
            summary=summary,
            revision_id=revision_id,
            author=author,
            bug_id=bug_id,
            raw_diff=raw_diff,
            test_signals_block=test_signals_block,
        )
        result = await self._structured.ainvoke(
            [SystemMessage(SCORING_SYSTEM_PROMPT), HumanMessage(user_msg)]
        )

        scores = result.get("parsed")
        if scores is None:
            raise ModelResultError(
                f"Risk/complexity scoring did not return valid scores: "
                f"{result.get('parsing_error')}"
            )

        usage = usage_from_messages([result["raw"]])
        return ScoringResult(scores=scores, model=self._model_name, usage=usage)
