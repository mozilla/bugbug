# profiler-cli walkthrough: Firefox macOS startup font initialization lock contention

Companion to `firefox-macos-startup-font-initialization.md`. Reproduces the same findings using `profiler-cli` with annotated output.

Profile: https://profiler.firefox.com/public/fx5sg9g8p9bp6bq36aggg74r8cmrbfcaxcm1rb0

## Load the profile

```
$ profiler-cli load https://profiler.firefox.com/public/fx5sg9g8p9bp6bq36aggg74r8cmrbfcaxcm1rb0 --session font-startup
Loading profile from https://profiler.firefox.com/public/fx5sg9g8p9bp6bq36aggg74r8cmrbfcaxcm1rb0...
Session started: font-startup
```

## Get an overview

```
$ profiler-cli profile info --session font-startup
[Thread: ... | View: Full profile | Full: 1.59s]

Name: Firefox 142 – macOS 15.5.0
Platform: macOS 15.5.0

This profile contains 19 threads across 11 processes.

Top processes and threads by CPU usage:
  p-0: Parent Process [pid 10860] [ts<0z → end] - 1236.908ms
    t-0: GeckoMain [tid 3590310] - 814.960ms
    t-2: Renderer [tid 3590371] - 190.684ms
    t-1: RegisterFonts [tid 3590316] - 120.713ms
    t-5: InitFontList [tid 3590386] - 87.176ms
    ...
```

Three threads in the parent process are interesting: the main thread, `RegisterFonts`, and `InitFontList`. The font threads are running during startup at the same time as the main thread.

## Confirm lock contention on the main thread

Select the main thread and search for `psynch`, the macOS kernel call that a thread lands in when blocked on a mutex:

```
$ profiler-cli thread select t-0 --session font-startup
Selected thread: t-0 (GeckoMain)

$ profiler-cli zoom push 0,0.4 --session font-startup
Pushed view range: ts-0 (0s) to ts-d (400ms) (duration: 400ms)
  Zoom depth: 1
```

Get the denominator first, from the unfiltered view, so the share attributed to the mutex later is grounded in a number the tool printed:

```
$ profiler-cli thread samples --include-idle --session font-startup
[Thread: t-0 (GeckoMain) | View: ts-0→ts-d (400ms) | Full: 1.587s]

Top Functions (by total time):
  f-0. (root) - total: 394 (100.0%)
  f-1. dyld!start - total: 394 (100.0%)
  f-3. firefox!main - total: 394 (100.0%)
  ...
```

Now add the search:

```
$ profiler-cli thread samples --search "psynch" --include-idle --session font-startup
[Thread: t-0 (GeckoMain) | View: ts-0→ts-d (400ms) | Full: 1.587s]

Search: "psynch"

Top Functions (by total time):
  f-0. (root) - total: 112 (100.0%)
  ...
  f-691. libsystem_pthread.dylib!_pthread_mutex_firstfit_lock_slow - total: 97 (86.6%)
  f-692. libsystem_kernel.dylib!__psynch_mutexwait - total: 97 (86.6%)
  f-753. libFontRegistry.dylib!TLocalFontRegistry::TLocalFontRegistry() - total: 93 (83.0%)
  ...
```

112 of the window's 394 samples contain `psynch` in the stack, all landing in `__psynch_mutexwait` via `TLocalFontRegistry::TLocalFontRegistry()`. That is 28% of the first 400ms blocked on the font registry mutex.

Note that the root total is the count of samples matching the search, not the thread's whole sample count: the percentages inside a filtered view are relative to the 112, which is why the denominator has to come from the unfiltered run above.

Check whether the contention is confined to the startup window by clearing the zoom and repeating the same search:

```
$ profiler-cli zoom clear --session font-startup
$ profiler-cli thread samples --search "psynch" --include-idle --session font-startup
[Thread: t-0 (GeckoMain) | View: Full profile | Full: 1.587s]

Search: "psynch"

Top Functions (by total time):
  f-0. (root) - total: 112 (100.0%)
  ...
```

The count is unchanged over the full 1.587s, so all 112 blocked samples fall inside the first 400ms. The contention is early and concentrated rather than spread across the profile.

## Trace the call chain

The zoom is already cleared, so the next command runs against the whole profile:

```
$ profiler-cli thread samples-top-down --search "mutex" --session font-startup
```

The heaviest path through the mutex wait traces back to LookAndFeel initialization:

```
NS_InitXPCOM
  nsComponentManagerImpl::Init
    nsLayoutModuleInitialize
      nsLayoutStatics::Initialize
        nsContentUtils::Init
          nsXPLookAndFeel::GetInstance
            nsLookAndFeel::EnsureInit
              [NSWindow initWithContentRect:...]
                [NSThemeFrame _updateTitleProperties:...]
                  [NSTextFieldCell initTextCell:]
                    UIFoundation!+[NSFont systemFontOfSize:width:]
                      CoreText!TDescriptor::CreateMatchingDescriptorInternal
                        CoreText!MakeSpliceDescriptor
                          libFontRegistry.dylib!XTCopyFontWithName
                            libFontRegistry.dylib!TLocalFontRegistry::TLocalFontRegistry()
                              _pthread_mutex_firstfit_lock_slow
                                __psynch_mutexwait
```

`nsLookAndFeel::EnsureInit()` creates a hidden AppKit window to probe the system theme. AppKit immediately resolves the system font for the title bar, which walks into `libFontRegistry.dylib` and tries to acquire the `TLocalFontRegistry` mutex, and blocks.

A sampled profile shows which threads are *waiting* on a lock, never which thread *owns* it: there is no owner field in a sample, only the stack of the blocked thread. So the profile does not by itself identify the holder. What it does establish is that the main thread is blocked on the `TLocalFontRegistry` constructor's mutex during startup, and that the only other threads touching that registry in this window are the two font threads examined next. Naming a specific holder would be an inference beyond the data.

## What the font threads are doing

```
$ profiler-cli thread select t-1 --session font-startup
Selected thread: t-1 (RegisterFonts)

$ profiler-cli thread samples-top-down --session font-startup
```

The `RegisterFonts` thread is scanning font directories and calling `CoreTextFontList::ActivateFontsFromDir` to add fonts to the index. It also blocks on the same lock:

```
Top-Down Call Tree:
f-0. (root) [total: 100.0%, self: 0.0%]
└─ ...
   f-10357. XUL!gfxPlatformMac::FontRegistrationCallback(void*) [total: 100.0%, self: 0.0%]
   ├─ f-10358. XUL!CoreTextFontList::ActivateFontsFromDir(...) [total: 99.4%, self: 0.0%]
   │  ├─ f-10364. CoreText!_CTFontManagerRegisterActionFontURLs() [total: 98.8%, self: 0.0%]
   │  │  ├─ f-10412. libFontRegistry.dylib!CopyFaceURLsForFonts(...) [total: 47.9%, self: 0.0%]
   │  │  │  f-887. libFontRegistry.dylib!XTCopyFontsWithProperties [total: 47.9%, self: 0.0%]
   │  │  │  ├─ f-890. ...CopyPropertiesForFontsMatchingRequest()... [total: 27.0%, self: 0.0%]
   │  │  │  │  ...
   │  │  │  └─ f-753. libFontRegistry.dylib!TLocalFontRegistry::TLocalFontRegistry() [total: 20.9%, self: 0.0%]
   │  │  │     f-691. libsystem_pthread.dylib!_pthread_mutex_firstfit_lock_slow [total: 20.9%, self: 0.0%]
   │  │  │     f-692. libsystem_kernel.dylib!__psynch_mutexwait [total: 20.9%, self: 20.9%]
```

The `RegisterFonts` thread itself spends 20.9% of its samples blocked on `__psynch_mutexwait`, reached through the same `TLocalFontRegistry::TLocalFontRegistry()` frame as the main thread. Note what this rules out: `RegisterFonts` cannot be the holder for the whole window, because for a fifth of its samples it is a waiter too. Most of the other 79% is real registration work, dominated by filesystem calls (`stat` 9.2%, `__getattrlist` 6.1%) plus string and URL construction, though 3.7% is a synchronous XPC wait to the font daemon rather than work. The registration portion is consistent with this thread holding the lock part of the time, but the samples do not show that directly.

```
$ profiler-cli thread select t-5 --session font-startup
Selected thread: t-5 (InitFontList)

$ profiler-cli thread samples-top-down --session font-startup
```

The `InitFontList` thread calls `CTFontDescriptorCreateMatchingFontDescriptorsWithOptions` to enumerate font families, which also touches the font registry and makes synchronous XPC calls to the font daemon.

`__NSXPCCONNECTION_IS_WAITING_FOR_A_SYNCHRONOUS_REPLY__` shows up in two separate branches of this thread's top-down tree, at 10.3% and 4.7%, so reading a single branch understates the total wait. Use a search to get the whole-thread figure instead of adding up branches by eye:

```
$ profiler-cli thread samples --search "__NSXPCCONNECTION_IS_WAITING_FOR_A_SYNCHRONOUS_REPLY__" --include-idle --session font-startup
  f-0. (root) - total: 17 (100.0%)

$ profiler-cli thread samples --include-idle --session font-startup
  f-0. (root) - total: 107 (100.0%)
```

17 of 107 samples, so about 16% of this thread's time is spent waiting on synchronous XPC replies from the font daemon.

## Summary

All three threads need the `TLocalFontRegistry` mutex at overlapping times:

- `RegisterFonts` registers fonts from disk, and blocks on the mutex for 20.9% of its own samples
- `InitFontList` enumerates font families, touching the same registry (and also waits on XPC)
- `GeckoMain` needs it because `nsLookAndFeel::EnsureInit()` creates an AppKit window that immediately asks CoreText for the system font

The result is that the main thread spends about 28% of the first 400ms blocked in the kernel waiting for this single mutex. The low-CPU appearance in the profiler timeline during startup reflects this: the thread is not idle, it is ready to work but stalled on a lock.

What the profile supports and what it does not, stated separately because the distinction matters when writing this up in a bug:

- Supported: all three threads block in `TLocalFontRegistry::TLocalFontRegistry()`; the main thread loses 112 of 394 startup samples to it; the contention is confined to the first 400ms.
- Not supported: which thread holds the mutex at any given moment. Sampled stacks record waiters only. Establishing the holder needs lock-contention instrumentation or a `dtrace`/`lldb` session, not this profile.

---

```
$ profiler-cli stop --session font-startup
```
