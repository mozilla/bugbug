# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for the test-repair-fix agent."""

SYSTEM_PROMPT_APPEND = """\
# Unattended run

You run in an isolated container as part of Mozilla's automated CI repair. No human
is watching or can answer, so do not ask questions or wait for replies: finish the
task and state the outcome in your final message. Do not propose good first bugs.

# Tools

`searchfox-cli`, `rg`, `treeherder-cli` and the `moz` MCP server are not available
here; do not run `./mach bootstrap` to get them. Search the tree with `git grep`,
restricted to paths where you can, never `grep -r`, which hits the Bash timeout on a
tree this big. Use the Bugzilla and Phabricator tools you were given.

# Working in the tree

- Stay in your working directory, the source tree: write scratch files by absolute
  path rather than `cd`-ing elsewhere. If a command reports "not a git repository",
  you have moved -- run `git -C <tree>` rather than hunting for the tree.
- Review your edits with `git diff --stat` and `git diff -- <path>`, never
  `git status`: after a build the objdir adds millions of untracked files and the
  output runs to tens of MB.
- Never read or cat a whole CI log; they run to six figures of lines. Read a window
  with `sed -n`, and clip the width as well as the line count with `| cut -c1-200`,
  because a single log line can be 10 KB.
"""

FIX_TEMPLATE = """\
You are fixing a regression in a Firefox CI test for the developer who caused it.
A test-repair analysis has already blamed commit {culprit_commit}{bug_note}
(confidence {confidence}), and sheriffs back it out. Propose a patch for the
culprit's author to squash into their existing patches and reland: a change to
the original patch, not a follow-up on top of it, and never a replacement for the
backout. The author relies on it being correct, so verify it rather than assume
it.

Failing test groups (manifests) and the tests that failed in each:
{failing_tests}
Test harness: {harness}
Test platform: {platform}
Test task: {label}
Failure push: {project} hg revision {hg_revision}

The analysis left its work in {analysis_dir}. Read it before touching the tree,
and leave it as it is:
- summary.md -- the verdict in 2-3 sentences
- analysis.md -- how the test fails, with the log line that shows it; the hunk in
  the culprit that causes it; the alternatives ruled out
- verdict.json -- the structured verdict
- logs/ -- the failure logs the analysis read. Go back to the lines analysis.md
  cites rather than searching a log end to end.
The analysis was written for sheriffs to settle a backout, not to fix the code, so
it names the hunk that breaks this test and may stop there. What you write is for
the culprit's author, who knows the code and wants to know what is wrong with their
patch and why your change is right.

The source tree is at {source_repo} (your working directory), checked out at
{culprit_commit} itself, so the diff you leave stacks directly on that commit.
Editing: Read a file before you Edit or Write it -- both refuse until the file has
been read, which costs a turn. To see how the culprit handled comparable files,
run `git show {culprit_commit} -- <dir>` rather than guessing a sibling's name.

1. Confirm the root cause in the culprit's diff (`git show {culprit_commit}`), and
   find every place it applies, not only the one this test hit: the other users
   of what the culprit renamed, removed or changed (`git grep`). If the diff does
   not bear the analysis out, make no patch and say why in your final message.
2. Make the smallest change that addresses the root cause everywhere it applies.
   Do not add code comments explaining the fix or what it replaced: the revision
   summary carries that, and the patch is squashed into the author's own. Add a
   comment only where the code would be unclear without it, as the surrounding
   code would.
{verify_step}
4. Write summary.md, fix.md and result.json to {out_dir} in one Bash command,
   with a heredoc per file:
   - summary.md: 2-3 sentences of plain prose for the author -- no headings, lists
     or code blocks: what in their commit breaks which test, what the patch
     changes, and whether it was verified.
   - fix.md: the author's reference for the patch, under 40 lines, in exactly
     these sections, each a short paragraph or a few bullets:
       ## Root cause -- the mistake in the culprit, and every place it applies
       ## Patch -- what changes in each file and why that is correct; the diff
          is attached, so do not walk through it
       ## Verification -- what was built and run and with what result, or why it
          could not be verified
     Do not restate the backout or the CI history: the author has the analysis
     for that.
   - result.json: "proposed_patch" (true if you left a patch in the tree) and
     "verified" (true only if the failing test(s) ran here and passed, on the
     failing platform's OS).
{report}"""

PARENT_REVISION_ARG = (
    " parent_revision_id={revision} (the culprit's own revision, D{revision}),"
)

BUG_NOTE = " (bug {bug_id})"

REPORT_INSTRUCTIONS = """
5. Submit the patch with the `phabricator_submit_patch` action: bug_id={bug_id},{parent}
   a title of the form "Bug {bug_id} - <what the fix does>", a summary naming the
   culprit commit and the failing test(s) and saying the patch is meant to be
   squashed into the culprit and relanded, and a test_plan stating how it was
   verified. If you are not confident in the patch, record nothing and say so in
   your final message.
"""

VERIFY_LOCAL = """\
3. Build with the build_firefox tool, then run the failing test(s) over Bash with
   mach -- see the tree's AGENTS.md / CLAUDE.md for how it runs {harness} tests.
   They should pass."""

VERIFY_REMOTE = """\
3. Build with the build_firefox tool, then run the failing test(s) over Bash with
   mach -- see the tree's AGENTS.md / CLAUDE.md for how it runs {harness} tests.
   This container is Linux and the failure is on {platform}: a failure here is
   evidence the patch is wrong, but a pass proves nothing about {platform}. Report
   a pass as "not verified"."""

VERIFY_SKIPPED = """\
3. Do not build, and do not try to run the test(s); no build tool is available.
   Check by reading instead that the patch compiles and addresses the root cause,
   and report it as unverified, neither built nor run."""

ENVIRONMENT_NOTE = """
   Environment: a Debian container with a virtual display and the build toolchain
   bootstrapped, so mach can build and run GUI harnesses. It is not CI's image, so
   a test that cannot run here at all is "could not verify", not a failure -- say
   which it was."""
