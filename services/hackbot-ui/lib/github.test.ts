import assert from "node:assert/strict";
import { test } from "node:test";

import { GIT_REF, GIT_SHA, parsePullRequest } from "./github.ts";

const PR = { repo: "mozilla-firefox/firefox", number: 42 };

test("accepts a pull request URL", () => {
  assert.deepEqual(
    parsePullRequest("https://github.com/mozilla-firefox/firefox/pull/42"),
    PR
  );
  assert.deepEqual(
    parsePullRequest("  github.com/mozilla-firefox/firefox/pull/42  "),
    PR
  );
});

test("accepts a pull request's tabs and fragments", () => {
  assert.deepEqual(
    parsePullRequest(
      "https://github.com/mozilla-firefox/firefox/pull/42/files#diff-1"
    ),
    PR
  );
  assert.deepEqual(
    parsePullRequest("https://github.com/mozilla-firefox/firefox/pull/42/"),
    PR
  );
});

test("accepts the owner/repo#number short form", () => {
  assert.deepEqual(parsePullRequest("mozilla-firefox/firefox#42"), PR);
});

test("rejects anything that is not a pull request", () => {
  for (const value of [
    "",
    "42",
    "#42",
    "firefox#42",
    "mozilla-firefox/firefox#0",
    "mozilla-firefox/firefox#4x",
    "https://github.com/mozilla-firefox/firefox/issues/42",
    "https://github.com/mozilla-firefox/firefox",
    "https://gitlab.com/mozilla-firefox/firefox/pull/42",
    "https://bugzilla.mozilla.org/show_bug.cgi?id=42",
  ]) {
    assert.equal(parsePullRequest(value), null, value);
  }
});

test("validates pins the way the API does", () => {
  assert.ok(GIT_SHA.test("0123456789abcdef0123456789abcdef01234567"));
  assert.ok(!GIT_SHA.test("0123456"));
  assert.ok(GIT_REF.test("release/v1.2+esr"));
  for (const ref of ["--upload-pack=x", "main branch", "a:b", ""]) {
    assert.ok(!GIT_REF.test(ref), ref);
  }
});
