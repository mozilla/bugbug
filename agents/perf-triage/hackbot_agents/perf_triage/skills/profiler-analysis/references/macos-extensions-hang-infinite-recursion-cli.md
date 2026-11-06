# profiler-cli walkthrough: 1Password infinite recursion in WebExtensions process

Companion to `macos-extensions-hang-infinite-recursion.md`. Reproduces the same findings using `profiler-cli` with annotated output.

Profile: https://profiler.firefox.com/public/jek6z1sbxtybk77ptwk79yna774r6ab6g2t8n6g

---

## Load the profile and get an overview

```
profiler-cli load https://profiler.firefox.com/public/jek6z1sbxtybk77ptwk79yna774r6ab6g2t8n6g --session ext-recursion
profiler-cli profile info --session ext-recursion
```

```
Name: Firefox 143 – macOS 15.5.0
Platform: macOS 15.5.0

This profile contains 14 threads across 9 processes.

Top processes and threads by CPU usage:
  p-8: WebExtensions [pid 39769] [ts-A → end] - 4054.658ms   ← nearly all CPU
    t-13: GeckoMain [tid 48838071] - 4054.658ms
  p-0: Parent Process [pid 39753] [ts<0z → end] - 1565.397ms
    t-0: GeckoMain [tid 48837837] - 1061.705ms
    t-1: Renderer [tid 48837905] - 460.349ms
    ...
```

The story is immediate: `p-8: WebExtensions` has 4054ms of CPU in a 5.46-second profile. Every other process is negligible. `t-13` is the only thread in that process and holds all of it.

---

## Check what the WebExtensions thread is doing

```
profiler-cli thread select t-13 --session ext-recursion
profiler-cli thread samples --session ext-recursion
```

```
Thread: WebExtensions

Note: active samples only (idle excluded) — use --include-idle to include idle samples.

Top Functions (by total time):
  (For a call tree starting from these functions, use: profiler-cli thread samples-top-down)

  f-0.     (root) - total: 4440 (100.0%)
  f-13684. XRE_InitChildProcess - total: 4440 (100.0%)
  f-1347.  js::RunScript - total: 4299 (96.8%)
  f-18033. resource://gre/modules/ExtensionChild.sys.mjs!recvRunListener - total: 4172 (94.0%)
  f-18034. resource://gre/modules/ExtensionChild.sys.mjs!fire - total: 4172 (94.0%)
  f-18035. Extension "1Password – Password Manager"!yRj - total: 4172 (94.0%)
  f-18053. Extension "1Password – Password Manager"!Kq/this.isEnabled - total: 4171 (93.9%)
  f-18054. Extension "1Password – Password Manager"!jj - total: 4171 (93.9%)
  f-18055. Extension "1Password – Password Manager"!getItem - total: 4171 (93.9%)
  f-18056. Extension "1Password – Password Manager"!getItem - total: 4171 (93.9%)   ← duplicate!
  f-18057. Extension "1Password – Password Manager"!RjA - total: 4171 (93.9%)
  f-18058. Extension "1Password – Password Manager"!vj - total: 4171 (93.9%)
  f-18059. Extension "1Password – Password Manager"!YG - total: 4171 (93.9%)
  f-18126. Extension "1Password – Password Manager"!og - total: 4053 (91.3%)
  f-18135. Extension "1Password – Password Manager"!setItem - total: 4052 (91.3%)
  f-18136. Extension "1Password – Password Manager"!setItem - total: 4052 (91.3%)   ← duplicate!
  f-11091. XUL!js::CaptureStack - total: 3265 (73.5%)
  f-3941.  XUL!JS::CaptureCurrentStack - total: 3263 (73.5%)
  f-2832.  js::SavedStacks::saveCurrentStack - total: 3261 (73.4%)
  ...
  f-18755. XUL!js::jit::CheckOverRecursedImpl - total: 1494 (33.6%)
  f-18757. XUL!js::ReportOverRecursed - total: 1481 (33.4%)

Top Functions (by self time):
  (For a call tree showing what calls these functions, use: profiler-cli thread samples-bottom-up)

  f-18800. XUL!js::jit::CompactBufferReader::readByte - self: 178 (4.0%)
  f-11094. XUL!js::SavedStacks::getOrCreateSavedFrame - self: 122 (2.7%)
  f-18870. XUL!mozilla::detail::EntrySlot<js::WeakHeapPtr<js::SavedFrame*>>::matchHash - self: 112 (2.5%)
  f-18144. Extension "1Password – Password Manager"!mLA - self: 101 (2.3%)
  ...
```

Two things stand out immediately:

1. `getItem` appears **twice** in the total-time list (f-18055 and f-18056), and so does `setItem` (f-18135 and f-18136). The same function name at multiple stack depths is a classic sign of recursion.

2. `js::SavedStacks::saveCurrentStack` at 73% and `js::ReportOverRecursed` at 33% are the JS engine's stack-capture and over-recursion-reporting machinery. The engine is hitting the recursion depth limit repeatedly and throwing errors, each of which captures a full JS stack trace.

The self-time breakdown is also telling: the top self-time functions are all deep inside `saveCurrentStack` (hash table lookups, JIT frame iteration). No 1Password JS code appears in self time at all, confirming the extension itself is not doing any useful work.

---

## Confirm the infinite recursion with the top-down tree

```
profiler-cli thread samples-top-down --max-lines 40 --session ext-recursion
```

```
Top-Down Call Tree:
f-0. (root) [total: 100.0%, self: 0.0%]
├─ f-13684. XRE_InitChildProcess [total: 93.8%, self: 0.0%]
│  f-8290.  Task PWindowGlobal::Msg_RawMessage [total: 93.8%, self: 0.0%]
│  f-8291.  PWindowGlobal::Msg_RawMessage [total: 93.8%, self: 0.0%]
│  f-7526.  JSActor message handler [total: 93.8%, self: 0.0%]
│  f-7529.  JSActor receive message [total: 93.8%, self: 0.0%]
│  f-1347.  js::RunScript [total: 93.8%, self: 0.0%]
│  f-1347.  js::RunScript [total: 93.8%, self: 0.0%]
│  f-18033. recvRunListener [total: 93.8%, self: 0.0%]
│  f-18034. fire [total: 93.8%, self: 0.0%]
│  f-18035. 1Password!yRj [total: 93.8%, self: 0.0%]
│  f-18053. 1Password!Kq/this.isEnabled [total: 93.8%, self: 0.0%]
│  f-18054. 1Password!jj [total: 93.8%, self: 0.0%]
│  f-18055. 1Password!getItem [total: 93.8%, self: 0.0%]
│  f-18056. 1Password!getItem [total: 93.8%, self: 0.0%]
│  f-18057. 1Password!RjA [total: 93.8%, self: 0.0%]
│  f-18058. 1Password!vj [total: 93.8%, self: 0.0%]
│  f-18059. 1Password!YG [total: 93.8%, self: 0.0%]
│  f-18054. 1Password!jj [total: 93.8%, self: 0.0%]   ← same as above
│  f-18055. 1Password!getItem [total: 93.8%, self: 0.0%]
│  f-18056. 1Password!getItem [total: 93.8%, self: 0.0%]
│  f-18057. 1Password!RjA [total: 93.8%, self: 0.0%]
│  f-18058. 1Password!vj [total: 93.8%, self: 0.0%]
│  f-18059. 1Password!YG [total: 93.8%, self: 0.0%]
│  f-18054. 1Password!jj [total: 93.8%, self: 0.0%]   ← and again
│  ...
```

The recursion cycle (`jj → getItem → getItem → RjA → vj → YG → og → setItem → setItem`) repeats at 93.8% at every level. Note `og` and `setItem` appear at 91.3% rather than 93.9% because a small fraction of stacks are captured mid-cycle before reaching that branch. The profiler captured stacks at many different recursion depths, and the top-down tree displays each unique depth as a separate node, which is why the same functions appear over and over at the same percentage.

---

## Isolate the recursion with a filter

To strip away the engine entry path and focus only on the `isEnabled` subtree, use `filter push --root-at`:

```
profiler-cli filter push --root-at f-18053 --session ext-recursion
profiler-cli thread samples-top-down --max-lines 25 --session ext-recursion
```

```
Filters: [1] root-at: f-18053

Top-Down Call Tree:
f-18053. 1Password!Kq/this.isEnabled [total: 100.0%, self: 0.0%]
└─ f-18054. 1Password!jj [total: 100.0%, self: 0.0%]
   f-18055. 1Password!getItem [total: 100.0%, self: 0.0%]
   f-18056. 1Password!getItem [total: 100.0%, self: 0.0%]
   f-18057. 1Password!RjA [total: 100.0%, self: 0.0%]
   f-18058. 1Password!vj [total: 100.0%, self: 0.0%]
   f-18059. 1Password!YG [total: 100.0%, self: 0.0%]
   f-18054. 1Password!jj [total: 100.0%, self: 0.0%]   ← cycle repeats
   f-18055. 1Password!getItem [total: 100.0%, self: 0.0%]
   f-18056. 1Password!getItem [total: 100.0%, self: 0.0%]
   f-18057. 1Password!RjA [total: 100.0%, self: 0.0%]
   f-18058. 1Password!vj [total: 100.0%, self: 0.0%]
   f-18059. 1Password!YG [total: 100.0%, self: 0.0%]
   f-18054. 1Password!jj [total: 100.0%, self: 0.0%]   ← and again
   ...
```

With the engine frames gone, 100% of all samples are inside the `isEnabled` call, and every single sample is the same recurring cycle. This is the clearest possible view of the recursion.

```
profiler-cli filter clear --session ext-recursion
```

---

## GC pressure from the error flood

```
profiler-cli thread markers --category "GC / CC" --min-duration 50 --session ext-recursion
```

```
Markers in thread t-13 (WebExtensions) — 8 markers

By Name:
  GCMajor   8 markers  (interval: min=55.30ms, avg=65.15ms, max=74.39ms)
    Examples: m-21 (74.39ms), m-22 (73.79ms), m-23 (71.79ms)
```

```
profiler-cli thread markers --category "GC / CC" --session ext-recursion
```

```
By Name:
  Parallel marking ran   1334 markers
  Parallel marking wait  1303 markers
  GCSlice                  59 markers  (avg=2.73ms)
  GCMinor                  55 markers  (avg=2.84ms)
  GCMajor                   8 markers  (avg=65.15ms)
```

Every recursive stack overflow creates a new Error object with a full JS stack trace attached. The JS heap fills rapidly with these objects. The GC response: 8 major GCs averaging 65ms each, 59 incremental slices, and 55 minor GCs. Combined that is roughly 850ms of garbage collection in 5.46 seconds, about 15% of total recording time.

```
profiler-cli thread markers --search "Jank" --session ext-recursion
```

```
By Name:
  Jank   1 marker   (4.17s)
    Examples: m-20 (4.17s at 1.29s - 5.46s)
```

The Jank marker confirms the hang started at 1.29s and lasted until the end of the profile, 4.17 seconds.

---

## What the engine is doing about it

The 73% time in `js::SavedStacks::saveCurrentStack` explains why the CPU is so high despite the JS doing no useful work. Each time the recursion hits the depth limit, the engine:

1. Detects over-recursion via `js::jit::CheckOverRecursedImpl`
2. Calls `js::ReportOverRecursed` and throws an error
3. Captures the full JS stack trace for the Error object (`js::SavedStacks::saveCurrentStack`)

The stack at this point is thousands of frames deep, so capturing it is expensive. Since the recursion immediately restarts after the error is caught, this happens continuously, producing 100% CPU with no forward progress.

The bottom-up tree confirms this: the top self-time function is `CompactBufferReader::readByte` inside the JIT frame iterator, which is what `saveCurrentStack` calls as it walks the stack. None of the 1Password JS functions appear in self time at all.

---

```
profiler-cli stop --session ext-recursion
```
