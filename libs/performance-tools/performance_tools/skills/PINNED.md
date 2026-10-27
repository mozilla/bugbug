# Vendored skill: `profiler-analysis`

## Source

- Repo: `github.com/mozilla-firefox/firefox` (mozilla-central)
- Path: `.claude/skills/profiler-analysis/`
- Pinned revision: `b77a9b0fd0fa7afa5d5caab5d02d644311453200`
- Revision date: 2026-08-14
- Revision subject: "Bug 2059026 - Improve session handling and evidence rigor in
  the profiler-analysis skill. r=canova,ai4dev-reviewers,padenot"
- Vendored on: 2026-08-21
- Vendored by: Patricia Lawless (`plawless@mozilla.com`)

The tree under `skills/profiler-analysis/` was copied verbatim from a mozilla-central
checkout at the revision above (21 files: `SKILL.md` + 20 files under `references/`).
It was verified byte-for-byte identical to the git blobs recorded in that commit — do
not hand-edit any file in this tree. It is someone else's actively maintained artifact;
we consume it, we do not fork it.

## Why vendored, not fetched at build/run time

A full mozilla-central tree checkout costs minutes and gigabytes per run. That is fine
for a full agent flow that already needs the tree for other reasons, but it is ruinous
for a triage sweep at well under $1/bug across hundreds of bugs — the checkout
would dwarf the actual cost of the work. Vendoring the skill directory (212K) into this
package means every consumer gets it for free as part of the normal `performance-tools` install,
with no extra network fetch, no extra checkout, and no dependency on mozilla-central
being reachable or clean at run time.

## Why pinned, not left to float

`profiler-analysis` is actively maintained: 9 commits in the 6 months before this pin,
from padenot, canova, florian, and ai4dev-reviewers. A pin means those improvements
arrive in a reviewable diff when we deliberately bump it — not silently (an unreviewed
behavior change landing in our agents) and not never (frozen at whatever state we
happened to vendor first).

## How to bump the pin

1. Update your mozilla-central checkout and find the new revision to pin.
2. Re-copy the tree verbatim:
   ```bash
   rm -rf libs/performance-tools/performance_tools/skills/profiler-analysis
   cp -R ~/src/firefox/.claude/skills/profiler-analysis \
       libs/performance-tools/performance_tools/skills/
   ```
3. Update the revision, date, and subject recorded in this file.
4. Run the tests: `uv run --package performance-tools pytest libs/performance-tools/tests/ -q`
5. Commit with the new SHA in the message, e.g.:
   `Bump vendored profiler-analysis skill to <sha>`

Bumping the pin is a deliberate, reviewable act, not something this task automates.

---

# Vendored skill: `triage-perf-bug`

## Source

- Repo: `github.com/mozilla/performance-triage-calculator`
- Path: `.claude/skills/triage-perf-bug/SKILL.md`
- Pinned revision: `c78430d` (`main`)
- Revision date: 2026-10-30
- Revision subject: "triage-perf-bug: profile wins a component disagreement; fix garbled .json.gz sentence (#17)"
- Vendored on: 2026-10-30
- Branch at time of vendoring: `main`

`SKILL.md`, `criteria.md`, `fetch_attachment.py` and `memory_report_summary.py`
were copied verbatim from the git blobs at the revision above. The two scripts are
new in `10f1237`: Step 3 downloads attached profiles and Step 3b summarises attached
about:memory reports with them. `fetch_attachment.py` fetches from
bugzilla.mozilla.org directly, without credentials, so it reaches public
attachments only and needs that egress from the agent container. Since `c78430d`
agent-mode Step 5 validates its JSON with `jq`, which the Dockerfile installs.
`criteria.md` is new in `478e3d3`: Step 2 of `SKILL.md` now
reads its attribute rules from it, so the skill is broken without it. The
Dockerfile copies the whole directory, so it ships with the skill.

`perf_triage.py` is vendored alongside it (unchanged between `9bca4f4` and `478e3d3`), into
`performance_tools/calculator/`. The skill shells out to it and cannot work without it.
Stdlib only (`json`, `sys`, `argparse`, `urllib.parse`, `typing`), so it needs no
packaging beyond the copy.

## `top_sites.json` — pages-affected from Firefox traffic (`a88861b`)

`criteria.md` now has the skill look up `--pages-affected` with
`perf_triage.py --classify-domain <host> --platforms ...` instead of judging it
against a hard-coded top-50 list. The lookup reads `top_sites.json` from the
script's own directory, so it is vendored into `calculator/` beside it and the
Dockerfile copies it to `/app/` next to `/app/perf_triage.py`. Without it the
lookup exits 1 and the skill has no way to pick a tier. The data is a snapshot
(`generated` inside the file); refreshing it is an upstream re-pin, not an edit here.

## Re-pinned from an unmerged branch — breaking change for callers

The previous pin (`f5b39ec`, 2026-08-25) was taken from the tip of the then-unmerged
`structured-output` branch. That branch has since landed on `main` as `c64320c`
unchanged in substance, and `9bca4f4` landed on top of it with one behavior change
that matters here: **`--reproducible` went from a bare boolean flag to a
three-state argument that requires a value** (`reproduced` / `not-reproduced` /
`not-attempted`, default `not-attempted`). The old bare-flag invocation now exits 2
instead of running. `--pages-affected` also gained an `unclear` choice, additive
and non-breaking.

Nothing in this package invokes `perf_triage.py` or passes `--reproducible`
directly — `PerfTriage` (`performance_tools/triage/task.py`) only points the agent at the
skill and lets it follow `SKILL.md`'s own instructions, which were re-vendored in
the same commit as this file. So the breaking change is absorbed by the re-vendor
itself; there was no separate call site to update.

`tests/test_triage_contract.py::test_the_skill_actually_implements_agent_mode` reads
the live `~/projects/performance-triage-calculator` checkout and is the drift alarm
for this pin; it is skipped when the checkout is absent.

## Why vendored rather than fetched

Same reason as `profiler-analysis`: a build that reaches the network is a build
that can fail or silently change. This one has a second reason — the source repo
is a sibling checkout on the developer's machine, outside this repo's Docker
build context, so it is not reachable from a `COPY` at all.

## Do not hand-edit

Unlike `profiler-analysis`, this skill IS ours to change — but change it
upstream, in `performance-triage-calculator`, then re-vendor. Editing the copy
here would fork the calibration that `perf-triage-validator`'s 90-bug corpus
measures, which is the whole reason step 1 consumes this skill instead of
reimplementing the flag mapping.

## Agent mode

Step 1 runs this skill with `PERF_TRIAGE_AGENT_MODE=1`, set by
`PerfTriage.agent_options()`. Without it the skill runs interactive mode: it
walks the profile a second time and returns four-backtick-fenced Markdown that
`TriageResult` cannot represent.
