# Bug fix agent

Claude agent that triages a Bugzilla bug, fixes it in a Firefox checkout and
records the fix as a Phabricator patch. It also answers `@hackbot` mentions on
Phabricator revisions and `needinfo?` requests on Bugzilla. Agent logic in
`hackbot_agents/bug_fix/`.

## How it works

Every run reads Bugzilla and Phabricator through a [broker](hackbot_agents/bug_fix/broker.py)
sidecar that holds the API keys, so the agent container has none. The agent can
edit the tree, build and run Firefox (a debug build, see
[`agent.py`](hackbot_agents/bug_fix/agent.py)), and delegate focused questions to a
read-only `investigator` subagent. General instructions are in
[`prompts/system.md`](hackbot_agents/bug_fix/prompts/system.md); the inputs select one
of three modes:

- **Triage and fix** ([prompt](hackbot_agents/bug_fix/prompts/triage-and-fix.md)):
  reproduces a defect, fixes and tests it, and submits a patch. A bug it can't
  reproduce gets a comment instead of a patch.
- **Phabricator follow-up** ([prompt](hackbot_agents/bug_fix/prompts/follow-up.md)):
  the run starts from the revision's latest diff and addresses the quoted comments:
  updates the revision for code or commit message changes, or replies on it for
  questions.
- **Bugzilla needinfo** ([prompt](hackbot_agents/bug_fix/prompts/bugzilla-needinfo.md)):
  works out what the developer asked for and acts on it. If the run records any
  action, it also records clearing the needinfo flag.

### Triage rules

Rulesets in [`rules/`](hackbot_agents/bug_fix/rules/) are free-form Markdown; the
agent reads only those relevant to the bug. See the
[rules README](hackbot_agents/bug_fix/rules/README.md) for how to write one.

### Triggers

hackbot-api starts follow-up runs from webhooks:

- An `@hackbot` mention on a Phabricator revision, unless autowebcompat-intervention
  filed it ([`phabricator_webhook.py`](../../services/hackbot-api/app/phabricator_webhook.py)).
- A `needinfo?` for the bot on a public Bugzilla bug
  ([`bugzilla_webhook.py`](../../services/hackbot-api/app/bugzilla_webhook.py)).

## Input

- `BUG_ID` - the Bugzilla bug.
- `REVISION_ID` and `COMMENT` (optional, together): follow up on a Phabricator
  revision, addressing the quoted comments.
- `BUGZILLA_NEEDINFO_FLAG_ID` and `COMMENT` (optional, together): answer a needinfo
  request. Can't be combined with `REVISION_ID`.
- `MODEL`, `MAX_TURNS`, `EFFORT` (optional): Claude settings.

## Output

The run's actions are recorded in `summary.json`, from those allowed in
[`config.py`](hackbot_agents/bug_fix/config.py): Bugzilla updates, comments,
attachments and new bugs, `phabricator.submit_patch` / `update_patch` /
`add_comment`, `try_server.push` for checks it couldn't run locally, and
`feedback.report_problem` when it is blocked.

In production, hackbot-api applies them automatically when the run succeeds, and
reports a run that records no action as an error
([`agents.py`](../../services/hackbot-api/app/agents.py)).

## Test the agent

With `ANTHROPIC_API_KEY`, `BUGZILLA_API_URL`, `BUGZILLA_API_KEY`, `PHABRICATOR_URL`
and `PHABRICATOR_API_KEY` in a repo-root `.env`, from the repo root:

```sh
BUG_ID=1234567 docker compose up bug-fix-agent --build
```

A local run records actions but doesn't apply them. Artifacts are written to
`~/hackbot/artifacts/<run_id>`.
