# profiler-cli walkthrough: PR_BAD_DESCRIPTOR_ERROR from file descriptor exhaustion

Companion to `macos-network-pr-bad-descriptor-error.md`. Reproduces the same findings using `profiler-cli` with annotated output.

Initial profile (incomplete): https://profiler.firefox.com/public/40dd5kd2h8v748vy3x311q3ecny7gjjpbek40k8
Good profile (with hidden threads): https://profiler.firefox.com/public/zhnstb1fghm5mdpm3s363930grr0rgvmhjhnzer/

---

## Load the first (incomplete) profile and check thread count

```
profiler-cli load https://profiler.firefox.com/public/40dd5kd2h8v748vy3x311q3ecny7gjjpbek40k8 --session initial
profiler-cli profile info --session initial
```

```
Name: Firefox 143 – macOS 15.5.0
Platform: macOS 15.5.0

This profile contains 2 threads across 1 processes.

Top processes and threads by CPU usage:
  p-0: Parent Process [pid 54986]
    t-0: GeckoMain [tid 5905042] - 171.973ms
    t-1: Socket Thread [tid 5905114] - 22.431ms
```

Only 2 threads. For a networking bug, this is immediately suspicious. The DNS resolver thread pool, any active connection threads, and background networking threads are all absent. This profile was captured without "Include Hidden Threads" enabled in the profiler upload UI.

---

## Confirm the incomplete picture on the Socket Thread

```
profiler-cli thread select t-1 --session initial
profiler-cli thread markers --category Network --session initial
```

```
Markers in thread t-1 (Socket Thread) — 15 markers (filtered from 5247)

By Name (top 15):
  SocketTransportService::Poll    10 markers  (interval: min=123µs, avg=33.33ms, max=278.81ms)
    Examples: m-6 ✗ (278.81ms), m-7 ✗ (43.76ms), m-8 ✗ (9.70ms)
  nsHostResolver::ResolveHost     5 markers  (instant)
    Examples: m-1 ✗, m-2 ✗, m-3 ✗
```

There is a `SocketTransportService::Poll` running up to 278ms, and 5 DNS resolution markers on the Socket Thread side. These are references to DNS lookups being kicked off, but the DNS Resolver threads that actually perform the lookups are nowhere in this profile. Without those threads, we cannot tell whether DNS succeeded or failed, nor can we see what was happening in parallel.

The GeckoMain network markers show a `DispatchTransaction` lasting 287ms, which is very long for a connection setup, but the profile does not contain enough context to explain why.

At this point, we need a new profile. The ask: re-capture with "Include Hidden Threads" checked in the profiler UI before uploading.

---

## Load the second profile and compare thread count

```
profiler-cli load https://profiler.firefox.com/public/zhnstb1fghm5mdpm3s363930grr0rgvmhjhnzer/ --session good
profiler-cli profile info --session good
```

```
Name: Firefox 143 – macOS 15.5.0
Platform: macOS 15.5.0

This profile contains 21 threads across 1 processes.

Top processes and threads by CPU usage:
  p-0: Parent Process [pid 54986]
    t-0: GeckoMain [tid 5905042] - 18.555ms
    t-1: Socket Thread [tid 5905114] - 14.411ms
    t-19: StreamTrans #4416 [tid 8598105] - 4.044ms
    t-10: DNS Resolver #339 [tid 8531836] - 0.391ms
    t-15: DNS Resolver #346 [tid 8531926] - 0.350ms
    t-16: DNS Resolver #347 [tid 8531928] - 0.216ms
    t-2: DNS Resolver #316 [tid 8421236] - 0.000ms
    t-3: DNS Resolver #317 [tid 8421357] - 0.000ms
    ... (10 more DNS Resolver threads with 0.000ms CPU)
```

21 threads vs 2. Now we can see the DNS Resolver pool. Notice the thread numbers: #316 through #349. Firefox numbers these threads sequentially from process start. Being in the 300s means hundreds of DNS resolver threads have been created and recycled over the lifetime of this session. Also notice `StreamTrans #4416`, an HTTP/2 stream transport thread numbered in the thousands. Both are signals of a very long-running browser session that has accumulated substantial state.

---

## Confirm DNS resolved successfully

```
profiler-cli thread select t-16 --session good
profiler-cli thread markers --session good
```

```
Markers in thread t-16 (DNS Resolver #347) — 26 markers

By Name (top 15):
  LogMessages                  12 markers  (instant)
    Examples: m-2 ✓, m-3 ✓, m-4 ✓
  Histogram::Add                5 markers  (instant)
    Examples: m-7 ✗, m-8 ✗, m-9 ✗
  ...
  Awake                         1 markers  (interval: min=32.92ms, avg=32.92ms, max=32.92ms)
    Examples: m-1 ✗ (32.92ms)
  nsHostResolver::CompleteLookupLocked     1 markers  (instant)
    Examples: m-18 ✗
```

The DNS Resolver #347 thread was awake for 32.92ms. The LogMessages markers have stack traces, so we can inspect them to see exactly what the resolver was doing.

### Use --auto-group to read the DNS log sequence

```
profiler-cli thread markers --auto-group --session good
```

```
  LogMessages                  12 markers  (instant)
    Grouped by name:
    DNS lookup thread - Calling getaddrinfo for host [coolgoodtranscendentjoke.neverssl.com].
: 1 markers
      Examples: m-2 ✓
    DNS lookup thread - lookup completed for host [coolgoodtranscendentjoke.neverssl.com]: success.
: 1 markers
      Examples: m-3 ✓
    nsHostResolver::CompleteLookup coolgoodtranscendentjoke.neverssl.com 43a09acf0 0 resolver=0 stillResolving=0
: 1 markers
      Examples: m-4 ✓
    Caching host [coolgoodtranscendentjoke.neverssl.com] record for 60 seconds (grace 60).: 1 markers
      Examples: m-6 ✓
    CompleteLookup: coolgoodtranscendentjoke.neverssl.com has 2600:1f13:37c:1400:ba21:7165:5fc7:736e
: 1 markers
      Examples: m-715 ✓
    CompleteLookup: coolgoodtranscendentjoke.neverssl.com has 34.223.124.45
: 1 markers
      Examples: m-716 ✓
    STS dispatch [4f2a84080]
: 1 markers
      Examples: m-718 ✓
    PollableEvent::Signal
: 1 markers
      Examples: m-719 ✓
    ...
```

`--auto-group` groups the LogMessages by their log text, turning 12 anonymous instant markers into a readable DNS sequence. We can see `getaddrinfo` was called, completed with success, returned both an IPv6 and IPv4 address, cached the record for 60 seconds, and signaled the Socket Thread to wake up. DNS is fully healthy.

```
profiler-cli marker info m-2 --session good
```

```
Marker m-2: LogMessages - LogMessages

Type: Log
Category: Other
Time: 173µs (instant)
Thread: t-16 (DNS Resolver #347)

Fields:
  Module: nsHostResolver
  Name: DNS lookup thread - Calling getaddrinfo for host [coolgoodtranscendentjoke.neverssl.com].
```

```
profiler-cli marker info m-3 --session good
```

```
Marker m-3: LogMessages - LogMessages

Type: Log
Category: Other
Time: 32.89ms (instant)
Thread: t-16 (DNS Resolver #347)

Fields:
  Module: nsHostResolver
  Name: DNS lookup thread - lookup completed for host [coolgoodtranscendentjoke.neverssl.com]: success.
```

DNS completed successfully in about 32ms. The problem is not at the DNS layer.

---

## Find the failure on the Socket Thread

```
profiler-cli thread select t-1 --session good
profiler-cli thread markers --category Network --session good
```

```
Markers in thread t-1 (Socket Thread) — 16 markers (filtered from 3426)

By Name (top 15):
  SocketTransportService::Poll    12 markers  (interval: min=108µs, avg=25.91ms, max=277.19ms)
    Examples: m-64 ✗ (277.19ms), m-65 ✗ (32.40ms), m-66 ✗ (191µs)
  nsHostResolver::ResolveHost     4 markers  (instant)
    Examples: m-33 ✗, m-34 ✗, m-35 ✗
```

### Use --auto-group to read the Poll sequence

```
profiler-cli thread markers --category Network --auto-group --session good
```

The `SocketTransportService::Poll` section groups markers by their `Details` field content, which encodes both the socket count and timeout mode:

```
  SocketTransportService::Poll    12 markers  (interval: min=108µs, avg=25.91ms, max=277.19ms)
    Grouped by name:
    Poll count: 17, Poll timeout: NO_WAIT: 8 markers (avg=132µs, max=153µs)    ← normal drain polls
      Examples: m-67 ✗ (153µs), m-68 ✗ (144µs), m-200 ✗ (138µs)
    Poll count: 18, Poll timeout: NO_WAIT: 2 markers (avg=150µs, max=191µs)    ← quick checks after new socket
      Examples: m-66 ✗ (191µs), m-203 ✗ (108µs)
    Poll count: 17, Poll timeout: NO_TIMEOUT: 1 markers (avg=32.40ms, max=32.40ms)   ← waiting for DNS signal
      Examples: m-65 ✗ (32.40ms)
    Poll count: 18, Poll timeout: NO_TIMEOUT: 1 markers (avg=277.19ms, max=277.19ms) ← the suspicious block
      Examples: m-64 ✗ (277.19ms)
```

The groups immediately show the anomaly: there is exactly one 277ms poll with count 18, and all the other NO_TIMEOUT polls use count 17. A socket was added and then the thread blocked for 277ms waiting for it to do anything. Let's inspect those key markers.

```
profiler-cli marker info m-65 --session good
```

```
Marker m-65: SocketTransportService::Poll
  Time: 660µs - 33.06ms (32.40ms)
  Details: Poll count: 17, Poll timeout: NO_TIMEOUT
```

```
profiler-cli marker info m-66 --session good
```

```
Marker m-66: SocketTransportService::Poll
  Time: 35.23ms - 35.42ms (191µs)
  Details: Poll count: 18, Poll timeout: NO_WAIT
```

```
profiler-cli marker info m-64 --session good
```

```
Marker m-64: SocketTransportService::Poll
  Time: 40.05ms - 317.25ms (277.19ms)
  Details: Poll count: 18, Poll timeout: NO_TIMEOUT
```

```
profiler-cli marker info m-68 --session good
```

```
Marker m-68: SocketTransportService::Poll
  Time: 321.62ms - 321.76ms (144µs)
  Details: Poll count: 17, Poll timeout: NO_WAIT
```

The sequence:

- **0.66ms to 33ms** (m-65): 17 sockets being polled while DNS resolves. Normal.
- **35ms** (m-66): Quick NO_WAIT poll, count still 18. A new socket was just created for the `coolgoodtranscendentjoke.neverssl.com` connection.
- **40ms to 317ms** (m-64): The Socket Thread blocks for **277ms** with NO_TIMEOUT while polling those 18 sockets. This is the suspicious one. A fresh connection socket should either connect within milliseconds (local network conditions) or fail fast. Instead it sits here for 277ms.
- **321ms** (m-68): Poll count drops back to 17. The new socket was removed, meaning it failed.

### Zoom into the 277ms window to confirm

```
profiler-cli zoom push m-64 --session good
profiler-cli thread samples --session good
```

```
Pushed view range: ts-Bg (40.055ms) to ts-X (317.25ms) (duration: 277.19ms)
  Zoomed to: Marker m-64 - SocketTransportService::Poll

Top Functions (by self time):

  f-4433. libsystem_kernel.dylib!__select - self: 277 (100.0%)    ← entire window is one syscall
```

Every sample in the 277ms window is `__select`. The Socket Thread has zero CPU samples during that window. It is fully blocked in the OS-level `select()` syscall via NSPR's `PR_Poll`.

```
profiler-cli zoom pop --session good
```

### Bottom-up call tree confirms the blocking path

```
profiler-cli thread samples-bottom-up --session good
```

```
Bottom-Up Call Tree:
f-4433. libsystem_kernel.dylib!__select [total: 95.1%, self: 95.1%]
└─ f-4530. libnss3.dylib!_pr_poll_with_poll [total: 95.1%, self: 0.0%]
   f-4432. libnss3.dylib!PR_Poll [total: 95.1%, self: 0.0%]
   f-4437. XUL!mozilla::net::Poll(mozilla::BaseTimeDuration<...>) [total: 95.1%, self: 0.0%]
   f-4436. XUL!mozilla::net::DoPollIteration(mozilla::BaseTimeDuration<...>*) [total: 95.1%, self: 0.0%]
   f-4435. XUL!mozilla::net::nsSocketTransportService::Run() [total: 95.1%, self: 0.0%]
   f-4434. XUL!{virtual override thunk(..., mozilla::net::nsSocketTransportService::Run())} [total: 95.1%, self: 0.0%]
   f-106. XUL!nsThread::ProcessNextEvent(bool, bool*) [total: 95.1%, self: 0.0%]
   ...
   f-0. (root) [total: 95.1%, self: 0.0%]
```

The bottom-up tree reads from the hot leaf upward. `__select` accounts for 95.1% of total thread time, called through `_pr_poll_with_poll` and `PR_Poll` from NSPR, then `mozilla::net::Poll` and `DoPollIteration` from Firefox's socket service loop. The remaining 5% is logging overhead. The entire thread is doing one thing: waiting in `select()`.

---

## Verify the connection failure on GeckoMain

```
profiler-cli thread select t-0 --session good
profiler-cli thread markers --category Network --session good
```

```
  DispatchTransaction           1 markers  (interval: min=287.80ms, avg=287.80ms, max=287.80ms)
    Examples: m-219 ✗ (287.80ms)
  nsHttpChannel::OnStartRequest     1 markers  (interval: min=132µs, avg=132µs, max=132µs)
    Examples: m-221 ✗ (132µs)
  nsHttpChannel::OnStopRequest      1 markers  (instant)
    ...
```

```
profiler-cli marker info m-92 --session good
```

```
Marker m-92: DispatchTransaction

Type: Url
Category: Network
Time: 36.07ms - 323.87ms (287.80ms)
Thread: t-0 (Parent Process)

Fields:
  url: http://coolgoodtranscendentjoke.neverssl.com/online
  Duration: 287.80ms
```

The transaction took 287ms to dispatch (matching the long PR_Poll window on the Socket Thread), and `OnStopRequest` was called immediately after. The request failed.

---

## What the profile tells us, and what it doesn't

The profile shows:

1. DNS resolved successfully (DNS Resolver #347 completes in 32ms, returns both IPv4 and IPv6 addresses).
2. A new socket was created and added to the poll list (count goes from 17 to 18).
3. The Socket Thread blocked in `PR_Poll` / `__select` for 277ms with that socket.
4. The connection failed. The poll count returned to 17 and the request was aborted.

The profile does not directly show the socket's file descriptor number. But the high thread sequence numbers (DNS Resolver #347, StreamTrans #4416) are consistent with a long-running Firefox instance, and the separate `about:logging` trace from the same session showed `outFlags=16` when PR_Poll returned, which is `PR_POLL_NVAL` (0x10), the NSPR flag for an invalid file descriptor.

The root cause: the socket's fd number exceeded `FD_SETSIZE` (4096), so NSPR's `select()`-based polling could not safely include it in the fd_set bitmap and marked it as invalid. This happened because the session had accumulated thousands of leaked Unix domain sockets (5564 total visible in `lsof`, with 5353 pointing to `(none)`).

---

## Clean up

```
profiler-cli stop --session initial
profiler-cli stop --session good
```
