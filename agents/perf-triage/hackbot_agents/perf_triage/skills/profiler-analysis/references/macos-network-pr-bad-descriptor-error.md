# Example analysis of a profile of Firefox failing network requests with PR_BAD_DESCRIPTOR_ERROR

Initial profile (incomplete): https://profiler.firefox.com/public/40dd5kd2h8v748vy3x311q3ecny7gjjpbek40k8

Good profile (with hidden threads): https://profiler.firefox.com/public/zhnstb1fghm5mdpm3s363930grr0rgvmhjhnzer/

Bugzilla: https://bugzilla.mozilla.org/show_bug.cgi?id=1980171

## Profiled scenario

After Firefox Nightly 143 had been running for an extended period with many tabs open, network requests started failing silently with `NS_ERROR_FAILURE` in DevTools. Visiting any new site would result in the navigation being immediately aborted. Restarting Firefox resolved the issue temporarily.

The reporter (Rob Wu) profiled the issue using the Firefox Profiler on macOS 15.5.0 while reproducing with a plain HTTP site (`http://coolgoodtranscendentjoke.neverssl.com/online`) to eliminate TLS as a factor.

## Analysis

### The first profile is missing critical threads

When I load the first profile Rob shared, something stands out immediately: it only contains 2 threads.

```
This profile contains 2 threads across 1 processes.

  p-0: Parent Process [pid 54986]
    t-0: GeckoMain - 172.958ms
    t-1: Socket Thread - 22.970ms
```

For a networking investigation, this is a red flag. Firefox's networking stack runs across several threads. The Socket Thread handles connection I/O, but DNS resolution happens on a pool of DNS Resolver threads. None of those are visible here.

Looking at the Socket Thread, we can see `nsHostResolver::ResolveHost` markers for `overheid.nl` (the domain Rob was testing with at the time), and a `DispatchTransaction` marker spanning 341ms. That is a very long time for a connection to be dispatched. But we cannot see what DNS resolution was doing, whether it completed successfully, or whether any of the underlying socket operations logged anything useful.

The network markers on GeckoMain tell a similar story: the page load for `https://overheid.nl/testhere` spans the entire 355ms profile duration but we can't tell from this profile alone whether the failure happened at the DNS stage, the TCP stage, or somewhere in NSPR's polling layer.

When Andrew Creskey reviewed this profile, he noted: "this last one is missing some threads like the DNS resolver." He suspected that Rob had not checked "Include Hidden Threads" in the profiler UI when uploading the profile. Without that option, the profiler omits threads that are in the thread pool but showed no activity in the currently selected time range, even though they may have been relevant to the issue.

The ask: capture a new profile with "Include Hidden Threads" enabled.

### The second profile reveals the full picture

With hidden threads included, the profile looks very different:

```
This profile contains 21 threads across 1 processes.

  p-0: Parent Process [pid 54986]
    t-0: GeckoMain - 19.411ms
    t-1: Socket Thread - 15.084ms
    t-19: StreamTrans #4416 - 4.044ms
    t-16: DNS Resolver #347 - 0.488ms
    t-10: DNS Resolver #339 - 0.391ms
    t-15: DNS Resolver #346 - 0.350ms
    t-2: DNS Resolver #316 - 0.000ms
    t-3: DNS Resolver #317 - 0.000ms
    ... (10 more DNS Resolver threads)
```

Now we can see 16 DNS Resolver threads. Most of them are idle (0ms CPU) but 3 were active during this profile. The thread numbers are telling: they range from #316 to #349. Since Firefox creates DNS Resolver threads sequentially as needed, numbers in the 300s mean this Firefox session has been running long enough to have created and reused hundreds of resolver threads. This is one early hint that the session has accumulated significant state.

Even more striking is `StreamTrans #4416`. HTTP/2 stream transactions are numbered sequentially too. #4416 means this browser session has processed thousands of HTTP/2 stream transactions. Both signals point to the same thing: this is a very long-running Firefox instance.

### DNS resolution completed fine

Selecting `DNS Resolver #347` (the most active resolver thread) and looking at its log markers shows a clean DNS lifecycle:

```
Marker: LogMessages (nsHostResolver)
  "DNS lookup thread - Calling getaddrinfo for host [coolgoodtranscendentjoke.neverssl.com]."
  Time: 0.5ms into profile

Marker: LogMessages (nsHostResolver)
  "DNS lookup thread - lookup completed for host [coolgoodtranscendentjoke.neverssl.com]: success."
  Time: 33ms into profile

Marker: LogMessages (nsHostResolver)
  "Caching host [coolgoodtranscendentjoke.neverssl.com] record for 60 seconds (grace 60)."
  Time: 33ms into profile
```

DNS resolved successfully in about 32ms. The problem is not DNS.

### The Socket Thread sat blocked for 277ms waiting on a bad socket

Looking at the Socket Thread's `SocketTransportService::Poll` markers:

```
m-6: 32.40ms   Poll count: 17, Poll timeout: NO_TIMEOUT   (0ms - 32ms)
m-8: 0.153ms   Poll count: 17, Poll timeout: NO_WAIT      (32ms - 32.15ms)
m-7: 0.191ms   Poll count: 18, Poll timeout: NO_WAIT      (35ms - 35.2ms)
m-5: 277.19ms  Poll count: 18, Poll timeout: NO_TIMEOUT   (40ms - 317ms)
m-9: 0.144ms   Poll count: 17, Poll timeout: NO_WAIT      (320ms - 320.15ms)
```

The sequence here is readable:

1. While DNS is resolving, the Socket Thread polls 17 existing sockets every iteration (m-6, covering 0 to 32ms).
2. DNS completes at 32ms. Two quick NO_WAIT polls happen as the stack processes the DNS result.
3. Between m-7 and m-5, the poll count jumps from 17 to 18. A new socket was created for the `coolgoodtranscendentjoke.neverssl.com` connection.
4. The Socket Thread then blocks for 277ms with that new socket in the poll list (m-5, NO_TIMEOUT, poll count 18).
5. After m-5 returns, the poll count drops back to 17. The new socket was removed, meaning the connection failed.

The Socket Thread has zero CPU samples during m-5. It is fully blocked in PR_Poll, the NSPR socket polling abstraction that wraps the `select()` syscall on macOS.

After the profile, `DispatchTransaction` on the GeckoMain thread shows the total time from dispatching the transaction to its completion was 287ms, and `nsHttpChannel::OnStopRequest` is called shortly after. The request failed.

### What caused the 277ms block on a fresh socket?

The log output from the bug (captured separately via `about:logging`) confirmed: when the Socket Thread eventually returned from PR_Poll, the new socket had `outFlags=16`. The value 16 in hex is 0x10, which is `PR_POLL_NVAL`, the NSPR flag meaning "the file descriptor is invalid."

NSPR's `select()`-based poll implementation on macOS defines `FD_SETSIZE` as 4096. When it encounters a file descriptor whose numeric value exceeds 4096, it cannot safely include it in the `fd_set` bitmap (doing so would write past the end of the stack-allocated struct), so it marks the fd as `PR_POLL_NVAL` instead. The socket itself was created successfully by the OS, but its fd number was too large for NSPR to poll.

Why was the fd number so high? Because this Firefox session had accumulated an enormous number of open file descriptors, as seen in the `lsof` output Rob captured: 5564 Unix domain sockets, of which 5353 pointed to `(none)`, suggesting a file descriptor leak. The high thread numbers (#347 DNS resolver, #4416 stream transport) visible in the profile were consistent with this long-running, resource-accumulating session.

The profiler alone cannot show the fd number assigned to a socket. But the profile with hidden threads makes the context clear: many long-lived threads, high sequence numbers, and a 277ms block on a fresh socket that should have connected immediately. Together these point toward a resource exhaustion problem.

The root cause was a file descriptor leak introduced in bug 1964600. The fix (bug 1981002) addressed the leak, and a crash guard was added (this bug) to detect out-of-range fds explicitly rather than silently failing.
