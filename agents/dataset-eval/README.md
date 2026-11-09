# Dataset eval agent

Runs an agent over an eval dataset, grades every example, and logs one Weave
evaluation per run, so experiments (agent × harness × model × effort) can be
compared. Logic in [`hackbot_agents/dataset_eval/`](hackbot_agents/dataset_eval/).

The code knows nothing about any task: what the agent is asked and which tests
grade it come from the dataset, and the judge's rubric from a Weave prompt. Both
live in a private Weave project.

## How a run works

One run is one experiment, in one container ([`runner.py`](hackbot_agents/dataset_eval/runner.py)):

1. Clone the latest Firefox, build it, and run every row's tests once for a
   baseline. The commit is recorded with the results.
2. For each example, in turn: reset the tree, run the agent with a fresh HOME and
   TMPDIR and a time limit ([`isolation.py`](hackbot_agents/dataset_eval/isolation.py)),
   then grade it and log it to Weave.

Grading restores the test harness's own files, builds, reruns the row's tests and
asks a judge, blind to the harness and model, for a 1-5 score. A patch that edits
the test files fails `integrity`.

## The agent under test

`prompt-executor` ([`prompt_executor/`](hackbot_agents/dataset_eval/prompt_executor/))
is a hackbot agent like the others -- inputs from env, result in `summary.json`,
logged with `Reporter` and traced to Weave under this run's id -- that runs a coding
harness unmodified on the prompt: `claude_code` (Claude Agent SDK)
or `codex` (Codex Python SDK). Each sees only its own vendor's credentials, and
nothing in its environment says it is being evaluated.

## Dataset

One row per example; extra fields are ignored:

```json
{
  "prompt": "...",
  "tests": [
    {
      "harness": "wpt",
      "paths": ["dom/feature"],
      "prefs": { "dom.feature.enabled": true }
    }
  ]
}
```

[`scripts/publish.py`](scripts/publish.py) publishes a local directory of
`dataset/<id>.json` rows and `prompts/<name>.md` prompts to Weave. Keep that
directory off the repo.

## Metrics

`test_progress` (per test file, the share of the gap to passing that the patch
closes), `test_regressions`, `builds`, `integrity`, `quality`, and cost from
token usage ([`prices.py`](hackbot_agents/dataset_eval/prices.py)).

## Input

`DATASET` and `JUDGE_RUBRIC` (Weave prompt ref) are required; `AGENT`, `HARNESS`,
`MODEL`, `EFFORT`, `REPS`, `TASKS` (JSON list of row ids), `EXAMPLE_TIMEOUT_MIN`,
`JUDGE_MODEL`, `LABEL` (appended to the experiment's name) and
`WEAVE_PROJECT`.

## Running locally

```sh
FIREFOX_GIT_REPO=/path/to/firefox DATASET=<dataset>:latest JUDGE_RUBRIC=<rubric>:latest \
  docker compose --env-file .env -f agents/dataset-eval/compose.yml run --rm --build dataset-eval
```

`.env` needs `ANTHROPIC_API_KEY` and `WANDB_API_KEY` (and `CODEX_API_KEY` for the
`codex` harness); `WEAVE_PROJECT` defaults to `moz-bugbug/hackbot-evals`. The local
Firefox clone is mounted read-only and lends its objects to the tree, so nothing
is cloned; the run uses its checked-out HEAD.
