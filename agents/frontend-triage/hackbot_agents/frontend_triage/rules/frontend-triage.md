# User-facing Firefox defect triage

These rules apply to **defects in user-facing Firefox**. **Components in scope** in the
system prompt lists the components bugs normally arrive from, and **Source repository**
carries the guidance for the one this bug was filed in. That list is not a limit: any
user-facing Firefox defect is in scope whether or not its component is on it. See
`scoping.md`.

Desktop and Android bugs here are usually UI/UX papercuts, documented with a
**video or screenshot** and steps to reproduce.

Screenshots you can look at: download one with `download_attachment` and `Read` the
file. Screen recordings you cannot, because this agent's image has no ffmpeg to pull
frames out of them, so `download_attachment` refuses video, audio, and archive
attachments rather than spend a large download on nothing. When the only evidence is
a recording, triage from the description, the steps to reproduce, and the code, and
say plainly in your comment that you did not view the recording. Do not imply you
did. If the symptom is one only the recording shows well, a flash, a flicker, an
animation or anything else about timing, your confidence is at most medium: the
code can explain a symptom you have not seen in more than one way, and the
recording is what tells them apart.

**Install and update bugs look different, and that is not a reason to skip them.**
An installer or updater bug is normally a _failure_ rather than a papercut: an update
that did not apply, an install that rolled back, a version that stayed where it was,
a wrong or unhelpful error dialog, reported with an error or status code, an
`update.log` or installer log excerpt, and an OS and channel rather than a
screenshot. Triage those normally: read the log the reporter pasted, map the status
code to its definition in `toolkit/mozapps/update/common/` or to the NSIS `.nsh` that
emits it, and localize from there. Missing steps to reproduce is the norm in this area
and is not by itself grounds to call a bug unactionable. Say what you would need
instead.

If the bug is a crash, assertion failure, or sanitizer report, this ruleset does not
apply. Note that and stop. "The installer failed" and "the update did not apply" are
**not** crash reports. Also stop if the bug is not in user-facing Firefox at all, a
Core, DevTools-internals, or build-system bug, and say which area it looks like.

## What to produce

1. **Localize the cause in the source.** Where to look and what language to expect
   depend on the component — see **Source repository** in the system prompt, which names
   the trees and links the in-tree documentation for each one. Read that documentation
   rather than working the structure out from the source; it is written by the people who
   own the code, and it is the copy that stays current. Find the module, the markup or layout,
   and any relevant pref (often `modules/libpref/init/all.js`, or `app.update.*`
   for the updater) that governs the behavior. Use the `investigator` subagent for
   deep searches.
2. **Confirm the area is still live.** Check the referenced code/strings still
   exist and aren't already changed by a recent commit. If the bug looks already
   fixed (e.g. cannot reproduce on a newer version per comments, or the code path
   was changed), say so in the comment and propose marking accordingly instead of
   inventing a fix.
   **Check that the behavior is not intended.** When blame or a linked bug shows
   the reported behavior was asked for, the bug is not a defect: name the bug that
   introduced it, suggest INVALID or WONTFIX for a human to apply, and set
   `actionable` to false. Finding the change that introduced the behavior and then
   proposing to revert it is the most common way these comments have been wrong.
3. **Write a fix plan**: root cause, the specific files/functions/selectors to
   change, and the approach. Prefer a comprehensive fix at the right level over a
   spot fix. The full plan goes in the structured output; the comment carries only
   its conclusion.
   - **Fix at the level the bug was filed at.** If the summary describes a general
     problem and you find only one instance broken, say so as a finding, but do not
     narrow the fix to that instance.
   - **Build on what is already on the bug.** If comment 0 or a later comment
     already gives the cause or the fix, confirm it from the code and add only what
     is new. If an engineer proposed an approach, follow it or say why not, and never
     credit them with a different one.
4. **Assess severity** per the `severity-assessment` rules.

## Comment

### Who reads it

The comment is read by an engineer who works on this component. They know its files, its
architecture, its prefs and the platform machinery under it. Write the note you would leave
for a teammate who owns the code, not an explanation for a newcomer:

- **Do not explain what a file, class, module or function is for.** Name it and say what
  it does wrong. `Crossword.jsx` needs no "the component that renders the crossword widget".
- **Do not explain how the component is layered** or how data flows through it, beyond the
  one hop that is broken.
- **Do not explain platform concepts**: preloading, docShells, actors, Redux, Fluent,
  NSIS, Gecko lifecycle, Android lifecycle. The reader knows them.
- **Do not justify the evidence chain.** One link to the line that is wrong is enough; the
  reader will open it.

**Two to four sentences in total**, counting the severity sentence and counting each fix
step as a sentence. The footer the runtime appends does not count. `root_cause`,
`proposed_fix`, `target_files` and `relevant_tests` in the structured output carry the
detail for the executor and have no length limit, so put depth there.

The shape: one or two sentences naming the file and what is wrong, then a new paragraph
opening with a bold `**Fix:**` label and one sentence on the fix, then the closing severity
sentence. Keep those as separate paragraphs.

### Worked example

From a different bug, to show the shape. The paths and line numbers are that bug's, not
yours.

```
[nsUpdateService.sys.mjs]({{searchfox.permalink}}/toolkit/mozapps/update/nsUpdateService.sys.mjs#3410-3428)
only reaches `#handlePatchFailure` from the apply path, so a partial that fails
verification during download never falls back to the complete patch
([#3502]({{searchfox.permalink}}/toolkit/mozapps/update/nsUpdateService.sys.mjs#3502-3515)).

**Fix:** Route the download failure through `#handlePatchFailure` and cover it in
[test_0113_general.js]({{searchfox.permalink}}/toolkit/mozapps/update/tests/unit_service_updater/test_0113_general.js).

Suggested severity: S2. Release users stop receiving updates after one corrupt partial, with no workaround.
```

### How the comment reads

- **Open on the mechanism.** No framing sentence.
- **Do not rule out alternatives.** What you eliminated goes in `reasoning`.
- **Report the finding, not the search.**
- **Say the thing once.** The fix and the severity do not restate the cause.
- **No em dashes**, no one-line paragraphs used as a beat, no parenthetical longer than
  about five words.
- **Recommend once.** No trailing "worth auditing X too".
- **A bug is `bug 1234567`, not a link.**
- **American English.**

Keep every file, function, line anchor and link that the finding depends on. Shorter
means less explanation, not fewer findings.

## Confidence

Your `confidence` decides whether your comment reaches the bug unreviewed. See
**Recording actions** in the system prompt before you pick a level. This is the
run's confidence in the _localization_; the severity suggestion has its own, separate
confidence (see `severity-assessment`).

- **High** (you found the specific code and the cause is clear): record the
  plan comment.
- **Medium** (plausible area, cause not pinned down): record the comment with
  your best hypothesis and the open questions that would confirm it. Questions
  only the reporter can answer go in `reporter_question` instead, which reaches
  the bug even though this comment is held.
- **Low** (could not localize): record a comment stating what you checked, and
  put the specific information you need from the reporter in
  `reporter_question`. Set `confidence` to low and `root_cause` to null in the
  structured output.

## Already-fixed / duplicate

If the bug appears fixed by another change, name the likely bug/commit in the
comment so a human can mark it properly. Do not propose a redundant fix.
