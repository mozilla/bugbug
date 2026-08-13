# The runtime (`hackbot-runtime`)

The library that runs inside every agent container. It owns the parts of a run that are
identical for all agents, so an agent's code is only its own logic.

## What it does around `main()`

```
run_async(main)
  ├─ discover hackbot.toml (cwd, then above the entry point's module)
  ├─ build HackbotContext from that config + environment
  ├─ configure Anthropic + W&B credentials (Workload Identity Federation, or API keys)
  ├─ start a Weave trace labelled with the agent's name
  ├─ call main(ctx)  ──> HackbotAgentResult, or an exception
  ├─ publish logs/agent.log
  ├─ publish changes/changes.patch + changes.json (+ phabricator_diff.json if needed)
  ├─ publish summary.json  { status, error, findings, actions }
  └─ exit 0 / 1
```

Publishing is best-effort per artifact and ordered so the most important thing —
`summary.json` — is written even if an earlier step fails. A run that produces no
`summary.json` is treated by the API as a failure, so this ordering matters.

## `HackbotContext`

The single object `main()` receives. Its platform fields come from the environment (set by
the orchestrator); its capabilities come from `hackbot.toml`.

### Capabilities

| Member                             | Gives you                                                      |
| ---------------------------------- | -------------------------------------------------------------- |
| `await prepare_repo(ref=, depth=)` | The source checkout, cloned or refreshed. Call once, up front. |
| `repo_path`                        | The prepared checkout (raises if `prepare_repo` hasn't run)    |
| `firefox`                          | `FirefoxContext` — build paths derived from the checkout       |
| `anthropic.api_key`                | Model credentials, validated on access                         |

`prepare_repo` resolves its ref from the argument, then `SOURCE_REF`, then
`[source].ref`. Preparing twice at conflicting refs raises rather than silently editing
the wrong tree — the checkout is a shared, single-use resource for the run.

The checkout is **shallow by default** (`depth=1`, or `2` when pinned to a ref so the
commit's own diff is computable). An agent that needs real history passes `depth`
explicitly. `ensure_source_repo` is idempotent and recovers a checkout left broken by an
earlier failed run.

`checkout_revision(ctx, revision_id, broker_url)` is the variant for follow-up runs: it
asks the broker for a Phabricator revision's base commit and raw diff, checks out the base,
and applies the diff **uncommitted** — so the run's change base stays at the revision's
base and the final submission is the complete updated revision.

### Results and artifacts

| Member                          | Does                                                     |
| ------------------------------- | -------------------------------------------------------- |
| `publish_file(key, path, type)` | Upload a file under `key`                                |
| `publish_json(key, payload)`    | Upload JSON under `key`                                  |
| `publish_changes()`             | Collect and publish the agent's source diff              |
| `log_path`                      | A writable path for the run log; published automatically |
| `actions`                       | The `ActionsRecorder` — see [actions.md](actions.md)     |
| `run_artifacts_dir`             | Local artifact dir, used when no uploader is configured  |

**The one publishing rule:** if `RESULTS_POLICY_URL` is set, the artifact is POSTed to GCS
under `key`; otherwise it is written to `artifacts_dir/run_id/key`. Same key either way, so
a downstream apply step resolves it identically against GCS or a local directory. This is
what makes local runs faithful.

### Standard artifact keys

| Key                               | Content                                                    |
| --------------------------------- | ---------------------------------------------------------- |
| `summary.json`                    | The run contract: status, error, findings, actions         |
| `logs/agent.log`                  | The rendered agent transcript                              |
| `changes/changes.patch`           | mbox patch, applied with `git am`                          |
| `changes/changes.json`            | Base commit, repo URL, commits and files touched           |
| `changes/phabricator_diff.json`   | Prebuilt Phabricator submission payload (only when needed) |
| `attachments/<action_idx>/<name>` | Files attached to a recorded action                        |

## Capturing source changes

After the agent runs, its work may be committed locally, uncommitted, or untracked.
`changes.collect` captures all of it relative to the commit the checkout started from:
any uncommitted remainder is wrapped into one synthetic commit, then `git format-patch`
produces a single mbox that `git am` applies in one command, preserving each local commit's
message and author. Binary and untracked files included.

The checkout is ephemeral, which is what makes mutating its index safe.

When the agent recorded a Phabricator patch action, `publish_changes` _also_ builds the
Phabricator submission payload here — while the checkout still exists — using moz-phab's
own diff-building code as a library. The apply step downstream therefore never needs a
checkout of its own. It's best-effort: a failure to build it doesn't fail the run.

## Model credentials

Anthropic (model access) and W&B (tracing) are both configured before `main()` runs, so an
agent just reads `ctx.anthropic.api_key` and never touches the environment. Deployed, both
use Workload Identity Federation and the container holds no long-lived key; locally both
fall back to their API-key env var, with no special casing needed either side. Mechanism
and failure modes: [security.md](security.md).

## Tools and actions

The tools a model can call are **not** in this library — read tools live in `agent-tools`
([tools.md](tools.md)), recordable write-actions in `hackbot_runtime/actions/`
([actions.md](actions.md)). The runtime's part is `ctx.actions`, the recorder those
write-actions append to.

## Other helpers

- **`claude.Reporter`** — renders streamed claude-agent-sdk messages (turns, thinking,
  tool calls, results, cost) to stdout and the run log. Every agent would otherwise
  reimplement this.
- **`searchfox`** — expands `{{searchfox.permalink}}` placeholders in recorded comments
  into revision-pinned Searchfox URLs at record time, and declines to link paths that
  don't exist in the checkout. Registered as an action hook.
- **`errors.AgentError`** — the "expected, explainable failure" exception.
