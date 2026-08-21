# Duplicate detection

These rules apply to **every** bug: the `duplicate_hunter` subagent runs before
localization and asks one question — is this already filed?

The verdict is a **suggestion only**. Naming a duplicate never stops the triage: the fix
plan is written either way, `actionable` stays `true`, and confidence is unaffected. A
human decides whether the bugs are really the same and marks them. The agent cannot mark
one — it has no tool that changes a Bugzilla field.

## What counts as the same defect

The test is whether **one fix would resolve both**. Same component, same feature and a
similar summary are not enough on their own — most bugs in a component sound alike.

- **Same symptom, different cause** is not a duplicate. Two bugs about the New Tab weather
  widget disappearing are different bugs if one is a region gating fault and the other a
  trainhop config race.
- **Same cause, different symptom** usually _is_ one. A single wrong CSS selector reported
  once as a dialog shift and once as a misaligned doorhanger is one bug.
- **A regression and the change that caused it** are not duplicates. That is a
  `regressed_by` relationship, and it belongs in the fix plan instead.
- **A meta or tracking bug** is never a duplicate of a defect. It coordinates work.

## Per-area signals

What makes two reports the same thing differs by area, because the discriminating detail
does:

- **Desktop frontend and Android.** The affected element and the user action. A papercut
  in the same widget triggered a different way is usually a different bug. Screenshots and
  videos in the description are often the fastest way to tell.
- **Installer and updater.** The error or status code, and the phase it failed in. Two
  failed updates with the same status code in the same phase are very likely the same bug
  even when the prose differs; the same code in different phases is not.
- **Site permissions.** Which layer — the prompt, the state, or the store. "The permission
  came back after restart" and "the doorhanger shows the wrong state" are different bugs
  even though both are about the same permission.
- **IP Protection.** Whether the proxy state is _displayed_ wrong or _actually_ wrong.
  Those are different defects with different severities and should not be merged.

## Reporting

- **Candidate found** — the triage comment opens with `**Possible duplicate:** <link>` and
  nothing else about it. No hedging, no explanation; the reader opens the bug and judges.
- **Nothing found** — say nothing. The comment opens with the analysis as usual. Absence is
  not reported.
- **Uncertain** — prefer reporting nothing. A wrong duplicate sends someone to the wrong
  bug and costs more trust than a missed one costs time.

Record the same verdict in the `duplicate_assessment` structured output either way, so a
run that found nothing is distinguishable from one that never looked.
