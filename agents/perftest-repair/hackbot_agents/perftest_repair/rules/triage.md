# Triage of perf test failure bugs

These rules apply to every run.

## Reading the bug

Failure bugs are usually filed from Treeherder. The summary is typically
`Intermittent <job or test> | <failure line>`, or starts with `Perma` for a
permanent failure. The keyword `intermittent-failure` is normally set.

- The first comment names the job, the push and links the full and parsed logs.
- Later automated comments report the failure rate ("N failures in M pushes")
  with a platform breakdown and a link to Treeherder's intermittent-failures view.
  Use the most recent ones: an old failure that stopped months ago is not worth a
  patch.
- The whiteboard may carry a `[stockwell ...]` tag. Typical values are
  `needswork`, `disable-recommended`, `unknown` and `infra`. It records what the
  sheriffs have concluded so far, but it is not proof.

## Permafail or intermittent

- **Permafail:** every recent run of the job fails the same way, starting at
  one push. Use `treeherder-cli <rev> --repo <project> --filter '<job>'
--lookback 50 --suspects | head -80` to find the push it started in. The cause
  is usually a change in that push. Look at the test, the harness, the
  taskcluster config and Firefox itself. Name the bug it landed under in
  `regressor_bug`.
- **Intermittent:** the job passes on most runs of the same push or of
  neighbouring pushes. `--similar-history <job id>` gives the pass rate.
- **Unknown:** say so rather than guess.

## Where the failure lives

- **infra:** the job never got to measure Firefox. Common signs include an
  Android device that is offline or unresponsive (adb errors, "device not
  found"), and disk full or worker shutdown. Others are artifact, toolchain or
  tooltool downloads failing, and `Hit MaxRunTime` without any test progress
  before it. A failure confined to one machine pool or worker type is usually
  infra. Nothing in the tree fixes these: diagnose them in the summary, never with a
  change.
- **firefox:** Firefox itself misbehaves under the test, for example a crash,
  hang, assertion or error page. The perf job is just where it shows. Find the
  crash signature or the error. A small, clearly understood product fix may be
  suggested. Otherwise report what you found and, when there is one, the
  likely regressor.
- **harness:** the measuring code fails, independent of Firefox's behaviour. This
  covers mozperftest, raptor, browsertime scripts, talos or AWSY: for example a
  Python traceback, a race in waiting for the browser, a bad retry, or a
  result-parsing error. These are the most often fixable in the tree.
- **test:** the test definition itself is wrong or stale. Examples: a script
  relying on page content or UI that changed, a recorded page set that no longer
  matches, a test config or task definition with a wrong value, or a test that
  was not updated for an intentional product change.

## What is and is not a fix

A perf test exists to measure Firefox, and its results are compared over time. A
change that alters what or how it measures breaks that continuity, even when it
makes the job green.

Acceptable:

- Fixing a harness bug, race or crash in the measuring code.
- Making a wait condition correct, e.g. waiting for the right event rather than a
  fixed sleep.
- Updating a test for an intentional product change, while it measures the same
  thing.
- A targeted retry of a step that is not being measured (setup, download,
  device prep), with a cap.
- A small product fix for a clearly understood Firefox bug.

Not acceptable -- do not suggest these:

- Raising a timeout without evidence that the operation legitimately takes
  longer. A timeout is usually a symptom.
- Disabling or skipping a test, a platform or a subtest. The exception is when
  the bug is tagged `[stockwell disable-recommended]` and the harness ruleset
  says how to do it. Even then, say so prominently in the summary.
- Changing what is measured: the metrics, page cycles, iteration counts,
  thresholds, alerting config, or the page set. These are decisions for the
  test's owner.
- Retrying the measured operation until it passes.
- Changes outside the test, the harness and the failing code path.

If the right fix is one of the above, suggest no change; say in the summary
which it is, so the owner can decide.

## Who to point at

Many tests declare an owner: mozperftest scripts carry an `owner` field, and
raptor and talos tests have one in their config or docs. A summary naming
the right team is more useful than a guessed patch.
