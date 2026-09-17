import assert from "node:assert/strict";
import { test } from "node:test";

import {
  addSessionMarkers,
  type Profile,
  sessionSpans,
} from "./session-markers.ts";

const analysis = [
  {
    type: "user",
    timestamp: "2026-09-17T22:21:41.000Z",
    message: { role: "user", content: "You are investigating a failing test" },
  },
  { type: "assistant", timestamp: "2026-09-17T22:24:32.000Z" },
  { type: "ai-title", aiTitle: "Debug failing test" },
];
const fix = [
  {
    type: "user",
    timestamp: "2026-09-17T22:24:34.000Z",
    message: {
      role: "user",
      content:
        "Commit abc regressed the failing test. Propose a minimal patch fixing it.",
    },
  },
  { type: "assistant", timestamp: "2026-09-17T22:25:08.000Z" },
];

function emptyProfile(startTime: number): Profile {
  return {
    meta: {
      startTime,
      categories: [{ name: "Other" }, { name: "Messages" }],
      markerSchema: [],
    },
    shared: { stringArray: ["Activity"] },
    threads: [
      {
        markers: {
          length: 0,
          name: [],
          startTime: [],
          endTime: [],
          phase: [],
          category: [],
          data: [],
        },
      },
    ],
  };
}

test("spans are ordered by start and titled from ai-title or the first prompt", () => {
  assert.deepEqual(sessionSpans([fix, analysis, [{ type: "user" }]]), [
    {
      title: "Debug failing test",
      start: Date.parse("2026-09-17T22:21:41.000Z"),
      end: Date.parse("2026-09-17T22:24:32.000Z"),
    },
    {
      title: "Commit abc regressed the failing test. Propose a minimal pat",
      start: Date.parse("2026-09-17T22:24:34.000Z"),
      end: Date.parse("2026-09-17T22:25:08.000Z"),
    },
  ]);
});

test("adds one interval marker per session on the main track", () => {
  const profile = emptyProfile(Date.parse("2026-09-17T22:21:41.000Z"));
  addSessionMarkers(profile, sessionSpans([analysis, fix]));

  const m = profile.threads[0].markers;
  assert.equal(m.length, 2);
  assert.deepEqual(m.name, [1, 1]);
  assert.equal(profile.shared.stringArray[1], "Session");
  assert.deepEqual(m.startTime, [0, 173000]);
  assert.deepEqual(m.endTime, [171000, 207000]);
  assert.deepEqual(m.phase, [1, 1]);
  assert.deepEqual(m.category, [1, 1]);
  assert.deepEqual(m.data[0], {
    type: "Session",
    index: 1,
    title: "Debug failing test",
  });
  assert.equal(
    (profile.meta.markerSchema[0] as { name: string }).name,
    "Session"
  );
});

test("single-session runs get no marker", () => {
  const profile = emptyProfile(0);
  addSessionMarkers(profile, sessionSpans([analysis]));
  assert.equal(profile.threads[0].markers.length, 0);
  assert.equal(profile.meta.markerSchema.length, 0);
});
