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

export interface ProviderBalance {
  supported: boolean;
  ok: boolean;
  balance: string;
  message: string;
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
  published_version: string;
  published_at: string | null;
  created_at: string;
}

export interface PicreteCourseOption {
  id: string;
  title: string;
  organization: string | null;
}

export interface CoursePublishResult {
  ok: boolean;
  version: string;
  published_at: string;
  assistant_name: string;
  course_id: string;
}

export type PromptRole = "grader" | "generator" | "tutor";

export interface PromptVersion {
  id: string;
  assistant_id: string;
  role: PromptRole;
  version: number;
  system_prompt: string;
  notes: string;
  source: "manual" | "generated";
  target_family: string;
  architect_model: string;
  status: "draft" | "active" | "archived";
  created_at: string;
}

export type TaskKind = "calculation" | "conceptual" | "test_tf" | "test_mc" | "derivation";
export type AnswerFormat = "numeric" | "formula" | "text" | "choice";

export interface ExampleTask {
  statement: string;
  solution: string;
  answer: string;
}

export interface TaskTemplate {
  id: string;
  assistant_id: string;
  name: string;
  topic: string;
  difficulty: string;
  instructions: string;
  example: string;
  task_kind: TaskKind;
  answer_format: AnswerFormat;
  numeric_tolerance_pct: number;
  reference_sheet_ids: string[];
  example_tasks: ExampleTask[];
  kb_query: string;
  validation_solver: boolean;
  validation_data_check: boolean;
}

export type GeneratedTaskStatus = "draft" | "validated" | "needs_review" | "approved" | "rejected";

export interface TaskValidation {
  solver?: {
    status?: "match" | "mismatch" | "uncertain" | "error" | "skipped";
    answer?: string;
    solution?: string;
    model?: string;
    error?: string;
  };
  data?: { status?: "ok" | "warn" | "skipped"; unknown_numbers?: string[] };
  sanity?: { issues?: string[] };
  dedup?: { duplicate?: boolean; similarity?: number };
  verdict?: "validated" | "needs_review";
  reasons?: string[];
}

export interface GeneratedTask {
  id: string;
  assistant_id: string;
  template_id: string | null;
  batch_id: string | null;
  statement: string;
  reference_solution: string;
  answer: string;
  rubric: Array<{ criterion_name: string; max_score: number; description?: string }>;
  max_score: number;
  difficulty: string;
  topic: string;
  model_used: string;
  status: GeneratedTaskStatus;
  validation: TaskValidation;
  grounding: { sheets?: Array<{ id: string; title: string }>; kb_chunks?: number };
  approved: boolean;
  created_at: string;
}

export interface GenerationBatch {
  id: string;
  assistant_id: string;
  template_id: string | null;
  status: "running" | "completed" | "failed";
  params: Record<string, unknown>;
  model_used: string;
  requested_count: number;
  generated_count: number;
  validated_count: number;
  progress: { stage?: string; done?: number; total?: number };
  error: string;
  created_at: string;
  finished_at: string | null;
}

export type KnowledgeDocType = "rpd" | "notes" | "textbook" | "problem_book" | "reference" | "methodical" | "other";
export type MaterialAuthority = "course_policy" | "course_lecture" | "reference" | "unverified";
export type MaterialVisibility = "student" | "teacher_only" | "assessment_private" | "quarantine";

export interface KnowledgeDocument {
  id: string;
  assistant_id: string;
  title: string;
  doc_type: KnowledgeDocType;
  authority: MaterialAuthority;
  visibility: MaterialVisibility;
  course_scope: string;
  effective_version: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  status: "uploaded" | "parsing" | "parsed" | "failed";
  page_count: number;
  extract_method: "" | "text" | "ocr";
  analysis_status: "none" | "running" | "ready" | "applied" | "failed";
  analysis_error: string;
  error: string;
  created_at: string;
  chunk_count: number;
}

export interface AnalyzeSheetProposal {
  title: string;
  kind: ReferenceSheetKind;
  description: string;
  content_markdown: string;
}

export interface DocumentAnalysis {
  summary: string;
  topics: string[];
  sheets: AnalyzeSheetProposal[];
  notation_notes: string;
}

export interface KnowledgeDocumentDetail extends KnowledgeDocument {
  markdown: string;
}

export interface KnowledgeChunk {
  id: string;
  document_id: string;
  assistant_id: string;
  ord: number;
  heading: string;
  content: string;
  kind: "text" | "table";
  char_len: number;
}

export type ReferenceSheetKind = "data_table" | "glossary" | "conventions" | "formulas" | "other";

export interface ReferenceSheet {
  id: string;
  assistant_id: string;
  title: string;
  kind: ReferenceSheetKind;
  description: string;
  content_markdown: string;
  source_document_id: string | null;
  visibility: MaterialVisibility;
  is_canonical: boolean;
  ord: number;
  created_at: string;
  updated_at: string;
}

export interface TutorMessage {
  role: "user" | "assistant";
  content: string;
}

export interface TutorRun {
  id: string;
  assistant_id: string;
  task_id: string | null;
  prompt_version_id: string | null;
  provider_name: string;
  model_id: string;
  student_work: string;
  messages: TutorMessage[];
  rating: number | null;
  comment: string;
  created_at: string;
  updated_at: string;
}

export interface PromptPreview {
  system_prompt: string;
  user_message: string;
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
