# Uplift Agents

Home for the backport/uplift agents that help land patches on Firefox's
stable branches (release/beta/esr). One Cloud Run image
(`hackbot-agent-uplift`); each agent is a module under `hackbot_agents/`
with its own entrypoint, job and registry entry. Module names are prefixed
`uplift_` because the runtime traces under the directory holding `__main__.py`,
which has to match the registry name.

## Merge-conflict resolver

`hackbot_agents/uplift_merge_conflict_resolver/` — the first of them. It
reproduces a failed uplift cherry-pick on the target branch, resolves the
conflicts, and returns the patch with a confidence level for review. It never
pushes or lands anything, and it does not build Firefox.

### Input

Set per run (see `UpliftInputs` in hackbot-api for the full schema):

- `TARGET_BRANCH` — branch to uplift onto, e.g. `beta`, `esr128`.
- `TARGET_COMMIT` — optional commit on that branch, for reproducing a specific
  failed uplift. Branch names move; without one the tip is used.
- `SOURCES` — JSON list, applied in order; a stack is several entries. Each is
  `{"kind": "git", "commit": "<sha>"}` or
  `{"kind": "phabricator", "revision_id": 12345, "diff_id": 67890}`, where
  `diff_id` is optional and defaults to the revision's latest.
- `BUG_ID` — optional Bugzilla bug for context.

`BUGBUG_MCP_URL` and `BROKER_URL` are deploy-time constants: the bugbug MCP
server, and the sidecar holding the Conduit key the agent fetches diffs through.

### Output

- `changes/changes.patch` — the resolved uplift, as an mbox preserving each
  commit's message and author. This is what a caller consumes.
- `report.json` — `resolved`, `confidence`, `conflicts`, `unresolved`,
  `verification_failures`. `resolved` is not taken on trust: the run checks the
  checkout for conflicted paths, an unfinished cherry-pick, uncommitted work
  and whether the commits would collect to a patch at all, and overrules the
  claim if they disagree. Confidence stays the agent's own judgment.
- `report.unverified.json` — the same report as the agent wrote it, before
  those checks. For debugging; read `report.json`.
- `summary.md` — what conflicted and how it was resolved, for a reviewer.

The run summary also records `base_commit` (what the patches were applied onto)
and `requested_sources` (what was asked for and what each source resolved to).

### Run locally

With `ANTHROPIC_API_KEY`, `BUGZILLA_API_KEY` and `PHABRICATOR_API_KEY` in a
repo-root `.env`, from the repo root:

```sh
TARGET_BRANCH=beta \
SOURCES='[{"kind":"git","commit":"<sha>"}]' \
  docker compose up uplift-merge-conflict-resolver --build
```

Artifacts land in `~/hackbot/artifacts/<run_id>`; apply the patch with
`git am changes/changes.patch`.

### Tests

```sh
uv run --package hackbot-agent-uplift --extra test pytest agents/uplift/tests
```
