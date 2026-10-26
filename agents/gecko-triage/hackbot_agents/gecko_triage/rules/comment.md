# The comment

Applies to every comment the agent records. Some rules point to the team's `comment.md`.

## Voice

The triage account is a bot, not a person. Write advisory, never volitional. Avoid "I'd
like to", "I'll take a look", "I'll start", "I want to", "let me". Use "the next step
is", "this looks like", "suggest", "recommend", "we can".

**Never state severity or priority in the comment.** They are Bugzilla fields and the
field is the single source of truth; a label in the prose goes stale the moment someone
changes the dropdown. Do not write "marking this S2", "this is a P1", or any `S<n>` or
`P<n>` token. Describe the user-facing impact instead ("a complete failure with no
workaround").

**Keep the findings; make only the next steps reporter-plain.** The analysis, with its
root cause, citations and direction, stays in the comment for the developers who read it
later. "An expert leads the analysis" in `workflow.md` gives the one exception. What must
be plain and actionable is the part the reporter acts on: a numbered workaround or a
concrete "what to capture".

**Never link a local file or an internal document.** Cite bugs, Searchfox permalinks and
pages on the allowed link hosts that the system prompt lists. The recorder refuses a link
to any other host.

**Never ask a reporter to send data by email.** The agent cannot read email, so the data
does not reach the triage. Ask the reporter to attach the file to the bug or to add a
share URL. If the content is private, ask for a public sample that shows the same
problem.

**Never write a `:nick` and never ask a person for action without a needinfo.** Bugzilla
sends no email for a `:nick` in a comment, so the person never sees it. To involve a
developer, set a needinfo as the "Needs info" rules in `workflow.md` allow, and ask them
by name in the comment. Never write that a team or a role "can decide" either: nobody
gets that request. A hand-off note that says who drives the bug now is not a request,
and "Handing off" in `workflow.md` says when it needs no needinfo.

## Writing standard: ASD-STE100

Write every comment in ASD-STE100 Simplified Technical English. It keeps a comment short
and clear for readers whose first language is not English.

- **Words.** Use simple, common words with one meaning each. Do not use synonyms for the
  same thing: if you write "the decoder" once, do not write "the codec" later.
- **Technical names stay as they are.** Function, class, file, pref, API, codec and
  product names, and bug numbers.
- **Instructions** (steps the reader follows): imperative, one instruction per sentence,
  20 words maximum.
- **Requests to a named person:** polite. "Could you please …?" One request per
  sentence. The numbered steps under it stay imperative.
- **Descriptive sentences:** 25 words maximum. A sentence over the limit is two sentences.
- **Paragraphs:** one topic each, 6 sentences maximum.
- **Active voice and simple tenses.** "The decoder rejects the stream", not "The stream
  is rejected" or "has been rejecting".
- **No noun clusters** of more than 3 words. **Keep articles**; no telegraph style.
- **No dash or hyphen used as an aside.** Use a comma, a colon, or two sentences.
- **Warnings first.** "Caution: this also resets your other modified prefs."
- **Exempt:** code, identifiers, quoted log lines, link text, the capture block and the
  mozregression block.

The sentence and paragraph limits, the dash rule and the severity-label rule are checked
when the comment is recorded. A comment outside them is refused with the reason.

## Keep it short

- **Do not restate what the reporter already told you.** One clause of acknowledgement.
- **Do not explain why you need an artifact.** Ask for it.
- **Do not report what you eliminated.** The root cause, the citations and the direction
  stay, with the exception above; the search path that found them goes.
- **Do not narrate the investigation.** Open on the conclusion.
- **One measurement per claim.**
- **American English.**

## Audience

The user prompt says who filed the bug.

- **reporter:** a user. Ask for the artifacts the rules list, in the plainest words.
- **qa:** a QA engineer. Ask for what is missing as you would ask a reporter.
- **developer:** Mozilla staff who are not QA. Never ask them for about:support, steps to
  reproduce or a log. Expect "Analyzed" (severity, priority, meta bug) or
  "Close or move", almost never "Needs info".

## A needs-info comment

- **No praise or restatement paragraph.** One clause at most.
- **No hypothesis.** A guess at the mechanism is for us, not for the reporter, who cannot
  act on it.
- **No justification of the ask.**

**At most one narrowing question**, chosen from the team's `comment.md`.

Shape:

```
[One clause of acknowledgement, optional.]

**Analysis**

[Optional. 1 to 3 sentences on what about:support or another artifact showed, for
example a non-default pref or a blocked feature. Omit it when there is nothing
significant.]

**Next steps**

[The capture block placeholder, as its own paragraph, when the team's `comment.md` asks
for it.]

[The other missing items, one short line each.]

[The mozregression block placeholder, as its own paragraph, for a regression.]
```

The inventory of what is present and missing is internal; never paste it into the comment.

## An analyzed comment

The analysis, with one or two sentences per finding, then the next step. Name the meta bug
and any related or regressor bug by number, since a reader may not look at `blocks` or
`regressed_by`. Never name a bug that the agent cannot read.

**Say how sure you are.** Write "probable" when no log, test or profile proves the cause.
Then name the check that can confirm it. A regressor is "probable" unless it is a known
regressor, as step 3 in `workflow.md` defines. Never write "only X" unless a test shows
it. For example, do not write "only HEVC" when nobody tested VP9.

## A hand-off comment

When the comment hands the bug to a developer, give the regression status: a regression,
not a regression, or not known yet. Give the version range when you know it.

A hand-off alone does not make the decision `needs_info`. In the same comment, ask the
reporter for a log only when all of these are true:

- No log, test or profile proves the cause.
- A log can show the cause.
- Nobody asked for that log before.

That ask is a needinfo on the reporter, so the decision is `needs_info`. Never ask for a
log in a move to another team. With a team capture block, write its placeholder. With no
capture block, name the `MOZ_LOG` modules for the log. Do not ask a reporter who is a
developer.

## An error in a recorded comment

Read the comment again after you record it. If you find an error, call
`remove_recorded_actions`. It removes the comment and every recorded field change. Then
record the corrected comment and every field change again, in the same order. Every check
runs again. You can do this two times per run. Never leave an error that you know of.
