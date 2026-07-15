import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default="teacher")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), default="custom")
    purpose: Mapped[str] = mapped_column(String(16), default="production")
    base_url: Mapped[str] = mapped_column(String(512))
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    extra_headers: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    models: Mapped[list["ModelEntry"]] = relationship(back_populates="provider", cascade="all, delete-orphan")


class ModelEntry(Base):
    __tablename__ = "model_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), index=True)
    model_id: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(256), default="")
    family: Mapped[str] = mapped_column(String(32), default="generic")
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_json: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    provider: Mapped[Provider] = relationship(back_populates="models")


class Assistant(Base):
    __tablename__ = "assistants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(256))
    discipline: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    audience: Mapped[str] = mapped_column(String(256), default="студенты вуза")
    language: Mapped[str] = mapped_column(String(16), default="ru")
    topics: Mapped[list] = mapped_column(JSON, default=list)
    criteria: Mapped[list] = mapped_column(JSON, default=list)
    nuances: Mapped[list] = mapped_column(JSON, default=list)
    default_grader_model_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    default_generator_model_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    updated_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assistant_id: Mapped[str] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    term: Mapped[str] = mapped_column(String(128), default="")
    external_course_id: Mapped[str] = mapped_column(String(64), default="")
    published_version: Mapped[str] = mapped_column(String(64), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assistant_id: Mapped[str] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer, default=1)
    system_prompt: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")
    target_family: Mapped[str] = mapped_column(String(32), default="generic")
    architect_model: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskTemplate(Base):
    __tablename__ = "task_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assistant_id: Mapped[str] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    topic: Mapped[str] = mapped_column(String(256), default="")
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
    instructions: Mapped[str] = mapped_column(Text, default="")
    example: Mapped[str] = mapped_column(Text, default="")
    task_kind: Mapped[str] = mapped_column(String(16), default="calculation")
    answer_format: Mapped[str] = mapped_column(String(16), default="numeric")
    numeric_tolerance_pct: Mapped[float] = mapped_column(Float, default=2.0)
    rubric: Mapped[list] = mapped_column(JSON, default=list)
    reference_sheet_ids: Mapped[list] = mapped_column(JSON, default=list)
    example_tasks: Mapped[list] = mapped_column(JSON, default=list)
    kb_query: Mapped[str] = mapped_column(String(512), default="")
    validation_solver: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_data_check: Mapped[bool] = mapped_column(Boolean, default=True)
    chemistry_check: Mapped[str] = mapped_column(String(64), default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GeneratedTask(Base):
    __tablename__ = "generated_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assistant_id: Mapped[str] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    statement: Mapped[str] = mapped_column(Text)
    reference_solution: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    rubric: Mapped[list] = mapped_column(JSON, default=list)
    max_score: Mapped[float] = mapped_column(Float, default=10.0)
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
    topic: Mapped[str] = mapped_column(String(256), default="")
    model_used: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    grounding: Mapped[dict] = mapped_column(JSON, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assistant_id: Mapped[str] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    doc_type: Mapped[str] = mapped_column(String(32), default="other")
    authority: Mapped[str] = mapped_column(String(32), default="reference")
    visibility: Mapped[str] = mapped_column(String(32), default="student")
    course_scope: Mapped[str] = mapped_column(String(128), default="")
    effective_version: Mapped[str] = mapped_column(String(128), default="")
    original_filename: Mapped[str] = mapped_column(String(512), default="")
    file_path: Mapped[str] = mapped_column(String(1024), default="")
    # Оригинал выгружается в S3 после парсинга; локальный файл удаляется.
    s3_key: Mapped[str] = mapped_column(String(1024), default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    markdown: Mapped[str] = mapped_column(Text, default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    # text = извлечён текстовый слой (дёшево), ocr = распознан DataLab (дорого, fallback)
    extract_method: Mapped[str] = mapped_column(String(16), default="")
    # Авто-разбор архитектором после парсинга: none/running/ready/failed + результат
    analysis_status: Mapped[str] = mapped_column(String(16), default="none")
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    analysis_error: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    assistant_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    ord: Mapped[int] = mapped_column(Integer, default=0)
    heading: Mapped[str] = mapped_column(String(512), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="text")
    char_len: Mapped[int] = mapped_column(Integer, default=0)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class ReferenceSheet(Base):
    __tablename__ = "reference_sheets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assistant_id: Mapped[str] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(32), default="data_table")
    description: Mapped[str] = mapped_column(Text, default="")
    content_markdown: Mapped[str] = mapped_column(Text, default="")
    source_document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    visibility: Mapped[str] = mapped_column(String(32), default="student")
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=True)
    ord: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GenerationBatch(Base):
    __tablename__ = "generation_batches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assistant_id: Mapped[str] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    model_used: Mapped[str] = mapped_column(String(256), default="")
    requested_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_count: Mapped[int] = mapped_column(Integer, default=0)
    validated_count: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TutorRun(Base):
    __tablename__ = "tutor_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assistant_id: Mapped[str] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_name: Mapped[str] = mapped_column(String(128), default="")
    model_id: Mapped[str] = mapped_column(String(256), default="")
    student_work: Mapped[str] = mapped_column(Text, default="")
    messages: Mapped[list] = mapped_column(JSON, default=list)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assistant_id: Mapped[str] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipelines.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    steps_log: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(32), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlaygroundRun(Base):
    __tablename__ = "playground_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assistant_id: Mapped[str] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"), index=True)
    prompt_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_text: Mapped[str] = mapped_column(Text, default="")
    reference_solution: Mapped[str] = mapped_column(Text, default="")
    rubric: Mapped[list] = mapped_column(JSON, default=list)
    max_score: Mapped[float] = mapped_column(Float, default=10.0)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    images: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    results: Mapped[list["PlaygroundResult"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class PlaygroundResult(Base):
    __tablename__ = "playground_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("playground_runs.id", ondelete="CASCADE"), index=True)
    provider_name: Mapped[str] = mapped_column(String(128), default="")
    model_id: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="completed")
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_winner: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback_comment: Mapped[str] = mapped_column(Text, default="")

    run: Mapped[PlaygroundRun] = relationship(back_populates="results")
