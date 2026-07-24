# Test Repair Agent

Two-stage Claude agent that finds the commit which regressed a failing Firefox CI
test and proposes a fix. Agent logic in `hackbot_agents/test_repair/`.

The pulse listener only forwards failures that already passed its regression and
flakiness filters, so a regression is the prior, but the agent still reports the
classification it reaches. Its only input is a Taskcluster task id.

Run the Docker command below from the repo root, with secrets in a local `.env`
(`ANTHROPIC_API_KEY`; `BUGZILLA_API_KEY` is optional).

## Deterministic prep

Before Claude is invoked, `resolve.py` turns the task id into everything the
investigation needs (no log parsing):

1. Project + hg revision from the Taskcluster task.
2. The failing test groups, via mozci.
3. The revision at which the group was last green, by walking mozci push
   ancestors.
4. The git range that landed since then, from the hg pushlog + lando. Only the
   range endpoints are mapped to git; the commit count sizes the clone and is
   capped so an old last-green can't produce an unbounded one.

The agent gets the range (`base..head`), not a list of shas, and enumerates and
narrows it itself with `git log`. When the range isn't known to reach a green run
it is passed as `HEAD~N..HEAD` and the prompt stops asserting the culprit is in
it.

`SOURCE_REF` / `SOURCE_DEPTH` pin the shallow clone to the failure commit, deep
enough to walk the range. The task's full and sanitized logs are written to files
for the agent to search.

## Input

- `FAILURE_TASKS` - a dictionary of failed Taskcluster test tasks
  `{task_name: taskcluster_task_id}`. Everything else is resolved from the first
  task id.

## Output

First stage - analysis (read-only):

- `summary.md` - a short verdict
- `analysis.md` - detailed reasoning, with evidence from the logs and diffs
- `verdict.json` - `classification` (`regression` / `intermittent`),
  `culprit_commit`, `culprit_bug`, `intermittent_bug`, `recommendation`
  (`backout` / `land_fix` / `do_not_backout`) and `confidence`

Second stage - fixing (only when a culprit was identified):

- A patch in Hackbot format

The result reports the `culprit_commit` so the caller can attribute the
regression to a developer.

## Test the agent

```sh
FAILURE_TASKS='{"test-linux1804-64/opt-xpcshell-1":"XyU4b_BIRdO_IeK6z_kcQg"}' \
  docker compose up test-repair-agent --build
```

Artifacts are written to `~/hackbot/artifacts/`.
