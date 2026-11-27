# Perftest-repair rules

`triage.md` applies to every run. The other files are one ruleset per perf harness;
the agent reads only the one matching the failing job.

Rules are free-form prose. Start each file with when it applies. Tree paths and
commands in them are starting points the agent confirms with `git grep`, so a moved
file costs it a search rather than sending it the wrong way -- but keep them current.

This is where the perf test team's knowledge goes: the failure signatures you
recognise on sight, which ones are infra, and the fixes you would and would not
accept.
