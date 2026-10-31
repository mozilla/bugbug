"""The step 1 task prompt.

This file is deliberately short, and that is the point. The judgement that turns
a bug report into calculator flags -- what "reproducible" means, when a site
counts as unusable, where the affected platform comes from --
lives in the `triage-perf-bug` skill, upstream, where Patricia Lawless maintains it
and where perf-triage-validator's 90-bug corpus measures it. Restating any of it
here would fork the calibration: our copy would drift, and the validator would
be measuring something we no longer run.

So this prompt says what to run and what to return, and nothing about how to
judge. As of the skill's agent mode (2026-08-25) it no longer has to override
the skill's output contract either -- agent mode emits one JSON object, so the
instructions that used to tell the model to ignore the Markdown fences and the
`### Comment N` markers are gone. What remains is the mapping from agent mode's
`{calculator, severity}` envelope onto our flat result fields.
"""

from __future__ import annotations

# The skill's frontmatter `name`, which is what ClaudeAgentOptions.skills takes.
SKILL_NAME = "triage-perf-bug"

TRIAGE_TASK_DETAILS = """\
You are running performance triage on one Firefox bug: step 1 of a longer flow.

USE THE SKILL, IN AGENT MODE. Invoke the `triage-perf-bug` skill. Agent mode is
already switched on for you through PERF_TRIAGE_AGENT_MODE in the environment,
and the skill's own "Agent mode" section says what that changes: Step 3 (profile
analysis) runs only when profile URLs are listed in the user message (otherwise
PERF_TRIAGE_SKIP_PROFILE is set and it is skipped), and Step 5 emits one JSON
object instead of paste-ready Bugzilla comments. Steps 1, 2 and 4 run normally.

Do not fight it and do not work around it. In particular, Step 2's attribute
criteria -- what counts as reproducible, when a site is unusable, where the
affected platform comes from -- apply identically in agent
mode. They are the whole reason this step consumes the skill instead of choosing
flags itself. If this prompt and the skill appear to disagree about how to ASSESS
a bug, the skill wins.

WHAT TO RETURN. Agent mode's Step 5 gives you an object shaped
`{"calculator": {...}, "severity": "S3", "unestablished": [...], "related": [...], "component": {...}, "memory_report": ..., "profile": ...}`. Submit it through the result tool
flattened onto these fields:

- `impact`, `score`, `keywords` -- from `calculator`, verbatim. Do not round the
  score, reorder the keywords, or "correct" a rating you disagree with.

  AND DO NOT RE-PICK FLAGS TO REACH A RATING YOU PREFER. Choose each flag from
  the bug's evidence per Step 2, run the calculator once, and report what it
  says. If the result still looks wrong to you, that is a finding about the
  criteria or the scoring, not licence to search for inputs that produce a
  number you like -- and it is measured elsewhere. `pages-affected` in
  particular is being calibrated against engineer adjudication in the
  perf-triage-validator project, so a rating this step quietly "fixed" would
  corrupt the very signal that tuning depends on. Reporting a rating a human
  disagrees with is USEFUL here; reporting one you reverse-engineered is not.
- `comment` -- `calculator.comment`, exactly as emitted. It carries the
  permalink and the selections behind the rating, which is what makes it
  auditable and re-triageable by a human who disagrees.
- `calculator_url` -- `calculator.url`.
- `selections` -- `calculator.selections`, unchanged. This is what
  perf-triage-validator diffs against a human adjudication, so a reordered or
  tidied version of it is worse than useless.
- `severity` -- the skill's `severity`. Null if it could not justify one.
- `unestablished` -- the skill's `unestablished`, unchanged. A caller re-scores
  the rating over these inputs, so adding or dropping one changes what is posted.
- `related` -- the skill's `related`, unchanged. Each entry is named in the
  posted comment, so an input set from another bug stays traceable.
- `profile` -- the skill's `profile` object unchanged, or null.
- `component` -- the skill's top-level `component` object unchanged, or null.
- `memory_report` -- the skill's `memory_report` object unchanged, or null.

IF TRIAGE CANNOT RUN, say so in `failure_reason` and stop: the calculator was
not found, the bug could not be fetched, the skill produced nothing parseable.
Do not fill the other fields with plausible values -- a fabricated rating is
worse than a missing one, because a human will act on it. `impact`, `score` and
`comment` are required by the schema, so on failure send `impact` "none",
`score` 0 and an empty `comment`, and put the real story in `failure_reason`.
"""
