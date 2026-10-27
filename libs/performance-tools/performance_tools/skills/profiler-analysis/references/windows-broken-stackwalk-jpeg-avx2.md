# Example analysis of a profile showing broken stack walking in AVX2 JPEG assembly on Windows

https://profiler.firefox.com/public/bshgqkfff7kny34jzk5ht8nn2m99hg8rjm4fd80

Bugzilla: https://bugzilla.mozilla.org/show_bug.cgi?id=1981690

## Profiled scenario

A user opened an HTML page that generates JPEG images via canvas. On Windows x86_64 with Firefox Nightly 143, hovering over the profile flame graph showed suspiciously short stacks for some samples that landed in `jsimd_idct_islow_avx2`. The reporter noted this was a broken stack walking issue. The profile was captured using the Firefox Profiler.

## Analysis

Looking at the profile overview, the file:// Content process (pid 33488) dominates CPU usage with about 1332ms of the 4.66-second recording. Inside that process, two threads are active: GeckoMain (929ms) and TaskController #0 (399ms). The GeckoMain activity is plausible overhead, but the TaskController thread is interesting because task controller threads are where image decoding work runs.

I select TaskController #0 and check its CPU timeline. It is essentially idle for the first 3 seconds, then spikes to 85% CPU from 3.033s to 3.504s (about 470ms), then goes quiet. That active window is the JPEG decoding work triggered by the canvas operation.

Zooming into that 470ms window and looking at the hot functions, the picture is mostly what you would expect from JPEG decoding: `memset` accounts for 43% of self time (clearing frame buffers during `imgFrame::InitForDecoder`), `jsimd_idct_islow_avx2` is 26.6%, `ZwWaitForAlertByThreadId` is 13.5% (waiting for more work), and `decode_mcu_fast` is 8%. That is reasonable. But the top-down tree tells a more interesting story.

In the top-down tree, the thread's samples split at the root into two branches. About 69% of samples start with `RtlUserThreadStart -> patched_BaseThreadInitThunk -> ... -> ThreadFuncPoolThread -> RunPoolThread` and then continue down into the full JPEG decoder call chain. These stacks are 15+ frames long and look correct. The path down to `jsimd_idct_islow_avx2` in this branch goes through `decompress_onepass -> jsimd_idct_islow -> jsimd_idct_islow_avx2` and accounts for only 0.8% of total samples.

The other 30.8% of samples land in a completely different branch. At the root level, these samples show `Task ImageDecodingTask -> Image decoding` and then jump directly to `jsimd_idct_islow_avx2` (or its sibling `jsimd_ycc_extbgrx_convert_avx2`), with at most one or two intermediate frames that have nonsensical addresses: `0x1`, `0x2`, `0x2ee0`, `0x1588`. These are not real return addresses. They are garbage the stack walker recovered by accident before giving up. The thread startup frames (`RtlUserThreadStart`, etc.) and all the intermediate JPEG decoder frames (`nsJPEGDecoder::DoDecode`, `decompress_onepass`, etc.) are completely absent.

This is the classic pattern for a stack walking failure on Windows. The profiler uses `MozStackWalk` on Windows, which relies on the Windows structured exception handling (SEH) unwind tables (`.pdata` and `.xdata` sections in the PE binary). When the stack walker encounters a frame with no unwind info, it cannot recover the saved return address or the previous stack pointer, and the walk terminates or produces garbage.

The `Task ImageDecodingTask` and `Image decoding` pseudo-frames appear correctly because the profiler injects those through a separate mechanism (profiler markers that track which task/label is active), not through stack unwinding. So the profiler knows a `DecodingTask` is running and labels the sample accordingly, but it cannot recover the native frames between the task boundary and the assembly leaf.

Looking up `jsimd_idct_islow_avx2` in the source, it is a handwritten x86_64 AVX2 assembly function in `media/libjpeg/simd/x86_64/jidctint-avx2.asm`. This is vendored code from the libjpeg-turbo project. The assembly file uses a plain `PROC` directive without `PROC FRAME`, and there are no `.xdata`/`.pdata` entries for this function. On Windows x86_64, the platform ABI requires every non-leaf function to declare its frame layout via these unwind tables so that tools (debuggers, exception handlers, stack walkers) can unwind through it. Without them, the Windows stack unwinder hits the function, cannot determine how to unwind past it, and fails.

The same issue affects `jsimd_ycc_extbgrx_convert_avx2` and likely other handwritten AVX2/SSE2 assembly routines in the same directory.

The fix, per the bug discussion, is to add Windows unwind prologue directives to the assembly: declare the function with `PROC FRAME`, add the frame setup instructions with appropriate `.push`/`.allocstack` directives, and close the prologue with `.endprolog`. This is how other Mozilla assembly (for example in `xpcom/reflect/xptcall/md/win32/xptcinvoke_asm_x86_64.asm`) handles the requirement. However, since this is vendored third-party code, the practical path is to upstream the fix to libjpeg-turbo.
