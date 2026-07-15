import assert from "node:assert/strict";
import test from "node:test";

import { isKnownAdvisoryModel } from "../src/lib/modelPolicy.ts";

test("recognizes explicitly advisory control models from stable metadata", () => {
  assert.equal(
    isKnownAdvisoryModel({ modelId: "deepseek-v4-flash", displayName: "DeepSeek V4 Flash", notes: "" }),
    true,
  );
  assert.equal(
    isKnownAdvisoryModel({ modelId: "custom-preview", displayName: "Экспериментальная", notes: "advisory only" }),
    true,
  );
});

test("keeps non-advisory models for the server-side decision-policy preflight", () => {
  assert.equal(
    isKnownAdvisoryModel({ modelId: "deepseek-v4-pro", displayName: "DeepSeek V4 Pro", notes: "" }),
    false,
  );
  assert.equal(
    isKnownAdvisoryModel({ modelId: "custom-max", displayName: "Custom Max", notes: "" }),
    false,
  );
  assert.equal(
    isKnownAdvisoryModel({ modelId: "custom-pro", displayName: "Custom Pro", notes: "Not a preview model" }),
    false,
  );
});
