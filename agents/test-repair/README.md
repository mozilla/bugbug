# Test Repair Agent

Read-only Claude agent that decides whether sheriffs should back out a commit for a
failing Firefox CI test: it finds the commit that regressed it, or identifies the
failure as a known intermittent, and reports the root cause behind the
recommendation. A blamed culprit is handed
on to [test-repair-fix](../test-repair-fix/README.md), which proposes the patch. Agent
logic in `hackbot_agents/test_repair/`.

Run the Docker command below from the repo root, with secrets in a local `.env`
(`ANTHROPIC_API_KEY`; `BUGZILLA_API_KEY` is optional).

## Deterministic prep

Before Claude is invoked, `resolve.py` turns the task id into the investigation
context (no log parsing):

1. Project + hg revision from the Taskcluster task.
2. The failing test groups, via mozci.
3. The revision the failing tests were last green at, by walking mozci push
   ancestors, restricted to those tests on the failing platform.
4. The git range that landed since, from the hg pushlog + lando. Only the range
   endpoints are mapped to git; the commit count sizes the shallow clone and is
   capped.
5. Open intermittent bugs Treeherder's `bug_suggestions` ties to the failure.

The agent gets the range (`base..head`), not a list of shas, and narrows it itself
with `git log`. When the range is not known to reach a green run it is passed as
`HEAD~N..HEAD` and the prompt stops asserting the culprit is in it.

## Handing off the fix

The analysis never edits the tree, builds or plans a fix. When it blames a culprit,
the model calls the `agents.start_run` action for `test-repair-fix`, which works out
the fix from the root cause. hackbot-api starts that run once this one has succeeded,
so the verdict reaches sheriffs without waiting for a patch. A hand-off without a
resolved culprit is dropped.

## Input

- `FAILURE_TASKS` - failed Taskcluster test tasks,
  `{task_name: taskcluster_task_id}`. Everything else is resolved from the first
  task id.

## Output

For sheriffs, deciding the backout:

- `summary.md` - 2-3 sentences: the action, the fact that settles it, what failed
- `analysis.md` - a brief technical analysis: verdict, failure, cause and
  alternatives ruled out, under 30 lines
- `investigation.json` - the task id and the resolved investigation
- `verdict.json` - `classification` (`regression` / `intermittent`),
  `culprit_commit`, `candidate_commits`, `intermittent_bug`, `recommendation`
  (`backout` / `do_not_backout` / `rerun`) and `confidence`; `culprit_bug` is read
  off the culprit's commit subject;
  the same fields land in `summary.json`'s `findings`

On a hand-off, `verdict.json` and the failure logs (`logs/**/live_backing_log*`) are
published too, for the fix run.

`recommendation` is the action for the sheriff, so a genuine regression is always
`backout`.

## Notifications

A run whose verdict a sheriff has to act on records a `slack.post_message` action
carrying it -- the recommendation, the classification and confidence, the failing job
(linked to the push in Treeherder), the culprit or the candidates that could not be
ruled out. A known intermittent -- `intermittent` classified `do_not_backout` -- is
not posted: it asks nothing of a sheriff and is the
majority verdict, so it would be noise. An intermittent recommending `rerun` is still
posted, since the retrigger is the sheriff's to run.

Every verdict also records an `email.send` action carrying the full analysis, for the
hackbot team to track what the agent decided -- unfiltered. Treeherder is re-read just
before it is recorded and the email shows the job's current classification. The
culprit's author hears from the test-repair-fix run, which has a patch for them.

Both are delivered by the apply step, not from the run, so they are visible in the
hackbot UI before they land and are delivered at most once. Both always apply, as does
the hand-off, so a succeeded run reports without waiting for a human.

## Test the agent

```sh
FAILURE_TASKS='{"test-linux1804-64/opt-xpcshell-1":"XyU4b_BIRdO_IeK6z_kcQg"}' \
  docker compose up test-repair-agent --build
```

Artifacts are written to `~/hackbot/artifacts/`.
