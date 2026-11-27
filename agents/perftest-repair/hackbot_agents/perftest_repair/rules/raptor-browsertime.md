# Raptor / browsertime

These rules apply to raptor jobs, which nowadays run browsertime. Treeherder
groups and labels them under `browsertime` or `raptor`, for pageload, benchmark,
interactive and power tests, on desktop and Android.

## Where things are

- Harness: `testing/raptor/raptor/`. Its unit tests are in `testing/raptor/test/`;
  run them with `./mach python-test testing/raptor`.
- Test definitions are manifests under `testing/raptor/raptor/tests/`. They set
  the page or benchmark, page cycles, timeouts, playback recording and alert
  settings. Treat measurement settings as off limits (see `triage.md`).
- Browsertime support scripts (custom test scripts, pageload and interactive
  drivers) are under `testing/raptor/browsertime/`.
- Jobs are defined under `taskcluster/kinds/`. `git grep '<label fragment>'
taskcluster/` finds the kind and the exact raptor options CI passes.
- Run locally with `./mach raptor -t <test> [--browsertime]` plus the options from
  the task definition.

## Failure signatures

- Page load timeout (`TEST-UNEXPECTED-FAIL ... timed out` or similar). Find out
  why the page did not finish: Firefox crashed or hung, playback served nothing,
  or the page waits on something the recording lacks. Raising the timeout is
  not a fix.
- Playback (mitmproxy) errors: recordings are fetched from tooltool. Missing
  responses or a page needing live resources means the recording is stale. That
  is the owner's call (re-record), so report it instead of changing code.
- Live-site tests depend on third-party sites. A site change or outage is not a
  Firefox problem: report it, change nothing.
- Cross-browser variants (Chrome, Chromium, Safari) fail on driver or browser
  version mismatches. Check the toolchain fetch and the driver version in the
  task first.
- Visual metrics failures come from video processing. Check for missing or
  corrupt video, and for errors in the visual metrics script.
- Conditioned profile (condprof) failures happen while fetching or using the
  profile, before the test runs.
- Benchmark tests (Speedometer, MotionMark, JetStream, ...) failing inside the
  benchmark page are usually Firefox or benchmark-version problems, not harness
  ones.
