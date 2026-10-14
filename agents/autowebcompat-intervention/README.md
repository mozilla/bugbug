# Autowebcompat intervention agent

Writes a Firefox webcompat intervention for a broken-site bug: confirms the
breakage reproduces, adds the intervention and a test, verifies the fix, and
records a Phabricator patch.

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
