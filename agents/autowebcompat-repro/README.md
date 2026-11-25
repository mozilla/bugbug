# Autowebcompat repro agent

Checks whether a broken-site report is a Firefox webcompat issue and reproduces
it in Firefox Nightly, stable and ESR, cross-checking in Chrome. It records
no actions; the findings are the result. Agent logic is in
`hackbot_agents/autowebcompat_repro/`.

## How it works

The run is a chain of Claude tasks in [agent.py](hackbot_agents/autowebcompat_repro/agent.py).
Each task reports through `submit_result`, shaped by
[result.py](hackbot_agents/autowebcompat_repro/result.py). All of them share
[prompts/system.md](hackbot_agents/autowebcompat_repro/prompts/system.md).

1. **Plan:** reads the report and decides whether it is a webcompat issue and
   which platforms and channels it affects. The run stops here if it isn't a
   webcompat issue or only affects iOS.
2. **Reproduce in Nightly:** drives the reported site with the Firefox and
   Chrome DevTools MCP servers. If the issue reproduces, the agent writes a
   Puppeteer script following [repro_reference.mjs](repro_reference.mjs) that
   exits 1 in Firefox and 0 in Chrome. For visual breakage it also takes a
   screenshot. The run stops if the issue also reproduces in Chrome
   (`not_firefox_specific`), or if it doesn't reproduce and only affects
   Android.
3. **Other channels:** tries stable, then ESR, based on the plan's channels.
   ESR is always tried once the issue has reproduced, as a regression baseline.
   After a reproduction, the script is rerun first. If it doesn't exit 1, a
   task replays the recorded steps.
4. **Chrome Mask:** reruns the steps in the channel that first reproduced the
   issue, with the [Chrome Mask](https://addons.mozilla.org/firefox/addon/chrome-mask/)
   extension spoofing a Chrome user agent. This checks whether a UA override
   would fix the site. The profile is built by
   [setup_profile.py](hackbot_agents/autowebcompat_repro/setup_profile.py).

Firefox and Chrome for Testing are downloaded at startup by
[libs/autowebcompat-tools](../../libs/autowebcompat-tools/autowebcompat_tools/browser.py).
Bugzilla is read through a [broker sidecar](hackbot_agents/autowebcompat_repro/broker.py)
that holds the API key.

## Input

- `BUG_DATA` or `BUG_ID`: the report as inline text, or a Bugzilla bug.
  `BUG_DATA` wins if both are set.
- `MODEL`, `MAX_TURNS`, `EFFORT` (optional): Claude settings.
- `HEADLESS` (optional, default `false`): run the browsers without Xvfb.

## Output

`summary.json`'s `findings.result`:

- `reproduced`, `summary`, `steps`: whether it reproduced in Firefox, a short
  summary, and the steps that reproduce it
- `failure_reason`: why it wasn't reproduced as a Firefox webcompat issue,
  e.g. `not_firefox_specific`, `login`, `blocked_captcha`; `null` when it was
- `chrome_reproduced`, `chrome_mask_fixed`: the Chrome cross-check and the
  Chrome Mask result
- `plan_result`: the plan's webcompat verdict, platforms, OSes and channels
- `reproductions`: the result for each channel tried
- `script_url`, `screenshot_url`: the published Puppeteer script and
  screenshot, when there are any

## Run locally

With `ANTHROPIC_API_KEY`, `BUGZILLA_API_URL` and `BUGZILLA_API_KEY` in a
repo-root `.env`, from the repo root:

```sh
BUG_ID=1234567 docker compose up autowebcompat-repro-agent --build
```

Only `BUG_ID`, `BUG_DATA` and `RUN_ID` are passed to the container by
[compose.yml](compose.yml). Artifacts land in `~/hackbot/artifacts/<run_id>`.
The Puppeteer script also works as a `mach mozregression --command` (see the
header of [repro_reference.mjs](repro_reference.mjs)).
