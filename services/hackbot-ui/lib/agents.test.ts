import assert from "node:assert/strict";
import { test } from "node:test";

import { GECKO_TRIAGE_TEAMS, parseGeckoTriageTeam } from "./agents.ts";

test("a gecko-triage run takes the first team by default", () => {
  assert.equal(parseGeckoTriageTeam(null), GECKO_TRIAGE_TEAMS[0]);
});

test("a known gecko-triage team is kept", () => {
  assert.equal(parseGeckoTriageTeam("media"), "media");
});

test("an empty gecko-triage team means no team", () => {
  assert.equal(parseGeckoTriageTeam(""), "");
});

test("an unknown gecko-triage team falls back to the first team", () => {
  assert.equal(parseGeckoTriageTeam("graphics"), GECKO_TRIAGE_TEAMS[0]);
});
