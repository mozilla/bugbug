import assert from "node:assert/strict";
import { test } from "node:test";

import { describingCommits, parsePatch } from "./patch.ts";

// Verbatim `git format-patch --stdout --binary` output for a commit that edits,
// adds and deletes a file. The added file's second line is literally "- not a
// deletion" and the mbox ends with git's "-- " signature, so both sit where a
// terminator-based parser would misread them.
const MBOX = `From 7373a0aa45277bbccfa0e05658f7fbb3f22ebac6 Mon Sep 17 00:00:00 2001
From: T <t@t>
Date: Tue, 25 Aug 2026 12:43:53 -0700
Subject: [PATCH] the fix

---
 a.txt     | 2 +-
 added.txt | 2 ++
 gone.txt  | 1 -
 3 files changed, 3 insertions(+), 2 deletions(-)
 create mode 100644 added.txt
 delete mode 100644 gone.txt

diff --git a/a.txt b/a.txt
index 4cb29ea..ddc897f 100644
--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
diff --git a/added.txt b/added.txt
new file mode 100644
index 0000000..4ba76f2
--- /dev/null
+++ b/added.txt
@@ -0,0 +1,2 @@
+new
+- not a deletion
diff --git a/gone.txt b/gone.txt
deleted file mode 100644
index 587be6b..0000000
--- a/gone.txt
+++ /dev/null
@@ -1 +0,0 @@
-x
--
2.50.1 (Apple Git-155)
`;

test("splits an mbox into one entry per file", () => {
  const files = parsePatch(MBOX);
  assert.deepEqual(
    files.map((f) => [f.path, f.status]),
    [
      ["a.txt", "modified"],
      ["added.txt", "added"],
      ["gone.txt", "deleted"],
    ]
  );
});

test("counts and classifies hunk lines, skipping file headers", () => {
  const [edited] = parsePatch(MBOX);
  assert.equal(edited.added, 1);
  assert.equal(edited.removed, 1);
  assert.deepEqual(edited.lines, [
    { kind: "hunk", text: "@@ -1,3 +1,3 @@" },
    { kind: "ctx", text: " one" },
    { kind: "del", text: "-two" },
    { kind: "add", text: "+TWO" },
    { kind: "ctx", text: " three" },
  ]);
});

test("reads added content literally, dashes and all", () => {
  const added = parsePatch(MBOX)[1];
  assert.equal(added.added, 2);
  assert.equal(added.removed, 0);
  assert.deepEqual(
    added.lines.map((l) => l.text),
    ["@@ -0,0 +1,2 @@", "+new", "+- not a deletion"]
  );
});

test("stops at the hunk's line count, not at git's -- signature", () => {
  const deleted = parsePatch(MBOX)[2];
  assert.equal(deleted.removed, 1);
  assert.deepEqual(deleted.lines, [
    { kind: "hunk", text: "@@ -1 +0,0 @@" },
    { kind: "del", text: "-x" },
  ]);
});

test("flags a binary diff instead of rendering the blob", () => {
  const files = parsePatch(
    [
      "diff --git a/logo.png b/logo.png",
      "index 0000000..1111111 100644",
      "GIT binary patch",
      "literal 8",
      "zcmZQzU|?<",
      "",
    ].join("\n")
  );
  assert.equal(files.length, 1);
  assert.equal(files[0].binary, true);
  assert.deepEqual(files[0].lines, []);
});

test("keeps a rename's old path", () => {
  const files = parsePatch(
    [
      "diff --git a/old/name.js b/new/name.js",
      "similarity index 92%",
      "rename from old/name.js",
      "rename to new/name.js",
      "",
    ].join("\n")
  );
  assert.equal(files[0].status, "renamed");
  assert.equal(files[0].oldPath, "old/name.js");
  assert.equal(files[0].path, "new/name.js");
});

test("returns nothing for a patch with no file diffs", () => {
  assert.deepEqual(parsePatch(""), []);
  assert.deepEqual(parsePatch("From 7373a0a Mon Sep 17\nSubject: nope\n"), []);
});

const META = {
  base_commit: "a70d6d22",
  wrapped_uncommitted: false,
  commits: [
    {
      sha: "1",
      author_name: "Hackbot",
      authored_date: "",
      subject: "Bug 1 - Fix it",
      body: "why",
    },
  ],
};

const WRAPPER = {
  sha: "2",
  author_name: "Hackbot",
  authored_date: "",
  subject: "Uncommitted agent changes",
  body: "",
};

test("uses the agent's own commits as the patch description", () => {
  assert.deepEqual(describingCommits(META), META.commits);
});

test("drops the placeholder commit wrapping uncommitted work", () => {
  const wrapped = {
    ...META,
    wrapped_uncommitted: true,
    commits: [META.commits[0], WRAPPER],
  };
  assert.deepEqual(describingCommits(wrapped), [META.commits[0]]);
});

test("describes nothing when the patch is only uncommitted work", () => {
  const onlyWrapper = {
    ...META,
    wrapped_uncommitted: true,
    commits: [WRAPPER],
  };
  assert.deepEqual(describingCommits(onlyWrapper), []);
  assert.deepEqual(describingCommits(null), []);
});
