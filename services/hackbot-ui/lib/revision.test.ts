import assert from "node:assert/strict";
import { test } from "node:test";

import { revisionMessage } from "./revision.ts";

test("reads the pending revision's message off the run's actions", () => {
  const actions = [
    { type: "email.send", params: { subject: "x" } },
    {
      type: "phabricator.submit_patch",
      params: { title: "Bug 1 - Fix it", summary: "why" },
    },
  ];
  assert.deepEqual(revisionMessage(actions), {
    title: "Bug 1 - Fix it",
    summary: "why",
  });
});

test("no message without a submit action or a title", () => {
  assert.equal(revisionMessage(null), null);
  assert.equal(revisionMessage([]), null);
  assert.equal(
    revisionMessage([{ type: "phabricator.submit_patch", params: {} }]),
    null
  );
});

test("a blank summary is dropped, not rendered empty", () => {
  assert.deepEqual(
    revisionMessage([
      {
        type: "phabricator.submit_patch",
        params: { title: "T", summary: " " },
      },
    ]),
    { title: "T", summary: null }
  );
});
