# Talos

These rules apply to talos jobs, which are desktop only. Treeherder labels them
`talos-<suite>`, e.g. `talos-tp5o`, `talos-damp-*` or `talos-other`.

## Where things are

- Harness and tests: `testing/talos/talos/`. Each test is a class in `test.py`.
  The suites, which group tests into the jobs CI runs, are in
  `testing/talos/talos.json`. Test pages, add-ons and extensions are under
  `testing/talos/talos/tests/`.
- Some talos tests live with the code they measure. For example, DAMP is the
  DevTools test, under `testing/talos/talos/tests/devtools/`; its owner is
  DevTools, not perf.
- Jobs are defined under `taskcluster/kinds/test/`. `git grep talos
taskcluster/kinds/test/` finds them.
- Run locally with `./mach talos-test -a <test>` or `--suite <suite>`.

## Failure signatures

- `TEST-UNEXPECTED-ERROR | <test> | ...` from talos itself: read the message and
  the traceback above it. A harness exception is fixable in tree.
- The browser did not start, did not shut down, or timed out: look for a crash,
  hang or shutdown leak in Firefox's output earlier in the log before blaming
  talos.
- A DAMP subtest failing is almost always a DevTools test or DevTools product
  problem. Point at DevTools.
- Missing or truncated results usually come from an earlier exception, or from a
  test page that never reported.
