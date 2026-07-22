# Product / component verification

Bugs are frequently filed against the wrong `Product :: Component`. Part of triage is
confirming the bug is filed where it belongs and, when it isn't, proposing the correct
`Product :: Component` so it reaches the right team.

Do this **after** you have localized the affected code (you need concrete file paths to
verify ownership), and record the result in the `component_assessment` structured-output
object.

## How to verify

1. **Find the ownership map.** The Firefox source tree ships a module-ownership file,
   `mots.yaml` (the maintainer/module metadata; commonly at the repo root or under
   `source/`). Glob for it (`**/mots.yaml`) and Read it. Each module lists path globs it
   owns (e.g. an `includes:` list) and its Bugzilla product/component (e.g. under a
   `meta`/`components` mapping — field names may vary slightly, read the file to see the
   shape).
2. **Match your localized paths to a module.** Take the file path(s) you identified as
   the root cause and find the module whose path globs own them. That module's Bugzilla
   product/component is the _expected_ component for a bug in that code. Use Searchfox to
   confirm ownership when a path spans directories or the mapping is ambiguous.
3. **Compare with the bug.** Compare the expected `Product :: Component` against the
   bug's current values.

## What to record

- If the current component matches, set `component_assessment.correct` to `true`, leave
  the suggestions null, and give a one-line rationale.
- If it clearly belongs elsewhere, set `correct` to `false`, put the corrected values in
  `suggested_product` / `suggested_component`, and in the rationale cite **both** the
  `mots.yaml` module that owns the path **and** the path evidence.

## Confidence and field changes

- **High** — the localized path is unambiguously owned by one module and the mismatch is
  clear. Only then may you record a `bugzilla_update_bug` proposing the corrected
  `product` / `component` (see the system prompt's recording rules). Cite the mots.yaml
  module and path in the `reasoning`.
- **Medium / low** — the code area is uncertain or the path is owned by multiple modules.
  Report the assessment (and your best suggestion) in the comment and structured output,
  but do **not** record a field change.

## Interaction with scoping

A wrong-component finding can mean the bug is out of scope for a _frontend fix_ — e.g.
the code you localized is Core (layout, DOM, graphics), not a Firefox frontend
component. In that case, still record the component assessment and the proposed
correction, but do not invent a frontend fix plan: set `actionable` per the scoping
rules and let the comment focus on the re-triage.
