# profiler-cli walkthrough: a mochitest timeout caused by a cross-process timer race

Companion to `windows-test-timeout-cross-process-race.md`. Reproduces the findings using `profiler-cli` with annotated output.

This is the **opposite** of the CPU-pegged hangs (`macos-extensions-hang-infinite-recursion`, `macos-ipc-blob-url-accumulation`): the main thread is almost entirely **idle**. It is deadlocked on a promise that never resolves, not spinning. Reach for this playbook whenever a test fails with `TEST-UNEXPECTED-FAIL | ... | Test timed out` and a startup-profiled run is attached.

Scenario: `browser_creditCard_telemetry_manage.js` perma-failed on Windows with `MOZ_PROFILER_STARTUP=1` (`test_histogram - Test timed out`), and was ~5% intermittent without the profiler.

---

## Load and get an overview

```
profiler-cli load <profile-url> --session timer-race
profiler-cli profile info --session timer-race
```

```
Name: Firefox 153 – Windows 11
This profile contains 23 threads across 16 processes.

Top processes and threads by CPU usage:
  p-0: Parent Process [pid 5692] - 0.000ms
    t-0: GeckoMain [tid 6568] - 0.000ms
    ...
  p-13: WebExtensions [pid 4708]
    t-17: GeckoMain [tid 8108]      ← daemon auto-selected this; NOT the thread we want

CPU activity over time:
- 25% for 7216.5ms: [ts-01 → ts-N] (2.597ms - 28.856s)
  - 100% for 5389.9ms: ... (2.597ms - 5.384s)
  ...
```

Two things to internalize immediately:

1. **Every per-thread CPU number is `0.000ms`.** Do not conclude "nothing ran." Some captures (often Windows) don't carry per-thread CPU deltas, so this column is meaningless here. The aggregate "CPU activity over time" section *does* have real data, and it shows activity tapering off after ~12s in a 49s profile — the classic shape of a test that did work, then stalled until the harness timeout. Pivot to `--include-idle` samples and markers; ignore the zero column.

2. **The auto-selected thread (`t-17`) is rarely the one you want.** For a browser mochitest, the test driver runs on the **parent process GeckoMain** (`t-0` here). Select it explicitly.

---

## Step 1 — Spin or wait? Look at the main thread with idle included

```
profiler-cli thread select t-0 --session timer-race
profiler-cli thread samples --include-idle --session timer-race
```

```
Thread: Parent Process

Top Functions (by total time):
  f-5.    XREMain::XRE_main - total: 3091 (99.8%)
  f-1633. nsAppShell::ProcessNextNativeEvent::Wait - total: 2748 (88.8%)
  f-1634. win32u.dll!ZwUserMsgWaitForMultipleObjectsEx - total: 2741 (88.5%)
  ...
```

**88.8% of wall-clock is parked in a native event-loop wait.** This is a deadlock, not a busy-loop — so stop hunting for hot functions. The question is no longer "what is burning CPU" but "what event is the main thread blocked waiting for, and why does it never arrive."

Wait-primitive frames that mean "idle, blocked": `ProcessNextNativeEvent::Wait`, `PR_Wait`, `ZwWaitForAlertByThreadId`, `poll`/`epoll_wait`, `CVStateMonitor`. If you see one dominating with `--include-idle`, you're in this case study.

---

## Step 2 — Locate the stall: the timeout marker, then the last marker before it

The harness logs a marker when it gives up. Find it, then find the last thing that happened before the dead air. Test-progress markers live in the `Test` category:

```
profiler-cli thread markers --category Test --list --session timer-race
```

```
  ...
  m-236  SpecialPowers   t=12.823s   7.732ms   ✗  Spawn
  m-237  SpecialPowers   t=12.824s   45.600μs  ✗  ProxiedAssert
  m-238  INFO            t=12.825s   instant   ✗  focus on element (id=cc-number)
  m-107  TEST-UNEXPECTED-FAIL  t=49.229s  instant  ✓  Test timed out
```

There it is: the last meaningful marker is at **12.825s** (`focus on element (id=cc-number)`), then **36 seconds of nothing** until the timeout at 49.229s. The gap *is* the hang, and that last marker names exactly where in the test source execution stopped — the test focused `#cc-number` and then blocked.

> Tip: don't shell-filter the marker list by timestamp with `awk`/`grep` — marker times mix units (`t=13.867ms` vs `t=12.825s`) and you'll get garbage. Use `profiler-cli zoom push <start>,<end>` to the gap and re-run marker/sample commands inside the zoom, or `thread markers --list` and read the tail.

Confirm the timeout is just the harness alarm, not the bug:

```
profiler-cli marker stack m-107 --session timer-race
```

```
  [4] resource://testing-common/StructuredLog.sys.mjs!testStatus
  [6] chrome://mochikit/content/browser-test.js!timeoutFn
  [8] setTimeout handler
  [9] nsTimerImpl::Fire
  [10] Task TimeoutExecutor Runnable
```

A `setTimeout`-driven harness timeout — expected. The real bug is upstream, at 12.825s.

---

## Step 3 — Map the stall marker to the test code

The marker text (`focus on element (id=cc-number)`) is logged by `focusUpdateSubmitForm` in the formautofill test `head.js`. Reading it shows the structure:

```js
let fieldsIdentifiedPromise = new Promise(resolve => { ... addMessageObserver(observer); });
await SpecialPowers.spawn(target, ..., () => { element.focus(); });   // logs "focus on element"
...
await fieldsIdentifiedPromise;     // <-- blocks here; next step "submit form" never logged
```

So the parent is waiting for a `FieldsIdentified` notification from the **content** process that never comes. Now we need the content side.

---

## Step 4 — Find the foreground tab's content process

Don't scan all 12 content threads by hand. The active tab's content process is the one at **`Process Priority: FOREGROUND`** at the time of interest. A quick sweep of each content GeckoMain's last marker / priority finds it:

```
t-14: last marker  m-150937  Process Priority  t=49.231s  priority: FOREGROUND   ← the tab with the form
```

```
profiler-cli thread select t-14 --session timer-race
profiler-cli thread markers --list --session timer-race  # read the window around 12.8s
```

```
  m-158802  DOMEvent  t=12.824s  ✗  focus - input id="cc-number"          ← the focus landed
  m-158848  Runnable  t=12.838s  ✗  setTimeout() for prepareFillingFieldsOnFormChange/
                                       clearFillOnFormChangeTimeoutID<[FormAutofillChild.sys.mjs]
  ... only LayerActivityTracker / IdlePurge / GC after this — content goes idle
```

A search for the awaited message across the whole content thread returns **nothing** — the notification was never sent:

```
profiler-cli thread markers --search "FieldsIdentified" --list --session timer-race  # (empty)
```

---

## Step 5 — The smoking gun: correlate timestamps across the two processes

Line up the two threads' absolute timestamps:

| Time | Process | Event |
|------|---------|-------|
| 12.824s | content (t-14) | `#cc-number` receives focus |
| **12.838s** | content (t-14) | `clearFillOnFormChangeTimeoutID` fires — clears the "dynamic form change" threshold |

The focus arrived **14 ms before** the threshold cleared. Reading `FormAutofillChild.identifyFieldsWhenFocused` confirms the consequence: while still within that threshold it deliberately **bails out and sends no `FieldsIdentified`**. The parent's `await fieldsIdentifiedPromise` therefore never resolves.

It's a near-tie race between a parent-side wait timer and a content-side clear timer, both ~1000 ms but anchored at different moments in different processes — so it loses ~5% of the time normally, and **the profiler's content-process overhead pushes the clear consistently past the focus**, making it deterministic. That cross-process timestamp correlation is the only way to see a 14 ms race; no single thread's view reveals it.

```
profiler-cli stop --session timer-race
```

---

## The reusable playbook for "Test timed out"

1. **Select the right thread.** For a browser mochitest it's the parent GeckoMain (`t-0`), not the auto-selected one. Ignore the per-thread CPU column if it's all `0.000ms`.
2. **Spin vs. wait:** `thread samples --include-idle`. A dominant wait primitive (`ProcessNextNativeEvent::Wait`, `PR_Wait`, ...) means deadlock — stop looking for hot functions.
3. **Find the dead air:** the timeout marker minus the last meaningful marker before it = the hang. That last marker names where execution stopped. Use `zoom`, not `awk`, to navigate the timeline.
4. **Find what never fired:** identify the foreground content process (`Process Priority: FOREGROUND`) and correlate markers across processes **by absolute timestamp**. A gap that closes a few ms on the wrong side of an event is a cross-process race.
