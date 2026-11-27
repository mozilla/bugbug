# Chromium-as-Release (CaR)

Load this alongside the harness ruleset when the failing job involves CaR, Mozilla's
own build of Chromium that browsertime measures as the `custom-car` app: a
browsertime job with `custom-car` in its label, or a `toolchain-*custom-car*` task
it depends on.

CaR is built in CI by toolchain tasks (`taskcluster/kinds/toolchain/custom-car.yml`,
`taskcluster/scripts/misc/build-custom-car.sh`), from upstream Chromium. So a CaR
failure is usually one of:

- **the toolchain build broke**: an upstream Chromium change, an SDK or toolchain
  bump, or our build config. The perf job then fails because there is no CaR
  binary, and the real error is in the toolchain task's log, not the perf job's.
- **CaR itself misbehaves under the test**: a crash or hang in the Chromium
  binary, which is upstream's to fix, not ours.
- **the harness mishandles CaR**: browsertime or raptor code specific to
  `custom-car`, which is ours.

## car-mechanic

`car-mechanic` diagnoses CaR failures against known breakage patterns. Use it
before reasoning from scratch. Always pass `--json`.

1. `car-mechanic diagnose --json --url <task id>`: matches the failure log of a CaR
   task against known patterns. Give it the toolchain task when the toolchain
   failed. It returns the cause, fix steps, related bugs and upstream tracker
   links. A pattern tagged `[retry first]` is transient: that is infra, and
   nothing in the tree fixes it.
2. When `diagnose` matches nothing, `car-mechanic triage --json --url <task id>`:
   the failing phase, which platforms failed, whether the cause is upstream or our
   config, and the last good line before the error.
3. `car-mechanic explain --json <pattern id>` for a matched pattern's full detail,
   `car-mechanic search '<query>'` to read upstream Chromium source, and
   `car-mechanic check --json <platform>` for the live CaR config in the checkout.

Never run `car-mechanic update`: the version is pinned in the image.

## What counts as a fix

- A change to our CaR build config or `build-custom-car.sh` that a matched pattern's
  fix steps describe, or that `triage` attributes to our config.
- A harness fix for `custom-car`-specific code.

An upstream Chromium breakage, or a crash in the CaR binary, is not fixable in the
tree: report it in the summary with the upstream tracker link, and change nothing.
