# Example analysis of a resource usage profile for a Firefox Android fat AAR build

https://profiler.firefox.com/public/3wwdpnhv2bqs9zvr4nk226c45vrqa646wk8s8f0

Bugzilla: https://bugzilla.mozilla.org/show_bug.cgi?id=1918653

## Profiled scenario

Shippable fat AAR builds for Firefox Android were taking 50+ minutes in CI, blocking downstream
AC/Fenix/Focus builds. This is a resource usage profile captured from a mozilla-central build job
to investigate where that time goes. Unlike a runtime profile, this has no CPU samples — everything
is marker-based, recording build phases, Rust crate compilations, C++ object file compilations,
Gradle tasks, and periodic CPU/memory snapshots.

## Analysis

I'm starting with one question: where is the time going?

There's one thread (the mach build process), 0 CPU samples, and 45,476 markers. The profile is
1285 seconds long (~21 minutes). Looking at the top-level build phases:

- configure — 36s
- export — 70.5s
- compile — **1023.6s** (~17 minutes)
- android-archive-geckoview — 95.9s
- buildsymbols — 30.6s

The compile phase alone takes 80% of the total wall time, so that's where to focus.

The compile phase has a CPU time of 17h3m at 45.4% average utilization — roughly 60 logical cores
active on average. So the machine is well-utilized; the bottleneck isn't idle time or poor
parallelism.

Looking at the Rust library markers within compile, `libgkrust.a` jumps out immediately at **839.5 seconds** (~14 minutes). The next two, `libjsrust.a`
(44s) and `libminidump_analyzer_export.a` (41s), are much smaller. So `libgkrust.a` is the dominant
cost within compile.

Drilling into individual Rust crate markers, the slowest crates are:

- gkrust v0.1.0 — 396.9s
- firefox-on-glean v0.1.0 — 280.2s
- webrender v0.62.0 — 222.1s
- style v0.0.1 — 200.9s
- swgl v0.1.0 build script — 162.4s

These run in parallel (the wall time of 839s is less than the sum), but the Rust compiler's
dependency graph still forces serial work at the top. There's nothing to optimize here without
reducing what needs to be compiled in the first place.

The C++ object files also compile during this phase. The biggest individual ones are `rlbox.wasm.o` (148s), `Unified_cpp_dom_canvas3.o` (121s), and
`UnifiedBindings27.o` (100s), but they overlap with the Rust compilation and don't appear to be on
the critical path.

After compile, the android-archive-geckoview phase at 95.9s is almost entirely
`geckoview:assembleDebug` (94.8s). That's just the Gradle packaging step and looks expected.

One other thing I noticed while looking at the dumpsymbols markers: `libxul.so_syms.track` takes 62.9s. As Markus Stange pointed out in the bug, the fat AAR build
generates 1.2–2.4 GB of crashreporter-symbols that are immediately discarded, the final AAR uses
binaries from upstream dependent tasks, not the ones compiled here. So this symbolication work is
entirely wasted.

So to summarize: the build is slow because shippable fat AAR builds do a **full Rust+C++ compilation
from scratch**, while opt builds use artifact builds that skip recompilation by reusing previously
built binaries. The profile confirms there's nothing to optimize within the compile phase itself,
the fix has to be structural: make fat AAR builds behave as artifact builds while still handling
multi-localization, and stop generating the unused crashreporter symbols.
