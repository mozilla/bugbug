# Test Repair Agent

Two-stage Claude agent that finds the commit which regressed a failing Firefox CI
test, or identifies the failure as a known intermittent, and proposes a fix. Agent
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

## Stages

Stage 1 (analysis) is read-only and always runs. Stage 2 (fix) runs whenever
stage 1 blamed a commit.

By default the fix stage does not build (`SKIP_FIREFOX_BUILD`): the patch is
written but never compiled or run, and is reported as unverified. With
`SKIP_FIREFOX_BUILD=false` it writes a mozconfig mirroring the failing CI build
(debug/opt, plus asan/tsan/ccov), runs `mach bootstrap`, builds, and runs the
failing tests with mach. The container is Linux, so for a Windows or Mac failure a
failure here is evidence the patch is wrong while a pass proves nothing.

## Input

- `FAILURE_TASKS` - failed Taskcluster test tasks,
  `{task_name: taskcluster_task_id}`. Everything else is resolved from the first
  task id.
- `SKIP_FIREFOX_BUILD` (optional, default from `config.SKIP_FIREFOX_BUILD`) - skip
  the build and report the patch as unverified.

## Output

Stage 1:

- `summary.md` - 2-3 sentences: the action, the fact that settles it, what failed
- `analysis.md` - verdict, failure, cause and alternatives ruled out, under a page
- `verdict.json` - `classification` (`regression` / `intermittent`),
  `culprit_commit`, `candidate_commits`, `culprit_bug`, `intermittent_bug`,
  `recommendation` (`backout` / `do_not_backout` / `rerun`) and `confidence`

`recommendation` is the action for the sheriff, so a genuine regression is always
`backout`. A patch is reported separately, via `proposed_patch`, as advice for the
developer to squash into their existing patches and reland.

Stage 2:

- A patch in Hackbot format

## Slack notification

A run whose verdict a sheriff has to act on records a `slack.post_message` action
carrying it -- the recommendation, the classification and confidence, the failing job
(linked to the push in Treeherder), the culprit or the candidates that could not be
ruled out, and whether a patch is attached. A known intermittent -- `intermittent`
classified `do_not_backout` -- is not posted: it asks nothing of a sheriff and is the
majority verdict, so it would be noise. An intermittent recommending `rerun` is still
posted, since the retrigger is the sheriff's to run. The hackbot team gets every
verdict either way, by email from the pulse listener.

The message is posted by the apply step, not from the run, so it is visible in the
hackbot UI before it lands and is delivered at most once. `test-repair` opts into
auto-apply, so a succeeded run posts without waiting for a human.

## Test the agent

```sh
FAILURE_TASKS='{"test-linux1804-64/opt-xpcshell-1":"XyU4b_BIRdO_IeK6z_kcQg"}' \
  docker compose up test-repair-agent --build
```

Artifacts are written to `~/hackbot/artifacts/`.
