# User-facing Firefox defect triage

These rules apply to **defects in user-facing Firefox** — the desktop frontend,
Firefox for Android, and the Windows installer and application updater. **Components in
scope** in the system prompt lists the components bugs normally arrive from, grouped by
the area whose code layout **Source repository** describes. Any user-facing Firefox
defect is in scope, though, whether or not its component is on that list; see
`scoping.md`.

Typical components:

- Desktop frontend, all under `Firefox`: `Tabbed Browser`,
  `Tabbed Browser: Split View`, `New Tab Page`, `Address Bar`, `Menus`,
  `Toolbars and Customization`, `Sidebar`, `Theme`.
- Android: `Firefox for Android :: History`.
- Install and update: `Firefox :: Installer`, `Toolkit :: Application Update`.
- Messaging System: `Firefox :: Messaging System`.

Desktop and Android bugs here are usually UI/UX papercuts, documented with a
**video or screenshot** and steps to reproduce.

**Install and update bugs look different, and that is not a reason to skip them.**
An installer or updater bug is normally a _failure_ rather than a papercut: an update
that did not apply, an install that rolled back, a version that stayed where it was,
a wrong or unhelpful error dialog — reported with an error or status code, an
`update.log` or installer log excerpt, and an OS and channel rather than a
screenshot. Triage those normally: read the log the reporter pasted, map the status
code to its definition in `toolkit/mozapps/update/common/` or to the NSIS `.nsh` that
emits it, and localize from there. Missing steps to reproduce is the norm in this area
and is not by itself grounds to call a bug unactionable — say what you would need
instead.

If the bug is a crash, assertion failure, or sanitizer report, this ruleset does not
apply — note that and stop. "The installer failed" and "the update did not apply" are
**not** crash reports. Also stop if the bug is not in user-facing Firefox at all — a
Core, DevTools-internals, or build-system bug — and say which area it looks like.

## What to produce

1. **Localize the cause in the source.** Where to look and what language to expect
   depend on the component — see **Source repository** in the system prompt for the
   per-area layout. In short: desktop frontend under `browser/`, `toolkit/`, and
   `devtools/` (JS/JSM, CSS, XUL/HTML); Android under `mobile/android/` (Kotlin,
   Fragment/Store/Middleware/View); the updater under `toolkit/mozapps/update/`
   (`.sys.mjs`, IDL, C++); the installer under `browser/installer/windows/nsis/`
   (NSIS `.nsi`/`.nsh`); the Messaging System under `browser/components/asrouter/`,
   `browser/components/aboutwelcome/`, `toolkit/components/messaging-system/`
   (JS/JSM, CSS, XUL/HTML, JSON, JSON Schema). Find the module, the markup or layout,
   and any relevant pref (often `modules/libpref/init/all.js`, or `app.update.*`
   for the updater) that governs the behaviour. Use the `investigator` subagent for
   deep searches.
2. **Confirm the area is still live.** Check the referenced code/strings still
   exist and aren't already changed by a recent commit. If the bug looks already
   fixed (e.g. cannot reproduce on a newer version per comments, or the code path
   was changed), say so in the comment and propose marking accordingly instead of
   inventing a fix.
3. **Write a fix plan**: root cause, the specific files/functions/selectors to
   change, and the approach. Prefer a comprehensive fix at the right level over a
   spot fix.
4. **Assess severity.** Apply the `severity-assessment` rules to judge the bug's
   severity from its user impact. It goes in the severity block at the end of your
   comment and in the structured output — never on the bug's `severity` field,
   which you cannot set.

## Comment

Record a single brief comment (a few sentences) with: the suspected root cause,
the target file(s), and the proposed approach. Cite concrete paths, each as an
inline Markdown link to its Searchfox permalink (with a line anchor where you
know the line) per the **Linking source files** section of the system prompt. Do
not restate the whole bug. Do not claim the fix is verified — you did not run it.

Close with the severity block — a horizontal rule, `Suggested severity: <level>`,
then the reasoning — unless your severity confidence is low or you could not assess
it, in which case leave the block out entirely. Exact shape is in **Severity in the
comment** in the system prompt.

## Confidence

Your `confidence` decides whether your comment reaches the bug unreviewed — see
**Recording actions** in the system prompt before you pick a level. This is the
run's confidence in the _localization_; the severity block has its own, separate
confidence (see `severity-assessment`).

- **High** (you found the specific code and the cause is clear): record the
  plan comment.
- **Medium** (plausible area, cause not pinned down): record the comment with
  your best hypothesis and the open questions that would confirm it.
- **Low** (could not localize): record a comment stating what you checked and
  the specific information needed to proceed. Set `confidence` to low and
  `root_cause` to null in the structured output.

## Already-fixed / duplicate

If the bug appears fixed by another change, name the likely bug/commit in the
comment so a human can mark it properly. Do not propose a redundant fix.
