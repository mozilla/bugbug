# Test Repair Agent

Two-stage Claude agent that finds the commit which regressed a failing Firefox CI
test and proposes a fix. Agent logic in `hackbot_agents/test_repair/`.

The pulse listener only forwards failures that already passed its regression and
flakiness filters, so the agent assumes a genuine regression and does not
re-classify. Its only input is a Taskcluster task id.

Run the Docker command below from the repo root, with secrets in a local `.env`
(`ANTHROPIC_API_KEY`; `BUGZILLA_API_KEY` is optional).

## Deterministic prep

Before Claude is invoked, `resolve.py` turns the task id into everything the
investigation needs (no log parsing):

1. Project + hg revision from the Taskcluster task.
2. The failing test groups, via mozci.
3. The revision at which the group was last green, by walking mozci push
   ancestors.
4. The git commits that landed since then (head first) from the hg pushlog +
   lando; capped so an old last-green can't produce an unbounded clone.

The head (failure) commit and a depth spanning back to last-green are set as
`SOURCE_REF` / `SOURCE_DEPTH`, so the runtime shallow-clones exactly deep enough
for the agent to `git show` every candidate. The task's full and sanitized logs
are written to files for the agent to search.

## Input

- `FAILURE_TASKS` - a dictionary of failed Taskcluster test tasks
  `{task_name: taskcluster_task_id}`. The agent resolves the push, last-green
  revision and candidate commit range from the first task id itself.

## Output

First stage - analysis (read-only):

- `summary.md` - a short verdict
- `analysis.md` - detailed reasoning, with evidence from the logs and diffs
- `verdict.json` - `culprit_commit`, `culprit_bug`, `recommendation`
  (`backout` / `land_fix`) and `confidence`

Second stage - fixing (only when a culprit is identified):

- A patch in Hackbot format

The result reports the `culprit_commit` so the caller can attribute the
regression to a developer.

## Test the agent

```sh
FAILURE_TASKS='{"test-linux1804-64/opt-xpcshell-1":"XyU4b_BIRdO_IeK6z_kcQg"}' \
  docker compose up test-repair-agent --build
```

Artifacts are written to `~/hackbot/artifacts/`.
