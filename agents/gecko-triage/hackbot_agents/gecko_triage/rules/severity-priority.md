# Severity and priority

Applies on the "Needs info" and "Analyzed" paths, also when the root cause is unknown. It
also applies to the intermittent case in "WORKSFORME, never prematurely" in `workflow.md`.
Severity and priority are **fields**, recorded with `bugzilla_update_bug` after the
comment, and only when the current value is `--`. A value a developer set is never
overwritten; disagree in the comment instead. The agent never sets S1: when the standard
says S1, record S2 and say in the run's summary why a person should consider S1.

## Severity

The definitions are Mozilla's, quoted from the [BMO user guide][bmo-severity]. The
in-tree guide [`docs/bug-mgmt/guides/severity.md`][severity-guide] links to it.

[bmo-severity]: https://wiki.mozilla.org/BMO/UserGuide/BugFields#bug_severity
[severity-guide]: https://firefox-source-docs.mozilla.org/bug-mgmt/guides/severity.html

| Severity | Definition                                                                                                                                               |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| S1       | "(Catastrophic) Blocks development/testing, may impact more than 25% of users, causes data loss, likely dot release driver, and no workaround available" |
| S2       | "(Serious) Major functionality/product severely impaired or a high impact issue and a satisfactory workaround does not exist"                            |
| S3       | "(Normal) Blocks non-critical functionality or a work around exists"                                                                                     |
| S4       | "(Small/Trivial) minor significance, cosmetic issues, low or no impact to users"                                                                         |
| N/A      | "(Not Applicable) ... reserved for bugs of type Task or Enhancement"                                                                                     |

The in-tree guide gives these examples. S1: "Crashes with data loss". S2: "Bugs that could
reasonably be expected to cause a Firefox user to switch browsers, either because the
severity is bad enough, or the frequency of occurrence is high enough." It also says: "Do
not assign bugs of type defect the severity N/A".

The agent never sets N/A. On a bug of type task or enhancement, do not set severity. Say
in the comment that severity stays for a human to set.

## Priority

The definitions are Mozilla's, quoted from the in-tree guide
[`docs/bug-mgmt/guides/priority.md`][priority-guide].

[priority-guide]: https://firefox-source-docs.mozilla.org/bug-mgmt/guides/priority.html

| Priority | Definition                                                                    |
| -------- | ----------------------------------------------------------------------------- |
| P1       | "Fix in the current release cycle"                                            |
| P2       | "Fix in the next release cycle or the following (nightly + 1 or nightly + 2)" |
| P3       | "Backlog"                                                                     |
| P4       | "Do not use, this priority is for web platform test bots"                     |
| P5       | "Will not fix, but will accept a patch"                                       |

The agent sets only P1, P2 or P3. It never sets P4: P4 is reserved for web platform test
bots. It never sets P5: "will not fix" is a person's decision.

## Crashes

Rate a crash by its effect and its volume, not by the word "crash".

The decision matrix below gives the cases. S1 is only for a crash with data loss, or a
crash with a very wide impact as the S1 definition says. A tab crash that a reload does
not recover, for example a crash on every load of a site, has no workaround, so it is S2.
A crash that only happens in CI, however frequent, is S3: it has no user impact. For a
crash that users see, frequency counts. The in-tree S2 example names "the frequency of
occurrence".

## Decision matrix

| Situation                                                       | Severity                        | Priority |
| --------------------------------------------------------------- | ------------------------------- | -------- |
| Parent-process crash, a GPU process loss the user sees          | S2                              | P1 or P2 |
| High-volume or top crash                                        | S2                              | P1 or P2 |
| Tab crash that a reload does not recover                        | S2                              | P2       |
| Crash with data loss                                            | S1 (record S2, see above)       | P1       |
| Content-process (tab) crash, low volume, a reload recovers      | S3                              | P2 or P3 |
| CI or test-infra crash (xpcshell), no user-facing impact        | S3                              | P3       |
| Debug-only assertion (`MOZ_ASSERT`, fuzzing), benign in release | S4                              | P3       |
| Bug of type task or enhancement, such as a feature request      | Do not set (N/A is for a human) | P3       |

The team's `severity-priority.md` adds examples and situations for its area.

**Debug-only assertions.** A `MOZ_ASSERT`, typically filed from fuzzing with the
`assertion` or `pernosco` keywords, does not exist in release builds. Rate it by its
release-mode consequence: benign or cosmetic is S4; rate it higher only when the same path
produces a release crash, a use-after-free or data corruption once the assert is gone. Do
not default a debug-only assert to S3.

**Nightly and DevEdition are not debug-only.** A crash in code that ships in Nightly or
Developer Edition reaches users, so rate it at least S3, even when the code is
not in Beta or Release.
