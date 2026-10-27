# profiler-cli walkthrough: resource usage Linux fat AAR build

Companion to `resource-usage-linux-fat-aar-build.md`. Reproduces the same findings using `profiler-cli` with annotated output.

Profile: https://profiler.firefox.com/public/3wwdpnhv2bqs9zvr4nk226c45vrqa646wk8s8f0

---

## Load the profile and get an overview

```
profiler-cli load https://profiler.firefox.com/public/3wwdpnhv2bqs9zvr4nk226c45vrqa646wk8s8f0 --session aar-build
profiler-cli profile info --session aar-build
```

```
Name: Firefox Nightly 143.0a1 – x86_64-pc-linux-gnu
Platform: x86_64-pc-linux-gnu

This profile contains 1 threads across 1 processes.

Top processes and threads by CPU usage:
  p-0: mach [pid 0] [ts-0 → end] - 0.000ms
    t-0:  [tid 0] - 0.000ms

CPU activity over time:
No significant activity.
```

One thread, zero CPU samples, zero CPU activity. This is a marker-only profile: every piece of data is in the 45,476 markers, not sampled stacks. The profile is 1285 seconds (~21 minutes) long.

---

## Find the top-level build phases

```
profiler-cli thread select t-0 --session aar-build
profiler-cli thread markers --category Phases --session aar-build
```

```
Markers in thread t-0 () — 15 markers (filtered from 45476)

By Name:
  Phase   15 markers  (interval: min=161.06ms, avg=86.27s, max=1023.64s)
    Examples: m-16 ✗ (1023.64s), m-17 ✗ (95.86s), m-18 ✗ (70.53s)

By Category:
  Phases   15 markers (100.0%)
```

15 phase markers span the full build, dominated by one 1023s outlier. The top five by duration:

```
profiler-cli marker info m-16 --session aar-build   # compile
profiler-cli marker info m-17 --session aar-build   # android-archive-geckoview
profiler-cli marker info m-18 --session aar-build   # export
profiler-cli marker info m-19 --session aar-build   # configure
profiler-cli marker info m-20 --session aar-build   # buildsymbols
```

```
Marker m-16: Phase - compile
  Time: 112.48s - 1136.12s (1023.64s)
  Fields:
    CPU Time: 17h3m
    CPU Percent: 45.4%

Marker m-17: Phase - android-archive-geckoview
  Time: 1148.30s - 1244.16s (95.86s)
  Fields:
    CPU Time: 1h36m
    CPU Percent: 18.5%

Marker m-18: Phase - export
  Time: 41.91s - 112.44s (70.53s)
  Fields:
    CPU Time: 1h10m
    CPU Percent: 12.2%

Marker m-19: Phase - configure
  Time: 5.83ms - 35.97s (35.97s)
  Fields:
    CPU Time: 35m49s
    CPU Percent: 1.3%

Marker m-20: Phase - buildsymbols
  Time: 1244.37s - 1274.98s (30.61s)
  Fields:
    CPU Time: 30m32s
    CPU Percent: 3.7%
```

The five major phases in order: configure (36s) → export (70.5s) → compile (1023.6s) → android-archive-geckoview (95.9s) → buildsymbols (30.6s). The compile phase is 80% of total wall time, so that is where to focus.

The compile phase CPU time is 17h3m at 45.4% average utilization. On a machine with ~60 logical cores that is roughly 27 cores active on average. The machine is well-utilized, so the bottleneck is not idle time or poor parallelism; it is just a lot of work.

The remaining 10 phase markers are all under 10 seconds: android-stage-package (9.97s), package (5.71s), android-fat-aar-artifact (5.50s), teardown (5.06s), upload (4.82s), package-generated-sources (3.79s), misc (1.82s), pre-export (0.31s), libs (0.25s), tools (0.16s).

---

## Survey the compile phase task types

Zoom into the compile marker and survey what task categories appear inside it:

```
profiler-cli zoom push m-16 --session aar-build
profiler-cli thread markers --category Tasks --min-duration 60000 --session aar-build
```

```
[Thread: t-0 () | View: ts-6→ts-w (1023.64s) | Full: 1285.00s]

Markers in thread t-0 () — 29 markers (filtered from 45476)

By Name:
  Object        15 markers  avg=85.78s   max=147.72s   ← C++ object files
    Examples: m-41 ✗ (147.72s), m-42 ✗ (121.33s), m-43 ✗ (100.18s)
  RustCrate      8 markers  avg=186.22s  max=396.86s   ← Rust crate compilations
    Examples: m-86 ✗ (396.86s), m-87 ✗ (280.19s), m-88 ✗ (222.09s)
  Gradle         3 markers  avg=78.06s   max=94.82s
    Examples: m-56 ✗ (94.82s), m-57 ✗ (70.14s), m-58 ✗ (69.22s)
  file_generate  1 markers  avg=70.36s   max=70.36s
    Examples: m-31 ✗ (70.36s)
  Rust           1 markers  avg=839.50s  max=839.50s   ← libgkrust.a link step
    Examples: m-81 ✗ (839.50s)
  dumpsymbols    1 markers  avg=62.89s   max=62.89s
    Examples: m-101 ✗ (62.89s)
```

The instant taxonomy: Rust crate compilation and C++ objects are the two dominant task categories by count and individual duration. The single `Rust` marker at 839.5s is the `libgkrust.a` link step spanning almost the entire compile window.

```
profiler-cli zoom pop --session aar-build
```

---

## Rust library link steps

```
profiler-cli thread markers --search Rust --min-duration 10000 --session aar-build
```

```
By Name:
  RustCrate   58 markers  max=396.86s
    Examples: m-86 ✗ (396.86s), m-87 ✗ (280.19s), m-88 ✗ (222.09s)
  Rust         5 markers  max=839.50s
    Examples: m-81 ✗ (839.50s), m-82 ✗ (44.27s), m-83 ✗ (40.65s)
```

The `Rust` markers are per-library (linking steps), and `RustCrate` markers are per-crate (compilation). Inspecting the top `Rust` markers:

```
profiler-cli marker info m-81 --session aar-build   # libgkrust.a
profiler-cli marker info m-82 --session aar-build   # libjsrust.a
profiler-cli marker info m-83 --session aar-build   # libminidump_analyzer_export.a
profiler-cli marker info m-84 --session aar-build   # libcrash_helper_server.a
profiler-cli marker info m-85 --session aar-build   # http3server
```

```
Marker m-81: Rust - Rust
  Time: 130.66s - 970.16s (839.50s)   ← nearly the entire compile phase
  Fields:
    Description: libgkrust.a

Marker m-82: Rust - Rust
  Time: 970.16s - 1014.43s (44.27s)
  Fields:
    Description: libjsrust.a

Marker m-83: Rust - Rust
  Time: 1094.03s - 1134.68s (40.65s)
  Fields:
    Description: libminidump_analyzer_export.a

Marker m-84: Rust - Rust
  Time: 1064.48s - 1094.03s (29.55s)
  Fields:
    Description: libcrash_helper_server.a

Marker m-85: Rust - Rust
  Time: 1014.43s - 1042.99s (28.56s)
  Fields:
    Description: http3server
```

`libgkrust.a` alone spans 130s-970s. The next libraries are an order of magnitude smaller.

---

## Individual Rust crate compilations

```
profiler-cli thread markers --search RustCrate --min-duration 60000 --session aar-build
```

```
By Name:
  RustCrate   8 markers  max=396.86s
    Examples: m-86 ✗ (396.86s), m-87 ✗ (280.19s), m-88 ✗ (222.09s)
```

```
profiler-cli marker info m-86 --session aar-build   # gkrust
profiler-cli marker info m-87 --session aar-build   # firefox-on-glean
profiler-cli marker info m-88 --session aar-build   # webrender
profiler-cli marker info m-89 --session aar-build   # style
profiler-cli marker info m-90 --session aar-build   # swgl
profiler-cli marker info m-140 --session aar-build  # wgpu-core
profiler-cli marker info m-141 --session aar-build  # naga
profiler-cli marker info m-142 --session aar-build  # geckoservo
```

```
Marker m-86: RustCrate - RustCrate
  Time: 566.69s - 963.55s (396.86s)
  Fields:
    Description: gkrust v0.1.0

Marker m-87: RustCrate - RustCrate
  Time: 249.25s - 529.44s (280.19s)
  Fields:
    Description: firefox-on-glean v0.1.0

Marker m-88: RustCrate - RustCrate
  Time: 344.58s - 566.67s (222.09s)
  Fields:
    Description: webrender v0.62.0

Marker m-89: RustCrate - RustCrate
  Time: 213.37s - 414.23s (200.86s)
  Fields:
    Description: style v0.0.1

Marker m-90: RustCrate - RustCrate
  Time: 181.78s - 344.19s (162.41s)
  Fields:
    Description: swgl v0.1.0 build script (run)

Marker m-140: RustCrate - RustCrate
  Time: 202.86s - 295.18s (92.32s)
  Fields:
    Description: wgpu-core v26.0.0

Marker m-141: RustCrate - RustCrate
  Time: 181.84s - 254.50s (72.66s)
  Fields:
    Description: naga v26.0.0

Marker m-142: RustCrate - RustCrate
  Time: 263.62s - 325.98s (62.36s)
  Fields:
    Description: geckoservo v0.0.1
```

These crates run in parallel (their sum far exceeds the 839s wall time of `libgkrust.a`), but the Rust dependency graph still forces serial bottlenecks at the top. There is nothing to optimize within these crates without reducing what needs to be compiled.

---

## C++ object files

```
profiler-cli thread markers --search Object --min-duration 60000 --session aar-build
```

```
By Name:
  Object   15 markers  avg=85.78s  max=147.72s
    Examples: m-41 ✗ (147.72s), m-42 ✗ (121.33s), m-43 ✗ (100.18s)
```

```
profiler-cli marker info m-41 --session aar-build   # rlbox.wasm.o
profiler-cli marker info m-42 --session aar-build   # Unified_cpp_dom_canvas3.o
profiler-cli marker info m-43 --session aar-build   # UnifiedBindings27.o
profiler-cli marker info m-44 --session aar-build   # Unified_cpp_gfx_harfbuzz_src0.o
profiler-cli marker info m-45 --session aar-build   # Unified_cpp_dom_media2.o
```

```
Marker m-41: Object - Object
  Time: 452.93s - 600.65s (147.72s)
  Fields:
    Description: rlbox.wasm.o

Marker m-42: Object - Object
  Time: 239.26s - 360.59s (121.33s)
  Fields:
    Description: Unified_cpp_dom_canvas3.o

Marker m-43: Object - Object
  Time: 390.26s - 490.44s (100.18s)
  Fields:
    Description: UnifiedBindings27.o

Marker m-44: Object - Object
  Time: 360.51s - 456.08s (95.57s)
  Fields:
    Description: Unified_cpp_gfx_harfbuzz_src0.o

Marker m-45: Object - Object
  Time: 328.46s - 417.49s (89.03s)
  Fields:
    Description: Unified_cpp_dom_media2.o
```

The largest C++ objects fall entirely within the libgkrust.a window (130s-970s) and overlap with the Rust crate compilation. They are not on the critical path.

---

## android-archive-geckoview: Gradle packaging

```
profiler-cli marker info m-56 --session aar-build
```

```
Marker m-56: Gradle - Gradle
  Time: 1148.88s - 1243.69s (94.82s)
  Fields:
    Description: geckoview:assembleDebug
```

The `android-archive-geckoview` phase (95.9s) is almost entirely `geckoview:assembleDebug` (94.8s). That is the Gradle packaging step and is expected overhead.

---

## Wasted symbolication work

```
profiler-cli thread markers --search dumpsymbols --min-duration 60000 --session aar-build
```

```
By Name:
  dumpsymbols   1 marker   62.89s
    Examples: m-101 ✗ (62.89s)
```

```
profiler-cli marker info m-101 --session aar-build
```

```
Marker m-101: dumpsymbols - dumpsymbols
  Type: Text
  Category: Tasks
  Time: 971.31s - 1034.19s (62.89s)
  Fields:
    Description: libxul.so_syms.track
```

`libxul.so_syms.track` takes 62.9s generating 1.2-2.4 GB of crashreporter symbols that are immediately discarded. The final AAR uses binaries from upstream dependent tasks, not the ones compiled here, so this symbolication work is entirely wasted.

---

```
profiler-cli stop --session aar-build
```
