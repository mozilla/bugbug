# Frontend papercut triage

These rules apply to **Firefox desktop frontend defects** — UI/UX papercuts in
the browser chrome and built-in pages. Typical components: `Firefox :: Tabbed
Browser`, `Firefox :: Tabbed Browser: Split View`, `Firefox :: New Tab Page`,
`Firefox :: Address Bar`, `Firefox :: Menus`, `Firefox :: Toolbars and
Customization`, `Firefox :: Sidebar`, `Firefox :: Theme`. These are usually
documented with a **video or screenshot** and steps to reproduce, and are
**not** crashes, hangs, or sanitizer reports.

If the bug is a crash/assertion/sanitizer report, or is not a frontend bug, this
ruleset does not apply — note that and stop.

## What to produce

1. **Localize the cause in the source.** Frontend code lives mostly under
   `browser/`, `toolkit/`, and `devtools/`. Find the JS/JSM module, the CSS, the
   XUL/HTML, and any relevant pref (often `modules/libpref/init/all.js`) that
   governs the behaviour. Use the `investigator` subagent for deep searches.
2. **Confirm the area is still live.** Check the referenced code/strings still
   exist and aren't already changed by a recent commit. If the bug looks already
   fixed (e.g. cannot reproduce on a newer version per comments, or the code path
   was changed), say so in the comment and propose marking accordingly instead of
   inventing a fix.
3. **Write a fix plan**: root cause, the specific files/functions/selectors to
   change, and the approach. Prefer a comprehensive fix at the right level over a
   spot fix.

## Comment

Record a single brief comment (a few sentences) with: the suspected root cause,
the target file(s), and the proposed approach. Cite concrete paths. Do not
restate the whole bug. Do not claim the fix is verified — you did not run it.

## Confidence and field changes

- **High** (you found the specific code and the cause is clear): record the
  plan comment. If a rule or convention clearly applies, you may also record a
  `bugzilla_update_bug` for an obviously-correct field (e.g. adding a relevant
  keyword). Do not change `status`/`resolution`.
- **Medium** (plausible area, cause not pinned down): record the comment with
  your best hypothesis and the open questions that would confirm it.
- **Low** (could not localize): record a comment stating what you checked and
  the specific information needed to proceed. Set `confidence` to low and
  `root_cause` to null in the structured output.

## Already-fixed / duplicate

If the bug appears fixed by another change, name the likely bug/commit in the
comment so a human can mark it properly. Do not propose a redundant fix.
