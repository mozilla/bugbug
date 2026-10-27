# Example analysis of a profile of Firefox with an extension causing 100% CPU in the WebExtensions process

https://profiler.firefox.com/public/jek6z1sbxtybk77ptwk79yna774r6ab6g2t8n6g

Bugzilla: https://bugzilla.mozilla.org/show_bug.cgi?id=1980009

## Profiled scenario

After restarting Firefox with Privacy Badger 2025.5.30 and 1Password 8.11.2.21 installed, pages stopped loading. Visiting `about:processes` showed the "Extensions" process stuck at 100% CPU indefinitely.

The profile was captured using the Firefox Profiler on macOS 15.5.0 with Firefox 143.

## Analysis

The symptom is clear: the browser is unresponsive and the Extensions process is pegged at 100% CPU. So the first thing I want to understand is what that Extensions process is actually doing.

Looking at the profile overview, the WebExtensions process (pid 39769) is responsible for 4054ms of CPU time in a 5.46-second profile, nearly the entire recording. Every other process (Parent, Web Content, etc.) is either idle or using very little CPU. So the Extensions process is the only show in town.

The WebExtensions GeckoMain thread (t-13) holds essentially all of that 4054ms. Selecting it and checking the top functions immediately shows something unusual: the 1Password extension's `getItem` and `setItem` functions appear multiple times in the total-time list at 93.9% and 91.3% respectively. That repetition in the function list is a big hint. It usually means the profiler is seeing many different stack depths of the same function, i.e. recursion.

The top-down call tree makes this unmistakable. Starting from the extension event handler `recvRunListener -> fire -> yRj -> isEnabled`, the call chain enters `jj -> getItem -> getItem -> RjA -> vj -> YG -> og -> setItem -> setItem`, then immediately loops back into `jj -> getItem -> ...` over and over, many levels deep. The tree repeats this 9-function cycle continuously. This is infinite recursion.

The trigger is `webNavigation.onBeforeNavigate` firing (likely from the tab being opened during restart). The listener calls `isEnabled()` on the 1Password extension. `isEnabled()` calls into `jj()` which calls `getItem()` to check the storage. But storage hasn't finished initializing yet. `browser.storage.local.get()` is asynchronous and hasn't returned. So `getItem()` finds the storage in an uninitialized state and calls back into the initialization logic, which itself calls `getItem()` again, and so on.

The other major thing visible in the profile is that ~73% of the samples are inside `js::SavedStacks::saveCurrentStack`. The JS engine is constantly capturing stack traces. This happens because the over-recursion detector (`js::ReportOverRecursed`, `js::jit::CheckOverRecursedImpl`) keeps firing as each recursive call hits the recursion depth limit, throwing an error and capturing a full JS stack trace for every error object. Since the recursion immediately restarts after each throw, this happens thousands of times per second.

The GC markers tell the rest of the story: the constant creation of Error objects (each with a full JS stack trace attached) floods the JS heap. There are 8 major GC collections in the 5.46-second profile averaging 65ms each, plus 59 incremental GC slices and 55 minor GCs. Together that is roughly 850ms spent in garbage collection, about 15% of total recording time. The Jank marker on the thread covers 4.17 seconds (from 1.29s to profile end), and the `IPC Accumulator` is 4.40s.

So the CPU isn't doing useful work. It's spinning: recurse until stack overflow, capture giant stack trace for the error, catch the error, recurse again, GC the discarded error objects, repeat indefinitely.

Rob Wu confirmed this in the bug: 1Password calls `browser.storage.local.get()` during initialization, but also has logic that tries to call `getItem()` before storage finishes loading. When the storage API is slow (which can happen during restart), this races and triggers the infinite loop. The fix was a 1Password update (8.11.4.27) that corrected the initialization ordering.

This is a pure extension bug. Firefox itself behaved correctly, it just had no way to detect or break an extension spinning in infinite JS recursion.
