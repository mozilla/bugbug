# Build Repair Agent

Two-stage Claude agent that diagnoses a Firefox build failure and edits the source
tree to fix it. Agent logic in `hackbot_agents/build_repair/`; the Weave eval
harness in `evals/`.

Run the Docker commands below from this folder, with secrets in a local `.env`
(`ANTHROPIC_API_KEY`, `BUGZILLA_API_KEY`, plus `WANDB_API_KEY` for evals).

## Test the agent

```sh
BUG_ID=1987675 GIT_COMMIT=5477e3882d4e18f93de9f56b31e90533fd23b0d1 \
FAILURE_TASKS='{"build-linux":"XyU4b_BIRdO_IeK6z_kcQg"}' \
  docker compose up build-repair-agent --build
```

Artifacts are written to `~/hackbot/artifacts/`.

## Run evals

Each dataset row is a Firefox build failure; per trial the harness runs the agent
on a git worktree at the failure commit, builds the fix, and LLM-judges it against
the landed commits. Needs a bootstrapped Firefox checkout.

Local:

```sh
FIREFOX_GIT_REPO=/path/to/firefox \
  uv run --package hackbot-agent-build-repair --extra eval \
  python -m evals.eval --no-try-push --limit 1
```

Docker (reuses the broker, so no Bugzilla creds in the eval container):

```sh
FIREFOX_GIT_REPO=/path/to/firefox \
  docker compose run --rm build-repair-eval --no-try-push --limit 1
```

Flags: `--trials N`, `--parallelism N`, `--judge-model <id>`, `--dataset <ref>`,
`--no-try-push`, `--verbose`.

The agent reads the bug live from Bugzilla, so the harness skips examples whose fix
landed before the production model's training cutoff (`MODEL_CUTOFF_DATES` in
`evals/verify.py`) to avoid contamination.

## W&B metrics

`weave.init` + `weave.Evaluation` log success and diff rates, local and try build
pass rates, LLM fix-matching (analysis/fix quality, ground-truth match,
acceptance), and `total_cost_usd`.
