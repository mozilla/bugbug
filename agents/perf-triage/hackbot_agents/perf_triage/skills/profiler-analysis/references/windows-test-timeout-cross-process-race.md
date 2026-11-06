# A mochitest timeout that was really a 14-millisecond cross-process race

`browser_creditCard_telemetry_manage.js` failed every time on Windows when the test ran with the Gecko profiler enabled (`MOZ_PROFILER_STARTUP=1`), reporting `test_histogram - Test timed out`. The same test was already ~5% intermittent on Windows without the profiler. A startup profile of one failing run was attached, so I went looking for *what the test was waiting on*.

## It's a wait, not a spin

The first instinct with a "hang" is to find the thread pegged at 100% doing the wrong thing. This was the opposite. The profile was 49 seconds long — exactly a harness timeout — and the aggregate CPU activity tapered off after about 12 seconds and then flatlined. (The per-thread CPU column read `0.000ms` for everything, which is just a missing-data artifact of this capture; it nearly fooled me into thinking I had no signal.)

Selecting the parent-process GeckoMain — the thread the mochitest driver runs on, not the WebExtensions thread the daemon picked by default — and sampling **with idle included** told the real story: 88.8% of wall-clock was parked in `nsAppShell::ProcessNextNativeEvent::Wait`. The test wasn't doing anything. It was blocked on an event that never came. From that point the question changed from "what is slow" to "what is it waiting for, and why does that thing never happen."

## The gap names the culprit

Mochitests narrate themselves through `INFO`/`Test` markers, and the harness drops a `TEST-UNEXPECTED-FAIL | Test timed out` marker when it gives up. Listing the test markers showed a clean wall: the last meaningful one was `focus on element (id=cc-number)` at 12.825s, then 36 seconds of dead air until the timeout at 49.229s. The marker stack for the timeout itself was just `setTimeout → TimeoutExecutor` — the harness alarm, not the bug. The bug was wherever "focus on element" left the test stuck.

That marker is logged by `focusUpdateSubmitForm` in the formautofill test `head.js`, which focuses the field and then `await`s a `fieldsIdentifiedPromise` that only resolves when the **content** process sends a `FieldsIdentified` notification. The next step, "submit form", never logged. So the content process never sent the message.

## Two processes, one timeline

The active tab's content process was the one sitting at `Process Priority: FOREGROUND`. Its markers around 12.8s showed the focus landing on `#cc-number` at 12.824s, a `clearFillOnFormChangeTimeoutID` timer firing at 12.838s, and then nothing but idle housekeeping. A search for `FieldsIdentified` anywhere on that thread came up empty — the notification was genuinely never sent.

Lining the two processes up on one timeline made the 14-millisecond gap obvious. After an autofill, `FormAutofillChild` opens a "dynamic form change" window and, by design, **suppresses field identification** for any focus that lands inside it. The test waits out that window with its own timer before focusing — but the test's timer lives in the parent and the suppression window's clear-timer lives in content, both around 1000 ms, anchored at slightly different moments. The focus arrived 14 ms *before* the content cleared the window, so identification was suppressed, no `FieldsIdentified` was sent, and the parent waited forever.

That explained everything: a near-tie race that normally loses ~5% of the time, and that the profiler's per-sample overhead in the content process tips consistently to the losing side — hence the perma-fail under `MOZ_PROFILER_STARTUP=1`. The fix was to disable the heuristic in the tests that don't exercise it (which also let us delete the now-pointless 1 s wait, speeding the tests up) and re-enable the test on all platforms.

## What made this fast

- Sampling the **right** thread **with idle included** immediately classified it as a deadlock and saved me from chasing hot functions that didn't exist.
- The **last marker before the dead air** pointed straight at the offending line of test code.
- **Cross-process timestamp correlation** was the only thing that could expose a 14 ms race — no single thread's view contains it. That technique generalizes to any "event that never fired" timeout.
