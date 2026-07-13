import type { GeneratedTask } from "./types";

export function exportReadyTaskIds(tasks: Array<Pick<GeneratedTask, "id" | "export_ready">>): string[] {
  return tasks.filter((task) => task.export_ready === true).map((task) => task.id);
}
