# Classify: the media team's bug types

Applies in `workflow.md` step 3, in addition to the general types.

| Type                         | Indicators                                                | Typical component                 |
| ---------------------------- | --------------------------------------------------------- | --------------------------------- |
| Playback failure             | a media element not playing, codec or container mentioned | Audio/Video: Playback             |
| Recording                    | getUserMedia, MediaRecorder, microphone, camera           | Audio/Video: Recording            |
| Web Audio                    | AudioContext, AudioNode                                   | Web Audio                         |
| Web Codecs                   | VideoDecoder, VideoEncoder, AudioDecoder, AudioEncoder    | Audio/Video: Web Codecs           |
| Performance or audio quality | slow, high CPU, dropped frames, buffering, glitch         | Playback, cubeb, MediaStreamGraph |
| GMP / EME                    | CDM, Widevine, encrypted media                            | Audio/Video: GMP                  |
| PlayReady                    | PlayReady, the `com.microsoft.playready` key system       | Audio/Video: Playback             |
| Autoplay                     | autoplay blocked or allowed, the autoplay policy          | Audio/Video: Playback             |
