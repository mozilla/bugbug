# Severity assessment

Assess an appropriate Mozilla severity for the bug and record it in the
`severity_assessment` structured-output object. Base the judgment on **user impact and
reach** as evidenced by the bug report and the code you investigated — how badly the
user is affected, how many users hit it, and whether a workaround exists.

## Severity definitions

- **S1 — catastrophic.** Crash, hang, data loss, security issue, or a bug that blocks
  major functionality with **no workaround**. Affects a large number of users.
- **S2 — serious.** Major functionality is broken or a severe UX problem, and the
  workaround (if any) is painful or non-obvious. Affects many users.
- **S3 — normal.** Blocks non-critical functionality, or a reasonable workaround exists.
  **This is the default for most frontend papercuts.**
- **S4 — minor / trivial.** Cosmetic issues, small polish, or edge cases with negligible
  impact.

## Guidance

- Frontend UI/UX papercuts are usually **S3** (or **S4** when purely cosmetic). Reserve
  **S1 / S2** for genuine breakage: crashes, data/state loss, or a broken core workflow
  with no easy workaround.
- Weigh: is it functional vs cosmetic? Is there a workaround? How frequently and how
  broadly is it hit (mainline path vs rare configuration)?
- Do **not downgrade** an existing higher severity unless you have strong evidence the
  impact is lower than currently recorded.

## Confidence and field changes

- **High** — impact is clear-cut (clearly cosmetic, or clearly a crash/data-loss). Only
  then may you record a `bugzilla_update_bug` proposing the `severity` (see the system
  prompt's recording rules), with a `reasoning` citing the impact evidence. Prefer not to
  propose a change when the bug already carries a reasonable severity.
- **Medium / low** — suggest a severity in the comment and structured output, but do
  **not** record a field change.
