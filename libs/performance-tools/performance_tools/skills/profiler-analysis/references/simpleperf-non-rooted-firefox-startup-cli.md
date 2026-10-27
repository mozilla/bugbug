# profiler-cli walkthrough: simpleperf non-rooted Firefox startup

Companion to `simpleperf-non-rooted-firefox-startup.md`. Reproduces the same findings using `profiler-cli` with annotated output.

Profile: https://profiler.firefox.com/public/m19tkpgxjewvtbpjeyegtkhfj0k543fp5zxmcwg

Note: This is a simpleperf profile captured with `--trace-offcpu`. There are no Gecko profiler markers, so the analysis relies entirely on call stacks.

## Load the profile

```
$ profiler-cli load https://profiler.firefox.com/public/m19tkpgxjewvtbpjeyegtkhfj0k543fp5zxmcwg --session fenix-startup
Loading profile from ...
Session started: fenix-startup
```

## Orient: find the interesting threads

```
$ profiler-cli profile info --session fenix-startup
[Thread: t-31 (Gecko) | View: Full profile | Full: 20.14s]

Name: org.mozilla.fenix on samsung SM-G991W – Android 14
...

Top processes and threads by CPU usage:
  p-0: org.mozilla.fenix [pid 9124] ...
    t-31: Gecko [tid 9180] - 2615.438ms
    t-46: DefaultDispatch [tid 9208] - 887.878ms
    t-0: org.mozilla.fenix [tid 9124] - 844.963ms
    t-53: DefaultDispatch [tid 9218] - 630.258ms
    t-96: Socket Thread [tid 9280] - 387.570ms
    t-142: QuotaManager IO [tid 9372] - 381.843ms
    ...
```

The `Gecko` thread is the Firefox main thread with the most active CPU. `QuotaManager IO` and two `DefaultDispatch` threads are also worth investigating.

## The startup gap: QuotaManager IO blocking Gecko

Select the `QuotaManager IO` thread and look at its call tree:

```
$ profiler-cli thread select t-142 --session fenix-startup
$ profiler-cli thread samples-top-down --session fenix-startup
```

The active work (31.3% of the thread's samples) breaks down as:

```
InitTemporaryStorageOp::DoDirectoryWork
  QuotaManager::EnsureTemporaryStorageIsInitializedInternal
    InitializeTemporaryStorageInternal
      LoadQuota                        <- 28.0%
        InitializeRepository
          (walks per-origin storage directories, reads metadata headers)
      InvalidateCache                  <- 2.1%
        mozStorageTransaction::Commit
          sqlite3_exec / sqlite3_step
```

`LoadQuota -> InitializeRepository` walks every per-origin subdirectory under the temporary storage root and reads a binary metadata header from each one. The cost scales with the number of origins that have stored data, which increases as the browser is used. The Gecko thread cannot proceed with page load until this initialization completes.

## The SuggestStore secondary cost

Check the second `DefaultDispatch` thread (t-53, 630ms active CPU):

```
$ profiler-cli thread select t-53 --session fenix-startup
$ profiler-cli thread samples --search "suggest\|ingest" --session fenix-startup
```

The output shows 13.8% of all samples in the SuggestStore ingest path:

```
  f-15757. SuggestStore.ingest - total: 877 (13.8%)
  f-15775. <SuggestRemoteSettingsClient as Client>::get_records - total: 786 (12.4%)
  f-14783. remote_settings::RemoteSettingsClient::get_records - total: 648 (10.2%)
  f-14866. RemoteSettingsClient<C>::filter_records - total: 647 (10.2%)
```

Drilling into the filter path with a top-down view:

```
$ profiler-cli thread samples-top-down --search "filter_records" --session fenix-startup
```

The bottleneck is JEXL filter expression parsing:

```
filter_records
  JexlFilter::evaluate
    jexl_parser::Parser::parse
      ExpressionParser::new            <- 8.4% of all thread samples
        MatcherBuilder::new
          regex::RegexSet::new         <- 6.0%
            RegexSetBuilder::new
```

The JEXL filter parser and its backing regex automaton are being rebuilt from scratch for every Remote Settings record evaluated. `ExpressionParser::new` calls into the LALRPOP parser generator machinery, which includes compiling all the lexer regex patterns via `regex::RegexSet::new`. This should be compiled once and reused across records.

## Summary

Two independent issues slow down the startup:

1. **QuotaManager IO** (~2 second Gecko pause): `InitializeTemporaryStorageInternal` scans per-origin metadata files on every startup. The scan time grows with the number of stored origins. Bug 1903530 tracks this.

2. **SuggestStore ingest**: `FxSuggestStorage.ingest` calls `RemoteSettingsClient::get_records`, which evaluates a JEXL filter for each record but rebuilds the JEXL parser machinery (including regex compilation) for every evaluation instead of caching it. Bug 1978973 tracks the broader `get_records` cost; the JEXL parser allocation is the specific hot path here.

---

```
$ profiler-cli stop --session fenix-startup
```
