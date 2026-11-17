# Meta bugs: the media team

Applies in `workflow.md` step 4b. The table is a reference. A `search_bugs` for `[meta]`
in the team's components, with `include_fields=id,summary,keywords`, lists the live set
when something here looks stale.

| Alias            | Bug     | Area                                           |
| ---------------- | ------- | ---------------------------------------------- |
| `dropped-frames` | 1416090 | Video frames dropped                           |
| `video-perf`     | 1445470 | Video playback performance (CPU, GPU, power)   |
| `webvtt`         | 2010319 | WebVTT interop                                 |
| `hw-ffvpx`       | 1893427 | HW-accelerated video decoding through ffvpx    |
| `wakelock`       | 1665980 | Wake lock                                      |
| `autoplay`       | 2023365 | Autoplay                                       |
|                  | 1790066 | Twitter / X audio and video                    |
|                  | 1904915 | YouTube playback                               |
|                  | 1954546 | Twitch freezes and stutters                    |
|                  | 1770250 | Reddit media playback                          |
|                  | 1997593 | Bluesky media                                  |
|                  | 1951155 | Audio crackling and chipping                   |
|                  | 2011679 | Bluetooth device audio and video               |
|                  | 1676924 | 4K and above frame drops                       |
|                  | 1902427 | HDR video playback on Windows                  |
|                  | 1610199 | Linux VAAPI / ffmpeg video playback            |
|                  | 1766429 | Linux playback                                 |
|                  | 1648826 | AArch64 macOS media support                    |
|                  | 1452683 | AV1 playback support                           |
|                  | 1422891 | MKV / Matroska container support               |
|                  | 1884199 | Video startup performance, time to first frame |
|                  | 1746557 | WebCodecs API implementation                   |
|                  | 2023379 | Media-related sleep and wake                   |
|                  | 2026773 | Remove AudioChannelService / AudioChannelAgent |
|                  | 1511117 | Web Audio autoplay                             |
|                  | 1758786 | MediaCapabilities API                          |
|                  | 1954558 | Video Super Resolution                         |
|                  | 1980764 | Android MediaCodec NDK                         |
|                  | 2049891 | Video decoding and seeking performance         |
|                  | 2053144 | Vulkan video decoding on Linux                 |

A Linux hardware decode bug also blocks bug 1766429 (Linux playback), when the bug is a
Linux playback problem. Add it beside the more specific meta bug, for example bug 1610199,
1893427 or 2053144. Put the more specific one in the plan's `meta_bug`.
