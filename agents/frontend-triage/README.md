# frontend-triage agent

Triages a user-facing Firefox bug from Bugzilla and produces a **root-cause
analysis plus a proposed fix plan**. It reads the source tree, navigates the
codebase with Searchfox, and inspects regressor changesets on hg.mozilla.org. It
does **not** build Firefox, edit source, or reproduce the bug. It writes to
Bugzilla only after the run, and only when it rated itself confident — see [What
it writes back to Bugzilla](#what-it-writes-back-to-bugzilla).

It deliberately stops at a plan: visual and interaction bugs can't be verified by
the crash-reproduction loop the [`bug-fix`](../bug-fix/) agent relies on, so a
human (or a downstream execution agent) takes it from there.

## What it triages

**Defects in user-facing Firefox** — the kind documented with a screenshot, steps
to reproduce, or a log rather than a stack trace. `scoping.md` is what decides
scope, and it is broad: any user-facing Firefox defect qualifies.

What is _routed_ is narrower. `TRIAGE_SCOPE` in `config.py` lists the components bugs
normally arrive from, one entry each, carrying the Slack channel and the trees the
component's code lives in. A bug handed to the agent by hand in some other component
(`Firefox :: Menus`, say) is triaged the same way and reports to nobody. The components,
and the channel each reports to:

| Component                            | Reports to                       |
| ------------------------------------ | -------------------------------- |
| `Firefox :: New Tab Page`            | `#hnt-dev-triage`                |
| `Firefox :: Sidebar`                 | `#p10y-bots`                     |
| `Firefox :: Site Permissions`        | `#privacy-team-automation`       |
| `Toolkit :: Data Sanitization`       | `#privacy-team-automation`       |
| `Firefox :: Settings UI`             | `#fx-recomp-bots`                |
| `Firefox :: Sharing`                 | `#content-sharing-automation`    |
| `Firefox :: IP Protection`           | `#team-eng-ip-protection-triage` |
| `Firefox :: Messaging System`        | `#omc-triage`                    |
| `Core :: Machine Learning: Frontend` | `#smart-window-bug-triage`       |
| `Core :: Machine Learning: Models`   | `#smart-window-bug-triage`       |
| `Core :: Machine Learning: General`  | `#smart-window-bug-triage`       |
| `Firefox for Android :: History`     | `#android-core-dev`              |
| `Firefox for Android :: Toolbar`     | `#android-core-dev`              |
| `Firefox for Android :: Homepage`    | `#android-core-dev`              |
| `Toolkit :: Application Update`      | `#installer-updater-bug-triage`  |
| `Firefox :: Installer`               | `#installer-updater-bug-triage`  |

No doc path or URL is listed anywhere here. mozilla-central already records where a
component is documented in its `SPHINX_TREES` declarations, so `docs.py` runs one
`git grep` over the checkout and matches those declarations against each entry's `trees`.
`Toolkit :: Data Sanitization` is the one entry that also says which directory to search,
via `doc_trees`, because its article is registered by another component's `moz.build`; the
registration there is still read from the tree. That is what
replaced the `rules/areas/` directory: eight hand-written files restating structure that
`toolkit/mozapps/update/docs/`, `browser/installer/windows/docs/`,
`extensions/permissions/docs/`, `toolkit/components/ipprotection/docs/` and the rest
already document, and that drifted from them. What the docs cannot carry survives as a
`notes` string on each entry: which of two similar things a bug is about, what a symptom
in one layer means about another, and whether the area is tested at all.

Install and update bugs are the odd ones out: they arrive as a failure with an
error code and an `update.log` or installer log, usually with no steps to
reproduce and no screenshot. That is the normal shape of a bug in that area, so
`frontend-triage.md` says so explicitly — otherwise the ruleset's papercut
framing reads as a reason to skip them. `severity-assessment.md` starts them at
S2 rather than the S3 a papercut would get, since a user who cannot update is
left on an unpatched build with no in-product workaround.

IP Protection has the same S2 floor, for the same reason: turning the VPN off is
not a workaround for it not working. It carries one extra instruction, because the
distinction does not survive a bug report — state merely _displayed_ wrong is a UI
bug, while state actually wrong means traffic is unproxied and belongs above S2.

Poor fits: crashes, hangs, assertions and sanitizer reports (those belong to
[`bug-fix`](../bug-fix/)) — note that "the installer failed" is not a crash
report — anything outside user-facing Firefox, and bugs whose fix can only be
judged by _seeing_ the rendered result.

The `scoping.md` ruleset runs first and filters out non-defects, tracking/`meta`
bugs and intermittent test failures with a short note instead of an invented fix
plan.

Before any of that, a bug that already has a fix is stopped in Python.
`preflight.py` reads the bug's attachments in one `get_bugs` call and ends the run
if a non-obsolete Phabricator revision is attached — no checkout, no model turn, no
comment. The developer who posted the patch is on it, and a fix plan arriving
afterwards is noise in their review queue. `scoping.md` has always said so, but it
is prose: on bug 2066504 the agent named the revision and investigated anyway,
which is what this gate exists to prevent. A raw `is_patch` attachment deliberately
does **not** count — that flag is set by whoever attaches the file, so a reporter's
speculative diff would suppress triage on a bug nobody is working.

## Running it locally

Needs Docker running, an Anthropic API key with billing enabled, and a Bugzilla
API key (reads only — one from an account without edit rights works fine). Put
the secrets in a gitignored `.env` at the repo root:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
BUGZILLA_API_URL=https://bugzilla.mozilla.org
BUGZILLA_API_KEY=...
```

Then run from the repo root — the root `docker-compose.yml` includes this
agent's `compose.yml`, so the service and its broker sidecar are available:

```sh
BUG_ID=2014702 docker compose up frontend-triage-agent --build
```

The first run shallow-clones mozilla-central into a Docker volume: expect several
minutes and a large download. Later runs reuse the volume.

Three bugs that exercise the classes this agent handles:

| Bug       | Class         | Notes                                                        |
| --------- | ------------- | ------------------------------------------------------------ |
| `2014702` | Behavioral    | New Tab weather widget vanishing                             |
| `2014629` | Pure visual   | Split View group-line CSS gap                                |
| `2004297` | Regression    | Print Preview shift; traces the named regressor              |
| `2066504` | Already fixed | Phabricator revision attached; stops before the model starts |

## Inputs

Environment variables. `hackbot-api` derives them from the input schema; locally
they come from `.env`, `compose.yml`, or the command line.

| Env var             | Required | Meaning                                                                                                        |
| ------------------- | -------- | -------------------------------------------------------------------------------------------------------------- |
| `BUG_ID`            | yes      | The Bugzilla bug to triage                                                                                     |
| `BROKER_URL`        | yes      | Bugzilla broker base URL; the agent appends `/mcp`. `compose.yml` sets it                                      |
| `ANTHROPIC_API_KEY` | yes      | Drives the agent (billed per token)                                                                            |
| `BUGZILLA_API_URL`  | yes      | e.g. `https://bugzilla.mozilla.org` — **broker container only**                                                |
| `BUGZILLA_API_KEY`  | yes      | **Broker container only**; reads only. The agent never sees it                                                 |
| `MODEL`             | no       | Defaults to `claude-opus-5` (`DEFAULT_MODEL` in `__main__.py`); pinned so runs are reproducible and comparable |
| `MAX_TURNS`         | no       | Hard cap on loop iterations — a runaway guard, cut off if hit                                                  |
| `EFFORT`            | no       | `low` \| `medium` \| `high` \| `xhigh` \| `max`; only passed when set                                          |

## Output

Each run writes to `~/hackbot/artifacts/<run_id>/`:

- **`summary.json`** — `findings` holds the structured plan (`root_cause`,
  `proposed_fix`, `target_files`, `confidence`) plus the executor handoff fields
  `actionable`, `regressor_node` and `relevant_tests`, plus `auto_apply` — the
  run's own verdict on whether it may be posted without review. `actions` holds
  the **recorded** Bugzilla comment (and, at high confidence, possibly a field
  change). Recording is not posting, but see below: an `auto_apply` run's actions
  do reach the bug unattended.
- **`logs/agent.log`** — the streamed reasoning and every tool call, and the only
  record of which model actually ran.
- **No `changes/` directory.** Its absence confirms the run stayed read-only.
- **A skipped run** (see above) is `status: "ok"` with `num_turns: 0`, an empty
  `actions`, the reason in `result`, and no `logs/agent.log` — nothing ran.

Two caveats before acting on a plan:

- **`confidence` describes the diagnosis, not the fix.** It reflects how clearly
  the agent pinned a root cause in the code, never whether the fix works — it
  cannot run anything. Read `high` as "trust the diagnosis, still review the
  patch."
- **Line numbers are model-asserted.** The permalink pins the revision, so a
  cited line can't drift out from under the link, but the number is still the
  model's claim. Trust the files, functions and selectors it names; confirm exact
  lines against the source.

## What it writes back to Bugzilla

**Nothing, during a run.** `ENABLED_ACTION_TYPES` in `config.py` allows
`bugzilla.add_comment` and nothing else, and that tool comes from an in-process
actions server that appends to `summary.json` and makes no network calls. The only Bugzilla access the agent has is through the broker sidecar,
which exposes five read tools and holds the API key.

**Afterwards, though, a confident run posts itself.** When the run reports
`confidence: high` and does not report `actionable: false`, it sets
`findings.auto_apply`, and hackbot-api applies the recorded actions to the real
bug with nobody in between. `may_apply_unattended()` in `agent.py` is that
decision in full. Medium and low are held for a person to apply from the Hackbot
UI, as before.

Judgement and reach are bounded separately, because an action's params are model
output no matter how sure the agent is — and the agent spends the run reading bug
comments nobody controls. Two hooks in `hooks.py` refuse an action outright as it
is recorded, and they are the only thing bounding what an unattended run writes:
hackbot-api applies whatever it finds in `summary.json`, dispatching it against a
handler registry far wider than the tools this agent was given.

- `add_comment_hook` — one comment, public, on the bug being triaged.

That is the whole list, because a comment is the only thing this agent can write.
It has no tool that changes a bug's fields: `severity` was the one field a ruleset
directed it to set, and that is now a suggestion at the end of the comment for a
human to apply, so `bugzilla.update_bug` left `ENABLED_ACTION_TYPES` rather than
staying on with no caller.

A refusal reaches the agent as a tool error it can correct in the same run, and the
action never lands in `summary.json`. The action _type_ needs no check:
`ENABLED_ACTION_TYPES` decides which tools the actions server exposes at all.

Two further hooks shape the comment text as it is recorded:

- `permalink_hook` expands `{{searchfox.permalink}}/<path>#<line>` into
  `https://searchfox.org/firefox-main/rev/<sha>/…`, so every source reference
  keeps pointing at the code the agent actually read. A path that isn't in the
  checkout is unwrapped to backticked plain text rather than linked, because a
  permalink to a hallucinated path 404s for whoever clicks it. See the module
  docstring in `libs/hackbot-runtime/hackbot_runtime/searchfox.py` for the full
  scheme and why the revision comes from Searchfox's index rather than the
  checkout.
- `feedback_tags_hook` (`agent.py`) appends the triage-specific tags a reader can
  add to categorize a problem: `ai-triage-wrong-file`, `ai-triage-wrong-cause`,
  `ai-triage-hallucination`, `ai-triage-out-of-scope`, `ai-triage-wrong-fix`,
  `ai-triage-shallow-fix`. The last two are about the fix plan rather than the
  diagnosis: one for a fix that would not work, one for a fix that patches the
  symptom instead of the cause the comment just named.

The tags go under the runtime's shared footer inviting a 👍 or 👎 reaction, which
`bugzilla.add_comment` has already appended by the time the hook runs. Those
reactions and tags are the feedback channel — the agent does not request needinfo.

## Slack notification

A run that applies itself reports two lines to the channel of the team that owns
the bug's component: the bug, linked, with the run's one-line summary, and a link
to the run. An `S1` the run is confident about adds a `:red_circle:` and names the
level as `(suggested S1)` — suggested, because nothing was written to the field.
Below `REPORTABLE_SEVERITY_CONFIDENCES` there is no marker, matching the comment,
which omits its severity block on the same threshold. Nothing else — the analysis
is on the bug, the detail is in the run, and the channel already says which
component this is.

The audience is the team whose bug was just written to by nobody, so only an
auto-applied run notifies. A medium or low result wrote nothing to Bugzilla and
stays silent, even if someone applies it by hand later.

Routing is the `channel` on each `TRIAGE_SCOPE` entry in `config.py`, looked up by
`"<Product> :: <Component>"` through the derived `SLACK_CHANNELS` — so
`ScopedComponent("Firefox", "New Tab Page", "#hnt-dev-triage", trees=(...))`
sends a New Tab Page run to `#hnt-dev-triage`.

Four things about that which are not obvious from reading the registry:

- **The key is the component, not the team**, so two components may share a channel, as
  the installer and the updater do and as site permissions and data sanitization do,
  without either knowing about the other.
- **There is deliberately no default channel**, since posting one team's triage into
  another team's channel is worse than silence.
- **`TRIAGE_SCOPE` is narrower than what the agent will triage.** It is the routing
  table, and it should stay in step with bugbot's `TRIAGED_COMPONENTS`, which decides
  what arrives automatically. `scoping.md` puts _any_ user-facing Firefox defect in
  scope, so a bug handed to the agent by hand in some other component is triaged
  normally and reports to nobody. The system prompt says so explicitly, because a list
  of components read as exhaustive is how an in-scope bug gets declared out of scope.
- **Product and component come from the agent's `product`/`component` plan fields**,
  because nothing else carries them out of a run whose only input is a bug id. A garbled
  value matches no team and sends nothing, which is why the system prompt asks for them
  verbatim even for components the scope list does not name.

`notify.py` builds and records the message; the wording is code, not a model turn,
so `slack.post_message` is _not_ in `ENABLED_ACTION_TYPES` and the agent is never
given the tool. Like every other action it is recorded rather than sent, so it
shows up in the Hackbot UI before it lands and is delivered at most once. Delivery
needs `SLACK_BOT_TOKEN` on hackbot-api and the app in the channel — see
`libs/hackbot-runtime/hackbot_runtime/actions/handlers/slack_handler.py`. A failed
Slack post does not affect the Bugzilla writes, and it does not go the other way
either: the applier runs each action independently, so a rejected `PUT` still
notifies. The run page shows the failed action.

## Tuning

`rules/` and `prompts/` both live under `hackbot_agents/frontend_triage/`.

- **`rules/`** is the main behavior dial. `scoping.md` decides what gets skipped;
  `frontend-triage.md` sets comment content and the confidence thresholds for
  recording an action. The agent globs the directory and reads only what it judges
  relevant, so new `.md` files extend it — see `rules/README.md` for how to author
  one. Neither file lists components; `TRIAGE_SCOPE` in `config.py` does.
- **`prompts/system.md`** holds the standing instructions: output format, the
  read-only mandate, when to reach for Searchfox versus reading a file, and how to use
  the in-tree documentation. It names no component: the component index and the
  per-component guidance are rendered into it from `TRIAGE_SCOPE`.
- **Cost** scales with tool use, not just turns — Searchfox results are
  token-heavy, so narrowing queries (`path_filter`, a modest `limit`) matters
  more than `MAX_TURNS` when batching.

## Adding a triage component

One `ScopedComponent` entry in `config.py`, and a row in the table above:

```python
ScopedComponent(
    "Firefox",
    "Sidebar",
    "#some-team-triage",
    trees=("browser/components/sidebar/",),
    owns=("browser/components/sidebar/",),
    notes="...",
)
```

- **`trees`** is descriptive and may overlap another component. It drives the prompt's
  index and the docs lookup, so it is what makes the component triageable.
- **`doc_trees`** is for the one case where a component's docs are not under its code,
  and it **replaces** `trees` for the docs lookup rather than adding to it. Only
  `Toolkit :: Data Sanitization` needs it: its article is registered by
  `toolkit/components/antitracking/moz.build`, and its own
  `browser/base/content/sanitize*` files otherwise resolve to `browser/base/`'s
  tabbrowser and sslerrorreport trees. Leave it empty unless `docs_for` returns a
  sibling component's documentation, which is the symptom it treats.
- **`owns`** is the narrower claim that "no other component could mean this file", and it
  is what `component_guidance_hook` refuses comments on. Leave it empty rather than
  widening it to a tree that contains other components: `browser/` as an `owns` value
  refuses ordinary desktop chrome. Longest match wins, so a nested `owns` is how two
  components divide one tree.
- **`notes`** is only for what the source docs do not say. If a sentence restates a doc
  page, delete it rather than paraphrase it; the whole point is that the docs are the
  copy that stays current.
- **`related`** ships another component's guidance alongside this one, for bugs that
  routinely turn out to be somewhere else.

`tests/test_plan.py` fails if the entry has no `trees`, if a `related` key does not
resolve, or if its `notes` name a path the citation hook would then refuse.

## Registration

Registered with `hackbot-api` as `FrontendTriageInputs` in
`services/hackbot-api/app/schemas.py` and a `frontend-triage` entry in
`app/agents.py` (job `hackbot-agent-frontend-triage`). Local Compose runs don't
need the API.

`tests/` covers what an unattended run's reach depends on: the record-time hooks
(`test_hooks.py`), the plan parsing and `may_apply_unattended` (`test_plan.py`), the
Slack message and its routing (`test_notify.py`), the docs derivation
(`test_docs.py`, whose real-checkout test needs `SOURCE_REPO`), the
`load_component_guidance` tool (`test_guidance.py`), and the pre-flight gate
(`test_preflight.py`). Run them with
`uv run --package hackbot-agent-frontend-triage pytest agents/frontend-triage/tests`.
CI covers the shared machinery this builds on via the `libs/agent-tools` and
`libs/hackbot-runtime` suites.
