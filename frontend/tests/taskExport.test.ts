import assert from "node:assert/strict";
import test from "node:test";

import { exportReadyTaskIds } from "../src/lib/taskExport.ts";

test("exports only tasks with a complete current approval", () => {
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
