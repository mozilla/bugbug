# The comment: the media team

Applies with the general `comment.md`.

## Narrowing questions

At most one, from this table:

| Question                                                             | When                                                  |
| -------------------------------------------------------------------- | ----------------------------------------------------- |
| Does this happen in Private Browsing mode?                           | Any playback or recording issue; rules out extensions |
| Does this happen on all videos, or only certain ones?                | Playback failures                                     |
| Does this happen with one codec only (H.264, H.265, AV1, VP9)?       | Playback failures with a codec hint                   |
| <name>, could you please share a public link that shows the problem? | The reported URL needs a login                        |

## When to ask for the capture block

For audio, video and performance bugs, the media-log capture is the most useful
diagnostic. Make it the first item in "Next steps". The only exception: when the team's
`diagnostics.md` makes a pref reset the primary ask, the pref reset and restart come
first, and the capture follows, introduced by "If the problem continues after the
reset:".

Never put the video link of the team's capture block in a reference-style footnote.

When the in-tree page says to use another preset, such as **Graphics**, say so in one
sentence before the block; do not edit step 2 of the capture block.

For an Android bug, add one sentence after the block on the Android upload, as the
in-tree page says.
