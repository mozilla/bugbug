import assert from "node:assert/strict";
import { test } from "node:test";

import { resolveEnvironment } from "./environment.ts";

test("production on its custom and platform hostnames", () => {
  for (const host of ["hackbot.moz.tools", "hackbot.prod.webservices.mozgcp.net"]) {
    assert.equal(resolveEnvironment(host), "production");
    assert.equal(resolveEnvironment(`https://${host}/runs`), "production");
  }
});

test("staging on its custom and platform hostnames", () => {
  for (const host of ["hackbot.allizom.org", "stage.hackbot.nonprod.webservices.mozgcp.net"]) {
    assert.equal(resolveEnvironment(host), "staging");
    assert.equal(resolveEnvironment(`https://${host}`), "staging");
  }
});

test("development for everything else", () => {
  for (const value of [
    "hackbot-dev.allizom.org",
    "https://dev.hackbot.nonprod.webservices.mozgcp.net",
    "localhost",
    "",
    "https://hackbot.moz.tools.evil.example",
    "stage.example.com",
    "not a url://",
  ]) {
    assert.equal(resolveEnvironment(value), "development", value);
  }
});
