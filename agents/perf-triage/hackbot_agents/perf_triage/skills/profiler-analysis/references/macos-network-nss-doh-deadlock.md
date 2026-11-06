# Example analysis of a profile of Firefox networking frozen by an NSS deadlock

Profile (full thread set): https://share.firefox.dev/41Dg00o

Bugzilla: https://bugzilla.mozilla.org/show_bug.cgi?id=1979124 (related: https://bugzilla.mozilla.org/show_bug.cgi?id=1981578)

## Profiled scenario

On Firefox 141 on macOS, network requests stop loading entirely. Any URL entered in the address bar enters a perpetual loading cycle and never resolves. The browser UI stays responsive, but nothing loads. Quitting Firefox through the Dock menu does nothing, and eventually the only option is Force Quit.

Bug 1981578 is a user report of this same issue. The reporter there uploaded a profile, but shared it as a `profiler.firefox.com/from-file/...` URL. Those URLs only work locally as they are not uploaded yet. They cannot be opened by anyone else. The usable profile for this analysis comes from the parent bug 1979124, where a different reporter uploaded the profile properly via the share button, producing a `share.firefox.dev` link that anyone can open.

The reporter captured this profile after Markus Stange asked them to re-capture with "Bypass selections above and record all registered threads" checked in `about:profiling`. That option is what reveals the key threads in this investigation. Without it, the threads involved are hidden and the problem looks invisible.

## Analysis

### The Socket Thread is completely frozen

The profile spans 42 seconds. The Socket Thread shows 0ms of CPU activity across that entire window. This is the first and most striking signal. The Socket Thread is Firefox's entire networking I/O layer. When it stops doing anything, all network requests stop too.

Looking at the call stack for the Socket Thread, every single sample shows the same frame at the bottom:

```
libsystem_kernel.dylib!__psynch_mutexwait
  libsystem_pthread.dylib!_pthread_mutex_firstfit_lock_slow
    libnss3.dylib!PR_Lock
      libnss3.dylib!nssSession_EnterMonitor
        libnss3.dylib!find_objects
          libnss3.dylib!nssToken_FindObjectsByTemplate
            libnss3.dylib!nssToken_FindTrustForCertificate
              libnss3.dylib!nssTrustDomain_FindTrustForCertificate
                libnss3.dylib!stan_GetCERTCertificate
                  libnss3.dylib!STAN_GetCERTCertificateOrRelease
                    libnss3.dylib!CERT_NewTempCertificate
                      libnss3.dylib!ssl_DecodeResumptionToken
                        libnss3.dylib!SSLExp_SetResumptionToken
                          XUL!NSSSocketControl::SetResumptionTokenFromExternalCache(...)
                            XUL!nsSSLIOLayerAddToSocket(...)
                              ...
                                XUL!mozilla::net::nsSocketTransport::InitiateSocket()
```

The Socket Thread was in the middle of initiating a new TLS connection, trying to look up certificate trust for a TLS session resumption token. It called into NSS, which tried to acquire an NSS session monitor lock (`nssSession_EnterMonitor`), and has been blocked ever since. Something else is holding that lock.

### BgIOThreadPool #1 holds the NSS lock and is waiting for osclientcerts

The next thread to look at is `BgIOThreadPool #1`. Its stack tells the other half of the story:

```
libsystem_kernel.dylib!semaphore_wait_trap
  libdispatch.dylib!_dispatch_semaphore_wait_slow
    XUL!std::sys::sync::thread_parking::darwin::Parker::park
      XUL!std::thread::Thread::park
        XUL!std::thread::park
          XUL!futures_executor::local_pool::run_executor::{{closure}}
            XUL!futures_executor::local_pool::run_executor
              XUL!futures_executor::local_pool::block_on
                XUL!<osclientcerts::backend_macos::Backend as ...>::find_objects
                  XUL!osclientcerts::C_FindObjectsInit
                    libnss3.dylib!find_objects         <-- holds the NSS session lock here
                      libnss3.dylib!nssToken_FindObjectsByTemplate
                        libnss3.dylib!nssToken_TraverseCertificates
                          libnss3.dylib!NSSTrustDomain_TraverseCertificates
                            libnss3.dylib!PK11_ListCerts
                              XUL!nsNSSCertificateDB::GetCerts(...)
                                XUL!nsNSSCertificateDB::AsyncHasThirdPartyRoots(...)
```

This thread is running `nsNSSCertificateDB::AsyncHasThirdPartyRoots`, the DoH heuristics function that checks whether the system certificate store has third-party roots. That function calls `GetCerts` -> `PK11_ListCerts`, which acquires the NSS session lock and then, during the `find_objects` callback, calls into the `osclientcerts` PKCS#11 module.

The `osclientcerts` module dispatches the work asynchronously to the `osclientcerts` thread and then blocks via `futures_executor::local_pool::block_on`. The thread parks itself, waiting for the `osclientcerts` thread to wake it up with a result.

BgIOThreadPool #1 holds the NSS session lock the whole time it is parked.

### The osclientcerts thread is deadlocked with itself

The `osclientcerts` thread is also blocked in `semaphore_wait_trap`. Its stack shows:

```
libsystem_kernel.dylib!semaphore_wait_trap
  libdispatch.dylib!_dispatch_semaphore_wait_slow
    XUL!std::sys::sync::thread_parking::darwin::Parker::park
      XUL!std::thread::Thread::park
        XUL!std::thread::park
          XUL!futures_executor::local_pool::run_executor::{{closure}}
            XUL!futures_executor::local_pool::run_executor
              XUL!futures_executor::local_pool::block_on
                XUL!<...ThreadSpecificHandles as core::ops::drop::Drop>::drop
                  XUL!core::ptr::drop_in_place<osclientcerts::backend_macos::ThreadSpecificHandles>
                    XUL!core::ptr::drop_in_place<osclientcerts::backend_macos::Key>
                      XUL!core::ptr::drop_in_place<(core::result::Result<...>,core::...)>
                        XUL!osclientcerts::backend_macos::find_objects
                          XUL!<osclientcerts::backend_macos::Backend as ...>::find_objects::{{closure}}
```

The `osclientcerts` thread was already in the middle of running a `find_objects` task. During that work, a `ThreadSpecificHandles` value is being dropped. The `Drop` implementation calls `futures_executor::local_pool::block_on` to run some async cleanup. That `block_on` parks the `osclientcerts` thread waiting for the async future to complete.

But the future needs to run on the `osclientcerts` thread's local executor. The executor cannot process any futures because the thread is parked inside `block_on`. The `osclientcerts` thread is waiting for itself.

This self-deadlock in the `osclientcerts` thread means it will never wake up BgIOThreadPool #1. BgIOThreadPool #1 will never release the NSS session lock. The Socket Thread will never acquire that lock.

### The full cycle

Three threads form a chain, and none can make progress:

- **Socket Thread** is blocked waiting for the NSS session lock.
- **BgIOThreadPool #1** holds that lock, but is blocked waiting for the `osclientcerts` thread to complete its async `find_objects` work.
- The **osclientcerts thread** is deadlocked with itself: a `Drop` implementation called `block_on` on a future that needs the same thread to run it.

The trigger is `nsNSSCertificateDB::AsyncHasThirdPartyRoots`, called from the DoH heuristics system when Firefox starts. That function should not have been acquiring the NSS session lock on a background thread in a way that could overlap with certificate lookups on the Socket Thread. The fix (bug 1979124) was to remove the `thirdPartyRoots` check from DoHHeuristics entirely, which also removed the `nsNSSCertificateDB::AsyncHasThirdPartyRoots` function. Setting `network.trr.mode` to `5` in `about:config` was a confirmed workaround, since it disabled DoH heuristics and prevented the lock-acquiring code from running.
