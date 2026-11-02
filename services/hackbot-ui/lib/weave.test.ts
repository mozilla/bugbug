import assert from "node:assert/strict";
import { test } from "node:test";

import { weaveRunTracesUrl } from "./weave.ts";

test("links to the agents view filtered by run id", () => {
  assert.equal(
    weaveRunTracesUrl("moz-bugbug/hackbot-dev", "local-20260910-182751-1f1633"),
    "https://wandb.ai/moz-bugbug/hackbot-dev/weave/agents/conversations" +
      "?filters[custom_attrs_string:wandb.attributes.hackbot.run_id]=local-20260910-182751-1f1633"
  );
});
