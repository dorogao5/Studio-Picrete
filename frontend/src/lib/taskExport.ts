import type { GeneratedTask } from "./types";

type TaskAdmissionState = Pick<GeneratedTask, "status" | "export_ready">;
export const REVALIDATION_TASK_LIMIT = 100;

export function taskNeedsAttention(task: TaskAdmissionState): boolean {
  return task.export_ready !== true && task.status !== "rejected";
}

export function taskIsAutoReady(task: TaskAdmissionState): boolean {
  return task.export_ready === true && task.status === "validated";
}

export function taskIsManualReady(task: TaskAdmissionState): boolean {
  return task.export_ready === true && task.status === "approved";
}

export function nextRevalidationTaskIds(
  tasks: Array<Pick<GeneratedTask, "id" | "status" | "export_ready">>,
): string[] {
  return tasks.filter(taskNeedsAttention).slice(0, REVALIDATION_TASK_LIMIT).map((task) => task.id);
}

export function exportReadyTaskIds(tasks: Array<Pick<GeneratedTask, "id" | "export_ready">>): string[] {
  return tasks.filter((task) => task.export_ready === true).map((task) => task.id);
}
