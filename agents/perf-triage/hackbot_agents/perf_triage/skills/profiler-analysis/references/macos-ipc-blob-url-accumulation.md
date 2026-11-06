# macOS Firefox hang getting worse over time: extension blob URL accumulation and IPC I/O contention

https://profiler.firefox.com/public/325xgq000zjz7e3mdv85b4xhxn1jt6r03hjce3r

Bugzilla: https://bugzilla.mozilla.org/show_bug.cgi?id=1981051

## Profiled scenario

The user reported Firefox on macOS hanging for seconds at a time during normal web browsing. Typing, scrolling, and tab switching all pause and then catch up at once. The hangs get progressively worse as the session goes on, until the browser becomes completely frozen and unresponsive even to force-quit. The user was browsing with Google Meet, YouTube, Firestore-backed pages, and had several extensions including an enterprise extension called "SquareX Enterprise - Spreedly".

The profile was captured using the Firefox Profiler on macOS 15.5.0 with Firefox 141, with IPC Messages and Native Stacks enabled.

## Analysis

The reported symptom, hangs that get worse over time, immediately suggests accumulation of some resource. My first question is: where in the process tree is the time going?

The profile overview shows 13 processes. The top entry is the Parent Process with 1932ms of CPU in a 12.97-second window, which is reasonable. But something odd stands out immediately: the IPC I/O Parent thread (t-1) has 884ms of CPU time, nearly as much as the Parent's GeckoMain (889ms). The IPC I/O thread is normally a lightweight relay for passing messages between processes. Seeing it this heavy is a red flag.

Selecting the parent GeckoMain (t-0) and running `profiler-cli thread samples` shows 91% of its time in `mach_msg2_trap` (the macOS idle wait inside the event loop). The main thread is almost entirely idle. Yet when I check the IPC markers with `profiler-cli thread markers --min-duration 50`, I find 20 `IPCIn` markers each lasting between 3.96s and 4.20s. This is a contradiction: the main thread appears idle, but IPC messages from a content process are taking over four seconds to be processed.

The key insight about `IPCIn` marker durations: the duration is not the time the main thread spent executing the message handler. It is the queuing latency, the wall-clock time from when the sending process sent the message to when the receiving thread actually dispatched it. A 4.2-second `IPCIn` duration means the message sat in the queue for 4.2 seconds. All 20 of these long-latency messages originate from the same process (PID 51147, a content process hosting `mozilla.org` content).

Alongside the IPCIn pile-up, there is an `ExtensionParent` marker for `spreedly@onsqrx, api_event: webRequest.onCompleted` lasting 3.36 seconds. This overlaps with the blocked IPC window: both start around the same absolute timestamp and both clear at the same time. This is not a coincidence. The IPC I/O thread is stuck, which delays message delivery to the WebExtensions process, which in turn delays the whole `webRequest.onCompleted` lifecycle from completing.

To understand why the IPC I/O thread is stuck, I select it (t-1) and look at `profiler-cli thread samples`. The self-time breakdown is:
- 63.9% in `kevent` (normal idle waiting for I/O events)
- 32.0% in `__psynch_mutexwait`

That 32% blocked on a mutex is the problem. The top-down tree shows the full path: `OnFileCanReadWithoutBlocking -> NodeController::DropPeer -> LostConnectionToNode -> Node::DestroyAllPortsWithPeer -> NodeController::PortStatusChanged -> PortLink::OnPortStatusChanged -> TaskQueue::Dispatch -> MonitorAutoLock -> Monitor::Lock -> __psynch_mutexwait`.

When a content process dies, `DropPeer` is called on the IPC I/O thread. This triggers `DestroyAllPortsWithPeer`, which calls `PortStatusChanged` once for each port that had a peer on the dying process. Each `PortStatusChanged` call ends up calling `TaskQueue::Dispatch`, which needs to acquire the TaskQueue's `mQueueMonitor`. If that monitor is held by another thread (because tasks from that queue are currently running), the IPC I/O thread blocks. With a large number of ports, this happens many times in sequence, and the IPC I/O thread is stuck for seconds.

Nika confirmed in the bug that these ports are almost certainly `PRemoteLazyInputStream` actors, all sharing a single `TaskQueue` in the parent process. When the process dies and all its ports are being cleaned up, there is a thundering herd of dispatches to that one queue, creating extreme monitor contention.

That explains why the IPC I/O thread is stuck. But why are there so many ports? That brings me to the third finding.

Looking at the Isolated Service Worker process (p-2), its GeckoMain (t-5) was freshly spawned during the profile. The top-down tree shows it spending 24.3% of its early CPU time processing a single message: `PContent::Msg_InitBlobURLs`. This message is sent from the parent to every new child process at spawn time, and it contains the full list of all registered `blob:` URLs under broadcast principals (system principal and addon principals). The deserialization is in `IPC::ReadSequenceParamImpl<BlobURLRegistrationData>`, iterating over a very large array. Each entry contains a principal, an `IPCBlob`, and a `RemoteLazyInputStream` -- each `RemoteLazyInputStream` is exactly one of those ports that creates a `PRemoteLazyInputStream` actor.

So the picture comes together: the more blob URLs registered under addon principals, the larger `Msg_InitBlobURLs` becomes, the more `PRemoteLazyInputStream` actors are created in each new process, and the worse the `DestroyAllPortsWithPeer` contention becomes when any of those processes dies.

Markus Stange identified the source with a memory report: the `spreedly@onsqrx` extension (UUID `15237d20-dab3-4143-b9e7-1bc847749b7d`) had created 202,011 blob URLs without revoking them. That extension, installed as enterprise software the user could not disable, was leaking one blob URL per network request it monitored, and never calling `URL.revokeObjectURL()`. Over hours of browsing, the list grew to 200k entries. Every new process spawn had to receive all of them. Every process death had to tear down all the corresponding actors. The browser got slower with each passing hour because the list never stopped growing.

There are two independent Firefox-side issues here. The first is that `Msg_InitBlobURLs` scales linearly with the number of blob URLs, and there is no mechanism to lazily resolve addon blob URLs on demand instead of pushing all of them at spawn time (bug 1619943 tracks this). The second is that `DestroyAllPortsWithPeer` dispatching N tasks to the same `TaskQueue` creates severe monitor contention; reducing that contention (batch dispatches, or making the TaskQueue drain its queue with fewer lock acquisitions) would reduce the impact even without fixing the blob URL count (bug 1983309).

The surface presentation, an extension causing multi-second browser hangs, looks similar to the 1Password infinite recursion case. But the mechanism is completely different. There is no CPU-bound extension code here at all. The WebExtensions process is 99.2% idle throughout. The freezes are caused by IPC infrastructure contention in the parent process and the IPC I/O thread, triggered indirectly by an extension that accumulates resources it never releases.
