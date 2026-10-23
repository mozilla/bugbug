# Test Repair Fix Agent

Claude agent that proposes a patch for the regression a
[test-repair](../test-repair/README.md) run blamed. It is started by that run's
`agents.start_run` action, never by the listener. Agent logic in
`hackbot_agents/test_repair_fix/`.

## Input

- `PARENT_RUN_ID` - the test-repair run to continue from. The runtime downloads its
  artifacts before the agent starts.
- `SKIP_FIREFOX_BUILD` (optional, default from `config.SKIP_FIREFOX_BUILD`) - skip
  the build and report the patch as unverified.

The verdict comes from the parent's `summary.json`, the failing job from its
`investigation.json`. The analysis documents -- `summary.md`, `analysis.md`,
`verdict.json` and the failure logs -- are copied into a separate input directory the
agent reads first and leaves alone: they are written for sheriffs, and the agent writes
its own for the developer.

## The patch

The tree is prepared at the culprit commit itself, so the patch is a change to that
commit, for its author to squash in and reland -- the backout still stands. The
analysis is written to settle the backout, so the agent first confirms the root cause
in the culprit's diff and finds every place it applies, then fixes all of them.

The agent writes a mozconfig mirroring the failing CI build (debug/opt, plus
asan/tsan/ccov), runs `mach bootstrap`, builds, and runs the failing tests with mach.
With `SKIP_FIREFOX_BUILD=true` the patch is written but never compiled or run, and is
reported as unverified. The container is Linux, so for a Windows or Mac failure a
failure here is evidence the patch is wrong while a pass proves nothing.

When the culprit's bug is known -- `culprit_bug`, from the culprit's commit subject --
it records a `phabricator.submit_patch` action: a WIP revision carrying the patch, stacked as a child of the culprit's own revision when
its commit footer names one. The action waits for a human -- see
`always_apply_actions` in [`agents.py`](../../services/hackbot-api/app/agents.py).

## Output

For the culprit's author, not the sheriffs:

- A patch in Hackbot format
- `summary.md` - 2-3 sentences: what in their commit breaks which test, what the
  patch changes, whether it was verified
- `fix.md` - root cause, patch and verification, under 40 lines
- `proposed_patch` and `verified` in `summary.json`'s `findings`

When a patch was proposed, an `email.send` action carries `fix.md`, the reland steps
and the patch to the culprit's author, linking back to the analysis run (see
[notify.py](hackbot_agents/test_repair_fix/notify.py)); the team is copied apply-side. The author is not addressed while the job is classified intermittent.

## Test the agent

Run [test-repair](../test-repair/README.md#test-the-agent) first, then point this
agent at its run id, the directory name under `~/hackbot/artifacts/`:

```sh
PARENT_RUN_ID=local-20261020-120000-abcdef \
  docker compose up test-repair-fix-agent --build
```
