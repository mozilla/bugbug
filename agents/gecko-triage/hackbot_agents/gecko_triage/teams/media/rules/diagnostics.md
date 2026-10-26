# Diagnostics: the media team

Applies with the general `diagnostics.md`, in `workflow.md` steps 2a, 2b and 5.

## about:support

### Important Modified Preferences

Find non-default `media.*`, `gfx.*` and `layers.*` prefs and flag any that could explain
the issue. Read the value, not just the name: some prefs are normal user choices from
Settings.

| Pref                                          | Default                                                                              | Risk if changed                                                                                                                            |
| --------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `media.hardware-video-decoding.failed`        | not set; the GPU sanity test writes it                                               | High if `true`: the sanity test failed or crashed, so HW decode stays off. `false` is normal                                               |
| `media.hardware-video-decoding.force-enabled` | `false`                                                                              | High: skips the GPU blocklist; a blocked driver can give corrupt or green frames, freezes or crashes                                       |
| `layers.acceleration.disabled`                | `false` (the Settings checkbox "Use hardware acceleration when available", inverted) | High for video performance and HW decode bugs: turns off GPU compositing, and D3D11 compositing on Windows                                 |
| `media.hardware-video-decoding.enabled`       | `true`                                                                               | High for HEVC, 4K or DRM bugs, Medium otherwise: forces SW decode, removes HEVC on Windows and hides the Media Foundation CDMs (PlayReady) |
| `media.wmf.media-engine.enabled`              | `2` on Windows (encrypted content only)                                              | High for DRM and HEVC bugs: `0` turns off PlayReady; `1` or `3` send clear playback through the media engine                               |
| `media.eme.enabled`                           | `false` on Linux, `true` elsewhere (Settings, "Play DRM-controlled content")         | High if `false` outside Linux: all DRM playback fails. `true` on Linux is a normal opt-in                                                  |
| `media.gmp-widevinecdm.enabled`               | `true`                                                                               | High for EME bugs: `false` removes Widevine                                                                                                |
| `media.eme.playready.enabled`                 | `true` on Windows                                                                    | Medium: `false` makes sites use Widevine, often at a lower resolution                                                                      |
| `media.ffmpeg.enabled`                        | `true` on Linux, `false` on macOS; not on Windows or Android                         | High on Linux if `false`: no system FFmpeg, so no H.264, AAC or HEVC. Low on macOS if `true`                                               |
| `media.ffvpx-hw.enabled`                      | `true` on Linux, Nightly only on Windows, `false` elsewhere                          | Medium: `false` on Linux removes VA-API through the bundled FFmpeg; `true` elsewhere turns on a path that is off by default                |
| `gfx.webrender.software`                      | `false`                                                                              | Medium: software WebRender, more CPU, different video presentation                                                                         |
| `media.av1.enabled`                           | `true`                                                                               | Medium: `false` removes AV1; AV1-only content fails                                                                                        |
| `media.autoplay.default`                      | `1` (Settings, Autoplay)                                                             | Low for `0` and `1`. Medium for `5`: muted autoplay is also blocked, so some players never start                                           |
| `media.cubeb.backend`                         | not set                                                                              | High for "no audio" bugs: a wrong backend name gives no sound                                                                              |
| `media.peerconnection.enabled`                | `true`                                                                               | High for WebRTC bugs: `false` removes `RTCPeerConnection`, so calls fail                                                                   |

`media.ffmpeg.vaapi.enabled` was removed in Firefox 137 (bug 1949344). An old value in
about:support has no effect.

If you find high-risk prefs, make this the primary ask in "Needs info". Ask the reporter
to reset those prefs, close every Firefox window and restart. The restart is not
optional: the pref change does not take effect without it. The team's `comment.md` says
where the capture goes.

### Decision Log

Read the `HARDWARE_VIDEO_DECODING` and `HARDWARE_VIDEO_ENCODING` entries. They say whether
HW decoding is available and, if not, why: blocklisted, pref-disabled, driver issue,
capability missing. Note the failure code.

### Media: Codec Support Information

Per-codec decode and encode support. Check whether the relevant codec (H.264, AV1, VP9,
HEVC) shows SW or HW support. No support at all means a missing decoder rather than a
configuration problem.

### Media: Content Decryption Modules

Installed CDMs (Widevine, OpenH264, WMF CDM / PlayReady) and versions. For EME bugs, check
the expected CDM is present.

## Profiles

**Media profile versus generic profile.** A profile link by itself does not prove a media
bug has enough data to investigate. A media-preset profile has threads such as
`MediaDecoderStateMachine`, `MediaSupervisor`, `MediaPDecoder`, `AudioSink`, `AudioIPC`,
`cubeb` and markers like `DXVA` and `NS_ERROR_DOM_MEDIA_*`. A generic CPU, JS or
networking profile shows none of this and does not count as evidence for "Analyzed".
Record it as present but non-media, and add "media profile" to what is missing when the
bug type needs it (bug 2040168 is an example). The bug can still go to "Analyzed" through
another item in the decision rule.

The `about:logging` route in the capture block is a different capture path and often
works where the profiler UI failed (bug 2071427).

**Check every media thread**, not just `MediaDecoderStateMachine #1`:
`MediaSupervisor #N`, `MediaPDecoder #N` in the rdd process, the GPU process's
`Renderer`, and `GeckoMain`. Read CPU and wait state: a decoder thread sitting in
`NtWaitForMultipleObjects` or `ZwWaitForAlertByThreadId` with large inter-frame gaps
is starved, which is a different bug from a saturated one.

### HW decode failure pattern

When the profile shows repeated `DXVA failure:` or `NS_ERROR_DOM_MEDIA_*` entries, ask:

1. **Why does it fail each time?** DXVA slot exhaustion (`DXVA failure: D3D11: Too many
DXVA videos playing`; Firefox caps concurrent DXVA decoders at
   `media.wmf.dxva.max-videos`, default 8, and feed-style sites keep many videos alive),
   `CanUseDXVA()` false for this video's parameters, or D3D11 device creation failure
   (driver, GPU reset).
2. **Does the capability API reflect runtime availability?**
   `WMFDecoderModule::Supports()` (behind `MediaCapabilities.decodingInfo()`) checks only
   process-level `sDXVAEnabled` and MFT presence, not the current DXVA slot count, so
   Firefox can report `HardwareDecode` supported while new decoders fail at runtime.
3. **Does the failing codec have a software fallback?** In WMF, H.264 does; HEVC, VP9, AV1
   and VP8 do not (`WMFVideoMFTManager.cpp` returns `NS_ERROR_DOM_MEDIA_FATAL_ERR`).

When all three are present, the bug is "Analyzed" with two distinct problems. One is the
HW failure itself. The other is that the capability API does not show runtime
availability.

## Media logs

**Inline in a comment** (JSON or text): scan for `NS_ERROR_*` codes, decoder selection
(`AppleMedia`, `ffvpx`, `VPX`, `WMF`), MSE state transitions, network stalls,
`MediaDecoderStateMachine` errors. Summarize the findings in the inventory.

**Emailed to media-alerts@mozilla.com**: record it as present and not accessible to the
agent. Do not ask the reporter to re-send it by email.

## Required information

| Bug type                     | Required                                                                  |
| ---------------------------- | ------------------------------------------------------------------------- |
| Playback failure             | media URL or sample, codec or container, about:support, platform          |
| Recording                    | device information, getUserMedia constraints, a repro page or snippet     |
| Web Audio                    | a minimal reproducible snippet, AudioContext configuration, about:support |
| Web Codecs                   | a minimal reproducible snippet, codec configuration, about:support        |
| Performance or audio quality | an `about:logging` capture with the media preset, platform, steps         |
| GMP / EME                    | key system, platform, about:support                                       |

**Decision rule.** After step 2b, use the artifact findings, not just checklist presence,
to decide whether to spawn `bug_investigator`. The investigation needs enough data to
start, not a specific artifact. Any one of these is enough:

- a media profile;
- a media log with an `NS_ERROR` or decoder signal;
- a crash id with a media frame;
- steps that reproduce the problem every time;
- a cause that the reporter found, with source citations;
- a codec or configuration with a clear cross-browser failure pattern.

Performance and audio-quality bugs are the exception: they need the `about:logging`
capture from the table, because steps alone do not show the cause. When one item is
present, missing table items do not block the investigation. Spawn `bug_investigator`,
then use its "Fixable" answer and the Fixability table in `workflow.md` to choose the
branch. The table also covers the expert-led answer. "The decision" in `workflow.md`
then decides between `analyzed` and `needs_info`.
