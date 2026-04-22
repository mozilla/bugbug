# Unsupported Configurations

Bugs often specify prefs that need to be enabled or disabled in order for the bug to reproduce.

When a bug specifies prefs that are not the default on any supported platform or channel, it
becomes less important and the bug should be marked with the `unsupported-config` keyword.

## Finding default state prefs

Usually, the default values of prefs are found in `modules/libpref/init/all.js`.

However, there are cases where a pref isn't in there, then you might have to search the
source code for how it exactly behaves.

## Nightly-only features

If a pref is enabled only on Nightly (i.e. guarded by some kind of ifdef that prevents it
from becoming active in release), that still counts as supported but it can be mentioned
in a comment that it is Nightly-only.

## Pref only required for debugging/stability

In some cases, bug reports specify prefs because it makes something **easier to reproduce**,
but the pref is not actually required to trigger the bug itself. For example, some bugs
specify that it needs `FuzzingFunctions` to trigger a GC reliably in a particular area,
but GC can also be triggered by other means, it just makes the testcase more reliable.

Such cases must **not** be marked as `unsupported-config` because they could still apply without the pref set. It is important to try and disambiguate these two.

However, if the crashing code itself is guarded by the pref and there is no clear other
path for the crash, then this is likely testing-only and therefore unsupported.

## Channels / Versions

Only label something as `unsupported-config` if that assessment would be true for all
currently supported channels.

Currently supported versions are: ESR115, ESR140, 149, 150 and 151.

If a pref was changed in any of these versions, you should outline that only certain
versions might be affected.

## Commenting

When adding the `unsupported-config` keyword, comment in at most 1-2 sentences
why you are adding the keyword. If the configuration is instead supported but there
are prefs mentioned in the bug, also comment in at most 1-2 sentences why these
are supported.

Unless you add the `unsupported-config` keyword, append a `[prefs-checked]` tag to the
whiteboard so we don't have to repeat this process again.
