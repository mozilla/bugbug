# profiler-cli walkthrough: slow history sync fetch_outgoing

Companion to `simpleperf-non-rooted-fenix-sync-history-fetch.md`. Reproduces the same findings using
`profiler-cli` with annotated output.

Profile: https://profiler.firefox.com/public/m19tkpgxjewvtbpjeyegtkhfj0k543fp5zxmcwg

---

## Load the profile and get an overview

```
profiler-cli load https://profiler.firefox.com/public/m19tkpgxjewvtbpjeyegtkhfj0k543fp5zxmcwg --session fenix-sync
profiler-cli profile info --session fenix-sync
```

```
Name: org.mozilla.fenix on samsung SM-G991W – Android 14
Platform: Android 14

This profile contains 206 threads across 2 processes.

Top processes and threads by CPU usage:
  p-0: org.mozilla.fenix [pid 9124] [ts<16s → end] - 10594.290ms
    t-31: Gecko [tid 9180] - 2615.438ms
    t-46: DefaultDispatch [tid 9208] - 887.878ms   ← second busiest thread
    t-0: org.mozilla.fenix [tid 9124] - 844.963ms
    t-53: DefaultDispatch [tid 9218] - 630.258ms
    t-96: Socket Thread [tid 9280] - 387.570ms
    ...
```

`t-46` is a `DefaultDispatch` thread with 887ms of CPU time. That is interesting during a startup
investigation: something is running in the Kotlin coroutine pool. Let's look at when it runs.

---

## Check the timing of t-46's activity

```
profiler-cli thread select t-46 --session fenix-sync
profiler-cli thread info --session fenix-sync
```

```
CPU activity over time:
- 6% for 888.0ms: [ts-33 → ts-t] (181.17ms - 15.653s)
  - 20% for 8.2ms: ...  (5.335s - 5.375s)
  - 20% for 12.4ms: ... (5.399s - 5.461s)
  - 20% for 19.1ms: ... (5.671s - 5.765s)
  - 21% for 812.2ms: [ts-h → ts-M] (6.966s - 10.815s)   ← main burst
    ...
```

Almost all of the 887ms lives in one burst from 6.97s to 10.82s. The sync engine intentionally waits a
few seconds after startup before running, so this timing is expected. Let's zoom in and see what it's
doing.

---

## Zoom into the sync window

```
profiler-cli zoom push 6.9,10.9 --session fenix-sync
profiler-cli thread select t-46 --session fenix-sync
profiler-cli thread samples --session fenix-sync
```

```
Thread: DefaultDispatch

Top Functions (by total time):

  f-13000. base.odex!kotlinx.coroutines.scheduling.CoroutineScheduler$Worker.run - total: 3622 (100.0%)
  f-13001. base.odex!kotlinx.coroutines.internal.LimitedDispatcher$Worker.run - total: 3621 (100.0%)
  f-326.   base.odex!kotlinx.coroutines.DispatchedTask.run - total: 3621 (100.0%)
  f-327.   base.odex!kotlin.coroutines.jvm.internal.BaseContinuationImpl.resumeWith - total: 3621 (100.0%)
  f-13931. base.apk!mozilla.components.service.fxa.sync.WorkManagerSyncWorker$doWork$2.invokeSuspend - total: 3621 (100.0%)
  f-13947. base.apk!mozilla.appservices.syncmanager.SyncManager.sync - total: 3604 (99.5%)
  f-14013. libmegazord.so!sync_manager::manager::SyncManager::do_sync - total: 3604 (99.5%)
  ...
  f-14310. libmegazord.so!places::storage::history::history_sync::fetch_outgoing - total: 2175 (60.0%)   ← hot
  ...

Top Functions (by self time):

  f-817.  libc.so!pread64 - self: 2113 (58.3%)   ← reading database pages from disk
  f-239.  libc.so!syscall - self: 661 (18.2%)     ← thread waiting on network I/O
  f-2098. libc.so!fsync - self: 546 (15.1%)       ← journal flush
  f-13207. libmegazord.so!sqlite3VdbeExec - self: 46 (1.3%)
  f-13370. libmegazord.so!sqlite3BtreeTableMoveto - self: 24 (0.7%)
  f-14267. libmegazord.so!pcache1FetchNoMutex - self: 21 (0.6%)
  ...
```

58% of samples are in `pread64` (f-817). SQLite is doing heavy disk I/O, reading many pages from the
history database. The `fsync` at 15% is from a different part of the sync (logins DB commit), not from
the read path.

---

## Bottom-up: what calls pread64?

```
profiler-cli thread samples-bottom-up --session fenix-sync
```

```
Bottom-Up Call Tree:
f-817. libc.so!pread64 [total: 58.3%, self: 58.3%]
└─ f-13142. libmegazord.so!seekAndRead [total: 58.3%, self: 0.0%]
   f-13141. libmegazord.so!unixRead [total: 58.3%, self: 0.0%]
   f-13140. libmegazord.so!sqlite3OsRead [total: 58.3%, self: 0.0%]
   └─ f-13368. libmegazord.so!readDbPage [total: 58.2%, self: 0.0%]
      f-13367. libmegazord.so!getPageNormal [total: 58.2%, self: 0.0%]
      f-13366. libmegazord.so!sqlite3PagerGet [total: 58.2%, self: 0.0%]
      └─ f-13365. libmegazord.so!getAndInitPage [total: 54.3%, self: 0.0%]
         └─ f-13379. libmegazord.so!moveToChild [total: 54.2%, self: 0.0%]
            └─ f-13370. libmegazord.so!sqlite3BtreeTableMoveto [total: 52.2%, self: 0.0%]
               └─ f-13369. libmegazord.so!sqlite3VdbeFinishMoveto [total: 51.1%, self: 0.0%]
                  f-13207. libmegazord.so!sqlite3VdbeExec [total: 51.1%, self: 0.0%]
                  f-13206. libmegazord.so!sqlite3Step [total: 51.1%, self: 0.0%]
                  f-13202. libmegazord.so!sqlite3_step [total: 51.1%, self: 0.0%]
                  f-13201. libmegazord.so!rusqlite::raw_statement::RawStatement::step [total: 51.1%, self: 0.0%]
                  f-13200. libmegazord.so!rusqlite::statement::Statement::step [total: 51.1%, self: 0.0%]
                  f-13313. libmegazord.so!<rusqlite::row::Rows as ...>::advance [total: 51.1%, self: 0.0%]
                  f-13304. libmegazord.so!rusqlite::row::Rows::next [total: 51.1%, self: 0.0%]
                  └─ ...fetch_outgoing iterator chain → sql_support::conn_ext::ConnExt::query_rows_and_then
f-239. libc.so!syscall [total: 18.2%, self: 18.2%]
└─ ...GeckoResult.poll → GeckoViewFetchClient.fetch → viaduct → CollectionUpdate::upload (network wait)
f-2098. libc.so!fsync [total: 15.1%, self: 15.1%]
└─ ...full_fsync → syncJournal → vdbeCommit → LoginsSyncEngine::mark_as_synchronized
```

The bottom-up tree confirms that every `pread64` call is driven by SQLite traversing a B-tree
(`moveToChild`, `sqlite3BtreeTableMoveto`), which is what a full table scan looks like at the pager
level. It all flows up from the `fetch_outgoing` query, with no index to limit the rows touched.

The `syscall` branch (18%) is the network upload waiting on `GeckoViewFetchClient`, and `fsync` (15%)
is the logins journal flush -- two independent operations running on the same thread.

---

## Trace the hot path top-down

```
profiler-cli thread samples-top-down --session fenix-sync
```

```
Top-Down Call Tree:
f-2414. libc.so!_start_thread [total: 100.0%]
└─ f-2415. libc.so!__pthread_start(void*) [total: 100.0%]
   f-2416. libart.so!art::Thread::CreateCallback(void*) [total: 100.0%]
   f-1632. libart.so!art::ArtMethod::Invoke(...) [total: 100.0%]
   f-1633. libart.so!art_quick_invoke_stub [total: 100.0%]
   f-13000. base.odex!kotlinx.coroutines.scheduling.CoroutineScheduler$Worker.run [total: 100.0%]
   └─ f-13001. base.odex!kotlinx.coroutines.internal.LimitedDispatcher$Worker.run [total: 100.0%]
      f-326.   base.odex!kotlinx.coroutines.DispatchedTask.run [total: 100.0%]
      f-327.   base.odex!kotlin.coroutines.jvm.internal.BaseContinuationImpl.resumeWith [total: 100.0%]
      f-13931. base.apk!mozilla.components.service.fxa.sync.WorkManagerSyncWorker$doWork$2.invokeSuspend [total: 100.0%]
      f-13932. base.apk!mozilla.components.service.fxa.sync.WorkManagerSyncWorker.access$doSync [total: 100.0%]
      └─ f-13947. base.apk!mozilla.appservices.syncmanager.SyncManager.sync [total: 99.5%]
         [JNA/UniFFI bridge frames]
         f-13972. libmegazord.so!sync_manager::manager::SyncManager::sync [total: 99.5%]
         f-14013. libmegazord.so!sync_manager::manager::SyncManager::do_sync [total: 99.5%]
         └─ f-14014. libmegazord.so!sync15::client::sync_multiple::sync_multiple_with_command_processor [total: 99.4%]
            f-14015. libmegazord.so!sync15::client::sync_multiple::SyncMultipleDriver::sync [total: 99.4%]
            └─ f-14112. libmegazord.so!SyncMultipleDriver::sync_engines [total: 99.4%]
               f-14114. libmegazord.so!sync15::client::sync::synchronize_with_clients_engine [total: 99.4%]
               ├─ f-14308. libmegazord.so!HistorySyncEngine::apply [total: 60.0%]
               │  f-14309. libmegazord.so!places::history_sync::plan::get_planned_outgoing [total: 60.0%]
               │  f-14310. libmegazord.so!places::storage::history::history_sync::fetch_outgoing [total: 60.0%]   ← hot
               │    sql_support::conn_ext::ConnExt::query_rows_and_then
               │    [rusqlite iterator chain]
               │    rusqlite::statement::Statement::step → sqlite3Step
               │    → pread64 (58.3% self time)
               ├─ f-14109. libmegazord.so!CollectionUpdate::upload [total: 15.8%]
               │  (network: uploading records to the sync server, blocked on GeckoViewFetchClient)
               ├─ f-14121. libmegazord.so!LoginsSyncEngine::set_uploaded [total: 12.4%]
               │  mark_as_synchronized → UncheckedTransaction::commit
               │  → sqlite3VdbeHalt → vdbeCommit → syncJournal → fsync
               └─ f-14347. libmegazord.so!HistorySyncEngine::set_uploaded [total: 3.9%]
```

`places::storage::history::history_sync::fetch_outgoing` (f-14310) is the hot path at 60% of the sync
window. It runs a SQL query through rusqlite, and the cost is almost entirely disk reads (`pread64`).

The query it runs scans `moz_places` for records that need uploading:

```sql
SELECT ... FROM moz_places
WHERE (sync_change_counter > 0 OR sync_status != {Normal})
  AND NOT hidden
ORDER BY frecency DESC
LIMIT :max_places
```

There is no index on `sync_change_counter` or `sync_status`. SQLite reads the entire `moz_places` table
to evaluate the WHERE clause, then sorts the results by `frecency DESC`. On a phone with a large history
database and cold page cache, this causes hundreds of `pread64` calls to pull pages from storage.

---

## Isolate the fetch_outgoing subtree

```
profiler-cli filter push --root-at f-14310 --session fenix-sync
profiler-cli thread samples --session fenix-sync
```

```
Filters: [1] root-at: f-14310

Top Functions (by self time):

  f-817.  libc.so!pread64 - self: 2037 (93.7%)   ← nearly all self time is disk reads
  f-13370. libmegazord.so!sqlite3BtreeTableMoveto - self: 17 (0.8%)
  f-14267. libmegazord.so!pcache1FetchNoMutex - self: 16 (0.7%)
  f-13207. libmegazord.so!sqlite3VdbeExec - self: 15 (0.7%)
  f-14319. libmegazord.so!pcache1RemoveFromHash - self: 14 (0.6%)
  f-14315. libmegazord.so!sqlite3GetVarint - self: 8 (0.4%)
  ...
```

Filtering to just the `fetch_outgoing` subtree shows that 93.7% of its own samples bottom out in
`pread64`. This function is almost pure disk I/O: every iteration of the row cursor loads another
page from storage because none of the table rows can be skipped without reading them.

---

## Confirm: 60% of the sync window is this one query

In the 4-second sync window (6.9s to 10.9s), the breakdown is:
- `fetch_outgoing` (full table scan of `moz_places`): 60%
- `LoginsSyncEngine::mark_as_synchronized` (logins fsync): 12%
- `CollectionUpdate::upload` (network I/O): 16%
- Everything else: 12%

Two-thirds of the sync wall time is one SQL query with a missing index.

```
profiler-cli stop --session fenix-sync
```

---

## Summary

`history_sync::fetch_outgoing` issues a query against `moz_places` with a WHERE clause on
`sync_change_counter` and `sync_status`. Neither column is indexed, so SQLite must scan the entire table.
On a device with a large history database and cold storage, this results in ~2.6 seconds of `pread64`
calls to read all the table pages from disk. Adding an index on these columns would allow SQLite to find
only the rows that need syncing without touching the rest of the table.

Filed as https://bugzilla.mozilla.org/show_bug.cgi?id=1979764.
