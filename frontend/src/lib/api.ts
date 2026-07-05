import axios from "axios";
import type {
  Assistant,
  Course,
  GeneratedTask,
  Pipeline,
  PipelineRun,
  PipelineStep,
  PlaygroundResult,
  PlaygroundRun,
  Provider,
  ProviderPreset,
  PromptVersion,
  TaskTemplate,
  UserOut,
} from "./types";

export const api = axios.create({ baseURL: "/api", timeout: 600_000 });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("studio_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !window.location.pathname.startsWith("/login")) {
      localStorage.removeItem("studio_token");
      window.location.href = "/login";
    }
    return Promise.reject(error);
  },
);

export function apiErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg ?? JSON.stringify(d)).join("; ");
    return error.message;
  }
  return String(error);
}

export const authApi = {
  async login(username: string, password: string): Promise<string> {
    const body = new URLSearchParams({ username, password });
    const { data } = await api.post<{ access_token: string }>("/auth/login", body);
    return data.access_token;
  },
  me: () => api.get<UserOut>("/auth/me").then((r) => r.data),
  changePassword: (current_password: string, new_password: string) =>
    api.post<UserOut>("/auth/change-password", { current_password, new_password }).then((r) => r.data),
};

export const usersApi = {
  list: () => api.get<UserOut[]>("/auth/users").then((r) => r.data),
  create: (body: { username: string; password: string; full_name: string; role: string }) =>
    api.post<UserOut>("/auth/users", body).then((r) => r.data),
  update: (id: string, body: Partial<{ full_name: string; role: string; is_active: boolean; password: string }>) =>
    api.patch<UserOut>(`/auth/users/${id}`, body).then((r) => r.data),
  remove: (id: string) => api.delete(`/auth/users/${id}`),
};

export const coursesApi = {
  list: (assistantId: string) => api.get<Course[]>(`/assistants/${assistantId}/courses`).then((r) => r.data),
  create: (assistantId: string, body: Partial<Course>) =>
    api.post<Course>(`/assistants/${assistantId}/courses`, body).then((r) => r.data),
  update: (assistantId: string, courseId: string, body: Partial<Course>) =>
    api.patch<Course>(`/assistants/${assistantId}/courses/${courseId}`, body).then((r) => r.data),
  remove: (assistantId: string, courseId: string) => api.delete(`/assistants/${assistantId}/courses/${courseId}`),
};

export const providersApi = {
  list: () => api.get<Provider[]>("/providers").then((r) => r.data),
  presets: () => api.get<ProviderPreset[]>("/providers/presets").then((r) => r.data),
  create: (body: { name: string; kind: string; purpose: string; base_url: string; api_key: string }) =>
    api.post<Provider>("/providers", body).then((r) => r.data),
  update: (id: string, body: Partial<{ name: string; base_url: string; api_key: string; enabled: boolean }>) =>
    api.patch<Provider>(`/providers/${id}`, body).then((r) => r.data),
  remove: (id: string) => api.delete(`/providers/${id}`),
  test: (id: string) =>
    api.post<{ ok: boolean; message: string; duration_ms: number | null }>(`/providers/${id}/test`).then((r) => r.data),
  addModel: (
    providerId: string,
    body: { model_id: string; display_name?: string; family?: string; supports_vision?: boolean; supports_json?: boolean },
  ) => api.post(`/providers/${providerId}/models`, body).then((r) => r.data),
  removeModel: (providerId: string, modelId: string) => api.delete(`/providers/${providerId}/models/${modelId}`),
};

export const assistantsApi = {
  list: () => api.get<Assistant[]>("/assistants").then((r) => r.data),
  get: (id: string) => api.get<Assistant>(`/assistants/${id}`).then((r) => r.data),
  create: (body: Partial<Assistant>) => api.post<Assistant>("/assistants", body).then((r) => r.data),
  update: (id: string, body: Partial<Assistant>) => api.patch<Assistant>(`/assistants/${id}`, body).then((r) => r.data),
  addNuance: (id: string, text: string) =>
    api.post<Assistant>(`/assistants/${id}/nuances`, { text }).then((r) => r.data),
  remove: (id: string) => api.delete(`/assistants/${id}`),
};

export const promptsApi = {
  list: (assistantId: string) => api.get<PromptVersion[]>(`/assistants/${assistantId}/prompts`).then((r) => r.data),
  create: (assistantId: string, body: { role: string; system_prompt: string; notes?: string; target_family?: string }) =>
    api.post<PromptVersion>(`/assistants/${assistantId}/prompts`, body).then((r) => r.data),
  generate: (
    assistantId: string,
    body: { role: string; target_model_entry_id: string; extra_instructions: string },
  ) => api.post<PromptVersion>(`/assistants/${assistantId}/prompts/generate`, body).then((r) => r.data),
  activate: (assistantId: string, promptId: string) =>
    api.post<PromptVersion>(`/assistants/${assistantId}/prompts/${promptId}/activate`).then((r) => r.data),
  remove: (assistantId: string, promptId: string) => api.delete(`/assistants/${assistantId}/prompts/${promptId}`),
};

export const tasksApi = {
  templates: (assistantId: string) => api.get<TaskTemplate[]>(`/assistants/${assistantId}/templates`).then((r) => r.data),
  createTemplate: (assistantId: string, body: Partial<TaskTemplate>) =>
    api.post<TaskTemplate>(`/assistants/${assistantId}/templates`, body).then((r) => r.data),
  removeTemplate: (assistantId: string, templateId: string) =>
    api.delete(`/assistants/${assistantId}/templates/${templateId}`),
  list: (assistantId: string) => api.get<GeneratedTask[]>(`/assistants/${assistantId}/tasks`).then((r) => r.data),
  generate: (
    assistantId: string,
    body: {
      template_id?: string | null;
      model_entry_id: string;
      topic?: string;
      difficulty?: string;
      count?: number;
      instructions?: string;
    },
  ) => api.post<GeneratedTask[]>(`/assistants/${assistantId}/tasks/generate`, body).then((r) => r.data),
  update: (assistantId: string, taskId: string, body: Partial<GeneratedTask>) =>
    api.patch<GeneratedTask>(`/assistants/${assistantId}/tasks/${taskId}`, body).then((r) => r.data),
  remove: (assistantId: string, taskId: string) => api.delete(`/assistants/${assistantId}/tasks/${taskId}`),
};

export const pipelinesApi = {
  list: (assistantId: string) => api.get<Pipeline[]>(`/assistants/${assistantId}/pipelines`).then((r) => r.data),
  create: (assistantId: string, body: { name: string; description?: string; steps: PipelineStep[] }) =>
    api.post<Pipeline>(`/assistants/${assistantId}/pipelines`, body).then((r) => r.data),
  update: (assistantId: string, pipelineId: string, body: Partial<{ name: string; description: string; steps: PipelineStep[] }>) =>
    api.patch<Pipeline>(`/assistants/${assistantId}/pipelines/${pipelineId}`, body).then((r) => r.data),
  remove: (assistantId: string, pipelineId: string) => api.delete(`/assistants/${assistantId}/pipelines/${pipelineId}`),
  run: (
    assistantId: string,
    pipelineId: string,
    body: {
      task_id?: string | null;
      task_text?: string;
      reference_solution?: string;
      rubric?: unknown[];
      max_score?: number;
      ocr_text?: string;
      image_ids?: string[];
    },
  ) => api.post<PipelineRun>(`/assistants/${assistantId}/pipelines/${pipelineId}/run`, body).then((r) => r.data),
  runs: (assistantId: string, pipelineId: string) =>
    api.get<PipelineRun[]>(`/assistants/${assistantId}/pipelines/${pipelineId}/runs`).then((r) => r.data),
};

export const playgroundApi = {
  ocr: (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return api.post<{ ocr_text: string; image_ids: string[] }>("/playground/ocr", form).then((r) => r.data);
  },
  compare: (body: {
    assistant_id: string;
    prompt_version_id?: string | null;
    task_id?: string | null;
    task_text?: string;
    reference_solution?: string;
    rubric?: unknown[];
    max_score?: number;
    ocr_text: string;
    image_ids?: string[];
    model_entry_ids: string[];
    temperature?: number;
  }) => api.post<PlaygroundRun>("/playground/compare", body).then((r) => r.data),
  runs: (assistantId: string) =>
    api.get<PlaygroundRun[]>("/playground/runs", { params: { assistant_id: assistantId } }).then((r) => r.data),
  feedback: (resultId: string, body: { rating?: number; is_winner?: boolean; comment?: string }) =>
    api.post<PlaygroundResult>(`/playground/results/${resultId}/feedback`, body).then((r) => r.data),
};
