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
- **Install and update failures do not default to S3.** "Papercut usually means S3" is a
  desktop-frontend heuristic and does not carry over: a user whose update does not apply
  is left on an older, unpatched build, and a user whose install fails does not have
  Firefox at all. Neither has a workaround inside the product. Judge these on reach — a
  failure specific to one antivirus product or one locale is narrower than one that hits
  a whole channel or OS version — but start from **S2** and move up or down from there,
  rather than starting from S3.
- **IP Protection failures do not default to S3 either.** The user is paying for the
  feature, and turning it off is not a workaround for it not working — it is the absence
  of the thing they bought. A proxy that will not connect, that drops without saying so,
  or that reports itself active while traffic goes around `IPPChannelFilter` leaves them
  without the protection they believe they have. Start from **S2** and move up or down on
  reach, rather than starting from S3. Separate two cases that read identically in a bug
  report: the state is merely _displayed_ wrong (the panel disagrees with the proxy) or
  it is actually wrong (traffic is unproxied). The first is a UI bug, the second is a
  privacy exposure and belongs above S2. Say which one you concluded and what in the code
  told you.
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
