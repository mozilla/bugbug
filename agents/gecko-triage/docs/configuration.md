# Configuring gecko-triage

One agent serves every Gecko team. A run names its team with the optional `team` input
(`TEAM` in the job). The run reads that team's directory and the shared files. This page
says what each holds, how manual or automatic mode is set, and how a team joins.

| Source                   | Holds                                                         | Changes need      |
| ------------------------ | ------------------------------------------------------------- | ----------------- |
| `teams/<team>/team.toml` | The team's components, capture steps and needinfo experts     | a PR and a deploy |
| `teams/<team>/rules/`    | The team's rules                                              | a PR and a deploy |
| `rules/`                 | The general rules every team follows                          | a PR and a deploy |
| `shared/bugzilla.toml`   | Bugzilla keywords and fields, and the agent's whiteboard tags | a PR and a deploy |
| `shared/people.toml`     | How to tell Mozilla staff and QA apart                        | a PR and a deploy |

All paths are under `hackbot_agents/gecko_triage/`. The files are read when the run
starts. A team name that is not a lower-case name of a directory in `teams/`, a missing
team rules file, an unknown key at any level, a wrong type or an empty component list
stops the run before it reads a bug.

## A run with no team

A run with no `team` input reads `rules/` and `shared/` only.

- The scope is the bug's own product and component. The preflight does not skip the bug
  as out of scope. A move to the owning component is still allowed.
- The product must be on the `no_team.products` list in `shared/bugzilla.toml`. The
  list holds the products whose code is in mozilla-central. The preflight skips a bug in
  any other product.
- A bug with no product or no component has no scope, so the preflight skips it.
- The duplicate search looks in the bug's own component.
- There are no meta bugs. The hooks refuse every `blocks` change, and every change on the
  `dupe_of` bug.
- There is no capture block. The hooks refuse the capture placeholder, and the capture
  check passes.
- There are no needinfo experts.
- The system prompt names no team rules, and the general rules tell the model to skip
  the team-specific steps.
- The run never vouches for unattended apply. `findings.auto_apply` is always `false`, so
  a person applies its actions, also when `auto_apply_actions` is `True`.

## The team file

`teams/<team>/team.toml`:

| Key                                               | Meaning                                                                                                                 |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `label`                                           | How the prompt names the area, for example "Firefox Graphics"                                                           |
| `comment.capture_marker`, `comment.capture_block` | A request that names the marker must quote the block verbatim. A comment may name the marker to cite a capture.         |
| `comment.capture_labels`                          | Optional. UI labels from the block. Two of them outside the block mean retyped steps, and the hook refuses the comment. |
| `[[components]]`                                  | One entry per component: `product`, `component`, `trees` (the code), `doc_trees`, `notes`                               |
| `[[needinfo_experts]]`                            | Optional. One entry per person: `email`, `areas` (one or more) and an optional `when`, the condition for asking         |
| `needinfo_fallback`                               | Optional. Emails to ask when no expert's area matches and the code's history names nobody. The agent asks one at most.  |
| `[[issue_types]]`                                 | Optional. One entry per value: `value` and `meaning`, a main symptom for `issue_type`. `other` is not listed.           |

## The rules

The model reads two rule sets. Where a team rule is more specific, it wins.

| General, in `rules/`   | Team, in `teams/<team>/rules/`                                                       |
| ---------------------- | ------------------------------------------------------------------------------------ |
| `workflow.md`          | `classify.md`: the team's bug types and their components                             |
| `diagnostics.md`       | `diagnostics.md`: the team's prefs, captures, failure patterns, required information |
| `comment.md`           | `comment.md`: narrowing questions, when to ask for the capture block                 |
| `severity-priority.md` | `severity-priority.md`: examples and situations for the team's area                  |
|                        | `meta-bugs.md`: the team's meta bugs                                                 |

The general rules must not name a team's components or label; `test_rules.py` checks
this for every team.

## The shared files

`shared/people.toml`: `employee_domain` (the login domain of Mozilla staff),
`qa_name_markers` (words in a real name that mark a QA account) and `qa_accounts` (QA
logins the markers miss). They decide who filed a bug, and so who the comment is for:

| Filer                               | Audience    | Effect                                                             |
| ----------------------------------- | ----------- | ------------------------------------------------------------------ |
| a QA account                        | `qa`        | Triaged like any reporter's bug                                    |
| staff, not QA, severity unset       | `developer` | Triaged. The comment never asks for about:support, steps or a log. |
| staff, not QA, severity already set | none        | Skipped: the developer owns it                                     |
| anyone else                         | `reporter`  | Triaged                                                            |

`shared/bugzilla.toml`: `keywords.meta` marks a meta bug, `keywords.add` lists the
keywords the agent may add (`stalled`, `regression`), `fields.qa_whiteboard` is the QA
whiteboard field, `meta_summary` is the summary pattern of a meta bug, and `whiteboard`
holds the agent's skip tag, `[gecko-triage:skip]`, and the fix tags it may add to an
analyzed bug, `easy_fix` and `gfb_candidate`.

The team's `meta-bugs.md` table is also the list of meta bugs the agent may add to
`blocks`. Every table row must carry a bug id, or the run stops. A run with no team has
no list, so it adds no meta bug.

## Bot accounts

The bot accounts are `hackbot@mozilla.tld`, which posts every comment, and the logins in
the `BOT_ALIASES` env var. They decide two things:

- the preflight skips a bug when the agent wrote the newest comment and nothing that
  changes the analysis happened after it (`docs/bugzilla.md`);
- the agent never sends a needinfo to a bot account.

Every Hackbot agent posts as `hackbot@mozilla.tld`. Hackbot tags each comment with the
agent's name, so a comment from that account counts as this agent's only with the
`gecko-triage` tag. A `BOT_ALIASES` login counts by author alone, because the pilot bot
may post without the tag.

## Manual or automatic, and how often

These are the standard Hackbot and BugBot settings, the same as for other agents.

| Setting                 | Where                                                                          | Effect                                                                                                                                                                                           |
| ----------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Manual or automatic     | `auto_apply_actions` on `gecko-triage` in `services/hackbot-api/app/agents.py` | `False`: every run waits in the Hackbot UI for a person. `True`: a run that vouches for itself (`findings.auto_apply`) reaches the bug with nobody in between. A run with no team never vouches. |
| How often runs start    | `every_hours` for `gecko_triage` in BugBot's `configs/rules.json`              | The BugBot rule acts once per interval and picks the bugs changed since its last run                                                                                                             |
| How many runs at a time | `max_triggers` in the same entry                                               | The rest wait for the next cron                                                                                                                                                                  |

The first is a hackbot-api deploy; the other two wait for a BugBot release.

## Adding a team

1. Copy an existing `teams/<team>/` directory to `teams/<new team>/`.
2. Change `team.toml`: the label, the components, the capture steps and the needinfo
   experts.
3. Rewrite the five files in `rules/` for the team's area.
4. Add the team to `TEAMS` in BugBot's `gecko_triage` rule (mozilla/bugbot,
   `bugbot/rules/gecko_triage.py`), with the same components as `team.toml`, so it starts
   runs with `team=<new team>`. Nothing checks that the two lists agree.
5. Add the team to `team` in `GeckoTriageInputs` (`services/hackbot-api/app/schemas.py`).
   hackbot-api refuses a run for a team that is not listed there. The agent and its image
   stay the same.
