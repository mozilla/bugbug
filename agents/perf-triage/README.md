# perf-triage agent

Rates one Firefox performance bug with the Performance Impact Calculator (the
vendored `triage-perf-bug` skill, in agent mode) and writes the rating to the bug:
one comment, `cf_performance_impact`, and the calculator's keywords (added, never
removed). When the bug links a Firefox Profiler capture, the skill analyses it with
`profiler-analysis` first, so the profile can change the rating.

Design: `~/projects/docs/superpowers/bugbug/2026-10-21-perf-triage-agent-design.md`.

## Running it locally

```sh
cd agents/perf-triage
set -a; . ../../.env; set +a
export SOURCE_REF=$(git rev-parse HEAD)   # a build arg; without it the image ships stale code
docker compose build
BUG_ID=1839859 docker compose run --rm perf-triage-agent
```

Local runs record actions into `~/hackbot/artifacts/<run_id>/summary.json`; nothing
reaches Bugzilla unless hackbot-api applies it.

## Measured (2026-10-21, five live runs, record-only)

| Bug     | Mode                      | Profile | Rating    | Held?                                   | Cost  |
| ------- | ------------------------- | ------- | --------- | --------------------------------------- | ----- |
| 2079239 | full                      | none    | low 9.0   | held (reproducible, multiple-reporters) | $0.40 |
| 2080869 | full                      | 1       | high 45   | held (reproducible, pages-affected)     | $0.63 |
| 1839859 | comment-only (medium set) | 1       | low 7.2   | confident: disagreement comment         | $0.50 |
| 2041028 | comment-only (medium set) | 1       | medium 12 | agrees: nothing recorded                | $0.53 |
| 2061533 | full                      | 7       | low 1.8   | held (combination)                      | $0.51 |

$0.40-0.63/bug with or without a profile, 4 turns each. 3 of 3 full-mode runs were held.
