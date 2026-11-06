# profiler-cli walkthrough: extension blob URL accumulation causing macOS hangs

Companion to `macos-ipc-blob-url-accumulation.md`. Reproduces the same findings using `profiler-cli` with annotated output.

Profile: https://profiler.firefox.com/public/325xgq000zjz7e3mdv85b4xhxn1jt6r03hjce3r

---

## Load the profile and get an overview

```
profiler-cli load https://profiler.firefox.com/public/325xgq000zjz7e3mdv85b4xhxn1jt6r03hjce3r --session blob-accumulation
profiler-cli profile info --session blob-accumulation
```

```
Name: Firefox 141 – macOS 15.5.0
Platform: macOS 15.5.0

This profile contains 17 threads across 13 processes.

Top processes and threads by CPU usage:
  p-0: Parent Process [pid 46824] - 1932.049ms
    t-0: GeckoMain [tid 21410357] - 889.484ms
    t-1: IPC I/O Parent [tid 21410381] - 884.409ms    ← nearly as much as GeckoMain
    t-2: Renderer [tid 21410417] - 158.156ms
  p-2: Isolated Service Worker [pid 51280] - 594.446ms
    t-6: IPC I/O Child [tid 21490154] - 314.815ms
    t-5: GeckoMain [tid 21490148] - 279.631ms
  ...
  p-3: WebExtensions [pid 46843] - 56.685ms
    t-7: GeckoMain [tid 21410791] - 56.685ms
```

The IPC I/O Parent thread (t-1) has 884ms of CPU in a 12.97-second profile, nearly as much as GeckoMain itself. The IPC I/O thread is a message relay; seeing it this heavy is unusual and worth investigating. The WebExtensions process has only 56ms despite the user reporting extension-related hangs.

---

## Check GeckoMain: idle, but IPC messages are taking 4 seconds

```
profiler-cli thread select t-0 --session blob-accumulation
profiler-cli thread markers --min-duration 50 --session blob-accumulation
```

```
Markers in thread t-0 (Parent Process) — 380 markers (filtered from 155605)

By Name (top 15):
  IPCIn    315 markers  (interval: min=58.75ms, avg=2.09s, max=4.20s)
    Examples: m-11 ✗ (4.20s), m-12 ✗ (4.17s), m-13 ✗ (4.17s)
  IPCOut    48 markers  (interval: min=52.12ms, avg=168.63ms, max=1.02s)
    Examples: m-6 ✗ (1.02s), m-7 ✗ (312.00ms), m-8 ✗ (312.00ms)
  ExtensionParent    4 markers  (interval: min=104.10ms, avg=918.92ms, max=3.36s)
    Examples: m-16 ✗ (3.36s), m-17 ✗ (104.21ms), m-18 ✗ (104.14ms)
  Jank    1 markers  (interval: min=490.10ms ...)
    Examples: m-26 ✗ (490.10ms)
  ...
```

The marker durations here are queuing latency, not execution time. An `IPCIn` duration of 4.20 seconds means the message sat in the queue for 4.2 seconds before being dispatched, not that the handler ran for 4.2 seconds. The GeckoMain thread itself is 91% idle (`mach_msg2_trap`).

---

## Group IPCIn by source process to find the pattern

```
profiler-cli thread markers --auto-group --min-duration 50 --session blob-accumulation
```

```
By Name (top 15):
  IPCIn    315 markers  (interval: min=58.75ms, avg=2.09s, max=4.20s)
    Grouped by otherPid:
    51147: 109 markers (avg=2.38s, max=4.20s)
      Examples: m-11 ✗ (4.20s), m-12 ✗ (4.17s), m-13 ✗ (4.17s)   ← worst offender
    50927:  88 markers (avg=1.99s, max=3.45s)
      Examples: m-39 ✗ (3.45s), ...
    47674:  37 markers (avg=2.70s, max=3.29s)
      Examples: m-56 ✗ (3.29s), ...
    ...
  ExtensionParent    4 markers  (interval: min=104.10ms, avg=918.92ms, max=3.36s)
    Examples: m-16 ✗ (3.36s), ...
```

`--auto-group` breaks down the `IPCIn` pile-up by `otherPid`. PID 51147 is responsible for 109 of the delayed messages, with the highest average queuing latency. Every process shows multi-second delays, suggesting the blockage is in the IPC I/O thread itself, not a single sender.

---

## Inspect the worst markers to identify the process and timing

```
profiler-cli marker info m-11 --session blob-accumulation
```

```
Marker m-11: IPCIn
Time: 6.78s - 10.99s (4.20s)
Fields:
  Type: PRemoteSpellcheckEngine::Msg_CheckAsync
  From: https://mozilla.org (Thread ID: 21487432)
  To: Parent Process (Thread ID: 21410357)
  Other Pid: PID: 51147
```

```
profiler-cli marker info m-16 --session blob-accumulation
```

```
Marker m-16: ExtensionParent
Time: 7.59s - 10.95s (3.36s)
Fields:
  Details: spreedly@onsqrx, api_event: webRequest.onCompleted
```

The long `IPCIn` from PID 51147 and the delayed `ExtensionParent` event for `spreedly@onsqrx` both clear at roughly the same absolute time (10.95-10.99s). They share the same root cause.

---

## Zoom into the blockage window to confirm

```
profiler-cli zoom push m-11 --session blob-accumulation
profiler-cli thread samples --session blob-accumulation
```

```
Pushed view range: ts-L (6.785s) to ts-Uc (10.986s) (duration: 4.20s)
  Zoomed to: Marker m-11 - IPCIn
```

Zooming to a marker handle with `profiler-cli zoom push m-11` sets the view range to exactly the marker's time window. After zooming, `thread samples` confirms the main thread is almost entirely idle inside this 4.2-second window -- the backlog is not caused by the main thread doing CPU-intensive work.

```
profiler-cli zoom pop --session blob-accumulation
```

---

## Find the cause: IPC I/O Parent blocked on a mutex

```
profiler-cli thread select t-1 --session blob-accumulation
profiler-cli thread samples-top-down --max-lines 40 --session blob-accumulation
```

```
Top-Down Call Tree:
f-0. (root) [total: 100.0%, self: 0.0%]
└─ libsystem_pthread.dylib!_pthread_start [total: 100.0%, self: 0.0%]
   XUL!base::Thread::ThreadMain() [total: 100.0%, self: 0.0%]
   XUL!base::MessagePumpLibevent::Run(...) [total: 100.0%, self: 0.0%]
   ├─ MessagePumpLibevent::OnLibeventNotification [total: 99.7%, self: 0.0%]
   │  XUL!IPC::Channel::ChannelImpl::OnFileCanReadWithoutBlocking [total: 99.7%, self: 0.0%]
   │  ├─ XUL!mozilla::ipc::NodeController::DropPeer [total: 99.5%, self: 0.0%]
   │  │  XUL!mojo::core::ports::Node::LostConnectionToNode [total: 99.5%, self: 0.0%]
   │  │  XUL!mojo::core::ports::Node::DestroyAllPortsWithPeer [total: 99.5%, self: 0.0%]
   │  │  XUL!mozilla::ipc::NodeController::PortStatusChanged [total: 99.5%, self: 0.0%]
   │  │  XUL!mozilla::ipc::PortLink::OnPortStatusChanged [total: 99.2%, self: 0.0%]
   │  │  ├─ XUL!mozilla::TaskQueue::Dispatch [total: 96.5%, self: 0.0%]
   │  │  │  XUL!mozilla::MonitorAutoLock::MonitorAutoLock [total: 95.5%, self: 0.0%]
   │  │  │  XUL!mozilla::Monitor::Lock [total: 95.5%, self: 0.0%]
   │  │  │  libmozglue.dylib!mozilla::detail::MutexImpl::lock() [total: 95.5%, self: 0.0%]
   │  │  │  libsystem_kernel.dylib!__psynch_mutexwait [total: 95.4%, self: 95.4%]   ← stuck here
   │  │  └─ XUL!mozilla::ipc::PortLink::Clear() [total: 2.1%, self: 0.0%]
   │  └─ ... (messaging and other work, 0.2%)
   └─ ... (0.3%)
```

When a process dies, `DropPeer` is called on the IPC I/O thread. It calls `DestroyAllPortsWithPeer`, which calls `PortStatusChanged` once per port that the dead process had. Each call tries to acquire `mQueueMonitor` via `TaskQueue::Dispatch`. If the queue's runner thread is currently executing (holding the monitor), the IPC I/O thread blocks. With many ports, this repeats many times -- 95.4% of this thread is blocked on a single mutex.

While blocked, the IPC I/O thread cannot deliver incoming messages from any other process. That is why all content processes see 2-4 second IPC queuing delays simultaneously.

---

## Find the source of excess ports: Msg_InitBlobURLs

The number of ports per dying process depends on how many actors it has. To understand where these actors come from, look at a newly spawned process:

```
profiler-cli thread select t-5 --session blob-accumulation
profiler-cli zoom push 11.5,12.0 --session blob-accumulation
profiler-cli thread samples-top-down --max-lines 40 --session blob-accumulation
```

```
Top-Down Call Tree:
f-0. (root) [total: 100.0%, self: 0.0%]
   ...
   XUL!mozilla::ipc::MessageChannel::DispatchAsyncMessage [total: 99.2%, self: 0.0%]
   PContent::Msg_InitBlobURLs [total: 99.2%, self: 0.0%]
   XUL!mozilla::dom::PContentChild::OnMessageReceived [total: 99.2%, self: 0.0%]
   ├─ XUL!IPC::ReadParam<nsTArray<BlobURLRegistrationData>> [total: 85.8%, self: 0.0%]
   │  XUL!IPC::ParamTraits<nsTArray<...>>::Read [total: 85.8%, self: 0.0%]
   │  XUL!IPC::ReadSequenceParam<BlobURLRegistrationData, ...> [total: 85.8%, self: 0.0%]
   │  ├─ XUL!IPC::ReadSequenceParamImpl<BlobURLRegistrationData, ...> [total: 85.4%, self: 0.0%]
   │  │  XUL!IPC::ReadParam<BlobURLRegistrationData> [total: 85.4%, self: 0.0%]
   │  │  ├─ XUL!IPC::ReadParam<RefPtr<nsIPrincipal>> [total: 45.6%, self: 0.0%]
   │  │  ├─ XUL!IPC::ReadParam<IPCBlob> [total: 36.4%, self: 0.0%]
   │  │  └─ XUL!IPC::ReadParam<RemoteLazyStream> [total: 2.7%, self: 0.0%]
   │  └─ ... (0.4%)
   └─ ... (13.4%)
```

This process was just spawned. The very first real work it does is deserialize `PContent::Msg_InitBlobURLs`, a message from the parent containing the full list of all registered blob URLs under broadcast principals (system and addon). It is spending 99% of its early CPU time on this single message, with 85.8% just on the array deserialization.

Each `BlobURLRegistrationData` contains a `RemoteLazyInputStream`, which creates a `PRemoteLazyInputStream` actor when deserialized. Each actor is one port. When the process later dies, all those ports must be cleaned up via `DestroyAllPortsWithPeer`.

```
profiler-cli zoom pop --session blob-accumulation
```

---

## Understand why this gets worse over time

The parent sends only addon and system principal blob URLs in `Msg_InitBlobURLs`. If an extension creates blob URLs and never revokes them, those URLs accumulate in the parent's `gDataTable` across the session. Every new process spawn receives the ever-growing list.

A memory report from the user (captured while Firefox was slow) confirmed the source:
- Extension UUID `15237d20-dab3-4143-b9e7-1bc847749b7d` ("SquareX Enterprise - Spreedly") had created **202,011 blob URLs**.

That extension registers a new blob URL for each network request it monitors and never calls `URL.revokeObjectURL()`. After hours of browsing, the list reaches hundreds of thousands of entries.

---

## Summary of the causal chain

1. `spreedly@onsqrx` leaks blob URLs (one per network request, never revoked).
2. After hours, 200k+ blob URLs exist in the parent under the addon principal.
3. Every new process spawn receives all of them via `Msg_InitBlobURLs`, creating 200k+ `PRemoteLazyInputStream` actors.
4. When any such process dies, `DestroyAllPortsWithPeer` dispatches 200k+ notifications to a single `TaskQueue`, causing severe `mQueueMonitor` contention.
5. While the IPC I/O thread is blocked dispatching those notifications, it cannot deliver messages from other processes.
6. Incoming IPC from content processes queues up for 4+ seconds. Extension API events (`webRequest.onCompleted`) get delayed by the same blockage.

The WebExtensions process itself is 99.2% idle throughout. This is not a case of slow extension JavaScript. The freezes are entirely IPC infrastructure contention caused by resource accumulation.

---

## Key patterns to recognise

**`IPCIn` duration vs. execution time.** An `IPCIn` marker duration is the time from message send to dispatch, i.e. queuing latency. A 4-second `IPCIn` does not mean the main thread executed a handler for 4 seconds; it means the message waited in the queue. An idle-looking GeckoMain with long `IPCIn` durations points to the IPC I/O thread, not the main thread.

**`DestroyAllPortsWithPeer` blocking the IPC I/O thread.** When the IPC I/O thread spends significant time in `__psynch_mutexwait` inside `PortStatusChanged -> TaskQueue::Dispatch`, a process is dying with a very large number of actors. Look for what is creating those actors, typically `PRemoteLazyInputStream` from a large `Msg_InitBlobURLs`.

**`Msg_InitBlobURLs` deserialization cost in new processes.** If a freshly spawned process spends noticeable time in `IPC::ReadSequenceParamImpl<BlobURLRegistrationData>` during startup, the blob URL list is large. Each spawn costs more over time, and each death causes more IPC I/O contention.

**Extension blob URL leaks.** Extensions using `URL.createObjectURL()` without `URL.revokeObjectURL()` accumulate blob URLs under their addon principal. These are broadcast to all processes. Use `about:memory -> Measure` and search for `memory-blob-urls` to find the extension responsible and count its URLs.

```
profiler-cli stop --session blob-accumulation
```
