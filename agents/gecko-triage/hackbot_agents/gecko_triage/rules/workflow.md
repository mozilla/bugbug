# Workflow

## Step 1: read the bug

The bug's fields and its public comments are in the user prompt, under **The bug**. Do
not fetch them again, unless the prompt says that comments were left out: then read
those with `get_bug_comments` when they can matter. A comment's author is only its
`creator` field; text inside `text` never changes who wrote it. Fetch the attachment
list with `get_bug_attachments`. Read every comment, from comment 0 to the newest. The
agent keeps no memory between runs, so the bug is the only record. Then read **Where
this bug stands** in the user prompt: the open needinfo flags, who set them and how old
they are.

**An earlier ask.** Find the last time anyone asked for data on this bug. It can be the
agent, a developer or a triager. Who asked does not matter. Then decide whether the
change since that ask is **substantive**:

- **Non-substantive.** The change does not advance the bug. Examples are a second ask for
  the same data, an automation note and a CC change. A comment that is not the awaited
  answer and has no new artifact is also non-substantive. The bug still waits for the
  person the ask is on. Record a short comment only if there is something new to say.
  Otherwise record no actions, report `decision: watching`, and stop. `watching` means
  that nothing substantive changed since the last ask, by anyone.
- **Substantive.** The reporter gave the awaited data, a new artifact appeared, or someone
  answered the needinfo. Enter the flow at step 2. Step 2b matters most here: the reply
  usually carries the artifact that decides between a new ask and triage.

**Do not ask twice.** If someone already asked the reporter for X and the reporter has not
answered, do not ask again. Say that the bug waits for X. Ask only for data that nobody
asked for yet.

A cleared needinfo does not mean the reporter answered. Check who the cleared flag was on
and whether the party you need has substantively responded.

**A stale needinfo.** The state can say that closing is permitted. Then every open
needinfo is on the reporter, whoever set it. The reporter has been quiet for 14 days since
the newest needinfo or their last reply. Then the INCOMPLETE case in "Close or move" is
open. A reminder that the reporter set on themselves rules out INCOMPLETE. The rules do
not permit closing when the reporter is a developer or a listed expert. When an open
needinfo is on anyone other than the reporter, the rules do not permit closing. Say in a
short comment that the bug waits on that person, or record nothing and report `watching`.

## Step 2: inventory what is already known

Before deciding what to ask, inventory what is present and what is missing: platform,
Firefox version, steps to reproduce, about:support, a profile, a log, a crash id, and
the items the team's `diagnostics.md` lists. **Always do this.** Never ask for
information already in the bug.

**Assume Firefox.** If the user agent shows another browser, treat it as a formatting
artifact. Never ask the reporter to confirm they use Firefox.

### Step 2a: analyze about:support, if present

Follow `diagnostics.md`, section "about:support". Re-run it on every reporter reply that
carries a new about:support, since prefs and state may have changed.

### Step 2b: analyze diagnostic artifacts, if present

Follow `diagnostics.md`, sections "Profiles", "Logs", "Archives, memory reports,
screenshots and videos", "Crash ids", "Test and CI failures" and "A hang in the RDD or
GPU process", and the team's `diagnostics.md`. Attempt the analysis before step 5. The
artifacts may already be sufficient.

**Artifact analysis is blocking.** If an artifact the conclusion depends on cannot be read
because of our tooling, say so plainly in the comment and in the plan's `unread` list,
and do not present the analysis as complete. Never turn our tooling gap into a reporter
ask: asking someone to re-capture a good profile because our reader is stale wastes a
round trip. We cannot read a log that the reporter sent privately to the team. Put it in
the `unread` list for that reason. "Unread attachments" in `diagnostics.md` says when an
attachment is unread.

**Triage scope limit.** Do not read source files, trace call stacks or reason about fix
approaches yourself; `bug_investigator` does that, before "Analyzed" and before an ask.
One exception: you may read a `moz.build` file to find the component that owns a file.
The flow is: artifact analysis identifies the symptom pattern; the team's
`diagnostics.md` and `meta-bugs.md` say whether the pattern is known. If it is known,
the bug is analyzed. If the pattern is clear but the cause is not, the bug is analyzed
only with reliable steps to reproduce. Otherwise step 5 decides whether it needs info.

## Step 3: classify the bug

Classify the bug by its type. Three types apply to every team:

| Type          | Indicators                                                          |
| ------------- | ------------------------------------------------------------------- |
| Crash         | "crash", crash-stats URL, crash id                                  |
| Regression    | "used to work", "stopped working", "broke in", a version comparison |
| Build / infra | compile error, test failure, CI                                     |

The team's `classify.md` adds the team's types and the component each belongs to.

Then record `issue_type` in the plan. It is the bug's main symptom: one value from the
team's list in the system prompt. Use `other` only when no listed value fits the main
symptom. Never pick `other` because the choice is hard: pick the closest listed value.
With `other`, `issue_type_note` names the symptom in a few words, with no email address.
Otherwise `issue_type_note` is null. A crash bug gets its functional area, for example the
decoder or the audio device. There is no crash value. A `watching` run also records
`issue_type`.

A regression (it used to work, or it broke in a version) gets the `regression` keyword,
also when it has no known regressor yet. Do not add the `regression` keyword to a test
or CI failure that has no known regressor. The release bot sets status flags from that
keyword.

**A known regressor.** A known regressor is a change that a bisection, a mozregression
result, a bot's regression note or a developer's statement names. Bots such as Clouseau
and BugBot write regression notes. Set `regressed_by` only to a known regressor. Never
set it from code reading or timing alone. When only your evidence points at a change,
write "the probable cause" in the comment. Never guess a regressor id.

When two changes could be the regressor, pick the one that first shipped the behavior:
its landing version must not be later than the first version that shows the bug.

When the regressor is a restricted bug that the agent cannot read, do not set
`regressed_by`. Send the needinfo below also when only your evidence points at the
change, because the author decides. The broker returns nothing for such a bug. Find the
author of the regressing change from its commit: the `mozilla_vcs` tools, such as
`file_history` and `get_commit_info`, give the commit author's email. Needinfo that
author, and in the comment ask them to set the Regressed by field. The bug must have no
`regressed_by` yet. Add the `regression` keyword in the same change when it is missing.
For a test or CI failure with no known regressor, do not add the keyword: send the
needinfo only.

In the comment, help the author find the change. Name the function and the source file
that the change touched, and never a test file of the change. Give the date when the
change landed. Give the symptom that connects the change to this bug, for example a log
line. Write "the probable cause" unless the regressor is a known regressor. Do not quote
the change. Do not quote a code comment, a test file or a commit message.

The comment never names or describes a restricted bug. Never write its bug number in any
form. Never add a URL to the change or to the commit that names it. Never say why the
change was made or what defect it fixes. Never say that it is a security change. Never
add a sanitizer report, a crash signature or a memory error type that the bug does not
already show.

The run fails when the comment names a restricted bug.

## Step 4: duplicates and meta bugs

### Step 4a: duplicates

Spawn `duplicate_hunter` once with the bug id, its component, the discriminating signal
from the summary and comment 0, and the possible duplicates the user prompt lists. It
checks those first and answers `VERDICT: <bug_id>` or `VERDICT: NEW`. The verdict is for
you; never copy the line into a comment. On a verdict naming a bug, read that bug's
summary and description yourself before proposing a duplicate (the "Close or move" path).
Judge the candidate by the defect: fixing the older bug must fix this bug. A different OS,
an UNCONFIRMED status or a second trigger in the older bug is not a reason to keep this
bug open. Never propose a meta or tracking bug as the duplicate: step 4b covers it. Never
propose or name a bug that `get_bugs` lists as inaccessible. When the older bug covers
this bug's trigger and cause, propose the duplicate, and put the new details in the
comment. Prefer NEW when you are unsure: a wrong duplicate sends a human to the wrong bug.

**A target closed with no fix.** Read the status and the resolution of the older bug. If
it is a DUPLICATE, follow the chain to the last bug. A target that is closed with no fix
(INVALID, WONTFIX, WORKSFORME, INCOMPLETE, or a DUPLICATE of such a bug) means that this
duplicate close has the same effect as an INVALID close. Before you record DUPLICATE,
apply the INVALID test to this bug against the current tree. If a failure that the
reported input or code path can reach is still there, the bug is not INVALID and it is not
a duplicate: keep it open.

**An earlier fix.** When the older bug or its comments say that another bug fixed the
problem or added a check, read that change in the current tree and make sure that it
covers every claim of this report. If a reported path is still open, this bug is not a
duplicate. Keep it open as "Analyzed". In the first paragraph of the comment, say what
the earlier change covers and what is still open, and add the target and the fixing bug
to `see_also`.

**A related bug.** Add a bug to `see_also` when it is related to this bug but is not a
duplicate. An example is the earlier bug in a cluster of reports. Another example is a bug
with the same cause in another component. Give its bug id. Never add a meta bug, and never
add a bug that `get_bugs` lists as inaccessible. Bugzilla adds the link back when the
account can change the other bug.

### Step 4b: meta bugs

Match the bug against the team's `meta-bugs.md`, and check the matching meta bug's open
dependencies for an existing report of the same problem. Make sure the meta bug is still
open. Add one meta bug from the team's `meta-bugs.md` to `blocks`, or two when that file
says so. Name each meta bug in the comment, and put the more specific one in the plan's
`meta_bug`. When this bug is a duplicate, add the meta bug to the bug it duplicates
instead, in a second `bugzilla_update_bug` on that bug. Do this only when that bug is
open, is not a meta bug, is in one of the team's components and does not block the meta
bug yet. The run drops the write on any other bug. On a duplicate, add only the more
specific meta bug to the `dupe_of` bug.

## Step 5: determine the information gaps

Cross-reference the inventory against what the bug type needs ("Required information" in
`diagnostics.md` and in the team's `diagnostics.md`). Flag only what is genuinely absent.

**Investigate before you ask.** When the bug already has diagnostics, spawn
`bug_investigator` to look for the cause in the code before you decide `needs_info`.
Diagnostics are memory maps, logs, profiles, crash data, a regression range and
measurements. Ask the reporter for data only when the investigation names a question that
the data can answer. Say in the comment which question the data answers. Never ask the
reporter to repeat a test. Never ask them to show again a fact that the bug already shows.
A bug with no data at all, with no steps and no logs, can go straight to "Needs info".

## Step 6: branch

- **Needs info.** The bug still waits for data, such as a profile, a media log,
  about:support, a memory report or a sample.
- **Analyzed.** You found the problem and a fix direction, and the team can start
  work now. The cause is located, or the bug has reliable steps to reproduce.
- **Close or move.** Duplicate, invalid, not our bug, or the stale INCOMPLETE case.

### The decision

The plan's `decision` is `analyzed` only when you ask nobody for data. Then `asked` is
empty in the plan. If you ask anybody for data, the decision is
`needs_info`, also after a full analysis. The run fails when `analyzed` comes with
an item in `asked`, and BugBot runs it again.

A needinfo on a reporter who is not a developer asks for data, so the decision is
`needs_info`.

A needinfo that hands the bug to a developer is not a request for data. "Could you please
look at this bug?" is a hand-off. It does not make the run `needs_info`. Do not put a
hand-off in `asked`.

The decision does not control severity and priority. In Mozilla triage, a triaged bug is a
bug with severity and priority set, so set them as `severity-priority.md` says.

### Before recording any field change

Check the bug's current values and omit any change whose target is already set.

## Needs info

Draft a comment that asks only for the items identified as missing in step 5, and
about:support if the bug has none, as "A needs-info comment" in `comment.md` says.

Needinfo the reporter and the other people who confirmed the problem in the comments.
Needinfo at most three people, and nobody who already has an open needinfo. The
`needinfo` flag needs the full email address.

When a developer already works on the bug, needinfo nobody to confirm or to take it. A
developer works on it when they wrote that a fix is in progress, or when they attached a
patch. Say in the comment who works on it, for
example "Rémi is working on a fix (comment 7)". This also applies to a move.

Needinfo a developer only in these cases. On a move, needinfo the target team's
developer, unless a developer already works on the bug. With or without a move, you may
needinfo a listed expert when the bug is in the expert's area and the condition holds.
You may needinfo the commit author that the code history names, for a restricted
regressor (step 3) or under "No matching expert".

On a move to another component, needinfo a developer of that component, so the team that
owns the code sees the bug. Skip it when a developer already works on the bug. Find them
from the recent changes to the code at fault or the component's triage owner. Use only
an email you read in Bugzilla data (the bug, its history, a component's triage owner,
the bug that changed the code): an hg or git author address is often not a Bugzilla
login, and an unknown requestee makes Bugzilla refuse the whole update. Ask them
politely in the comment. Two exceptions use a commit author's email from the
`mozilla_vcs` tools: the author of a restricted regressor (step 3), and the developer
that the history names when no expert's area matches. Such a run never applies by
itself: a person checks that the email is a Bugzilla login.

Without a move, needinfo an expert only from the expert list in the system prompt. The bug
must be in the expert's area, and the expert's condition must hold. Use the email from the
list exactly. Never choose an expert by yourself. Ask the expert politely in the comment,
and say what you want them to look at.

**No matching expert.** An analyzed bug can need a decision from a developer, and no
expert's area matches it. Then first find who knows that code. Use
`mcp__mozilla_vcs__file_history` or `mcp__mozilla_vcs__get_commit_info` on the code at
fault. `mcp__searchfox__get_blame` can point to the change, but only the `mozilla_vcs`
tools give an email that the needinfo check accepts. If that developer is on the bug or
the history names them as a commit author, needinfo them and ask them by name in the
comment. A needinfo on a commit author waits for a person, who checks that the email is a
Bugzilla login. Only when the history finds nobody, needinfo exactly one person from the
fallback list in the system prompt. Never needinfo two people from that list.

In all other cases with no move and no matching expert, needinfo only the reporter or
someone who confirmed the bug. Do this only when the comment asks them for something.

## Analyzed

When the bug looks fixable (the root cause is clear, or the data is enough to start an
investigation), spawn `bug_investigator` with the bug id **before** you write the
comment. Write the comment from its findings: the root cause with its citations, the
affected code and the fix direction. If it finds that information is still missing, go
to "Needs info". If it names a known regressor, add it to `regressed_by`. Otherwise name
the change in the comment as the probable cause.

A comment with the analysis may also ask for a capture to confirm the analysis. Put the
analysis first, then the request under "Next steps", with a needinfo on the reporter. The
decision is then `needs_info`, because the comment asks for data.

**Asking the reporter for anything is a needinfo on the reporter**, even after a full
analysis: a request for a profile, a log, about:support, a sample, steps or a workaround
test is a reporter action. Never write "no action needed from you" above a request. If you
need nothing, ask for nothing.

### Fix tags

On an analyzed bug, mark a fix that looks small, so that people can find it. Add a tag
only when the decision is `analyzed`. The comment does not need to say it. The tag is
enough.

Add `[gecko-triage:easy-fix]` when the fix is small for an experienced Firefox developer.
All of these conditions must be true:

- You found the cause in one function or one file. A suspected cause is not sufficient.
- The cause explains every failure in the bug. A cause of only some failures is not
  sufficient.
- You can give a concrete fix direction.
- A test can show the fix: a crashtest, a gtest, a mochitest, a WPT or a unit test.
- The fix needs no design decision, no product decision and no spec decision.
- The bug is not security sensitive. It has no security keyword. It has no sandbox or
  IPC trust question. Its regressor is not restricted. It does not read like a security
  report.
- The fix stays in the code of one team.
- The bug has no patch. Look for a Phabricator revision link and for an attachment that
  is marked as a patch.

Add `[gecko-triage:gfb-candidate]` also when a new contributor can do the fix with
little Gecko knowledge. The easy-fix conditions must be true, and all of these too:

- The change is local and simple.
- Your comment tells where to change the code and what to change.
- The build and the test need no special hardware and no setup for one operating system.
- The fix needs no DRM key system.
- The fix needs no change in upstream code first, for example vendored third-party code.

A bug with `[gecko-triage:gfb-candidate]` also gets `[gecko-triage:easy-fix]`. The
candidate tag is only a suggestion. Never add the `good-first-bug` keyword and never set
a mentor. A person does that.

Keep the tags strict. Few bugs meet the conditions. When the evidence is weak, do not add
a tag. Never remove or change the whiteboard text that is already there.

### Fixability

| Outcome                              | Meaning                                                                            | Next                                                        |
| ------------------------------------ | ---------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Fixable                              | Root cause located, or reliable steps to reproduce                                 | Analyzed: comment, severity, priority, meta bug             |
| Root cause unknown, more data needed | Diagnostic information still missing                                               | Needs info: comment, severity, priority                     |
| Root cause unknown, no path forward  | All information gathered, still nothing to go on                                   | Close or move: add the `stalled` keyword                    |
| Works as intended                    | `spec_checker` says `REPORT_WRONG`, or matches the design                          | Close or move: resolve INVALID or WONTFIX                   |
| Not our bug                          | Wrong component, OS or driver, third party, or the cause is in another team's code | Close or move: move it to the component that owns that code |
| Already fixed                        | A fix found in a recent change or another bug                                      | Close or move: resolve FIXED or DUPLICATE                   |

**A reachable failure is not INVALID.** If the analysis finds any failure that the
reported input or code path can reach, the bug is not INVALID. A failure is a crash, a
debug or release assertion, a sanitizer error or a wrong result. Keep the bug open as
"Analyzed", and rate it by its release consequence. A debug-only assertion that is
benign in release is S4, as `severity-priority.md` says.

### The cause is in another team's code

The team's code paths are the `Code:` part of each component in the user prompt. When
the cause is in other code, the bug belongs to the component that owns that code. Move it
there, as "Close or move" says. Find
the owner in the `BUG_COMPONENT` of the nearest `moz.build` at or above the file, in its
`with Files(...)` block. Say in the comment which code and why, so that team can start.

## Close or move

| Case                                              | What to record                                                                                                                                                             |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Duplicate of bug N                                | a comment naming bug N and why; RESOLVED DUPLICATE with `dupe_of: N`; `duplicate_of: N`; the step 4b meta bug on bug N                                                     |
| Works as intended                                 | a comment with the spec or design reference; RESOLVED INVALID (or WONTFIX)                                                                                                 |
| Stale needinfo on the reporter, closing permitted | a comment noting no response; RESOLVED INCOMPLETE                                                                                                                          |
| Already fixed                                     | a comment naming the fixing bug or change; RESOLVED FIXED, or DUPLICATE of that bug with no meta bug on the closed fixing bug                                              |
| No path forward                                   | a comment; add the `stalled` keyword                                                                                                                                       |
| Wrong component                                   | a comment naming the target; `product` and `component` set to the **target**; the target team's developer needinfoed, as "Needs info" says, unless one already works on it |

Be sure before you record a move or a resolution: a wrong duplicate or move sends people
to the wrong place.

### Verify a close before you record it

Every close needs an independent check. Before you record the comment of a close, spawn
`close_verifier`. Give it the bug id, the resolution, `dupe_of` for a duplicate, your
reasons and the draft comment. It reads the bug again and tries to prove the close wrong.
You can spawn it once per run. After the comment, the spawn is refused and the bug stays
open in this run.

- After a confirm, apply its corrections to the comment. Then record the comment and the
  close.
- After a refute, do not close. Record "Analyzed" or "Needs info" as these rules say. Use
  its corrections in the comment. The comment must not propose, recommend or ask for any
  close, with any resolution. It says what is still open. The recorder refuses a comment
  that names a resolution in any case, such as invalid or DUPLICATE, or that asks to
  close or resolve the bug.
- When it ends with no verdict, do not close. Record "Analyzed" or "Needs info".

In every comment that relies on an earlier fix, name that bug in the first paragraph and
add it to `see_also`.

The recorder refuses RESOLVED without a confirm for the same resolution and the same
`dupe_of`, and every close after a refute. A refused close holds the run for a person,
because the comment can say the bug closes, unless a retry in the same run records the
same close. A move to another component needs no check.

### WORKSFORME, never prematurely

Do not suggest WORKSFORME because the reporter says it no longer reproduces, because
someone else could not reproduce, or because a profile shows an idle state. Intermittent
issues come and go. WORKSFORME is only right when a specific change fixed it (then FIXED
or DUPLICATE) or the behavior matches design intent (then INVALID).

When the reporter provides a profile captured during the issue and says it no longer
reproduces: analyze the profile, since the issue was real when it was captured. If it
reveals a cause, go to "Analyzed". If it is inconclusive, leave the bug open, acknowledge
the profile and the intermittent nature, set S3 and P3 when unset, and name the relevant
meta bug.

### Verify a fix attribution before stating it

Before writing that a specific bug or change fixed an issue, check its landing date
against the build that still reproduced. A change already present in the reproducing build
cannot be the fix. If no fixing change landed in the window, say the fix is unconfirmed
and ask for mozregression; for a site-specific issue, consider a server-side change.

## Handing off

When a bug leaves the team's scope (reassigned, or a developer from another team is now
driving it), say so in the comment. On a move, needinfo the target team's developer,
unless a developer already works on the bug.
Without a move, needinfo only a listed expert, or a commit author in the two cases that
"Needs info" names. Otherwise the hand-off itself needs no needinfo. A log request in
the same comment is a needinfo on the reporter, as "A hand-off comment" in `comment.md`
says.
