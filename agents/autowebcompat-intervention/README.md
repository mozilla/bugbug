# Autowebcompat intervention agent

Writes a Firefox webcompat intervention for a broken-site bug and records it as
a Phabricator patch.

## How it works

1. **Plan:** reads the bug, downloads the Puppeteer reproduction script
   attached to it, and works out which platforms are affected.
2. **Reproduce:** runs the script against Firefox Nightly. The run stops here
   if the breakage doesn't reproduce, or if it can't be verified (no script,
   or Linux isn't affected).
3. **Write the intervention:** in a Firefox checkout, following the rules in
   `browser/extensions/webcompat/AGENTS.md`: adds the intervention, builds
   Firefox, checks the fix with the script and on the page, adds a test, and
   records the patch.
4. **Verify:** re-runs the script against the build and checks the patch only
   touches webcompat files; the patch is dropped if either fails.

## Input

- `BUG_ID` or `BUG_DATA`: a Bugzilla bug, or the report as inline text. At
  least one is required.
- `MODEL`, `MAX_TURNS`, `EFFORT` (optional): Claude settings.
- `HEADLESS` (optional, default `false`): run Firefox without Xvfb.

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
