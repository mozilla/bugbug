# perf-triage agent

Rates one Firefox performance bug with the Performance Impact Calculator (the
vendored `triage-perf-bug` skill, in agent mode) and writes the rating to the bug:
one comment, `cf_performance_impact`, and the calculator's keywords (added, never
removed). When the bug links a Firefox Profiler capture or has one attached, the skill analyses it with
`profiler-analysis` first, so the profile can change the rating.

## What it triages

This agent focuses on **Performance Bugs**.

What gets routed to this agent:
Bugs that have Performance Impact (`cf_performance_impact`) set to `?` and bugs that are added to `Core::Performance:General`.

## Running it locally

Needs Docker running (Compose v2), an Anthropic API key with billing enabled, and
a Bugzilla API key. Local runs only read, so a key from an account without edit
rights works. Put the secrets in a gitignored `.env` at the repo root:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
BUGZILLA_API_URL=https://bugzilla.mozilla.org/rest
BUGZILLA_API_KEY=...
```

`BUGZILLA_API_URL` must end in `/rest`. Then build and run from this directory:

```sh
cd agents/perf-triage
set -a; . ../../.env; set +a
export SOURCE_REF=$(git rev-parse HEAD)   # a build arg; without it the image ships stale code
docker compose build
BUG_ID=1839859 docker compose run --rm perf-triage-agent
```

The root `docker-compose.yml` includes this `compose.yml`, so
`docker compose run --rm perf-triage-agent` also works from the repo root.
`docker compose up` does not rebuild an existing image, so build again after
changing code. The agent image needs no Firefox checkout or build. A run takes
2-3 minutes; a bug that links a profile takes longer, because profiler-cli loads it
whole in the container (deployed with 8Gi for that reason).

### Environment variables

`hackbot-api` derives them from the input schema. Locally they come from `.env`,
`compose.yml`, or the command line. Every `AgentInputs` field must also be listed
in `compose.yml`, or Docker drops it and the default applies silently.

| Env var             | Required | Meaning                                                                                                                                |
| ------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `BUG_ID`            | yes      | The Bugzilla bug to triage                                                                                                             |
| `ANTHROPIC_API_KEY` | yes      | Drives the agent (billed per token, roughly $0.25-0.85 a bug)                                                                          |
| `BUGZILLA_API_URL`  | yes      | e.g. `https://bugzilla.mozilla.org/rest`. **Broker container only**                                                                    |
| `BUGZILLA_API_KEY`  | yes      | **Broker container only.** The agent reads Bugzilla through the broker and never sees the key                                          |
| `BROKER_URL`        | yes      | Broker base URL; the agent appends `/mcp`. `compose.yml` sets it                                                                       |
| `SOURCE_REF`        | build    | Build arg naming the commit the image is built from. Defaults to `dev`                                                                 |
| `PERF_TRIAGE_PATH`  | no       | The calculator script. Defaults to the image's vendored `/app/perf_triage.py`; point it at a checkout to test an unreleased calculator |
| `MODEL`             | no       | Model override; unset uses the SDK default                                                                                             |
| `MAX_TURNS`         | no       | Hard cap on loop iterations, a runaway guard                                                                                           |
| `EFFORT`            | no       | `low` \| `medium` \| `high` \| `xhigh` \| `max`                                                                                        |
| `BLIND_BUG_ID`      | no       | Evaluation only: the broker serves that bug as it stood before it was triaged (`performance_tools/blind.py`)                           |

## Output

Each run writes to `~/hackbot/artifacts/<run_id>/`:

- **`summary.json`.** `findings` holds:

  - `triage`: the skill's result (`impact`, `score`, `keywords`, `selections`, `unestablished`, the calculator comment and URL);
  - `stability`: whether the rating holds across every value of the inputs the bug left open, plus `platforms`;
  - `write_mode`: `full` for an unrated bug, `comment_only` for a rated one;
  - `skipped_reason`;
  - `auto_apply`.

  `actions` holds the **recorded** comment and, in full mode, the field change.

- **`logs/agent.log`.** The streamed reasoning and every tool call.
- **A skipped run** has `skipped_reason` set and no actions. Causes: the bug lookup failed, triage failed, or a rated bug where the agent agrees with the existing rating.

## What it writes back to Bugzilla

**Nothing, during a run.** The two enabled action types, `bugzilla.add_comment`
and `bugzilla.update_bug`, only append to `summary.json`. Reads go through the
broker sidecar, which holds the API key.

**Afterwards, hackbot-api applies the recorded actions in one `PUT`.** It does this
unattended only when `findings.auto_apply` is true. That happens when the
calculator, re-scored over every unestablished input and every platform choice,
lands in a single band (`check_stability` in
`performance_tools/triage/stability.py`). Otherwise the actions wait for a person in
the Hackbot UI, and the comment says which inputs would change the rating.

What can be written depends on the bug's state when the run starts:

| Bug's `cf_performance_impact` | Writes                                                                                                                                    |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `---` or `?` (unrated)        | One comment; `cf_performance_impact`; the calculator's keywords (added, never removed); on `high`, a needinfo to the performance reviewer |
| Already rated by a person     | One comment only if the agent's rating differs; nothing if it agrees. The field is never overwritten                                      |

The hooks in `hooks.py` refuse anything else as it is recorded, so a refused
action never reaches `summary.json`. They are the only bound on what an unattended
run writes, because hackbot-api dispatches against a much wider handler registry:

- `add_comment_hook`: one public comment, on the triaged bug.
- `update_bug_hook`: one update, on the triaged bug, never in `comment_only` mode.
  It touches only `cf_performance_impact` (one of `none`/`low`/`medium`/`high`),
  `keywords` as `{"add": [...]}` limited to keywords the vendored calculator can
  emit, and `flags` limited to exactly the reviewer needinfo, on a `high` rating.

The comment text is code (`comment.py`), not a model turn. It carries the
calculator's own comment and permalink, the stability verdict, and a line telling
anyone who disagrees whom to needinfo.

## Deployment prerequisites

- **The hackbot Bugzilla account must be in the `perf-triage-team` Bugzilla group**, with
  edit rights to `cf_performance_impact`. hackbot-api sends the field, keywords and
  needinfo in one `PUT`, so without them that update is rejected on every unrated
  bug. Setting a needinfo needs no extra rights.
  Confirm with one real write on a test bug after the first deploy.
- **The agent is deployed before the bugbot `perf_triage` rule is enabled.** The
  rule's first production run uses `--backlog` to clear existing bugs; after that
  it looks back one day.
