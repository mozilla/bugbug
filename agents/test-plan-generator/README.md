# Test Plan Generator Agent

Claude agent that writes manual-QA test cases for a Firefox feature, runs them
in Firefox Nightly, and records the plan with each case's result for TestRail.
It only tests: it does not fix or patch anything. Agent logic in
`hackbot_agents/test_plan_generator/`.

Run the Docker command below from the repo root, with `ANTHROPIC_API_KEY` in a
local `.env`.

## How it works

1. **Install Firefox:** downloads the latest mozilla-central Nightly at startup
   ([firefox_install.py](hackbot_agents/test_plan_generator/firefox_install.py)).
   There is no source checkout or build.
2. **Generate:** writes up to 30 test cases from the inputs, each with a title
   and ordered steps, before running any of them.
3. **Run:** executes the cases in order in headless Firefox through the
   [Firefox DevTools MCP server](hackbot_agents/test_plan_generator/devtools_mcp.py),
   using content tools for pages and privileged-context tools for browser UI,
   state and prefs (tool list in
   [config.py](hackbot_agents/test_plan_generator/config.py)). A case stops at
   its first failed step. Cases that need things the tools can't do (restarting
   Firefox, OS settings, real hardware, Sync, ...) are marked `unsuitable`; see
   the [system prompt](hackbot_agents/test_plan_generator/prompts/system.md).
4. **Record:** records exactly one `testrail.submit_test_plan` action. The run
   fails if it finishes without one.

## Input

- `FEATURE_NAME` - the feature under test; also names the TestRail suite.
- `FEATURE_DESCRIPTION` - what the feature does.
- `TEST_SCOPE` - what the generated cases must stay within.
- `MODEL`, `MAX_TURNS`, `EFFORT` (optional) - Claude settings.

The same fields are the agent's input schema in hackbot-api
(`TestPlanGeneratorInputs` in
[schemas.py](../../services/hackbot-api/app/schemas.py)).

## Output

- `summary.json` - the agent's final message in `findings.result`, plus the recorded
  `testrail.submit_test_plan` action: the feature, the generated cases (steps
  and a `passed` / `failed` / `unsuitable` result each) and an overall
  `summary`.

Nothing reaches TestRail during the run. When the action is applied,
[testrail_handler.py](../../services/hackbot-api/app/action_handlers/testrail_handler.py)
creates a new `[Hackbot] - <feature>` suite with the cases, then a run with
their results, using the action's `summary` as the run description. The agent
does not opt into auto-apply, so the action waits to be applied from the
Hackbot UI.

## Test the agent

```sh
FEATURE_NAME='Reader View' \
FEATURE_DESCRIPTION='Shows articles in a simplified, readable layout.' \
TEST_SCOPE='Entering and leaving Reader View on an article page.' \
  docker compose up test-plan-generator-agent --build
```

Artifacts are written to `~/hackbot/artifacts/`.
