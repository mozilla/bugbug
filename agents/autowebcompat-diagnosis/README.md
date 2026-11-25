# Autowebcompat diagnosis agent

Diagnoses why a site broken in Firefox works in Chrome: confirms the issue
still reproduces, then finds the root cause and, for an engine difference,
writes a reduced HTML testcase. It is read-only and records no actions. Agent
logic in [`hackbot_agents/autowebcompat_diagnosis/`](hackbot_agents/autowebcompat_diagnosis/agent.py).

## How it works

Up to three Claude tasks, run in order, each reporting through `submit_result`
([`result.py`](hackbot_agents/autowebcompat_diagnosis/result.py)):

1. **Plan:** reads the report, extracts the URL and reproduction steps, picks
   the Firefox channel (from an `autowebcompat-repro-channels:` line in the
   bug's user story or the report text, preferring Nightly), and downloads the
   Puppeteer reproduction script attached to the bug, if any.
2. **Reproduce:** installs that Firefox channel and Chrome stable. If the
   attached script still exits 1 in Firefox and 0 in Chrome, that settles it
   without a Claude task. Otherwise Claude reproduces the steps in both
   browsers through the Firefox and Chrome DevTools MCP servers and writes a
   new script following [`repro_reference.mjs`](repro_reference.mjs). The run
   stops here if the issue doesn't reproduce, with a `failure_reason`
   (`not_firefox_specific`, `login`, `blocked_captcha`, ...).
3. **Diagnose:** compares the two browsers on the site and forms a root-cause
   hypothesis with evidence. If the difference is an engine difference rather
   than the site switching behaviour per browser (e.g. UA sniffing), it writes
   a reduced testcase and checks it shows the same difference in both browsers.

The agent never sees Bugzilla credentials: a [broker sidecar](hackbot_agents/autowebcompat_diagnosis/broker.py)
serves the read-only Bugzilla MCP tools listed in
[`config.py`](hackbot_agents/autowebcompat_diagnosis/config.py).

## Input

- `BUG_ID` or `BUG_DATA`: a Bugzilla bug, or the report as inline text.
  `BUG_DATA` wins if both are set.
- `MODEL`, `MAX_TURNS`, `EFFORT` (optional): Claude settings.
- `HEADLESS` (optional, default `false`): run the browsers without Xvfb.

## Output

`summary.json`'s `findings.result`:

- `reproduced` and `failure_reason`
- `root_cause` and `evidence`, formatted to be posted as a Bugzilla comment
- `testcase_url`: the published `testcase.html`, or null

The run log is published as `logs/agent.log`.

## Run locally

With `ANTHROPIC_API_KEY`, `BUGZILLA_API_URL` and `BUGZILLA_API_KEY` in a
repo-root `.env`, from the repo root:

```sh
BUG_ID=1234567 docker compose up autowebcompat-diagnosis-agent --build
```

The local compose file forwards only `BUG_ID` and `BUG_DATA`. Artifacts land
in `~/hackbot/artifacts/<run_id>`.
