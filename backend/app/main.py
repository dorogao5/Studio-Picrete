from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import assistants, auth, integration, kb, pipelines, playground, preview, providers, tasks, tutor
from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import Base, ModelEntry, Provider, User
from app.security import encrypt_secret, hash_password


async def bootstrap_admin() -> None:
    settings = get_settings()
    async with SessionLocal() as db:
        exists = (
            await db.execute(select(User).where(User.username == settings.first_admin_username))
        ).scalar_one_or_none()
        if exists is None:
            db.add(
                User(
                    username=settings.first_admin_username,
                    password_hash=hash_password(settings.first_admin_password),
                    full_name="Администратор",
                    role="admin",
                )
            )
            await db.commit()


async def seed_architect() -> None:
    """Настраивает фоновую модель-архитектор из env. Преподаватели её не видят."""
    settings = get_settings()
    if not (settings.architect_base_url and settings.architect_api_key):
        return
    async with SessionLocal() as db:
        provider = (
            await db.execute(select(Provider).where(Provider.purpose == "architect"))
        ).scalars().first()
        if provider is None:
            provider = Provider(
                name="Архитектор промптов",
                kind="openai",
                purpose="architect",
                base_url=settings.architect_base_url.rstrip("/"),
                api_key_encrypted=encrypt_secret(settings.architect_api_key),
                enabled=True,
            )
            db.add(provider)
            await db.flush()
        else:
            provider.base_url = settings.architect_base_url.rstrip("/")
            provider.api_key_encrypted = encrypt_secret(settings.architect_api_key)
            provider.enabled = True
        model = (
            await db.execute(select(ModelEntry).where(ModelEntry.provider_id == provider.id))
        ).scalars().first()
        if model is None:
            db.add(
                ModelEntry(
                    provider_id=provider.id,
                    model_id=settings.architect_model,
                    display_name=settings.architect_model,
                    family=settings.architect_family,
                    supports_vision=True,
                    supports_json=True,
                )
            )
        else:
            model.model_id = settings.architect_model
            model.family = settings.architect_family
        await db.commit()


SQLITE_COLUMN_BACKFILL: dict[str, dict[str, str]] = {
    "courses": {
        "published_version": "VARCHAR(64) DEFAULT ''",
        "published_at": "DATETIME",
    },
    "assistants": {
        "updated_by": "VARCHAR(32) DEFAULT ''",
        "updated_at": "DATETIME",
    },
    "task_templates": {
        "task_kind": "VARCHAR(16) DEFAULT 'calculation'",
        "answer_format": "VARCHAR(16) DEFAULT 'numeric'",
        "numeric_tolerance_pct": "FLOAT DEFAULT 2.0",
        "rubric": "JSON NOT NULL DEFAULT '[]'",
        "reference_sheet_ids": "JSON DEFAULT '[]'",
        "example_tasks": "JSON DEFAULT '[]'",
        "kb_query": "VARCHAR(512) DEFAULT ''",
        "validation_solver": "BOOLEAN DEFAULT 1",
        "validation_data_check": "BOOLEAN DEFAULT 1",
    },
    "generated_tasks": {
        "batch_id": "VARCHAR(32)",
        "answer": "TEXT DEFAULT ''",
        "status": "VARCHAR(16) DEFAULT 'draft'",
        "validation": "JSON DEFAULT '{}'",
        "grounding": "JSON DEFAULT '{}'",
    },
    "knowledge_documents": {
        "extract_method": "VARCHAR(16) DEFAULT ''",
        "analysis_status": "VARCHAR(16) DEFAULT 'none'",
        "analysis": "JSON DEFAULT '{}'",
        "analysis_error": "TEXT DEFAULT ''",
        "s3_key": "VARCHAR(1024) DEFAULT ''",
        "authority": "VARCHAR(32) DEFAULT 'reference'",
        "visibility": "VARCHAR(32) DEFAULT 'student'",
        "course_scope": "VARCHAR(128) DEFAULT ''",
        "effective_version": "VARCHAR(128) DEFAULT ''",
    },
    "reference_sheets": {
        "visibility": "VARCHAR(32) DEFAULT 'student'",
    },
}


async def ensure_sqlite_columns(conn) -> None:
    for table, wanted in SQLITE_COLUMN_BACKFILL.items():
        result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        columns = {row[1] for row in result}
        for column, ddl in wanted.items():
            if column not in columns:
                await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


async def ensure_postgres_columns(conn) -> None:
    await conn.exec_driver_sql(
        "ALTER TABLE task_templates ADD COLUMN IF NOT EXISTS rubric JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    await conn.exec_driver_sql(
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS published_version VARCHAR(64) DEFAULT ''"
    )
    await conn.exec_driver_sql(
        "ALTER TABLE courses ADD COLUMN IF NOT EXISTS published_at TIMESTAMP WITH TIME ZONE"
    )
    await conn.exec_driver_sql(
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS authority VARCHAR(32) DEFAULT 'reference'"
    )
    await conn.exec_driver_sql(
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS visibility VARCHAR(32) DEFAULT 'student'"
    )
    await conn.exec_driver_sql(
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS course_scope VARCHAR(128) DEFAULT ''"
    )
    await conn.exec_driver_sql(
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS effective_version VARCHAR(128) DEFAULT ''"
    )
    await conn.exec_driver_sql(
        "ALTER TABLE reference_sheets ADD COLUMN IF NOT EXISTS visibility VARCHAR(32) DEFAULT 'student'"
    )


async def reconcile_interrupted_work() -> None:
    """Фоновые задачи не переживают рестарт — зависшие статусы переводим в failed, чтобы UI не ждал вечно."""
    from datetime import UTC, datetime

    from sqlalchemy import update

    from app.models import GeneratedTask, GenerationBatch, KnowledgeDocument

    async with SessionLocal() as db:
        await db.execute(
            update(GenerationBatch)
            .where(GenerationBatch.status == "running")
            .values(status="failed", error="Прервано перезапуском сервера", finished_at=datetime.now(UTC))
        )
        await db.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.status.in_(["uploaded", "parsing"]))
            .values(status="failed", error="Прервано перезапуском сервера — нажмите «Переразобрать»")
        )
        await db.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.analysis_status == "running")
            .values(analysis_status="failed", analysis_error="Прервано перезапуском сервера — нажмите «Разобрать»")
        )
        await db.execute(
            update(GeneratedTask)
            .where(GeneratedTask.approved.is_(True), GeneratedTask.status == "draft")
            .values(status="approved")
        )
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.kb import ensure_fts

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "sqlite":
            await ensure_sqlite_columns(conn)
        if conn.dialect.name == "postgresql":
            await ensure_postgres_columns(conn)
            # pgvector — задел под эмбеддинг-поиск; без superuser может не взлететь, это не фатально.
            try:
                await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception:
                pass
        await ensure_fts(conn)
    await bootstrap_admin()
    await seed_architect()
    await reconcile_interrupted_work()
    yield


app = FastAPI(title="Picrete Studio API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


for router in (
    auth.router,
    providers.router,
    assistants.router,
    tasks.router,
    pipelines.router,
    playground.router,
    kb.router,
    tutor.router,
    preview.router,
    integration.router,
):
    app.include_router(router, prefix="/api")
