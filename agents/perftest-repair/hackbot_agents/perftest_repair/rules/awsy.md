# AWSY (Are We Slim Yet)

These rules apply to AWSY memory test jobs, labelled `awsy` on Treeherder (base
and tp6 variants).

## Where things are

- Harness and tests: `testing/awsy/`. They are Marionette-driven tests that open
  pages, take memory reports at checkpoints and process them.
- Jobs are defined under `taskcluster/kinds/test/`. `git grep awsy
taskcluster/kinds/test/` finds them.
- Run locally with `./mach awsy-test` plus the options from the task definition.

## Failure signatures

- A Marionette error or timeout while loading pages, or between checkpoints.
  First check for a Firefox crash or hang; that makes it a firefox cause.
- Failure collecting or processing memory reports: a harness problem, unless
  Firefox crashed while writing the report.
- tp6 variants use recorded page sets. Playback errors there are the same story
  as raptor's: a stale recording is the owner's call, so report it.
