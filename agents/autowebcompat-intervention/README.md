# Autowebcompat intervention agent

Writes a Firefox webcompat intervention for a broken-site bug: confirms the
breakage reproduces, adds the intervention and a test, verifies the fix, and
records a Phabricator patch.

## Stages

1. **Plan** (Claude): reads the bug, downloads its Puppeteer reproduction
   script, and works out the affected platforms.
2. **Reproduce** (Python): runs the script against Firefox Nightly; exit 1
   means the breakage reproduces.
3. **Intervention** (Claude, in a Firefox checkout): writes the intervention,
   does an artifact build, re-runs the script, checks the page with the
   DevTools MCP, adds a test, lints, and records `phabricator.submit_patch`.
4. **Verify** (Python): if the agent claims a fix, re-runs the script against
   the build. The patch is dropped if the script doesn't exit 0
   (`verification_failed`), or if any changed file is outside
   `browser/extensions/webcompat/` and `testing/webcompat/`
   (`unexpected_changes`). The same path check also runs when the patch is
   recorded, so the agent can retry.

The run stops early, with a `failure_reason`, when:

- Linux isn't among the affected platforms, as interventions are verified
  on Linux desktop: `unsupported_ios` (iOS only), `unsupported_android`
  (Android only) or `unsupported_desktop_os` (e.g. Windows or macOS only);
- the bug has no reproduction script (`no_puppeteer_script`);
- the script doesn't reproduce the breakage, or gives no verdict
  (`not_reproducible`).

The intervention rules come from the Firefox tree:
`browser/extensions/webcompat/AGENTS.md` is inserted into the Intervention
prompt.

## Input

- `BUG_ID` or `BUG_DATA`: a Bugzilla bug, or the report as inline text. At
  least one is required.
- `MODEL`, `MAX_TURNS`, `EFFORT` (optional): Claude settings.
- `HEADLESS` (optional, default `false`): run Firefox without Xvfb.

## Output

The run summary's `result` contains:

- `plan`: `url`, `affected_platforms`, `script_path`;
- `result`: `wrote_intervention`, `build_succeeded`,
  `fixed_with_intervention`, `failure_reason`, `summary`;
- `files_changed`: taken from `git`, not reported by the model.

The recorded patch is the checkout's final diff, published as
`changes/changes.patch`.

## Run locally

With `ANTHROPIC_API_KEY`, `BUGZILLA_API_URL`, `BUGZILLA_API_KEY`,
`PHABRICATOR_URL` and `PHABRICATOR_API_KEY` in a repo-root `.env`, from the
repo root:

```sh
BUG_ID=1234567 docker compose up autowebcompat-intervention-agent --build
```

A local run records the Phabricator patch but doesn't submit it; only
production applies recorded actions. Artifacts land in
`~/hackbot/artifacts/<run_id>`; apply the patch to a Firefox checkout with
`git am ~/hackbot/artifacts/<run_id>/changes/changes.patch`.

The Firefox checkout and its objdir are kept in a Docker volume, so later runs
skip the full clone. To start from a clean checkout:

```sh
docker compose -f agents/autowebcompat-intervention/compose.yml down -v
```
