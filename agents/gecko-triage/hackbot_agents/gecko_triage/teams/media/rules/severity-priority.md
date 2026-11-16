# Severity and priority: the media team

Applies with the general `severity-priority.md`.

## Examples

| Severity | Examples                                                                                                                                            |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| S1       | A camera feed corrupts the recorded file                                                                                                            |
| S2       | Browser crashes on media playback on common sites; playback completely broken on a common codec; getUserMedia errors on all devices after an update |
| S3       | A tab crash on a rare media pattern that a reload recovers; playback fails for a rare container; audio desync on long files                         |
| S4       | Poster frame shown for one frame before play; wording in an error message                                                                           |

| Priority | Examples                                                                     |
| -------- | ---------------------------------------------------------------------------- |
| P1       | An S1 confirmed on release; a regression from a landed patch blocking uplift |
| P2       | A playback failure with clear steps; a crash that reproduces reliably        |
| P3       | An obscure codec variant; a single-reporter issue with no duplicates         |

## Situations

| Situation                                                      | Severity | Priority |
| -------------------------------------------------------------- | -------- | -------- |
| Playback regression introduced in the current Nightly          | S2       | P1       |
| Playback broken on a specific site, no workaround              | S2       | P2       |
| Playback broken for a codec or container, another format works | S3       | P2       |
| Tab crash on a rare media pattern, a reload recovers           | S3       | P3       |
| Recording works but quality is degraded                        | S3       | P3       |
