import assert from "node:assert/strict";
import test from "node:test";

import {
  exportReadyTaskIds,
  nextRevalidationTaskIds,
  REVALIDATION_TASK_LIMIT,
  taskIsAutoReady,
  taskIsManualReady,
  taskNeedsAttention,
} from "../src/lib/taskExport.ts";

test("exports only tasks admitted by the current automatic or manual policy", () => {
  const taskIds = exportReadyTaskIds([
    { id: "ready-1", export_ready: true },
    { id: "legacy-approved", export_ready: false },
    { id: "needs-review", export_ready: false },
    { id: "ready-2", export_ready: true },
  ]);

  assert.deepEqual(taskIds, ["ready-1", "ready-2"]);
});

test("keeps an empty export blocked when no task is ready", () => {
  assert.deepEqual(
    exportReadyTaskIds([
      { id: "legacy-approved", export_ready: false },
      { id: "draft", export_ready: false },
    ]),
    [],
  );
});

test("uses one admission predicate for stale, draft and review tasks", () => {
  assert.equal(taskNeedsAttention({ status: "validated", export_ready: false }), true);
  assert.equal(taskNeedsAttention({ status: "approved", export_ready: false }), true);
  assert.equal(taskNeedsAttention({ status: "needs_review", export_ready: false }), true);
  assert.equal(taskNeedsAttention({ status: "draft", export_ready: false }), true);
  assert.equal(taskNeedsAttention({ status: "rejected", export_ready: false }), false);
  assert.equal(taskNeedsAttention({ status: "validated", export_ready: true }), false);
});

test("distinguishes automatic admission from a documented manual exception", () => {
  assert.equal(taskIsAutoReady({ status: "validated", export_ready: true }), true);
  assert.equal(taskIsAutoReady({ status: "validated", export_ready: false }), false);
  assert.equal(taskIsManualReady({ status: "approved", export_ready: true }), true);
  assert.equal(taskIsManualReady({ status: "approved", export_ready: false }), false);
});

test("limits one background revalidation request to the next 100 attention tasks", () => {
  const queue = Array.from({ length: REVALIDATION_TASK_LIMIT + 7 }, (_, index) => ({
    id: `attention-${index}`,
    status: "draft" as const,
    export_ready: false,
  }));
  const ids = nextRevalidationTaskIds([
    { id: "ready", status: "validated", export_ready: true },
    { id: "discarded", status: "rejected", export_ready: false },
    ...queue,
  ]);

  assert.equal(ids.length, REVALIDATION_TASK_LIMIT);
  assert.equal(ids[0], "attention-0");
  assert.equal(ids.at(-1), "attention-99");
});
