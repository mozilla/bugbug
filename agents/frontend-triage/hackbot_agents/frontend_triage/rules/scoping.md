# Scoping — what to triage vs. skip

This ruleset applies to **every** bug this agent receives. Apply it **first**, before
the `frontend-triage` ruleset. Its job is to avoid spending a deep investigation (and a
fix plan) on bugs that aren't actionable frontend defects.

## Skip-or-flag (do not produce a fix plan)

For these, record a brief comment stating why it's out of scope and set the structured
output's `actionable` to `false`, `confidence` to `low`, and `root_cause` to null. Do
**not** invent a fix plan.

- **Not a defect.** `type` is `enhancement` or `task` — this agent triages defects
  (bugs in existing behavior), not feature work or chores.
- **Tracking/meta bugs.** Keyword `meta`, or a summary like `[meta]`/`[tracking]` — these
  coordinate other bugs and have no single fix.
- **Intermittent / test-infrastructure failures.** Keywords `intermittent-failure`,
  `intermittent-testcase`, `test-verify-fail`, or summaries that are CI/test failures
  (e.g. `Intermittent <testfile> | ...`). These need test-infra expertise, not a UX fix.
- **Already resolved or a developer already attached a fix.** State that and stop.

## Flag, then proceed with care

- **Accessibility** (keyword `access`, or component-tagged a11y). These are in-scope but
  specialized — proceed, but note in the comment that a11y review is advisable and keep
  confidence calibrated to how well you can localize without a11y-specific knowledge.

## Proceed normally

Everything else — a `defect` in a user-facing Firefox component without the skip signals
above — is in scope. That covers the desktop frontend (Bookmarks & History, New Tab Page,
Session Restore, Sidebar, Tabbed Browser: Split View / Tab Groups, Toolbars and
Customization, …), Firefox for Android (History, …), and install and update
(`Firefox :: Installer`, `Toolkit :: Application Update`). Continue to the
`frontend-triage` ruleset.

An install or update failure reported as an error code and a log, with no steps to
reproduce and no screenshot, is an in-scope defect — it is the usual shape of a bug in
that area, not a sign the report is too thin to work with. See the install-and-update
paragraph in the `frontend-triage` ruleset.

When in doubt about scope, prefer a short clarifying comment over a speculative fix plan.
