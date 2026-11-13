# profiler-cli walkthrough: NSS deadlock from DoH heuristics freezing all networking

Companion to `macos-network-nss-doh-deadlock.md`. Reproduces the same findings using `profiler-cli` with annotated output.

Profile (full thread set): https://share.firefox.dev/41Dg00o

---

## Load the profile and get an overview

```
profiler-cli load https://share.firefox.dev/41Dg00o --session nss-deadlock
profiler-cli profile info --session nss-deadlock
```

```
Name: Firefox 141 – macOS 15.6.0
Platform: macOS 15.6.0

This profile contains 239 threads across 11 processes.

Top processes and threads by CPU usage:
  p-0: Parent Process [pid 87060]
    t-18: Renderer [tid 2195712] - 2131.337ms
    t-0: GeckoMain [tid 2195644] - 1391.608ms
    t-54: BackgroundThreadPool #2 [tid 2195920] - 550.293ms
    t-42: WRRenderBackend#1 [tid 2195852] - 488.968ms
    t-31: Compositor [tid 2195725] - 204.447ms
    ...
    t-3: Socket Thread [tid 2195672] - 0.000ms
    t-35: osclientcerts [tid 2195803] - 0.000ms
    t-15: BgIOThreadPool #1 [tid 2195697] - 0.000ms
```

239 threads across 11 processes. The profile spans 42 seconds. The most important observation: the Socket Thread (t-3), the osclientcerts thread (t-35), and BgIOThreadPool #1 (t-15) all show 0.000ms CPU. Three threads that should be doing work are completely inactive over 42 seconds. That is not normal.

---

## Inspect the Socket Thread

```
profiler-cli thread select t-3 --session nss-deadlock
profiler-cli thread samples-top-down --session nss-deadlock
```

```
Thread: Socket Thread

Top-Down Call Tree:
f-0. (root) [total: 100.0%, self: 0.0%]
└─ f-2789. libsystem_pthread.dylib!_pthread_start [total: 100.0%, self: 0.0%]
   f-5562. libnss3.dylib!_pt_root [total: 100.0%, self: 0.0%]
   f-7184. XUL!nsThread::ThreadFunc(void*) [total: 100.0%, self: 0.0%]
   ...
   f-7419. XUL!mozilla::net::nsSocketTransport::OnSocketEvent(...) [total: 100.0%, self: 0.0%]
   f-7430. XUL!mozilla::net::nsSocketTransport::InitiateSocket() [total: 100.0%, self: 0.0%]
   f-7431. XUL!mozilla::net::nsSocketTransport::BuildSocket(...) [total: 100.0%, self: 0.0%]
   f-7432. XUL!nsSSLSocketProvider::NewSocket(...) [total: 100.0%, self: 0.0%]
   f-7433. XUL!nsSSLIOLayerNewSocket(...) [total: 100.0%, self: 0.0%]
   f-7434. XUL!nsSSLIOLayerAddToSocket(...) [total: 100.0%, self: 0.0%]
   f-7435. XUL!NSSSocketControl::SetResumptionTokenFromExternalCache(PRFileDesc*) [total: 100.0%, self: 0.0%]
   f-7420. libnss3.dylib!SSLExp_SetResumptionToken [total: 100.0%, self: 0.0%]
   f-7421. libnss3.dylib!ssl_DecodeResumptionToken [total: 100.0%, self: 0.0%]
   f-7422. libnss3.dylib!CERT_NewTempCertificate [total: 100.0%, self: 0.0%]
   f-7423. libnss3.dylib!STAN_GetCERTCertificateOrRelease [total: 100.0%, self: 0.0%]
   f-7424. libnss3.dylib!stan_GetCERTCertificate [total: 100.0%, self: 0.0%]
   f-7425. libnss3.dylib!nssTrustDomain_FindTrustForCertificate [total: 100.0%, self: 0.0%]
   f-7426. libnss3.dylib!nssToken_FindTrustForCertificate [total: 100.0%, self: 0.0%]
   f-7427. libnss3.dylib!nssToken_FindObjectsByTemplate [total: 100.0%, self: 0.0%]
   f-7428. libnss3.dylib!find_objects [total: 100.0%, self: 0.0%]
   f-7429. libnss3.dylib!nssSession_EnterMonitor [total: 100.0%, self: 0.0%]   ← waiting for NSS session lock
   f-2903. libnss3.dylib!PR_Lock [total: 100.0%, self: 0.0%]
   f-531. libsystem_pthread.dylib!_pthread_mutex_firstfit_lock_slow [total: 100.0%, self: 0.0%]
   f-532. libsystem_kernel.dylib!__psynch_mutexwait [total: 100.0%, self: 100.0%]   ← 100% of all samples
```

Every sample in the 42-second profile shows the Socket Thread blocked on `__psynch_mutexwait` inside `nssSession_EnterMonitor`. It called into NSS to look up certificate trust for a TLS session resumption token while initiating a connection, and has been waiting for an NSS session lock ever since.

The 0ms CPU figure from `profile info` is confirmed here: this thread has not executed a single instruction in 42 seconds. Something else holds that lock.

---

## Find the lock holder: BgIOThreadPool #1

```
profiler-cli thread select t-15 --session nss-deadlock
profiler-cli thread samples-top-down --session nss-deadlock
```

```
Thread: BgIOThreadPool #1

Top-Down Call Tree:
f-0. (root) [total: 100.0%, self: 0.0%]
└─ f-2789. libsystem_pthread.dylib!_pthread_start [total: 100.0%, self: 0.0%]
   ...
   f-7612. XUL!nsThreadPool::Run() [total: 100.0%, self: 0.0%]
   f-7613. XUL!mozilla::detail::RunnableFunction<nsNSSCertificateDB::AsyncHasThirdPartyRoots(...)::$_0>::Run() [total: 100.0%, self: 0.0%]
   f-7614. XUL!nsNSSCertificateDB::AsyncHasThirdPartyRoots(...)::$_0::operator()() const [total: 100.0%, self: 0.0%]
   f-7617. XUL!nsNSSCertificateDB::AsyncHasThirdPartyRoots(...)::$_0::operator()() const::operator()() const [total: 100.0%, self: 0.0%]
   f-7618. XUL!nsNSSCertificateDB::GetCerts(nsTArray<RefPtr<nsIX509Cert> >&) [total: 100.0%, self: 0.0%]
   f-7602. libnss3.dylib!PK11_ListCerts [total: 100.0%, self: 0.0%]
   f-7603. libnss3.dylib!NSSTrustDomain_TraverseCertificates [total: 100.0%, self: 0.0%]
   f-7604. libnss3.dylib!nssToken_TraverseCertificates [total: 100.0%, self: 0.0%]
   f-7605. libnss3.dylib!nssPKIObjectCollection_AddInstanceAsObject [total: 100.0%, self: 0.0%]
   f-7606. libnss3.dylib!cert_createObject [total: 100.0%, self: 0.0%]
   f-7607. libnss3.dylib!nssTrustDomain_AddCertsToCache [total: 100.0%, self: 0.0%]
   f-7608. libnss3.dylib!add_cert_to_cache [total: 100.0%, self: 0.0%]
   f-7609. libnss3.dylib!STAN_GetCERTCertificate [total: 100.0%, self: 0.0%]
   f-7424. libnss3.dylib!stan_GetCERTCertificate [total: 100.0%, self: 0.0%]
   f-7610. libnss3.dylib!fill_CERTCertificateFields [total: 100.0%, self: 0.0%]
   f-7611. libnss3.dylib!nssTrust_GetCERTCertTrustForCert [total: 100.0%, self: 0.0%]
   f-7425. libnss3.dylib!nssTrustDomain_FindTrustForCertificate [total: 100.0%, self: 0.0%]
   f-7426. libnss3.dylib!nssToken_FindTrustForCertificate [total: 100.0%, self: 0.0%]
   f-7427. libnss3.dylib!nssToken_FindObjectsByTemplate [total: 100.0%, self: 0.0%]
   f-7428. libnss3.dylib!find_objects [total: 100.0%, self: 0.0%]   ← inside NSS, holds session lock
   f-7619. XUL!osclientcerts::C_FindObjectsInit [total: 100.0%, self: 0.0%]
   f-7620. XUL!rsclientcerts::manager::Manager<B>::start_search [total: 100.0%, self: 0.0%]
   f-7621. XUL!rsclientcerts::manager::Manager<B>::maybe_find_new_objects [total: 100.0%, self: 0.0%]
   f-7622. XUL!<osclientcerts::backend_macos::Backend as ...>::find_objects [total: 100.0%, self: 0.0%]
   f-7623. XUL!futures_executor::local_pool::block_on [total: 100.0%, self: 0.0%]
   f-7624. XUL!futures_executor::local_pool::run_executor [total: 100.0%, self: 0.0%]
   ...
   f-7628. XUL!std::thread::park [total: 100.0%, self: 0.0%]
   f-7629. XUL!std::thread::Thread::park [total: 100.0%, self: 0.0%]
   f-7630. XUL!std::sys::sync::thread_parking::darwin::Parker::park [total: 100.0%, self: 0.0%]
   f-7615. libdispatch.dylib!_dispatch_semaphore_wait_slow [total: 100.0%, self: 0.0%]
   f-7616. libsystem_kernel.dylib!semaphore_wait_trap [total: 100.0%, self: 100.0%]   ← blocked
```

BgIOThreadPool #1 is running `nsNSSCertificateDB::AsyncHasThirdPartyRoots`, a function called by the DoH heuristics system at startup. It acquired the NSS session lock (inside `PK11_ListCerts` -> `find_objects`), and then the `find_objects` callback dispatched async work to the osclientcerts thread. The thread is now parked via `block_on`, waiting for osclientcerts to complete the work.

The NSS session lock that the Socket Thread is waiting for is held here, by this blocked thread.

---

## Inspect the osclientcerts thread

```
profiler-cli thread select t-35 --session nss-deadlock
profiler-cli thread samples-top-down --session nss-deadlock
```

```
Thread: osclientcerts

Top-Down Call Tree:
f-0. (root) [total: 100.0%, self: 0.0%]
└─ f-2789. libsystem_pthread.dylib!_pthread_start [total: 100.0%, self: 0.0%]
   ...
   f-10190. XUL!moz_task::dispatcher::RunnableFunction<F>::allocate::Run [total: 100.0%, self: 0.0%]
   f-10191. XUL!moz_task::dispatcher::RunnableFunction<F>::Run [total: 100.0%, self: 0.0%]
   f-10192. XUL!moz_task::executor::schedule::{{closure}} [total: 100.0%, self: 0.0%]
   ...
   f-10194. XUL!async_task::runnable::Runnable::run [total: 100.0%, self: 0.0%]
   f-10195. XUL!async_task::raw::RawTask<F,T,S>::run [total: 100.0%, self: 0.0%]
   f-10196. XUL!<osclientcerts::backend_macos::Backend as ...>::find_objects::{{closure}} [total: 100.0%, self: 0.0%]
   f-10197. XUL!osclientcerts::backend_macos::find_objects [total: 100.0%, self: 0.0%]
   f-10198. XUL!core::ptr::drop_in_place<(core::result::Result<...>,...)> [total: 100.0%, self: 0.0%]
   f-10199. XUL!core::ptr::drop_in_place<osclientcerts::backend_macos::Key> [total: 100.0%, self: 0.0%]
   f-10200. XUL!core::ptr::drop_in_place<osclientcerts::backend_macos::ThreadSpecificHandles> [total: 100.0%, self: 0.0%]
   f-10201. XUL!<osclientcerts::backend_macos::ThreadSpecificHandles as core::ops::drop::Drop>::drop [total: 100.0%, self: 0.0%]
   f-7623. XUL!futures_executor::local_pool::block_on [total: 100.0%, self: 0.0%]   ← Drop calls block_on on its own thread
   f-7624. XUL!futures_executor::local_pool::run_executor [total: 100.0%, self: 0.0%]
   ...
   f-7628. XUL!std::thread::park [total: 100.0%, self: 0.0%]
   f-7629. XUL!std::thread::Thread::park [total: 100.0%, self: 0.0%]
   f-7630. XUL!std::sys::sync::thread_parking::darwin::Parker::park [total: 100.0%, self: 0.0%]
   f-7615. libdispatch.dylib!_dispatch_semaphore_wait_slow [total: 100.0%, self: 0.0%]
   f-7616. libsystem_kernel.dylib!semaphore_wait_trap [total: 100.0%, self: 100.0%]   ← blocked
```

The osclientcerts thread was already running a `find_objects` task. During that work, a `ThreadSpecificHandles` value went out of scope. Its `Drop` implementation calls `futures_executor::local_pool::block_on` to run async cleanup. That `block_on` parks the osclientcerts thread, waiting for the future to complete on the thread-local executor.

The problem: the future needs the osclientcerts thread to run it. But the osclientcerts thread is parked inside `block_on`. The thread is waiting for itself. It will never make progress.

---

## Confirm the self-deadlock with the bottom-up view

```
profiler-cli thread samples-bottom-up --session nss-deadlock
```

```
Bottom-Up Call Tree:
f-7616. libsystem_kernel.dylib!semaphore_wait_trap [total: 100.0%, self: 100.0%]
└─ f-7615. libdispatch.dylib!_dispatch_semaphore_wait_slow [total: 100.0%, self: 0.0%]
   f-7630. XUL!std::sys::sync::thread_parking::darwin::Parker::park [total: 100.0%, self: 0.0%]
   f-7629. XUL!std::thread::Thread::park [total: 100.0%, self: 0.0%]
   f-7628. XUL!std::thread::park [total: 100.0%, self: 0.0%]
   f-7627. XUL!futures_executor::local_pool::run_executor::{{closure}} [total: 100.0%, self: 0.0%]
   ...
   f-7623. XUL!futures_executor::local_pool::block_on [total: 100.0%, self: 0.0%]
   f-10201. XUL!<osclientcerts::backend_macos::ThreadSpecificHandles as core::ops::drop::Drop>::drop [total: 100.0%, self: 0.0%]
   f-10200. XUL!core::ptr::drop_in_place<osclientcerts::backend_macos::ThreadSpecificHandles> [total: 100.0%, self: 0.0%]
   f-10199. XUL!core::ptr::drop_in_place<osclientcerts::backend_macos::Key> [total: 100.0%, self: 0.0%]
   f-10198. XUL!core::ptr::drop_in_place<(core::result::Result<...>,...)> [total: 100.0%, self: 0.0%]
   f-10197. XUL!osclientcerts::backend_macos::find_objects [total: 100.0%, self: 0.0%]
   f-10196. XUL!<osclientcerts::backend_macos::Backend as ...>::find_objects::{{closure}} [total: 100.0%, self: 0.0%]
   f-10195. XUL!async_task::raw::RawTask<F,T,S>::run [total: 100.0%, self: 0.0%]
   f-10194. XUL!async_task::runnable::Runnable::run [total: 100.0%, self: 0.0%]
   ...
   f-10190. XUL!moz_task::dispatcher::RunnableFunction<F>::allocate::Run [total: 100.0%, self: 0.0%]
   f-26. XUL!nsThread::ProcessNextEvent(bool, bool*) [total: 100.0%, self: 0.0%]
   ...
   f-0. (root) [total: 100.0%, self: 0.0%]
```

The bottom-up view confirms the full call chain reading upward from the leaf: `semaphore_wait_trap` is reached from `block_on`, which was called by the `Drop` implementation of `ThreadSpecificHandles`, inside an active `find_objects` async closure running on the thread's own executor. The thread is blocked waiting for the executor to run a future that the executor cannot run because the thread is blocked.

---

## The deadlock cycle

Three threads form a cycle:

```
Socket Thread
  blocked waiting for: NSS session lock
    held by: BgIOThreadPool #1
      blocked waiting for: osclientcerts thread
        blocked waiting for: itself (Drop handler calling block_on)
```

- The osclientcerts thread is self-deadlocked and will never complete.
- BgIOThreadPool #1 is blocked waiting for osclientcerts and will never release the NSS lock.
- The Socket Thread is blocked waiting for the NSS lock and will never proceed.

All network connections that require TLS certificate work are frozen. No new HTTPS connection can be set up. The browser becomes unable to load any page.

The root trigger is `nsNSSCertificateDB::AsyncHasThirdPartyRoots`, invoked at Firefox startup by the DoH heuristics module (`DoHHeuristics.sys.mjs`). It should not have been acquiring the NSS session lock in a way that could overlap with the osclientcerts thread's internal async work. The fix removed the `thirdPartyRoots` check from DoHHeuristics entirely.

---

## Clean up

```
profiler-cli stop --session nss-deadlock
```
