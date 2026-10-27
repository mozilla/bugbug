# profiler-cli walkthrough: broken stack walking in AVX2 JPEG assembly on Windows

Companion to `windows-broken-stackwalk-jpeg-avx2.md`. Reproduces the same findings using `profiler-cli` with annotated output.

Profile: https://profiler.firefox.com/public/bshgqkfff7kny34jzk5ht8nn2m99hg8rjm4fd80

---

## Load the profile and get an overview

```
profiler-cli load https://profiler.firefox.com/public/bshgqkfff7kny34jzk5ht8nn2m99hg8rjm4fd80 --session broken-stack
profiler-cli profile info --session broken-stack
```

```
Name: Firefox 143 – Windows 11
Platform: Windows 11.0; build=26100

This profile contains 114 threads across 13 processes.

Top processes and threads by CPU usage:
  p-6: file:// Content [pid 33488] [ts<0N → end] - 1332.815ms
    t-32: GeckoMain [tid 30724] - 929.223ms
    t-33: TaskController #0 [tid 34488] - 399.686ms      ← image decoding thread
    t-41: DOM Worker [tid 33468] - 2.357ms
    ...
  p-0: Parent Process [pid 28748] [ts<6I → end] - 74.829ms
    ...
```

The file:// Content process accounts for 1332ms of the 4.66-second profile. Inside it, `TaskController #0` (t-33) has 399ms. Task controller threads are where image decoding runs in Firefox, so this is worth investigating.

---

## Select the task controller thread and check its activity timeline

```
profiler-cli thread select t-33 --session broken-stack
profiler-cli thread info --session broken-stack
```

```
Name: TaskController #0
TID: 34488

This thread contains 2333 samples and 22 markers.

CPU activity over time:
- 85% for 399.8ms: [ts-P → ts-Sk] (3.033s - 3.504s)
```

The thread is essentially idle for the first 3 seconds, then goes to 85% CPU for about 470ms. That is the image decoding burst. Zoom into it.

---

## Zoom into the active window and check hot functions

```
profiler-cli zoom push 3.033,3.504 --session broken-stack
profiler-cli thread samples --include-idle --session broken-stack
```

Using `--include-idle` matches what the profiler UI shows (it includes kernel-wait samples). Active-only percentages will differ slightly.

```
Top Functions (by self time):

  f-479. VCRUNTIME140.dll!memset() - self: 102 (42.9%)
  f-1301. xul.dll!jsimd_idct_islow_avx2 - self: 63 (26.5%)
  f-1311. xul.dll!decode_mcu_fast(jpeg_decompress_struct*, short[64]**) - self: 19 (8.0%)
  f-1312. xul.dll!jsimd_ycc_extbgrx_convert_avx2 - self: 12 (5.0%)
  f-1309. xul.dll!decompress_onepass(jpeg_decompress_struct*, unsigned char***) - self: 7 (2.9%)
  ...

Top Functions (by total time):

  f-0. (root) - total: 238 (100.0%)
  f-1278. Task ImageDecodingTask - total: 205 (86.1%)
  f-1279. Image decoding - total: 205 (86.1%)
  f-1. ntdll.dll!RtlUserThreadStart - total: 165 (69.3%)       ← only 69.3%: broken stacks missing here
  ...
  f-1301. xul.dll!jsimd_idct_islow_avx2 - total: 63 (26.5%)   ← leaf function appears high in total time
  ...
  f-1299. 0x2 - total: 21 (8.8%)                               ← garbage address frames
  f-1289. 0x1 - total: 20 (8.3%)                               ← garbage address frames
  f-635. 0x2ee0 - total: 14 (5.9%)                             ← garbage address frames
  ...
  f-1313. xul.dll!jsimd_idct_islow(...) - total: 2 (0.8%)      ← correct C wrapper, barely visible
```

The self-time distribution looks like JPEG decoding: `memset` is clearing frame buffers, `jsimd_idct_islow_avx2` is the AVX2-optimized inverse DCT, `decode_mcu_fast` is the Huffman decoder, and `jsimd_ycc_extbgrx_convert_avx2` is colorspace conversion.

The total-time column reveals the problem: `jsimd_idct_islow_avx2` has 26.5% total time, but its correct C-level caller `jsimd_idct_islow` has only 0.8% total time. For a leaf function, total should be close to self if stacks are intact. The gap means roughly 61 of 63 samples (97%) with this function at the leaf have broken stacks above it. The raw address frames `0x1`, `0x2`, `0x2ee0` in the total-time list are the artifact of the failed unwind.

---

## Examine the top-down tree to see the broken stacks

```
profiler-cli thread samples-top-down --max-lines 80 --include-idle --session broken-stack
```

```
Top-Down Call Tree:
f-0. (root) [total: 100.0%, self: 0.0%]
├─ f-1. ntdll.dll!RtlUserThreadStart [total: 69.3%, self: 0.0%]
│  ...
│  f-418. nss3.dll!_PR_NativeRunThread(void*) [total: 69.3%, self: 0.0%]
│  f-1278. Task ImageDecodingTask [total: 69.3%, self: 0.0%]
│  f-420. xul.dll!mozilla::ThreadFuncPoolThread(void*) [total: 69.3%, self: 0.0%]
│  f-422. xul.dll!mozilla::TaskController::RunPoolThread(mozilla::PoolThread*) [total: 69.3%, self: 0.0%]
│  f-24. xul.dll!mozilla::TaskController::RunTask(mozilla::Task*) [total: 69.3%, self: 0.0%]
│  ...
│  f-1292. xul.dll!mozilla::image::nsJPEGDecoder::ReadJPEGData(char const*, unsigned long long) [total: 55.5%, self: 0.0%]
│  ├─ f-1293. xul.dll!mozilla::image::CreateReorientSurfacePipe(...) [total: 41.6%, self: 0.0%]
│  │  ...
│  │  f-479. VCRUNTIME140.dll!memset() [total: 41.6%, self: 41.6%]
│  └─ f-1302. xul.dll!mozilla::image::nsJPEGDecoder::OutputScanlines() [total: 13.9%, self: 0.0%]
│     ...
│     f-1309. xul.dll!decompress_onepass(...) [total: 13.0%, self: 2.9%]
│     ├─ f-1310. xul.dll!decode_mcu(...) [total: 8.0%, self: 0.0%]
│     │  f-1311. xul.dll!decode_mcu_fast(...) [total: 8.0%, self: 8.0%]
│     └─ f-1313. xul.dll!jsimd_idct_islow(...) [total: 0.8%, self: 0.0%]   ← correct path
│        f-1301. xul.dll!jsimd_idct_islow_avx2 [total: 0.8%, self: 0.8%]
└─ f-1278. Task ImageDecodingTask [total: 30.7%, self: 0.0%]                ← BROKEN STACKS
   f-1279. Image decoding [total: 30.7%, self: 0.0%]
   ├─ f-1301. xul.dll!jsimd_idct_islow_avx2 [total: 10.5%, self: 10.5%]    ← no native parent
   ├─ f-1299. 0x2 [total: 7.6%, self: 0.0%]                                ← garbage address
   │  f-1301. xul.dll!jsimd_idct_islow_avx2 [total: 7.6%, self: 7.6%]
   ├─ f-1289. 0x1 [total: 7.1%, self: 0.0%]                                ← garbage address
   │  f-1301. xul.dll!jsimd_idct_islow_avx2 [total: 7.1%, self: 7.1%]
   ├─ f-635. 0x2ee0 [total: 5.0%, self: 0.0%]                              ← garbage address
   │  f-1312. xul.dll!jsimd_ycc_extbgrx_convert_avx2 [total: 5.0%, self: 5.0%]
   └─ f-1347. 0x1588 [total: 0.4%, self: 0.0%]                             ← garbage address
      f-1301. xul.dll!jsimd_idct_islow_avx2 [total: 0.4%, self: 0.4%]
```

This is the key output. The tree shows two structurally different root branches.

**Branch 1 (69.3%)**: Starts with `RtlUserThreadStart`. Contains a full, correct call chain from thread startup all the way through the JPEG decoder to `jsimd_idct_islow_avx2`. In this branch, `jsimd_idct_islow_avx2` appears under `jsimd_idct_islow -> decompress_onepass` at 0.8%. The dominant work in this branch is `imgFrame::InitForDecoder -> ClearSurface -> memset` (41.6%), not JPEG computation.

**Branch 2 (30.7%)**: Starts with `Task ImageDecodingTask` directly at root, with no thread startup frames. Below `Image decoding`, there are no JPEG decoder frames at all. Instead, `jsimd_idct_islow_avx2` and `jsimd_ycc_extbgrx_convert_avx2` appear as near-direct children, separated at most by single frames with nonsensical raw addresses: `0x1`, `0x2`, `0x2ee0`, `0x1588`.

The raw address frames are the telltale sign of a failed Windows stack walk. The `Task ImageDecodingTask` and `Image decoding` labels come from profiler pseudo-frames injected via label stack markers, not native stack unwinding, so they appear correctly. But the native unwind chain breaks immediately inside the AVX2 assembly, and the walker produces garbage before giving up.

---

## Bottom-up view to confirm which callers are visible

```
profiler-cli thread samples-bottom-up --include-idle --session broken-stack
```

```
Bottom-Up Call Tree:
f-479. VCRUNTIME140.dll!memset() [total: 42.9%, self: 42.9%]
├─ f-1300. xul.dll!mozilla::image::ClearSurface(...) [total: 41.6%, self: 0.0%]
│  f-1298. xul.dll!mozilla::image::imgFrame::InitForDecoder(...) [total: 41.6%, self: 0.0%]
│  ...
│  f-1292. xul.dll!mozilla::image::nsJPEGDecoder::ReadJPEGData(...) [total: 41.6%, self: 0.0%]
│  ...
│  f-1. ntdll.dll!RtlUserThreadStart [total: 41.6%, self: 0.0%]          ← full stack visible
│  f-0. (root) [total: 41.6%, self: 0.0%]
└─ f-1315. xul.dll!jzero_far(void*, unsigned long long) [total: 1.3%, self: 0.0%]
   f-1309. xul.dll!decompress_onepass(...) [total: 1.3%, self: 0.0%]
   ...
f-1301. xul.dll!jsimd_idct_islow_avx2 [total: 26.5%, self: 26.5%]
├─ f-1279. Image decoding [total: 10.5%, self: 0.0%]                      ← broken: no native parent
│  f-1278. Task ImageDecodingTask [total: 10.5%, self: 0.0%]
│  f-0. (root) [total: 10.5%, self: 0.0%]
├─ f-1299. 0x2 [total: 7.6%, self: 0.0%]                                  ← broken: garbage address
│  f-1279. Image decoding [total: 7.6%, self: 0.0%]
│  f-1278. Task ImageDecodingTask [total: 7.6%, self: 0.0%]
│  f-0. (root) [total: 7.6%, self: 0.0%]
├─ f-1289. 0x1 [total: 7.1%, self: 0.0%]                                  ← broken: garbage address
│  f-1279. Image decoding [total: 7.1%, self: 0.0%]
│  ...
├─ f-1313. xul.dll!jsimd_idct_islow(...) [total: 0.8%, self: 0.0%]       ← correct caller, <1% only
│  f-1309. xul.dll!decompress_onepass(...) [total: 0.8%, self: 0.0%]
│  ...
└─ ... (1 more children: combined 0.4%, max 0.4%)
f-1311. xul.dll!decode_mcu_fast(...) [total: 8.0%, self: 8.0%]
└─ f-1310. xul.dll!decode_mcu(...) [total: 8.0%, self: 0.0%]
   f-1309. xul.dll!decompress_onepass(...) [total: 8.0%, self: 0.0%]
   ...                                                                     ← full stack visible
f-1312. xul.dll!jsimd_ycc_extbgrx_convert_avx2 [total: 5.0%, self: 5.0%]
└─ f-635. 0x2ee0 [total: 5.0%, self: 0.0%]                               ← broken: garbage address
   f-1279. Image decoding [total: 5.0%, self: 0.0%]
   ...
```

The bottom-up view makes the contrast explicit by function. For `memset` (42.9% self time), all callers chain back through full, named stacks all the way to `RtlUserThreadStart`. For `jsimd_idct_islow_avx2` (26.5% self time), only 0.8% of its samples have the correct caller `jsimd_idct_islow`. The remaining 25.7% have either raw garbage addresses or jump directly to the `Image decoding` pseudo-frame with nothing native in between.

For `decode_mcu_fast`, a neighboring JPEG function at the same call depth, the bottom-up tree shows a complete chain back through `decompress_onepass`, `process_data_simple_main`, and up through the full Firefox decoder stack. That function has no stack walking problem. The breakage is specific to entering the AVX2 assembly.

---

## Check for markers around the problematic code

```
profiler-cli thread markers --session broken-stack
```

```
Markers in thread t-33 (TaskController #0) — 16 markers

By Name (top 15):
  Awake                         6 markers  (interval: min=22µs, avg=68.29ms, max=409.57ms)
    Examples: m-1 ✗ (409.57ms), m-2 ✗ (46µs), m-3 ✗ (35µs)
  Runnable                      6 markers  (interval: min=15µs, avg=68.28ms, max=409.56ms)
    Examples: m-6 ✗ (409.56ms), m-7 ✗ (44µs), m-8 ✗ (25µs)
  TaskController::AddTask       4 markers  (instant)
    Examples: m-11 ✗, m-12 ✗, m-13 ✗

By Category:
  Other                        16 markers (100.0%)
```

The 16 markers on this thread are all scheduler-level events: `Awake`, `Runnable`, and `TaskController::AddTask`. None have stack traces (all marked `✗`), and none are image-decoding-specific markers. The profiler label stack (`Task ImageDecodingTask`, `Image decoding`) is what creates the pseudo-frames seen in the call trees, not any of these markers. There are no per-scanline or per-MCU markers that could narrow the broken-stack region further.

---

## Why the stack walking breaks

`jsimd_idct_islow_avx2` is a handwritten x86_64 AVX2 assembly function in `media/libjpeg/simd/x86_64/jidctint-avx2.asm` (vendored from libjpeg-turbo). The Windows x86_64 ABI requires non-leaf functions to have unwind tables (`.pdata`/`.xdata` PE sections) describing how to restore the previous frame. The profiler's `MozStackWalk` on Windows uses these tables to unwind frame by frame. Without them, it cannot find the saved return address when it reaches this frame, and the walk terminates with whatever garbage is on the stack.

The fix is to add `PROC FRAME` / `.endprolog` directives to the assembly, as done in other Mozilla assembly files (e.g. `xpcom/reflect/xptcall/md/win32/xptcinvoke_asm_x86_64.asm`). Since this is vendored code, the correct path is to upstream the fix to libjpeg-turbo.

The issue is specific to Windows x86_64 because macOS and Linux use DWARF unwind info (`.eh_frame` section), which the libjpeg-turbo assembly does include. Only the Windows SEH unwind tables are missing.

---

```
profiler-cli stop --session broken-stack
```
