import assert from "node:assert/strict";
import { test } from "node:test";

import { injectProfile, PROFILER_ORIGIN } from "./profiler.ts";

type Listener = (event: { origin: string; data: unknown }) => void;

function fakeProfilerTab() {
  const posted: { message: { name: string }; origin: string }[] = [];
  const listeners = new Set<Listener>();
  const source = {
    addEventListener: (_type: string, fn: Listener) => listeners.add(fn),
    removeEventListener: (_type: string, fn: Listener) => listeners.delete(fn),
  };
  const target = {
    postMessage(message: { name: string }, origin: string) {
      posted.push({ message, origin });
      if (message.name === "ready:request") {
        for (const fn of listeners) {
          fn({ origin: PROFILER_ORIGIN, data: { name: "ready:response" } });
        }
      }
    },
  };
  return { posted, listeners, source, target };
}

test("injects the profile once the profiler reports ready", async () => {
  const tab = fakeProfilerTab();
  const profile = { meta: { product: "Claude Code" } };

  await injectProfile(tab.target as never, profile, tab.source as never);

  assert.deepEqual(tab.posted, [
    { message: { name: "ready:request" }, origin: PROFILER_ORIGIN },
    { message: { name: "inject-profile", profile }, origin: PROFILER_ORIGIN },
  ]);
  assert.equal(tab.listeners.size, 0);
});

test("ignores ready messages from other origins", async () => {
  const tab = fakeProfilerTab();
  const listeners = tab.listeners;
  tab.target.postMessage = (message: { name: string }) => {
    tab.posted.push({ message, origin: PROFILER_ORIGIN });
    for (const fn of listeners) {
      fn({ origin: "https://evil.example", data: { name: "ready:response" } });
    }
  };

  let settled = false;
  injectProfile(tab.target as never, {}, tab.source as never).then(
    () => (settled = true),
    () => (settled = true)
  );
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(settled, false);
  assert.equal(listeners.size, 1);
  for (const fn of listeners) {
    fn({ origin: PROFILER_ORIGIN, data: { name: "ready:response" } });
  }
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(tab.posted.at(-1)?.message.name, "inject-profile");
});
