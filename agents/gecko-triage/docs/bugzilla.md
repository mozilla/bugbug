# What gecko-triage does on a bug

This page is for people who see the agent on a bug. It lists every mark the agent reads
and every change it can make.

## Which bugs it looks at

Open, public defects in the components the run's team lists (see `configuration.md`). A
run with no team looks at one bug in its own component.

The agent never triages a restricted or security bug. The preflight skips a bug, before
any model turn, when any of these is true:

- the bug is in another component, when the run has a team;
- the bug is in a product outside the `no_team.products` list, when the run has no team;
- the bug is a meta bug (the `meta` keyword, or a meta summary);
- the bug is resolved;
- the bug is restricted (it is in a security or other group);
- the bug has a security keyword (`sec-*`, `csectype-*` or `security*`);
- the whiteboard contains `[gecko-triage:skip]`;
- Mozilla staff who are not QA filed it and the severity is already set;
- the bug is assigned to a person, who owns it;
- the agent wrote the newest comment, and nothing that changes the analysis happened on
  the bug after it. A change to a field in the `[history]` table of `shared/bugzilla.toml`
  (CC, votes, ranks, release status and tracking flags) does not count, and nor does a
  change by a bot alias or a BMO automation account. The agent's comment comes from `hackbot@mozilla.tld` with the `gecko-triage` tag, or from
  a login in `BOT_ALIASES` with or without the tag. A comment by another Hackbot agent
  does not count. A change at the same time as the comment is the agent's own update. The
  bug still runs when it became closable as INCOMPLETE after that comment.

A bug that staff filed with the severity unset is triaged. The comment is written for a
developer and never asks them for about:support, steps or a log.

## Why the agent reads only public content

The agent writes public comments, so it must read only what a logged-out reader can see.
The broker's key can see more, because the Hackbot deploy chooses that key. So the code
filters every read, and it does not trust the key.

| File             | What it does                                                          |
| ---------------- | --------------------------------------------------------------------- |
| `broker.py`      | Holds the Bugzilla key and serves the read tools, public content only |
| `attachments.py` | Downloads one attachment for `fetch_attachment`, with no key          |
| `visibility.py`  | Decides whether a comment or an attachment is marked public           |

`broker.py` filters each reply before the model sees it:

- It drops every bug that is in a group.
- It refuses the comments and the attachment list of such a bug. A restricted bug's
  comments are often not marked private.
- It drops private comments and private attachments. It also drops an item that does not
  say that it is public.
- It refuses the fields that it cannot filter: `_all`, `_default`, `_extra`, comments,
  attachments and history. The `description` is comment 0, and only when that comment is
  public.
- A search cannot use terms that match private comments or attachments.
- It gives the history of a public bug as the names of the changed fields, with no
  values. It keeps only what a logged-out reader sees: no time tracking, no changes to
  private comments or attachments, no "made private" change on a comment, and no relation
  (depends on, blocks, regressions, see also) that names only bugs the reader cannot see.
- It gives attachment metadata. The prompt tells the model to ask for metadata only. With
  `include_data`, the broker can also put small public text attachments in the reply.

`attachments.py` does the download, in `fetch_attachment`. It reads the metadata with no
key. It checks again that the attachment is public and belongs to the triaged bug. Then it
downloads the file anonymously. So two checks stop a private file.

The agent also reads public attachments of the bug it triages. It fetches each one
anonymously from Bugzilla's REST API. It refuses a private attachment, an attachment of
another bug and a file over 64 MiB. It unpacks gzip, zip and tar files, and about:memory
reports. For a video or a screen recording, it reads the codec, the resolution and the
duration, and it looks at up to six frames. It reads up to 3 videos for each attachment.
It also looks at screenshots. The agent's code never runs a file from an attachment. The
rules tell the model never to obey instructions in one. The comment and the run's `unread`
list name an attachment that the agent could not fetch, unpack or read.

One risk stays. The duplicate search reads comment text with `longdesc`. If the API
key can read private comments, a match on a public bug tells one bit about them. For a
local run, use a key that cannot read private comments. In the cloud, the key is a shared
broker secret from the Hackbot deploy, and nothing in this repo limits what it can read.

## Marks it reads

The agent keeps no memory of its own. Each run reads the whole bug and every public
comment. Who asked for what does not matter.

| Mark                                | Where                        | Meaning                                                                                              |
| ----------------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------- |
| `[gecko-triage:skip]`               | the bug's whiteboard         | Keep the agent off this bug. Anyone can add it.                                                      |
| `[gecko-triage:easy-fix]`           | the bug's whiteboard         | The agent added it: the fix looks small. Search for it to find candidates.                           |
| `[gecko-triage:gfb-candidate]`      | the bug's whiteboard         | The agent added it: a new contributor can possibly do the fix. A person decides on `good-first-bug`. |
| comment tag `gecko-triage`          | each comment the agent posts | Hackbot adds it. It tells the agent's comments from other Hackbot agents' comments.                  |
| open needinfo flags                 | flags                        | Whom the bug waits for, whoever set the flag. After 14 days with no answer, the needinfo is stale.   |
| `last_change_time_non_bot`          | the bug                      | Whether a person changed the bug after the newest comment                                            |
| the newest comment's author and tag | the comments                 | Whether the agent had the last word                                                                  |

## Changes it can make

Each run makes at most one comment, plus these fields:

| Change           | When                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| a public comment | Every run that is not skipped and has something new to say                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| severity         | Only when it is `--`. Only `S2`, `S3` or `S4`, never `S1`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| priority         | Only when it is `--`. `P1`, `P2` or `P3`, never `P4` or `P5`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| needinfo         | On the reporter or someone who confirmed the bug, when the comment asks them for something. On a move, also the target team's developer, even if not on the bug yet, unless a developer already works on the bug. Without a move, also an expert from the team's `needinfo_experts` list, when the bug is in one of the person's areas and the condition holds. For an analyzed bug that needs a developer and matches no area, one person from `needinfo_fallback`, when the code's history names nobody. Never on someone with an open needinfo. A developer that the code history names as a commit author: for a restricted regressor, or when no expert's area matches. Then `findings.needinfo_warning` holds the run for a person, since an hg email may not be a Bugzilla login. The first needinfo on a reporter who is not a developer or a listed expert is refused when no `bug_investigator` ran in the run before it; a second attempt passes. |
| `blocks`         | Adds one meta bug, or two when the team's `meta-bugs.md` says so. Never removes one. A run with no team never changes it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `regressed_by`   | Adds the regressor's bug id only from a bisection or mozregression result, a bot's regression note or a developer's statement. Never from code reading or timing alone.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `see_also`       | Adds the id of a related bug that is not a duplicate. Never removes one. Bugzilla adds the link back when the account can change the other bug.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| keywords         | Adds a keyword from the list in `shared/bugzilla.toml`. Never removes one.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| whiteboard       | Adds `[gecko-triage:easy-fix]` to an analyzed bug whose fix looks small, and also `[gecko-triage:gfb-candidate]` when a new contributor can do it, as "Fix tags" in `rules/workflow.md` says. Adds the tags at the end of the text already there, and never removes or changes that text. The candidate tag only comes with the easy-fix tag and is a suggestion: a person adds the `good-first-bug` keyword and the mentor. A run that is not `analyzed` keeps no tag.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| component        | Moves a bug in the wrong component to the right one                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| resolution       | `RESOLVED` as INVALID, WONTFIX, FIXED or DUPLICATE (with the duplicate's bug id). Never WORKSFORME.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| INCOMPLETE       | Only when every open needinfo is on the reporter, whoever set it. Each needinfo has a date, and none is a reminder the reporter set on themselves. The reporter is not a developer and not a listed expert. The reporter has been quiet for 14 days since the newest needinfo or their last reply.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |

Every close needs a `confirm` from the `close_verifier` subagent for the same
resolution and `dupe_of`, in the same run and before the comment. The run spawns the
verifier once, and never after the comment. After a `refute`, or with no verdict, the run
cannot close the bug. When a tool result of the verifier is too large and the CLI saves it to a file, the
verifier refutes, and the verdict tool refuses its confirm. A refused
close that no retry recorded, a late verifier or a decision that does not match the
resolution sets `findings.close_warning`, and the run waits for a person. So does a
duplicate of a bug that was closed with a resolution other than FIXED. A move needs
no verdict. The README's "Close verification" section tells how.

An INCOMPLETE close can come up to 14 days after the bug becomes closable. The BugBot
stale pass sends a bug only after 14 days with no change.

The agent sends each needinfo as `{"name": "needinfo", "status": "?", "requestee":
"<email>"}` and nothing else. The hook adds `"new": true` before the change is recorded.
Without it, Bugzilla takes the flag as an edit of the bug's needinfo: it moves the only
open needinfo to the new person, or it refuses the whole change, comment included, when
the bug has two or more.

On a duplicate, the meta bug goes on the `dupe_of` bug instead, and only the more specific
one when two apply. That is the only other bug the agent writes to, and the change there
only adds that one meta bug to `blocks`. The `dupe_of` bug is never a meta bug. The
recorder refuses a known meta bug or a bug that the triaged bug blocks. The run fails
when the `dupe_of` bug turns out to be a meta bug after the run.

After the run, one broker read gets the bugs in the commit messages the run read, the
bugs the comment names with an explicit reference, and the `regressed_by`, `see_also`
and `dupe_of` bugs. The run fails when the comment names an unreadable or restricted
bug, or writes the number of a restricted commit message bug. When the read finds one
of these bugs restricted or unreadable, or fails, the run sets
`findings.restricted_warning` with their ids and does not vouch for unattended apply.
The ids stay in the findings and never reach a public comment. While the agent is
human-gated, a person reviews every apply anyway.

While the agent is human-gated (`auto_apply_actions=False` in hackbot-api), none of these
reach the bug until a person applies them in the Hackbot UI. Once `auto_apply_actions` is `True`, Hackbot
applies them all, moves and resolutions too, for a run that vouches for itself
(`findings.auto_apply`), with no person in between. A move to another product also
sets the version to `unspecified` and the milestone to `---`, which exist in every
product.

## What it never sets

The assignee, CC, WORKSFORME and any status other than `RESOLVED`. On the whiteboard it
only adds the two fix tags. When one of these is right, the agent names it in the comment so
that a person can set it.

## How to stop it

- On one bug: add `[gecko-triage:skip]` to the whiteboard.
- Everywhere: set `auto_apply_actions=False` for `gecko-triage` in hackbot-api. The agent
  still records actions, but nothing reaches a bug without a person.
