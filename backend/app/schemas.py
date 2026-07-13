from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.services.task_approval import has_complete_approval, validation_is_current_decision


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(ORMModel):
    id: str
    username: str
    full_name: str
    role: str
    is_active: bool


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6)
    full_name: str = ""
    role: str = "teacher"


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=6)


class ChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class ModelEntryOut(ORMModel):
    id: str
    provider_id: str
    model_id: str
    display_name: str
    family: str
    supports_vision: bool
    supports_json: bool
    notes: str
    enabled: bool


class ModelEntryCreate(BaseModel):
    model_id: str
    display_name: str = ""
    family: str = "generic"
    supports_vision: bool = False
    supports_json: bool = True
    notes: str = ""


class ModelEntryUpdate(BaseModel):
    display_name: str | None = None
    family: str | None = None
    supports_vision: bool | None = None
    supports_json: bool | None = None
    notes: str | None = None
    enabled: bool | None = None


class ProviderOut(ORMModel):
    id: str
    name: str
    kind: str
    purpose: str
    base_url: str
    enabled: bool
    has_api_key: bool = False
    models: list[ModelEntryOut] = []


class ProviderCreate(BaseModel):
    name: str
    kind: str = "custom"
    purpose: str = Field(default="production", pattern="^(production|architect)$")
    base_url: str
    api_key: str = ""
    extra_headers: dict = {}


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    extra_headers: dict | None = None
    enabled: bool | None = None


class Criterion(BaseModel):
    name: str
    max_score: float = 1
    description: str = ""


class AssistantOut(ORMModel):
    id: str
    name: str
    discipline: str
    description: str
    audience: str
    language: str
    topics: list
    criteria: list
    nuances: list
    default_grader_model_id: str | None
    default_generator_model_id: str | None
    created_by: str = ""
    created_by_name: str = ""
    updated_by_name: str = ""
    created_at: datetime
    updated_at: datetime | None = None


class NuanceAdd(BaseModel):
    text: str = Field(min_length=1)


class CourseOut(ORMModel):
    id: str
    assistant_id: str
    name: str
    description: str
    term: str
    external_course_id: str
    published_version: str = ""
    published_at: datetime | None = None
    created_at: datetime


class CourseCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    term: str = ""
    external_course_id: str = ""


class CourseUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    term: str | None = None
    external_course_id: str | None = None


class AssistantCreate(BaseModel):
    name: str
    discipline: str
    description: str = ""
    audience: str = "студенты вуза"
    language: str = "ru"
    topics: list[str] = []
    criteria: list[Criterion] = []
    nuances: list[str] = []


class AssistantUpdate(BaseModel):
    name: str | None = None
    discipline: str | None = None
    description: str | None = None
    audience: str | None = None
    language: str | None = None
    topics: list[str] | None = None
    criteria: list[Criterion] | None = None
    nuances: list[str] | None = None
    default_grader_model_id: str | None = None
    default_generator_model_id: str | None = None


class PromptVersionOut(ORMModel):
    id: str
    assistant_id: str
    role: str
    version: int
    system_prompt: str
    notes: str
    source: str
    target_family: str
    architect_model: str
    status: str
    created_at: datetime


class PromptVersionCreate(BaseModel):
    role: str = Field(pattern="^(grader|generator|tutor)$")
    system_prompt: str
    notes: str = ""
    target_family: str = "generic"


class PromptGenerateRequest(BaseModel):
    role: str = Field(pattern="^(grader|generator|tutor)$")
    target_model_entry_id: str
    extra_instructions: str = ""


class ExampleTask(BaseModel):
    statement: str
    solution: str = ""
    answer: str = ""


class TaskTemplateOut(ORMModel):
    id: str
    assistant_id: str
    name: str
    topic: str
    difficulty: str
    instructions: str
    example: str
    task_kind: str
    answer_format: str
    numeric_tolerance_pct: float
    reference_sheet_ids: list
    example_tasks: list
    kb_query: str
    validation_solver: bool
    validation_data_check: bool


class TaskTemplateCreate(BaseModel):
    name: str
    topic: str = ""
    difficulty: str = "medium"
    instructions: str = ""
    example: str = ""
    task_kind: str = Field(default="calculation", pattern="^(calculation|conceptual|test_tf|test_mc|derivation)$")
    answer_format: str = Field(default="numeric", pattern="^(numeric|formula|text|choice)$")
    numeric_tolerance_pct: float = Field(default=2.0, ge=0, le=50)
    reference_sheet_ids: list[str] = []
    example_tasks: list[ExampleTask] = []
    kb_query: str = ""
    validation_solver: bool = True
    validation_data_check: bool = True


class TaskTemplateUpdate(BaseModel):
    name: str | None = None
    topic: str | None = None
    difficulty: str | None = None
    instructions: str | None = None
    example: str | None = None
    task_kind: str | None = Field(default=None, pattern="^(calculation|conceptual|test_tf|test_mc|derivation)$")
    answer_format: str | None = Field(default=None, pattern="^(numeric|formula|text|choice)$")
    numeric_tolerance_pct: float | None = Field(default=None, ge=0, le=50)
    reference_sheet_ids: list[str] | None = None
    example_tasks: list[ExampleTask] | None = None
    kb_query: str | None = None
    validation_solver: bool | None = None
    validation_data_check: bool | None = None


class GeneratedTaskOut(ORMModel):
    id: str
    assistant_id: str
    template_id: str | None
    batch_id: str | None
    statement: str
    reference_solution: str
    answer: str
    rubric: list
    max_score: float
    difficulty: str
    topic: str
    model_used: str
    status: str
    validation: dict
    grounding: dict
    approved: bool
    created_at: datetime

    @computed_field
    @property
    def approval_ready(self) -> bool:
        return has_complete_approval(self.validation)

    @computed_field
    @property
    def validation_ready(self) -> bool:
        return validation_is_current_decision(self.validation)

    @computed_field
    @property
    def export_ready(self) -> bool:
        return self.status == "approved" and self.approved and has_complete_approval(self.validation)


class GeneratedTaskUpdate(BaseModel):
    statement: str | None = None
    reference_solution: str | None = None
    answer: str | None = None
    rubric: list | None = None
    max_score: float | None = None
    status: str | None = Field(default=None, pattern="^(draft|validated|needs_review|approved|rejected)$")
    approved: bool | None = None
    approval_reason: str | None = Field(default=None, max_length=500)


class TaskGenerateRequest(BaseModel):
    template_id: str | None = None
    model_entry_id: str
    prompt_version_id: str | None = None
    topic: str = ""
    difficulty: str = ""
    count: int = Field(default=3, ge=1, le=10)
    instructions: str = ""
    temperature: float = 0.7


class GenerationBatchRequest(BaseModel):
    template_id: str | None = None
    model_entry_id: str
    solver_model_entry_id: str | None = None
    prompt_version_id: str | None = None
    topic: str = ""
    difficulty: str = ""
    count: int = Field(default=5, ge=1, le=20)
    instructions: str = ""
    temperature: float = 0.7
    validate_tasks: bool = True


class GenerationBatchOut(ORMModel):
    id: str
    assistant_id: str
    template_id: str | None
    status: str
    params: dict
    model_used: str
    requested_count: int
    generated_count: int
    validated_count: int
    progress: dict
    error: str
    created_at: datetime
    finished_at: datetime | None


class RevalidateRequest(BaseModel):
    solver_model_entry_id: str | None = None


class TaskExportRequest(BaseModel):
    task_ids: list[str] = []
    mode: str = Field(default="bank", pattern="^(bank|variants)$")
    source_code: str = "studio"
    source_title: str = ""
    version: str = "1.0"


class KnowledgeDocumentOut(ORMModel):
    id: str
    assistant_id: str
    title: str
    doc_type: str
    authority: str = "reference"
    visibility: str = "student"
    course_scope: str = ""
    effective_version: str = ""
    original_filename: str
    mime_type: str
    size_bytes: int
    status: str
    page_count: int
    extract_method: str = ""
    analysis_status: str = "none"
    analysis_error: str = ""
    error: str
    created_at: datetime
    chunk_count: int = 0


class KnowledgeDocumentDetailOut(KnowledgeDocumentOut):
    markdown: str = ""


class KnowledgeChunkOut(ORMModel):
    id: str
    document_id: str
    assistant_id: str
    ord: int
    heading: str
    content: str
    kind: str
    char_len: int


class SyllabusExtractRequest(BaseModel):
    document_id: str


class SyllabusExtractResponse(BaseModel):
    topics: list[str]


class AnalyzeSheetProposal(BaseModel):
    title: str
    kind: str
    description: str = ""
    content_markdown: str


class DocumentAnalysisResponse(BaseModel):
    summary: str = ""
    topics: list[str] = Field(default_factory=list)
    sheets: list[AnalyzeSheetProposal] = Field(default_factory=list)
    notation_notes: str = ""


class ReferenceSheetOut(ORMModel):
    id: str
    assistant_id: str
    title: str
    kind: str
    description: str
    content_markdown: str
    source_document_id: str | None
    visibility: str = "student"
    is_canonical: bool
    ord: int
    created_at: datetime
    updated_at: datetime


class ReferenceSheetCreate(BaseModel):
    title: str = Field(min_length=1)
    kind: str = Field(default="data_table", pattern="^(data_table|glossary|conventions|formulas|other)$")
    description: str = ""
    content_markdown: str = ""
    source_document_id: str | None = None
    visibility: str = Field(
        default="student", pattern="^(student|teacher_only|assessment_private|quarantine)$"
    )
    is_canonical: bool = True
    ord: int = 0


class ReferenceSheetUpdate(BaseModel):
    title: str | None = None
    kind: str | None = Field(default=None, pattern="^(data_table|glossary|conventions|formulas|other)$")
    description: str | None = None
    content_markdown: str | None = None
    visibility: str | None = Field(
        default=None, pattern="^(student|teacher_only|assessment_private|quarantine)$"
    )
    is_canonical: bool | None = None
    ord: int | None = None


class SheetFromChunksRequest(BaseModel):
    document_id: str
    chunk_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: str = Field(default="data_table", pattern="^(data_table|glossary|conventions|formulas|other)$")


class TutorMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class TutorChatRequest(BaseModel):
    run_id: str | None = None
    task_id: str | None = None
    prompt_version_id: str | None = None
    model_entry_id: str
    preview: bool = False
    student_work: str = ""
    messages: list[TutorMessage] = Field(min_length=1)


class TutorRunOut(ORMModel):
    id: str
    assistant_id: str
    task_id: str | None
    prompt_version_id: str | None
    provider_name: str
    model_id: str
    student_work: str
    messages: list
    rating: int | None
    comment: str
    created_at: datetime
    updated_at: datetime


class TutorChatResponse(BaseModel):
    run: TutorRunOut
    reply: str


class TutorFeedbackRequest(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = None


class PromptPreviewRequest(BaseModel):
    role: str = Field(pattern="^(grader|generator|tutor)$")
    prompt_version_id: str | None = None
    task_id: str | None = None
    template_id: str | None = None
    ocr_text: str = "(здесь будет OCR-расшифровка решения студента)"
    student_work: str = ""


class PromptPreviewResponse(BaseModel):
    system_prompt: str
    user_message: str


class PipelineOut(ORMModel):
    id: str
    assistant_id: str
    name: str
    description: str
    steps: list
    updated_at: datetime


class PipelineCreate(BaseModel):
    name: str
    description: str = ""
    steps: list = []


class PipelineUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    steps: list | None = None


class PipelineRunRequest(BaseModel):
    task_id: str | None = None
    task_text: str = ""
    reference_solution: str = ""
    rubric: list = []
    max_score: float = 10
    ocr_text: str = ""
    image_ids: list[str] = []


class PipelineRunOut(ORMModel):
    id: str
    pipeline_id: str
    status: str
    input: dict
    steps_log: list
    error: str
    started_at: datetime
    finished_at: datetime | None


class PlaygroundResultOut(ORMModel):
    id: str
    run_id: str
    provider_name: str
    model_id: str
    status: str
    output: dict | None
    raw_text: str
    error: str
    duration_ms: int
    tokens_total: int | None
    rating: int | None
    is_winner: bool
    feedback_comment: str


class PlaygroundRunOut(ORMModel):
    id: str
    assistant_id: str
    prompt_version_id: str | None
    task_text: str
    reference_solution: str
    rubric: list
    max_score: float
    ocr_text: str
    images: list
    created_at: datetime
    results: list[PlaygroundResultOut] = []


class CompareRequest(BaseModel):
    run_id: str | None = Field(default=None, min_length=32, max_length=32, pattern="^[0-9a-f]{32}$")
    assistant_id: str
    prompt_version_id: str | None = None
    task_id: str | None = None
    task_text: str = ""
    reference_solution: str = ""
    rubric: list = []
    max_score: float = 10
    ocr_text: str
    image_ids: list[str] = []
    model_entry_ids: list[str] = Field(min_length=1, max_length=6)
    temperature: float = 0.1
    include_reference: bool = True


class FeedbackRequest(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    is_winner: bool | None = None
    comment: str | None = None


class OcrResponse(BaseModel):
    ocr_text: str
    image_ids: list[str]


class ProviderTestResponse(BaseModel):
    ok: bool
    message: str
    duration_ms: int | None = None


class ProviderBalanceOut(BaseModel):
    supported: bool
    ok: bool
    balance: str = ""
    message: str = ""
