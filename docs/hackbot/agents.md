# Agents

> For the hands-on recipe — copy the reference agent, folder layout, running it locally —
> see [agents/README.md](../../agents/README.md). This page covers the contract and how
> an agent integrates with the platform.

## The contract

An agent is a Python package started as `python -m hackbot_agents.<name>`. It owes the
platform three things:

```python
class AgentInputs(BaseSettings):        # per-run inputs, read from env (bug_id <- BUG_ID)
    bug_id: int

async def main(ctx: HackbotContext) -> BugFixResult:
    ...

run_async(main)                          # runtime takes over: config, auth, summary, exit
```

- **Success** is returning a `HackbotAgentResult` subclass. It lands in
  `summary.json`'s `findings`.
- **Failure** is raising. `AgentError` for an expected, explainable failure; anything else
  for a crash. Either way the runtime writes `summary.json` with `status: "error"` and
  exits non-zero.
- **`ctx`** is the agent's only window to the platform.

Everything around `main()` — config discovery, credentials, tracing, publishing artifacts,
writing `summary.json`, the exit code — is the runtime's job: [runtime.md](runtime.md).

## What an agent declares: `hackbot.toml`

Only capabilities the platform must **prepare** on the agent's behalf. Everything is
optional; an agent that needs nothing prepared ships a file with only comments.

```toml
[source]                                        # shallow checkout, prepared on request
repo_url = "https://github.com/mozilla-firefox/firefox.git"
checkout_path = "/workspace/firefox"
ref = "..."                                     # optional; SOURCE_REF overrides per run

[firefox]                                       # Firefox build paths from that checkout
enabled = true
objdir = "objdir-ff-asan"
```

Not here: per-run inputs, secrets, model choice, tool selection. Those are environment
or code.

## The catalog

| Agent                 | Does                                                                                                                                                                        | Source | Firefox build | Auto-applies actions |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :----: | :-----------: | :------------------: |
| `bug-fix`             | Triage a Bugzilla bug and produce a candidate fix as a Phabricator revision. Also handles `@hackbot` follow-ups on an existing revision, and Bugzilla `needinfo?` requests. |  yes   |      yes      |       **yes**        |
| `test-repair`         | Classify a CI test failure as regression or intermittent, blame the culprit commit, propose a fix.                                                                          |  yes   |      yes      |       **yes**        |
| `build-repair`        | Analyze a Firefox build failure at a specific commit and produce a candidate fix.                                                                                           |  yes   |      yes      |          no          |
| `frontend-triage`     | Read-only root-cause analysis and fix plan for a desktop frontend bug.                                                                                                      |  yes   |      no       |          no          |
| `autowebcompat-repro` | Reproduce a web-compatibility report in headless Firefox via DevTools MCP.                                                                                                  |   no   |      no       |          no          |
| `test-plan-generator` | Generate Firefox QA test cases, run them through DevTools MCP, report results.                                                                                              |   no   |      no       |          no          |

Two shapes recur. **Source agents** (`bug-fix`, `test-repair`, `build-repair`) check out
Firefox, often build it, edit the tree, and let the runtime capture the diff. **Browser
agents** (`autowebcompat-repro`, `test-plan-generator`) need no checkout; they drive a
Firefox binary through the DevTools MCP server.

Several agents run in **two stages** — a read-only analysis stage that reaches a verdict,
then a fix stage that only runs if the verdict warrants it. The two stages often use
different models. This keeps a "nothing to fix here" outcome cheap.

Agents needing credentialed reads also ship a **broker sidecar** holding the API keys
([security.md](security.md)).

## Registering an agent

Two additions in [services/hackbot-api/](../../services/hackbot-api/):

1. **[app/schemas.py](../../services/hackbot-api/app/schemas.py)** — a Pydantic input model. This _is_ the agent's public API: it is
   what `GET /agents` publishes as a JSON schema, what `POST /agents/{name}/runs`
   validates against, and what becomes the run's env overrides.
2. **[app/agents.py](../../services/hackbot-api/app/agents.py)** — one `AGENT_REGISTRY` entry: `name`, `description`, `job_name`
   (the Cloud Run Job), `input_schema`, and optionally `auto_apply_actions=True`.
   That flag makes the agent eligible for automatic application; an individual
   run created in `review` mode still holds its actions for a human.

Env vars are derived from the schema (`bug_id` → `BUG_ID`, lists and dicts JSON-encoded),
so there is no per-agent mapping code to write. `build_env` exists as an escape hatch for
an agent whose env genuinely doesn't map 1:1, and should stay unused.

**Deploy-time constants are not inputs.** The broker's loopback URL, model defaults, and
similar belong in the Job's static env, not in the input schema.

The UI's agent list ([services/hackbot-ui/lib/agents.ts](../../services/hackbot-ui/lib/agents.ts)) is a separate list and needs the
new name too.

## Conventions worth keeping

**One folder per agent, self-contained.** Logic, `hackbot.toml`, `Dockerfile`,
`compose.yml`, prompts and rules all live under that agent's own directory in
[agents/](../../agents/). You should be able to
understand one agent without reading another.

**Prompts and rules are files, not string literals.** `prompts/*.md` and `rules/*.md` are
read at startup. They are the part reviewers most often need to read.

**Never create `hackbot_agents/__init__.py`.** `hackbot_agents` is a PEP 420 namespace
package; an `__init__.py` makes agents overwrite each other when installed side by side.

**Reuse the shared pieces** rather than reimplementing them: `Reporter` for rendering the
streamed model messages into the run log, `actions_server_for` for recordable write
actions, [agent-tools](tools.md) for read tools. Assembling `ClaudeAgentOptions` and driving the
client loop stays in the agent — that is the part that should differ.
