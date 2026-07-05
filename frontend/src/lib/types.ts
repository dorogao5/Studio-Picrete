export interface UserOut {
  id: string;
  username: string;
  full_name: string;
  role: "admin" | "teacher";
  is_active: boolean;
}

export interface ModelEntry {
  id: string;
  provider_id: string;
  model_id: string;
  display_name: string;
  family: string;
  supports_vision: boolean;
  supports_json: boolean;
  notes: string;
  enabled: boolean;
}

export type ProviderPurpose = "production" | "architect";

export interface Provider {
  id: string;
  name: string;
  kind: string;
  purpose: ProviderPurpose;
  base_url: string;
  enabled: boolean;
  has_api_key: boolean;
  models: ModelEntry[];
}

export interface ProviderPreset {
  kind: string;
  title: string;
  purpose: ProviderPurpose;
  base_url: string;
  auth_note: string;
  docs_url: string;
  models: Array<{
    model_id: string;
    display_name: string;
    family: string;
    supports_vision: boolean;
    supports_json: boolean;
  }>;
}

export interface Criterion {
  name: string;
  max_score: number;
  description: string;
}

export interface Assistant {
  id: string;
  name: string;
  discipline: string;
  description: string;
  audience: string;
  language: string;
  topics: string[];
  criteria: Criterion[];
  nuances: string[];
  default_grader_model_id: string | null;
  default_generator_model_id: string | null;
  created_by: string;
  created_by_name: string;
  updated_by_name: string;
  created_at: string;
  updated_at: string | null;
}

export interface Course {
  id: string;
  assistant_id: string;
  name: string;
  description: string;
  term: string;
  external_course_id: string;
  created_at: string;
}

export interface PromptVersion {
  id: string;
  assistant_id: string;
  role: "grader" | "generator";
  version: number;
  system_prompt: string;
  notes: string;
  source: "manual" | "generated";
  target_family: string;
  architect_model: string;
  status: "draft" | "active" | "archived";
  created_at: string;
}

export interface TaskTemplate {
  id: string;
  assistant_id: string;
  name: string;
  topic: string;
  difficulty: string;
  instructions: string;
  example: string;
}

export interface GeneratedTask {
  id: string;
  assistant_id: string;
  template_id: string | null;
  statement: string;
  reference_solution: string;
  rubric: Array<{ criterion_name: string; max_score: number; description?: string }>;
  max_score: number;
  difficulty: string;
  topic: string;
  model_used: string;
  approved: boolean;
  created_at: string;
}

export interface PipelineStep {
  type: "ocr" | "grade" | "consensus";
  title?: string;
  config: Record<string, unknown>;
}

export interface Pipeline {
  id: string;
  assistant_id: string;
  name: string;
  description: string;
  steps: PipelineStep[];
  updated_at: string;
}

export interface PipelineRun {
  id: string;
  pipeline_id: string;
  status: "running" | "completed" | "failed";
  input: Record<string, unknown>;
  steps_log: Array<{
    index: number;
    type: string;
    title: string;
    status: string;
    output: Record<string, unknown>;
    duration_ms: number;
  }>;
  error: string;
  started_at: string;
  finished_at: string | null;
}

export interface GradingOutput {
  unreadable?: boolean;
  unreadable_reason?: string | null;
  total_score?: number;
  max_score?: number;
  criteria_scores?: Array<{ criterion_name: string; score: number; max_score: number; comment: string }>;
  detailed_analysis?: Record<string, unknown>;
  feedback?: string;
  recommendations?: string[];
  confidence?: number;
  needs_teacher_review?: boolean;
}

export interface PlaygroundResult {
  id: string;
  run_id: string;
  provider_name: string;
  model_id: string;
  status: "completed" | "failed";
  output: GradingOutput | null;
  raw_text: string;
  error: string;
  duration_ms: number;
  tokens_total: number | null;
  rating: number | null;
  is_winner: boolean;
  feedback_comment: string;
}

export interface PlaygroundRun {
  id: string;
  assistant_id: string;
  prompt_version_id: string | null;
  task_text: string;
  reference_solution: string;
  rubric: unknown[];
  max_score: number;
  ocr_text: string;
  images: string[];
  created_at: string;
  results: PlaygroundResult[];
}
