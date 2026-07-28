import assert from "node:assert/strict";
import { test } from "node:test";

import { parseBugId } from "./bugzilla.ts";

test("accepts a bare bug ID", () => {
  assert.equal(parseBugId("2058177"), 2058177);
  assert.equal(parseBugId("  2058177  "), 2058177);
});

test("accepts a show_bug.cgi URL", () => {
  assert.equal(
    parseBugId("https://bugzilla.mozilla.org/show_bug.cgi?id=2058177"),
    2058177
  );
  assert.equal(
    parseBugId("http://bugzilla.mozilla.org/show_bug.cgi?id=2058177"),
    2058177
  );
  assert.equal(
    parseBugId("//bugzilla.mozilla.org/show_bug.cgi?id=2058177"),
    2058177
  );
  assert.equal(
    parseBugId("bugzilla.mozilla.org/show_bug.cgi?id=2058177"),
    2058177
  );
});

test("ignores extra query parameters and fragments", () => {
  assert.equal(
    parseBugId(
      "https://bugzilla.mozilla.org/show_bug.cgi?id=2058177&comment=5#c5"
    ),
    2058177
  );
});

test("accepts the short bugzilla.mozilla.org/<id> form", () => {
  assert.equal(parseBugId("https://bugzilla.mozilla.org/2058177"), 2058177);
  assert.equal(parseBugId("bugzilla.mozilla.org/2058177/"), 2058177);
});

test("accepts the staging Bugzilla hosts", () => {
  assert.equal(
    parseBugId("https://bugzilla.allizom.org/show_bug.cgi?id=2058177"),
    2058177
  );
  assert.equal(
    parseBugId("https://BUGZILLA-DEV.allizom.org/show_bug.cgi?id=2058177"),
    2058177
  );
});

test("rejects a non-Bugzilla host carrying an id parameter", () => {
  for (const value of [
    "https://example.com/?id=2058177",
    "https://example.com/show_bug.cgi?id=2058177",
    "https://bugzilla.mozilla.org.evil.example/show_bug.cgi?id=2058177",
    "https://example.com/2058177",
    "//example.com/show_bug.cgi?id=2058177",
  ]) {
    assert.equal(parseBugId(value), null, `expected null for ${value}`);
  }
});

test("rejects input without a usable bug ID", () => {
  for (const value of [
    "",
    "   ",
    "0",
    "-1",
    "12abc",
    "abc",
    "https://bugzilla.mozilla.org/show_bug.cgi?id=abc",
    "https://bugzilla.mozilla.org/show_bug.cgi",
    "https://example.com/foo",
    "https://bugzilla.mozilla.org/describecomponents.cgi",
    "http://",
  ]) {
    assert.equal(parseBugId(value), null, `expected null for ${value}`);
  }
});
