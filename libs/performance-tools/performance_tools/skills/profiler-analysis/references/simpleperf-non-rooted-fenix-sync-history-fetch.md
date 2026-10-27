# Slow history sync in Fenix: fetch_outgoing does a full table scan

Profile: https://profiler.firefox.com/public/m19tkpgxjewvtbpjeyegtkhfj0k543fp5zxmcwg (full recording)
Zoomed view of sync activity: https://share.firefox.dev/46xnQfE

This is the same profile described in `simpleperf-non-rooted-firefox-startup.md`. That example focused on
the startup delay caused by QuotaManager. This one is about a separate issue noticed in the same recording:
a slow Firefox Sync operation that runs shortly after startup.

## Profiled scenario

I was profiling Fenix startup on my personal Samsung SM-G991W (Android 14) using simpleperf:

```
./app_profiler.py -p org.mozilla.fenix -r "-g --duration 20 -f 1000 --trace-offcpu -e cpu-clock:u" && samply import perf.data --breakpad-symbol-server https://symbols.mozilla.org/
```

The profile happened to capture some Firefox Sync activity that started several seconds into the recording.
I noticed that a background thread was burning a significant amount of time in a SQLite query inside the
sync engine. That led to bug https://bugzilla.mozilla.org/show_bug.cgi?id=1979764.

## Analysis

The highest-CPU thread after the Gecko main thread is `t-46: DefaultDispatch` with 887ms of CPU time.
Looking at its activity timeline, essentially nothing happens for the first 7 seconds, then there's a burst
of work starting at about 6.97 seconds and running until about 10.82 seconds - a wall-clock window of
roughly 3.85 seconds. This timing makes sense: the Fenix sync engine is intentionally delayed a few seconds
after startup so it doesn't compete with the initial page load.

The thread's call stack bottoms out in Kotlin coroutines (`WorkManagerSyncWorker.doWork`) dispatching
through JNA into Rust via UniFFI:

```
kotlinx.coroutines.DispatchedTask.run
  mozilla.components.service.fxa.sync.WorkManagerSyncWorker$doWork$2.invokeSuspend
    mozilla.appservices.syncmanager.SyncManager.sync   (JNA → UniFFI bridge)
      sync_manager::manager::SyncManager::do_sync
        sync15::client::sync_multiple::sync_multiple_with_command_processor
          sync_manager::manager::SyncManager::sync_engines
            sync15::client::sync::synchronize_with_clients_engine
```

Within `synchronize_with_clients_engine`, the time breaks down across several sub-operations:

- `HistorySyncEngine::apply` (which calls `fetch_outgoing`): 60% of samples in the sync window
- `LoginsSyncEngine::mark_as_synchronized` (journal fsync for logins DB commit): 12%
- `CollectionUpdate::upload` (uploading records over the network): 16%
- Various other sync-engine steps: 12%

The dominant cost is `fetch_outgoing` at 60%. Tracing into it, the call goes through a rusqlite query
iterator (`query_rows_and_then` -> `Rows::next` -> `Statement::step` -> `sqlite3Step`), and the self-time
in that window is overwhelmingly `pread64`:

```
Self-time in sync window (6.97s - 10.82s):
  pread64         58.3%   ← SQLite reading database pages from disk
  syscall         18.2%   ← thread idle/waiting
  fsync           15.1%   ← journal flush from logins DB commit
  sqlite3VdbeExec  1.3%
```

58% self time in `pread64` means SQLite is spending most of its time reading pages from disk. This is a
full table scan of the `moz_places` history database. The query being run is:

```sql
SELECT guid, url, id, title, hidden, typed, frecency,
       visit_count_local, visit_count_remote,
       last_visit_date_local, last_visit_date_remote,
       sync_status, sync_change_counter, preview_image_url,
       unknown_fields
FROM moz_places
WHERE (sync_change_counter > 0 OR sync_status != {SyncStatus::Normal})
  AND NOT hidden
ORDER BY frecency DESC
LIMIT :max_places
```

There is no index on `sync_change_counter` or `sync_status`, so SQLite cannot narrow the scan using an
index. It reads every row in `moz_places` to evaluate the WHERE clause, then sorts the qualifying rows by
`frecency DESC` to find the highest-priority records to upload. On a phone with a large history database
and cold storage (pages not in the OS page cache after a fresh start), reading all of those pages triggers
many `pread64` calls - and that's exactly what we see.

The 2.6 seconds reported in the bug title matches the 60% of the 3.85 second sync window occupied by
`fetch_outgoing` (about 2.3 seconds of samples, with the remainder accounted for by I/O latency time when
the thread is not scheduled).

The fix would be to add an index on the columns used in the WHERE clause, so SQLite can find the rows that
need syncing without scanning the whole table.
