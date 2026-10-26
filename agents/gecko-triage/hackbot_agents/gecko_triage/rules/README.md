# Triage rules

The triage knowledge of the gecko-triage agent. `render_rules` in `agent.py` puts every
file here but this one into the system prompt, `workflow.md` first, then the team's
files. Each file is in a `rule_file` block with its set and its name.

The general rules here apply to every team: `workflow.md` (the decision flow),
`diagnostics.md`, `comment.md` and `severity-priority.md`. Each team adds its own in
`teams/<team>/rules/`; `docs/configuration.md` lists them. A run with no team uses the
general rules alone.

The preflight skips run in code before the model starts, and the limits on what the
agent may write run as record-time hooks. A rule here says what to decide; the code
decides what may be written.

Profiles, logs and specs are read by subagents that follow the fx-bug-toolkit plugin's
`analyze-profile`, `check-log` and `spec-check` skills. `bug_investigator` runs the
`bug-start` skill in triage mode before an "Analyzed" comment, and before an ask on a
bug that has diagnostics. `close_verifier` checks every close before it is recorded.

Rules are prose. A new file extends the set; say at its top when it applies.
