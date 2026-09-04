# User-facing Firefox defect triage

These rules apply to **defects in user-facing Firefox**. **Components in scope** in the
system prompt lists the components bugs normally arrive from, grouped by the area whose
code layout **Source repository** describes. That list is not a limit: any user-facing
Firefox defect is in scope whether or not its component is on it. See `scoping.md`.

Desktop and Android bugs here are usually UI/UX papercuts, documented with a
**video or screenshot** and steps to reproduce.

Screenshots you can look at: download one with `download_attachment` and `Read` the
file. Screen recordings you cannot, because this agent's image has no ffmpeg to pull
frames out of them, so downloading a video buys nothing here. When the only evidence
is a recording, triage from the description, the steps to reproduce, and the code,
and say plainly in your comment that you did not view the recording. Do not imply you
did.

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
   depend on the component. **Source repository** in the system prompt carries the
   per-area layout; use the `investigator` subagent for deep searches.
2. **Confirm the area is still live.** Check the referenced code/strings still
   exist and aren't already changed by a recent commit. If the bug looks already
   fixed (e.g. cannot reproduce on a newer version per comments, or the code path
   was changed), say so in the comment and propose marking accordingly instead of
   inventing a fix.
3. **Write a fix plan**: root cause, the specific files/functions/selectors to
   change, and the approach. Prefer a comprehensive fix at the right level over a
   spot fix. The full plan goes in the structured output; the comment carries only
   its conclusion.
4. **Assess severity** per the `severity-assessment` rules.

## Comment

**Four to five sentences in total.** That is the whole comment, counting the severity
rationale and counting each numbered fix step as a sentence. The footer the runtime
appends does not count. Treat this as a hard budget: an earlier version of this file
asked for "a few sentences" and every run produced twelve to seventeen.

The budget is affordable because the comment is not the handoff. `root_cause`,
`proposed_fix`, `target_files` and `relevant_tests` in the structured output carry the
detail for the executor and have no length limit. The comment exists so a human
scanning the bug learns, in one screenful, what is broken and what to do about it. Put
the depth in the JSON and keep the comment to its conclusion.

A comment that fits is roughly one or two sentences naming the file and the mechanism,
one or two on the fix, and the closing severity sentence. Anything else has to earn its
sentence by displacing one of those.

### Worked example

From a different bug, in another area, to show the shape. The paths and line numbers
below are that bug's, not yours. Do not reuse them.

```
[nsUpdateService.sys.mjs]({{searchfox.permalink}}/toolkit/mozapps/update/nsUpdateService.sys.mjs#3410-3428)
gives up on the update when a partial patch fails verification, because
`#handlePatchFailure` is reached from the apply path and not from the download path,
so the complete-patch fallback at
[#3502]({{searchfox.permalink}}/toolkit/mozapps/update/nsUpdateService.sys.mjs#3502-3515)
never runs and the update stays pending across restarts. Route the download failure
through the same handler, and extend
[test_0113_general.js]({{searchfox.permalink}}/toolkit/mozapps/update/tests/unit_service_updater/test_0113_general.js)
with a partial-patch verification failure asserting the complete patch is fetched.

Suggested severity: S2. A user on the release channel stops receiving updates entirely
after one corrupt partial, with no in-product workaround and nothing telling them
anything is wrong.
```

Three sentences and a severity sentence. It names two files, a private method, two line
anchors and a test, and it never narrates the investigation that found them. The
severity closes the comment on its own line, with no rule above it and no break between
the level and the reason.

### How the comment reads

Engineers have told us these comments are hard to follow: correct, but written like
an essay about an investigation rather than a note from one engineer to another.
"Be brief" did not fix it, so here are the specific habits to drop. Each one is
checkable against your draft before you record it.

- **Open on the mechanism, not on a frame.** The first sentence names the file or
  function and says what it does wrong. Cut openers that tell the reader how to
  read what follows: "Two different mechanisms are in play here, and only one of
  them is actually broken", "Unlike every other widget", "The interesting case is".
- **Do not rule out the alternatives you considered.** "Pref-backed state is not at
  risk", "Redux widgets are unaffected", "the preloading machinery is fine" answer
  questions the reader did not ask, and they are the single largest source of length
  in these comments. What you eliminated goes in `reasoning`, not in the comment.
- **No em dashes.** Every one is a sentence that has not decided where it ends. Use
  a period, a comma, or a colon.
- **No one-line paragraph used as a beat.** "The crossword does not." on its own
  line is rhetoric. Fold it into the sentence carrying the evidence.
- **A parenthetical longer than about five words is a sentence.** Promote it or cut
  it. Do not nest one inside another.
- **Recommend once.** A "worth auditing the other widgets too" or "worth confirming
  with the vendor" bolted onto the end of a fix plan is a second, unranked proposal
  competing with the first. Either it is a numbered step or it is not in the
  comment.
- **Report the finding, not the search.** What you read and which tools you reached
  for belong in `reasoning`.
- **Say the thing once.** If the root cause is in the first sentence, the fix does
  not restate it and the severity rationale does not restate it again.
- **American English.** "behavior", "initialize", "generalize", "license", never
  "behaviour", "initialise", "generalise", "licence".

None of this is license to drop substance. Every file name, function name, line
anchor and Searchfox link that carried weight in the long version carries it in the
short one. The target is the same findings in fewer, flatter sentences, not fewer
findings.

## Confidence

Your `confidence` decides whether your comment reaches the bug unreviewed. See
**Recording actions** in the system prompt before you pick a level. This is the
run's confidence in the _localization_; the severity suggestion has its own, separate
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
