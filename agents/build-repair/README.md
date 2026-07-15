# Build Repair Agent

Two-stage Claude agent that diagnoses a Firefox build failure and edits the source
tree to fix it. Agent logic in `hackbot_agents/build_repair/`; the Weave eval
harness in `evals/`.

Run the Docker commands below from the repo root, with secrets in a local `.env`
(`ANTHROPIC_API_KEY`, `BUGZILLA_API_KEY`, plus `WANDB_API_KEY` for evals).

The second stage attempts building Firefox to verify the fix and iterate on it if it fails.
It also optionally bootstraps Firefox build if needed.

## Input

- `FAILURE_TASKS` - a dictionary of failed Taskcluster tasks {task_name: taskcluster_task_id}.
  The agent resolves the push from them: the failure commit (checked out) plus the other
  commits in the push, and blames the one that introduced the failure.
- `GIT_COMMIT` - Optional override for the failure commit (skips the hg->git lookup).
- `BUG_ID` - Optional Bugzilla bug id.

## Output

First stage - analysis:

- `summary.md` - a quick summary for a developer
- `analysis.md` - detailed analysis
- `planning.md` - intermediate file that outlines fixing steps for the second stage
- `blame.json` - the commit that introduced the failure (`blamed_commit`, `reason`); written
  only when the push has more than one commit

Second stage - fixing:

- A patch in Hackbot format

The result reports `blamed_commit` so the caller can attribute the failure to a developer.

## Test the agent

```sh
FAILURE_TASKS='{"build-linux":"XyU4b_BIRdO_IeK6z_kcQg"}' docker compose up build-repair-agent --build
```

Artifacts are written to `~/hackbot/artifacts/`.

## Evaluation

The evaluation dataset is prepared with [build_repair_create_dataset.ipynb](../../notebooks/build_repair_create_dataset.ipynb) and saved to Weights and Biases Weave.

### Run evals

Each dataset row is a Firefox build failure. The harness runs the agent
on a git worktree at the failure commit, builds the fix, and LLM-judges it against
the landed commits. Needs a bootstrapped Firefox checkout.

Local (use only for debugging as new agent is not sandboxed):

```sh
FIREFOX_GIT_REPO=/path/to/firefox \
  uv run --package hackbot-agent-build-repair --extra eval \
  python -m evals.eval --no-try-push --limit 1
```

Docker (reuses the broker container, so no Bugzilla creds passed to the eval container):

```sh
FIREFOX_GIT_REPO=/path/to/firefox \
  docker compose --env-file .env -f agents/build-repair/compose.yml run --rm --build build-repair-eval --no-try-push --limit 1
```

Flags:

`--trials N` - the number of times to run each example

`--parallelism N` - the number of runs to parallelize with Weave

`--judge-model <id>` - Claude model ID for LLM-as-a-judge

`--dataset <ref>` - Weave dataset name

`--no-try-push` - do not run TRY push to verify the results, only local build

`--verbose` - debugging log level

The harness skips examples whose fix
landed before the production model's training cutoff (`MODEL_CUTOFF_DATES` in
`evals/verify.py`) to avoid contamination.

Change the models in [config.py](hackbot_agents/build_repair/config.py) to older ones (`claude-opus-4-6`) to test on older datasets.

### W&B metrics

`weave.init` + `weave.Evaluation` log success and diff rates, local and TRY build
pass rates, LLM fix-matching (analysis/fix quality, ground-truth match,
acceptance), and `total_cost_usd`.

See https://wandb.ai/moz-bugbug/bugbug-build-repair-eval/weave/evaluations
