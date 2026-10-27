# Example analysis of a profile taken with simpleperf on a non-rooted Android device, of Firefox startup

https://profiler.firefox.com/public/m19tkpgxjewvtbpjeyegtkhfj0k543fp5zxmcwg

## Profiled scenario

I noticed a very slow page load when I opened a web page from a native Android app. The page was displayed in a "Custom tab" Firefox view.
Specifically, I was looking at a blank view for multiple seconds with a non-moving progress bar. After the delay, once the first page content showed up, the rest of the page load proceeded quickly, for example images appeared pretty much immediately.

I knew that this was a scenario where Firefox wasn't running in the background and had to be started up from scratch to display the custom tab content.

I took this profile using simpleperf, with `./app_profiler.py -p org.mozilla.fenix -r "-g --duration 20 -f 1000 --trace-offcpu -e cpu-clock:u" && samply import perf.data --breakpad-symbol-server https://symbols.mozilla.org/`

## Analysis

The reason I took this profile was because the page took too long to load. So in my investigation I'm starting with the following questions:

- What caused the page load delay?
- Do any other things stand out that deserve to be filed as bugs?

The first thing I can see is that the profile was recorded for longer than the page load happened - the profile is 20 seconds long, but the last 7 seconds have basically no activity. So I'll reduce my time range to the first 13 seconds.

Now I want to find the spot where the delay was over and the bulk of the page load happened - image network requests getting kicked off etc. This is a profile without any Gecko profiler marker data, so I have to find the right spot by looking at the sampled stacks and the CPU activity.

Looking at the activity of the "Gecko" thread, I can see a bunch of startup activity, then a pause of 130ms, then some more startup activity, and then long pause of almost two seconds. After this long pause, the next 300ms contain a lot of samples spending their time in network code, for example in `nsHttpChannel::AsyncOpen`, `nsHttpChannel::Connect`, and `nsInputStreamPump::OnInputStreamReady`. So I bet this is where we're actually loading the page, and the 2 second gap is the delay I was curious about.

So what happens on other threads during that gap?

The activity is on the `QuotaManager IO` thread, in `InitializeTemporaryStorageInternal`. The full call chain is `InitTemporaryStorageOp::DoDirectoryWork -> QuotaManager::EnsureTemporaryStorageIsInitializedInternal -> InitializeTemporaryStorageInternal -> LoadQuota -> InitializeRepository`. The `InitializeRepository` function walks every per-origin storage directory and reads the metadata file header from each one. This is a filesystem scan whose cost scales with how many origins have stored data, which is why the same operation takes longer over time. This is still https://bugzilla.mozilla.org/show_bug.cgi?id=1903530.

Ok that answers the first question.

I also noticed significant CPU usage on a `DefaultDispatch` thread in `suggest::store::SuggestStore::ingest`, spending its time in `remote_settings::RemoteSettingsClient::get_records`. Drilling further into that: `get_records` calls `filter_records`, which evaluates a JEXL expression against every Remote Settings record to decide whether it applies to this device. The hot path is:

```
filter_records
  JexlFilter::evaluate
    jexl_parser::Parser::parse
      ExpressionParser::new         <- builds a LALRPOP parser from scratch
        MatcherBuilder::new
          regex::RegexSet::new      <- compiles regex patterns for each new parser
```

The JEXL parser and its underlying regex set are being rebuilt for each record being evaluated, rather than being compiled once and reused. This is the same `remote_settings::RemoteSettingsClient::get_records` expense tracked in https://bugzilla.mozilla.org/show_bug.cgi?id=1978973, though with this profile showing the parser construction cost explicitly. This is now a different caller (`FxSuggestStorage.ingest`) than the three previously identified.
