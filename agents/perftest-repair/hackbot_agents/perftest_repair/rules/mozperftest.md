# mozperftest

These rules apply to `perftest-*` jobs, which are run by mozperftest.

## Where things are

- Harness: `python/mozperftest/`. It is built from layers (system, browser,
  metrics); a traceback names the layer that failed.
- Its unit tests are in `python/mozperftest/mozperftest/tests/`. Run them with
  `./mach python-test python/mozperftest`.
- Tests are scripts, usually named `perftest_*.js` or `perftest_*.py`, spread
  through the tree, many under `testing/performance/`. Each declares metadata
  such as `name`, `owner` and `description`; the `owner` is who to point at.
  Scripts are registered in manifests listed by `PERFTESTS_MANIFESTS` in
  `moz.build` files. `git grep PERFTESTS_MANIFESTS` finds them.
- Jobs are defined under `taskcluster/kinds/perftest/`. Find the job by its
  Treeherder label with `git grep '<label fragment>' taskcluster/`. That gives
  the exact `mach perftest` command line, flavor and options CI uses.
- Run locally with `./mach perftest <path to script>` plus the options from the
  task definition.

## Failure signatures

- A Python traceback from `mozperftest` is a harness failure. Find the layer and
  the first frame inside the tree.
- A browsertime-flavor failure shows browsertime's own log in the task log.
  Errors from the test script itself, such as a selector not found or a JS
  exception, are usually test failures. Check whether the page or the UI it
  drives changed.
- Android jobs fail on device setup often. adb, device or install errors before
  the test starts are infra.
- No metrics or an empty results file, after the test seemingly ran: look for
  the earlier error. That missing data is a consequence, not the cause.
