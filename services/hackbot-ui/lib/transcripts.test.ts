import assert from "node:assert/strict";
import { test } from "node:test";

import {
  hasTranscripts,
  mergeSessions,
  parseJsonl,
  transcriptArtifacts,
} from "./transcripts.ts";

const artifact = (name: string) => ({ name, size: 1, content_type: null });

test("groups session and subagent transcripts, ignoring other artifacts", () => {
  const files = transcriptArtifacts(
    [
      "summary.json",
      "logs/agent.log",
      "transcripts/s2.jsonl",
      "transcripts/s1.jsonl",
      "transcripts/s1/subagents/agent-a1.meta.json",
      "transcripts/s1/subagents/agent-a1.jsonl",
      "transcripts/s1/subagents/agent-orphan.meta.json",
    ].map(artifact)
  );
  assert.deepEqual(files, {
    sessions: ["transcripts/s1.jsonl", "transcripts/s2.jsonl"],
    subagents: [
      {
        id: "a1",
        jsonl: "transcripts/s1/subagents/agent-a1.jsonl",
        meta: "transcripts/s1/subagents/agent-a1.meta.json",
      },
    ],
  });
});

test("hasTranscripts needs a session file, not just subagents", () => {
  assert.equal(hasTranscripts([artifact("summary.json")]), false);
  assert.equal(
    hasTranscripts([artifact("transcripts/s1/subagents/agent-a1.jsonl")]),
    false
  );
  assert.equal(hasTranscripts([artifact("transcripts/s1.jsonl")]), true);
});

test("parses JSONL and tolerates blank lines", () => {
  assert.deepEqual(parseJsonl('{"a":1}\n\n{"a":2}\n'), [{ a: 1 }, { a: 2 }]);
});

test("merges sessions in start order without interleaving", () => {
  const fix = [
    { timestamp: "2026-09-17T10:05:00Z", n: 1 },
    { timestamp: "2026-09-17T10:06:00Z", n: 2 },
  ];
  const analysis = [
    { n: 0 },
    { timestamp: "2026-09-17T10:00:00Z", n: 3 },
    { timestamp: "2026-09-17T10:07:00Z", n: 4 },
  ];
  assert.deepEqual(
    mergeSessions([fix, analysis]).map((e) => e.n),
    [0, 3, 4, 1, 2]
  );
});
