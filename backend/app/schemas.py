from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    role: str = Field(pattern="^(grader|generator)$")
    system_prompt: str
    notes: str = ""
    target_family: str = "generic"


class PromptGenerateRequest(BaseModel):
    role: str = Field(pattern="^(grader|generator)$")
    target_model_entry_id: str
    extra_instructions: str = ""


class TaskTemplateOut(ORMModel):
    id: str
    assistant_id: str
    name: str
    topic: str
    difficulty: str
    instructions: str
    example: str


class TaskTemplateCreate(BaseModel):
    name: str
    topic: str = ""
    difficulty: str = "medium"
    instructions: str = ""
    example: str = ""


class GeneratedTaskOut(ORMModel):
    id: str
    assistant_id: str
    template_id: str | None
    statement: str
    reference_solution: str
    rubric: list
    max_score: float
    difficulty: str
    topic: str
    model_used: str
    approved: bool
    created_at: datetime


class GeneratedTaskUpdate(BaseModel):
    statement: str | None = None
    reference_solution: str | None = None
    rubric: list | None = None
    max_score: float | None = None
    approved: bool | None = None


class TaskGenerateRequest(BaseModel):
    template_id: str | None = None
    model_entry_id: str
    prompt_version_id: str | None = None
    topic: str = ""
    difficulty: str = "medium"
    count: int = Field(default=3, ge=1, le=10)
    instructions: str = ""
    temperature: float = 0.7


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
