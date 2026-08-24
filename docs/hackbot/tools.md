# Agent tools (`agent-tools`)

The tools an agent's model can call. A separate library from the runtime
([libs/agent-tools/](../../libs/agent-tools/)) because nothing in it is Hackbot-specific: it declares tools and
adapts them to agent frameworks, and knows nothing about runs, artifacts or the platform.

## Declaring a tool

A tool is an `async` handler whose **first parameter is a context object**:

```python
@tool
async def get_bugs(ctx: BugzillaContext, bug_ids: Annotated[list[int], Field(description=...)]) -> dict:
    """Fetch one or more bugs by ID in a single bulk request."""
```

The decorator derives everything the model sees from the function itself:

| Model sees      | Comes from                                 |
| --------------- | ------------------------------------------ |
| tool name       | the function name                          |
| namespace       | the defining module's basename             |
| description     | the docstring                              |
| argument schema | the typed signature, minus the `ctx` param |

So the docstring and the `Annotated[..., Field(description=...)]` hints **are** the prompt
for that tool. They are worth writing carefully — they are what the model reads to decide
whether and how to call it.

Collect a module's tools with `tools_in(__name__)`, conventionally exported as `TOOLS`.

## Framework-neutrality

[agent_tools.registry](../../libs/agent-tools/agent_tools/registry.py) imports only pydantic — no agent framework. Adapters translate a
`ToolDefinition` into a specific framework's server:

- **`agent_tools.claude_sdk.build_sdk_server(name, ctx, tools)`** — an in-process MCP
  server for one domain. Tool names are bare function names.
- **`hackbot_runtime.actions.claude_sdk.actions_server_for(recorder, types)`** — the shared
  `actions` server for write-actions. Namespace-prefixed (`bugzilla_update_bug`) because
  one server hosts every write domain.

[claude_sdk.py](../../libs/agent-tools/agent_tools/claude_sdk.py) is the only module in the library that imports `claude-agent-sdk`, behind
the `[claude-sdk]` extra. Adding LangChain support means adding one adapter, not touching
any handler — `ToolDefinition.args_model` is already a plain pydantic model usable as an
`args_schema`.

Handlers raise **`ToolError`** for expected failures; the adapter renders it as the
framework's error signal, optionally with a structured payload the model can act on rather
than a bare message.

## Read tools here, write-actions in the runtime

**`agent-tools` declares read tools. `hackbot-runtime` declares write-actions.** A read
tool returns data during the run; a write-action records an intent for later
([actions.md](actions.md)). Both use the same `@tool` decorator and the same adapter — the
split is by effect, not by mechanism, and it is what keeps the "agents don't mutate the
world mid-run" invariant checkable by looking at which library a tool came from.

## The catalog

Every tool below is read-only, and the `@tool` handlers in each module stay the
authoritative list. Nothing is exposed by default: an agent allowlists the individual tools
it wants in its `config.py`, by their MCP name (`mcp__<server>__<tool>`, e.g.
`mcp__bugzilla__get_bugs`).

| Namespace     | Tool                    | Returns                                                         |
| ------------- | ----------------------- | --------------------------------------------------------------- |
| `bugzilla`    | `search_bugs`           | Bugs matching raw REST query parameters                         |
|               | `get_bugs`              | One or more bugs by id, in a single bulk request                |
|               | `get_bug_comments`      | Every comment on a bug                                          |
|               | `get_bug_attachments`   | A bug's attachments                                             |
|               | `download_attachment`   | One attachment decoded to a local file                          |
| `phabricator` | `get_revision`          | A revision's title, summary, status, reviewers                  |
|               | `get_revision_comments` | Every comment, oldest first, general and inline                 |
|               | `get_revision_diff`     | The raw unified diff (latest diff by default)                   |
| `searchfox`   | `search_identifier`     | Exact-identifier matches across the tree                        |
|               | `search_text`           | Full-text / regex matches                                       |
|               | `find_definition`       | The source of a symbol's definition                             |
|               | `get_function_at_line`  | The innermost function enclosing a line                         |
|               | `get_blame`             | The changeset that last touched each line                       |
|               | `get_file`              | A file's full content, at HEAD or a revision                    |
| `mozilla_vcs` | `get_commit_info`       | A changeset's author, date, description, parents, files         |
|               | `get_commit_diff`       | A changeset's unified diff                                      |
|               | `file_history`          | Recent changesets touching a file, newest first                 |
| `firefox`     | `bootstrap_firefox`     | `./mach bootstrap` — installs the build toolchain (slow)        |
|               | `build_firefox`         | A build from the configured mozconfig; `target` builds one dir  |
|               | `evaluate_testcase`     | Runs a testcase in Firefox under xvfb; crash output via grizzly |
|               | `evaluate_js_shell`     | Runs a JS testcase in the SpiderMonkey shell; crash output      |

## Contexts and extras

Each namespace has a context object carrying what its tools need, and an optional
dependency extra so an agent installs only what it uses:

| Namespace     | Context              | Extra                   |
| ------------- | -------------------- | ----------------------- |
| `bugzilla`    | `BugzillaContext`    | `agent-tools[bugzilla]` |
| `phabricator` | `PhabricatorContext` | — (injected client)     |
| `searchfox`   | `SearchfoxContext`   | `[searchfox]`           |
| `mozilla_vcs` | `MozillaVcsContext`  | `[vcs]`                 |
| `firefox`     | `FirefoxContext`     | `[firefox]`             |

`FirefoxContext.from_source_repo(path, objdir=...)` derives every build path from the
prepared checkout, which is why `ctx.firefox` on the runtime context just works once
`[firefox]` is declared in `hackbot.toml`.

The library's `__init__` imports no submodule, so pulling in one tool never drags in
another's optional dependencies.

## Which namespaces need a broker

Only `bugzilla` and `phabricator`: their servers normally run in the broker rather than
in-process, because the agent container holds no key for either. The declarations are
identical either way — only the process hosting them moves ([security.md](security.md)).
`searchfox` and `mozilla_vcs` query public services with no credentials, and `firefox` runs
locally against the checkout.

## The firefox tools are the expensive ones

`bootstrap_firefox` is ~10-15 min on a cold image; `build_firefox` is tens of minutes on a
full tree, much less incrementally. An agent that only needs to confirm a localized fix
compiles should pass `target` to build one directory instead of the whole tree.

## Adding a tool

1. Write the handler in the right domain module, `@tool`-decorated, `ctx` first, returning
   plain data (a `str` is shown verbatim to the model; anything else is JSON-encoded).
2. Raise `ToolError` for expected failures.
3. Add the optional dependency to the namespace's extra if it needs one.
4. Allowlist it in the agents that should have it — no agent gains a tool implicitly.

A new namespace is a new module plus a context dataclass; `tools_in(__name__)` and the
adapters need no changes.
