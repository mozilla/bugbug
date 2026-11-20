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
harness unmodified on the prompt: `claude_code` (Claude Agent SDK),
`codex` (Codex Python SDK), or, for open models,
`oh_my_pi` ([omp](https://github.com/can1357/oh-my-pi) in JSON print mode),
`opencode` ([OpenCode](https://github.com/anomalyco/opencode) `run --format json`)
and `deepseek_harness` ([DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
headless). Each sees only its own vendor's credentials, and nothing in its
environment says it is being evaluated.

The open-model harnesses reach their models through an
[otari](https://github.com/mozilla-ai/otari) gateway
([`otari.py`](hackbot_agents/dataset_eval/prompt_executor/harnesses/otari.py)):
`MODEL` is named as otari names it, and the model's limits, the run's usage and
its cost come from otari. Pin a provider, e.g. `nebius:Qwen/Qwen3.8-27B`: with a
catalog id such as `alibaba/qwen3.8-27b`, otari picks the provider per request
and falls back between them, so requests can be served by different deployments
at different prices.

The image's [`machrc`](machrc) stops a single-test `mach wpt` from waiting for
someone to close the browser, which an unattended run would wait on until it
timed out.

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
token usage ([`prices.py`](hackbot_agents/dataset_eval/prices.py)), or from otari
for the models it serves.

## Input

`DATASET` and `JUDGE_RUBRIC` (Weave prompt ref) are required; `AGENT`, `HARNESS`,
`MODEL`, `EFFORT`, `REPS`, `TASKS` (JSON list of row ids), `EXAMPLE_TIMEOUT_MIN`,
`JUDGE_MODEL`, `JUDGE_EFFORT`, `LABEL` (appended to the experiment's name) and
`WEAVE_PROJECT`. Their defaults are in
[`__main__.py`](hackbot_agents/dataset_eval/__main__.py), and `WEAVE_PROJECT`'s
(`moz-bugbug/hackbot-evals-dev`) in [`hackbot.toml`](hackbot.toml); the API and UI
pass on only what a run sets. Local runs through `compose.yml` default to
`moz-bugbug/hackbot-evals`.

## Running locally

```sh
FIREFOX_GIT_REPO=/path/to/firefox DATASET=<dataset>:latest JUDGE_RUBRIC=<rubric>:latest \
  docker compose --env-file .env -f agents/dataset-eval/compose.yml run --rm --build dataset-eval
```

`.env` needs `ANTHROPIC_API_KEY` and `WANDB_API_KEY` (and `CODEX_API_KEY` for the
`codex` harness, and `OTARI_API_KEY`, with the `usage:read` scope, and
`OTARI_BASE_URL`, e.g. `https://api.otari.ai/api`, for the open-model harnesses).
The local
Firefox clone is mounted read-only and lends its objects to the tree, so nothing
is cloned; the run uses its checked-out HEAD, or `SOURCE_REF`.

The tree and its build persist in the `dataset-eval-workspace` volume. A run on
the commit the last one built skips the bootstrap, and the first build too when
the last build was of the clean tree; rows without tests are not built for
grading and get no `builds` score. On a fixed commit a run is mostly agent time.
