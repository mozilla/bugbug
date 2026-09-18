import assert from "node:assert/strict";
import { test } from "node:test";

import { parseUpliftSources } from "./uplift.ts";

test("accepts a git source", () => {
  const result = parseUpliftSources('[{"kind": "git", "commit": "abc123"}]');
  assert.deepEqual(result.sources, [{ kind: "git", commit: "abc123" }]);
});

test("accepts a phabricator source with and without a pinned diff", () => {
  const result = parseUpliftSources(
    '[{"kind": "phabricator", "revision_id": 12345},' +
      ' {"kind": "phabricator", "revision_id": 6, "diff_id": 500}]'
  );
  assert.deepEqual(result.sources, [
    { kind: "phabricator", revision_id: 12345 },
    { kind: "phabricator", revision_id: 6, diff_id: 500 },
  ]);
});

test("keeps a mixed stack in the order given", () => {
  const result = parseUpliftSources(
    '[{"kind": "git", "commit": "aaa"},' +
      ' {"kind": "phabricator", "revision_id": 9},' +
      ' {"kind": "git", "commit": "bbb"}]'
  );
  assert.deepEqual(result.sources?.map((source) => source.kind), [
    "git",
    "phabricator",
    "git",
  ]);
});

test("rejects input that is not a non-empty JSON list", () => {
  for (const value of ["", "   ", "{not json", "[]"]) {
    assert.ok(
      parseUpliftSources(value).error,
      `expected an error for ${JSON.stringify(value)}`
    );
  }
  assert.match(
    parseUpliftSources('{"kind": "git", "commit": "abc"}').error ?? "",
    /JSON list/,
    "a bare object should be reported as needing a list"
  );
});

test("rejects a git source with no usable commit", () => {
  for (const value of [
    '[{"kind": "git"}]',
    '[{"kind": "git", "commit": ""}]',
    '[{"kind": "git", "commit": "   "}]',
    '[{"kind": "git", "commit": 123}]',
  ]) {
    assert.match(
      parseUpliftSources(value).error ?? "",
      /commit/,
      `expected a commit error for ${value}`
    );
  }
});

test("rejects a phabricator source with no usable revision", () => {
  for (const value of [
    '[{"kind": "phabricator"}]',
    '[{"kind": "phabricator", "revision_id": "12345"}]',
    '[{"kind": "phabricator", "revision_id": 0}]',
    '[{"kind": "phabricator", "revision_id": 1.5}]',
  ]) {
    assert.match(
      parseUpliftSources(value).error ?? "",
      /revision_id/,
      `expected a revision error for ${value}`
    );
  }
});

test("rejects a pinned diff_id that is not a positive number", () => {
  assert.match(
    parseUpliftSources('[{"kind": "phabricator", "revision_id": 9, "diff_id": "500"}]')
      .error ?? "",
    /diff_id/
  );
});

test("rejects an unknown source kind", () => {
  assert.match(
    parseUpliftSources('[{"kind": "hg", "rev": "abc"}]').error ?? "",
    /unknown "kind"/
  );
});

test("names the offending source by position", () => {
  assert.match(
    parseUpliftSources('[{"kind": "git", "commit": "aaa"}, {"kind": "git"}]')
      .error ?? "",
    /source 2/,
    "the message should point at the entry that is wrong"
  );
});
