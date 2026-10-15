# Perftest Repair Agent

Claude agent that works on intermittent failures in Firefox's performance test
jobs (mozperftest, raptor/browsertime and talos; it also has rules for AWSY). Given
the Bugzilla bug tracking a failure, it fetches the failure logs, works out whether
the failure is in the test, the harness, Firefox or the infrastructure, and
suggests a fix when it is confident of one, pushing it to try with retriggers. It
reports every run to Slack. It reads Bugzilla but never writes to it, and submits
no patches. Agent logic is in `hackbot_agents/perftest_repair/`.

## How it works

First, `recent.py` checks Treeherder's intermittent failure data (what the
Intermittent Failures / OrangeFactor graph shows). A bug with no failures in the
last 7 days is skipped: no session, no try push, no Slack message. If it stays
gone it stays quiet; if it comes back, the robot's next failure comment triggers a
new run. If Treeherder cannot answer, the run goes ahead.

Otherwise, one session, driven by `prompts/investigate-and-fix.md`:

1. Read the bug and its Treeherder comments through the broker's Bugzilla tools;
   stop if it is closed or already has a patch or an owner working on it.
2. Load `rules/triage.md` and the ruleset for the failing harness.
3. Fetch a failure log with `treeherder-cli` (or the bug's log links) for every
   failing job and platform the bug tracks, not only the first. A run that cannot
   retrieve one fails rather than guessing.
4. Classify: permafail or intermittent; cause test, harness, firefox or infra.
5. Suggest a fix: for a confident in-tree fix, leave the smallest change
   uncommitted in the working tree, where the runtime captures it as the run's
   patch, and record one `try_server.push` of it that runs each failing job 3
   times for a permafail or 10 for an intermittent (`TRY_REBUILD_*`). Otherwise change nothing and push nothing; the
   diagnosis is the result.

Once the session ends, `__main__.py` posts the run's report to
`#perftest-intermittent-hackbot` (`notify.py`): the bug, the verdict, the agent's
`summary.md`, the try push's Treeherder link, and a link to the run, which holds
the analysis and any patch. The wording is code, not a model turn.

hackbot-api applies the try push and then the Slack message without review. The
message carries the link as `{{actions.try.url}}`, which hackbot-api fills in from
the applied push.

People read the try results on Treeherder and can retrigger more from there.

`rules/triage.md` holds the policy that makes this a perf agent rather than a
generic one: what counts as a fix, and what does not -- raising timeouts without
evidence, disabling tests, or changing what is measured.

Besides `treeherder-cli`, the image carries
[`car-mechanic`](https://github.com/92kns/car-mechanic-cli), which matches
Chromium-as-Release (`custom-car`) failures against known breakage patterns;
`rules/custom-car.md` says when and how the agent uses it. Both are built from
source in the `Dockerfile` and pinned, car-mechanic to a git commit since it is not
on crates.io.

By default no Firefox build is made (`SKIP_FIREFOX_BUILD`): most fixes are to
harness or test scripts, and many failures are Android, Mac or Windows only. The
agent runs the harness's Python unit tests where they cover its change and says
the fix is otherwise unverified. With `SKIP_FIREFOX_BUILD=false` it builds an
optimized desktop Firefox and can run the job locally under Xvfb.

Bugzilla is read through the broker sidecar, which holds the API key; the agent
container holds none. The agent checks the broker serves the bug before the
session starts, and fails the run if it does not: without the broker it would have
no Bugzilla tools, and would fall back to the public REST API, which cannot read
restricted bugs.

## Trigger

hackbot-api starts a run when the Intermittent Failures Robot
(`orangefactor@bots.tld`) posts its failure count on an open, public bug with the
`intermittent-failure` keyword in Testing :: AWSY, Raptor, Talos or mozperftest. A new
bug is not picked up on filing, but once the robot first reports failures on it.
See the Bugzilla webhook section of
[docs/hackbot/triggers.md](../../docs/hackbot/triggers.md). A run can also be
started by hand from the Hackbot UI.

## Input

- `BUG_ID` - the failure bug.
- `SKIP_FIREFOX_BUILD` (optional, default `true`).
- `MODEL`, `MAX_TURNS`, `EFFORT` (optional).

## Output

- `summary.md`, `analysis.md` (published as run artifacts)
- `findings`: `failure_type`, `harness`, `cause`, `regressor_bug`, `confidence`,
  and `outcome` (`fix` / `diagnosis`, derived from whether the working tree
  changed, not from what the model says; or `skipped` when the bug had no recent
  failures)
- The suggested fix, as the run's patch artifact, when `outcome` is `fix`.
- Recorded actions: `slack.post_message`, and with a fix `try_server.push`.
  hackbot-api applies both automatically for this agent.

## Test the agent

With `ANTHROPIC_API_KEY`, `BUGZILLA_API_URL` and `BUGZILLA_API_KEY` in the
repo-root `.env`:

```sh
BUG_ID=1234567 docker compose up perftest-repair-agent --build
```

Artifacts are written to `~/hackbot/artifacts/`. A local run records the try push
and the Slack message but performs neither: only hackbot-api applies actions.
