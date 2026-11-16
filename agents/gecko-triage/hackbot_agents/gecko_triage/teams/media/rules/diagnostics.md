# Diagnostics: the media team

Applies with the general `diagnostics.md`, in `workflow.md` steps 2a, 2b and 5. The
in-tree page `dom/media/docs/Diagnostics.md` has the media facts, and the system prompt
carries it. It covers the about:support sections, the media log signals and the media
profile threads. It also covers the hardware decode failure pattern, what a useful report
has and the capture steps. This file adds the triage decisions and a few facts that the
page does not have.

## Prefs

Read the prefs in "Important Modified Preferences", as the in-tree page says. A pref can
turn off hardware decoding, the GPU, a decoder, a CDM, audio output or WebRTC. If such a
pref can explain the bug, make the pref reset the primary ask in "Needs info". Ask the
reporter to reset those prefs, close every Firefox window and restart. The restart is
not optional. The team's `comment.md` says where the capture goes.

These values are easy to read wrong:

- `media.hardware-video-decoding.failed` is set by the GPU sanity test. `false` is
  normal. `true` means that the test failed or crashed, so hardware decoding stays off.
- `media.hardware-video-decoding.force-enabled` set to `true` skips the GPU blocklist. A
  blocked driver can then give corrupt frames, freezes or crashes.
- `layers.acceleration.disabled` is the Settings checkbox "Use hardware acceleration when
  available", inverted.
- `media.eme.enabled` set to `true` on Linux is a normal opt-in.
- `media.wmf.media-engine.enabled`: `0` turns off PlayReady, and `1` or `3` send clear
  playback through the media engine.
- `media.cubeb.backend` with a wrong backend name gives no sound.
- `media.peerconnection.enabled` set to `false` removes `RTCPeerConnection`, so calls
  fail.

## Profiles

Read each profile link with `profile_analyst`, as the general `diagnostics.md` says. Do
not use the skills that the in-tree page names for agents. The capture rule for
`profile_analyst` is the list of media threads in the "Profiles" section of the in-tree
page, and also `AudioSink` and `cubeb`.

A profile link by itself does not prove that a media bug has enough data. A profile
without those media threads does not count as evidence for "Analyzed". Record it as
present but non-media, and add "media profile" to what is missing when the bug type
needs it (bug 2040168 is an example). The bug can still go to "Analyzed" through another
item in the decision rule.

The `about:logging` route in the capture block is a different capture path. It often
works where the profiler UI failed (bug 2071427).

When a profile shows the hardware decode failure pattern of the in-tree page, check
three things. Does the failure repeat, for one of the causes that the page lists? Does
the capability API report support while new decoders fail? Does the codec have no
software fallback in WMF (`WMFVideoMFTManager.cpp`)? When all three are true, the bug is
"Analyzed" with two problems. One is the hardware failure. The other is that the
capability API does not show runtime availability.

## A hang in the RDD or GPU process

A hang or a busy thread in the RDD or GPU process can come from the sandbox. Before you
name a code fix, ask the reporter for the sandbox test of the in-tree page, and ask them
to start Firefox normally again after the test. The GPU variable works only on Windows and
macOS.

## Media logs

A media log inline in a comment: read it for the signals of the in-tree page, and give
the findings in the inventory. A media log sent by email to media-alerts@mozilla.com:
record it as present and not readable by the agent. Do not ask the reporter to send it
again by email.

## Required information

"What a useful report has" in the in-tree page lists the data for each media bug type.

**Decision rule.** After step 2b, use the artifact findings, not only the presence of an
item, to decide whether to spawn `bug_investigator`. The investigation needs enough data
to start, not a specific artifact. Any one of these is enough:

- a media profile;
- a media log with an `NS_ERROR` or decoder signal;
- a crash id with a media frame;
- steps that reproduce the problem every time;
- a cause that the reporter found, with source citations;
- a codec or configuration with a clear cross-browser failure pattern.

Performance and audio-quality bugs are the exception: they need the `about:logging`
capture, because steps alone do not show the cause. When one item is present, missing
items do not block the investigation. Spawn `bug_investigator`, then use its "Fixable"
answer and the Fixability table in `workflow.md` to choose the branch. The table also
covers the expert-led answer. "The decision" in `workflow.md` then decides between
`analyzed` and `needs_info`.
