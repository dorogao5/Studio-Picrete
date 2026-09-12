import hashlib
import json
import secrets
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.api.assistants import get_assistant_or_404, resolve_model
from app.models import (
    Assistant,
    Course,
    GeneratedTask,
    GenerationBatch,
    ModelEntry,
    PlaygroundResult,
    PlaygroundRun,
    PromptVersion,
    Provider,
    ReferenceSheet,
    TaskTemplate,
    User,
)
from app.schemas import PublishReviewRequest
from app.security import get_current_user
from app.services.content_preflight import create_review_token, verify_review_token
from app.services.model_policy import current_model_use_policy
from app.services.export import build_bank_export
from app.services.model_policy import ModelUsePolicyError, require_decision_model
from app.services.physical_chemistry import (
    is_physical_chemistry, student_grading_model_use, task_verifier_model_id, physical_json_schema_enabled,
)
from app.services.taskgen import GenerationError, resolve_generator_prompt_version, run_batch
from app.services.task_approval import task_is_export_ready

router = APIRouter(tags=["integration"])


class StudentTrainerGenerationRequest(BaseModel):
    assistant_id: str = Field(min_length=1, max_length=64)
    topic: str = Field(min_length=1, max_length=256)
    difficulty: str = Field(default="easy", pattern="^(easy|medium|hard)$")
    count: int = Field(default=5, ge=1, le=10)


def _authenticate_picrete_generation(authorization: str) -> None:
    expected = get_settings().picrete_integration_token.strip()
    actual = authorization.removeprefix("Bearer ").strip()
    if not expected or not actual or not secrets.compare_digest(actual, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недействительный внутренний токен")


def _picrete_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().picrete_integration_token}"}


def _ensure_configured() -> None:
    settings = get_settings()
    if not settings.picrete_api_url or not settings.picrete_integration_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Связь с Picrete ещё не настроена администратором платформы.",
        )


@router.post("/internal/trainer/generate")
async def generate_student_trainer_tasks(
    body: StudentTrainerGenerationRequest,
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Generate one fresh physical-chemistry set for the student trainer.

    This is deliberately a narrow server-to-server endpoint. It reuses the
    canonical Studio generation batch, so physical chemistry still has exactly
    one Qwen generation call and one DeepSeek verification call per requested
    task; a failed verifier repairs that same candidate in place and never
    opens a replacement wave.
    """
    _authenticate_picrete_generation(authorization)
    assistant = await get_assistant_or_404(body.assistant_id, db)
    if not is_physical_chemistry(assistant):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Генерация через студенческий тренажёр включена только для физической химии",
        )

    topic = body.topic.strip()
    template = (
        await db.execute(
            select(TaskTemplate)
            .where(TaskTemplate.assistant_id == assistant.id, TaskTemplate.topic == topic,
                   TaskTemplate.difficulty == body.difficulty)
            .order_by(TaskTemplate.created_at, TaskTemplate.id)
        )
    ).scalars().first()
    if template is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Для выбранной подтемы и сложности нет канонического шаблона")

    generator_id = getattr(assistant, "default_generator_model_id", None)
    grader_id = task_verifier_model_id(assistant)
    if not generator_id or not grader_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Для ассистента не выбраны модели генерации и верификации задач")
    generator_provider, generator_model = await resolve_model(db, generator_id)
    _, grader_model = await resolve_model(db, grader_id)
    try:
        require_decision_model(generator_model, allow_advisory=True)
        require_decision_model(grader_model)
    except ModelUsePolicyError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err)) from err
    try:
        prompt_version = await resolve_generator_prompt_version(db, assistant.id, None)
    except GenerationError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err)) from err

    batch = GenerationBatch(
        assistant_id=assistant.id,
        template_id=template.id,
        status="running",
        params={
            "model_entry_id": generator_model.id,
            "solver_model_entry_id": grader_model.id,
            "topic": topic,
            "difficulty": body.difficulty,
            "count": body.count,
            "instructions": "",
            "temperature": 0.7,
            "validate_tasks": True,
            "prompt_version_id": prompt_version.id if prompt_version else None,
        },
        model_used=f"{generator_provider.name}/{generator_model.model_id}",
        requested_count=body.count,
        progress={"stage": "В очереди", "done": 0, "total": body.count},
        created_by="student-trainer",
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)

    await run_batch(batch.id)
    await db.refresh(batch)

    tasks = list(
        (
            await db.execute(
                select(GeneratedTask)
                .where(
                    GeneratedTask.batch_id == batch.id,
                    GeneratedTask.status == "validated",
                )
                .order_by(GeneratedTask.created_at)
            )
        ).scalars()
    )
    ready = [task for task in tasks if task_is_export_ready(task)]
    if not ready:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            batch.error[:500] if batch.error else "Независимая проверка не подготовила ни одной корректной задачи",
        )

    export = build_bank_export(
        ready,
        source_code="studio_fizicheskaya_himiya_dynamic",
        source_title="Физическая химия · задачи, сгенерированные для тренажёра",
        version=f"student-{batch.id}",
    )
    for paragraph in export["paragraphs"]:
        for position, task in enumerate(paragraph["tasks"], start=1):
            task["number"] = f"student-{batch.id[:12]}-{paragraph['paragraph']}-{position}"
    export["student_batch_id"] = batch.id
    # A terminal batch can contain both ready tasks and candidates needing
    # manual repair. Deliver existing evidence-backed tasks without a refill.
    export["requested_count"] = body.count
    export["ready_count"] = len(ready)
    export["pending_count"] = max(0, body.count - len(ready))
    return export


@router.get("/integration/picrete/courses")
async def list_picrete_courses(_: User = Depends(get_current_user)) -> list[dict]:
    _ensure_configured()
    settings = get_settings()
    url = f"{settings.picrete_api_url.rstrip('/')}/api/v1/internal/studio/course-options"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.get(url, headers=_picrete_headers())
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Не удалось получить курсы Picrete.") from exc
    if not response.is_success:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Picrete не вернул список курсов.")
    return response.json()


async def _course_or_404(db: AsyncSession, assistant_id: str, course_id: str) -> tuple[Assistant, Course]:
    assistant = (
        await db.execute(select(Assistant).where(Assistant.id == assistant_id))
    ).scalar_one_or_none()
    course = (
        await db.execute(
            select(Course).where(Course.id == course_id, Course.assistant_id == assistant_id)
        )
    ).scalar_one_or_none()
    if assistant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Дисциплина не найдена")
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Курс не найден")
    return assistant, course


async def _build_runtime_policy(db: AsyncSession, assistant: Assistant) -> dict:
    if not assistant.default_grader_model_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Перед публикацией выберите основную модель проверки ассистента.",
        )
    grader = await db.get(ModelEntry, assistant.default_grader_model_id)
    if grader is None or not grader.enabled:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Основная модель проверки не найдена или отключена.",
        )
    grader_use = (student_grading_model_use(assistant, grader) if is_physical_chemistry(assistant)
                  else current_model_use_policy().classify(grader))
    if not grader_use.decision_capable:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Модель {grader.model_id} нельзя опубликовать для проверки решений: {grader_use.reason}.",
        )
    generator_id = getattr(assistant, "default_generator_model_id", None) or assistant.default_grader_model_id
    generator = await db.get(ModelEntry, generator_id)
    if generator is None or not generator.enabled:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Модель генерации/разбора не найдена или отключена.",
        )
    generator_provider_id = getattr(generator, "provider_id", None)
    grader_provider_id = getattr(grader, "provider_id", None)
    generator_provider = await db.get(Provider, generator_provider_id) if generator_provider_id else None
    grader_provider = await db.get(Provider, grader_provider_id) if grader_provider_id else None
    if generator_provider_id and (generator_provider is None or not generator_provider.enabled):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Провайдер выбранной модели генерации отключён или не найден.",
        )
    if grader_provider_id and (grader_provider is None or not grader_provider.enabled):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Провайдер выбранной модели проверки отключён или не найден.",
        )
    runtime = {
        "policy_version": grader_use.policy_version,
        "tutor_model_id": generator.model_id,
        "tutor_provider_kind": getattr(generator_provider, "kind", ""),
        "decision_model_id": grader.model_id,
        "decision_provider_kind": getattr(grader_provider, "kind", ""),
        "tier": grader_use.tier,
        "allowed_uses": (["student_tutor", "grading"] if is_physical_chemistry(assistant)
                         else ["student_tutor", "task_validation", "grading"]),
    }
    if is_physical_chemistry(assistant):
        runtime["decision_supports_json_schema"] = physical_json_schema_enabled(assistant, grader_provider, grader)
    runtime["decision_tools_enabled"] = getattr(assistant, "decision_tools_enabled", False) is True
    runtime["tutor_tools_enabled"] = getattr(assistant, "tutor_tools_enabled", False) is True
    # Compatibility for lightweight callers/tests that provide model objects
    # without provider metadata. Real persisted model entries always carry it.
    if generator_provider is None or grader_provider is None:
        runtime.pop("tutor_provider_kind", None)
        runtime.pop("decision_provider_kind", None)
    return runtime


def _seal_snapshot(snapshot: dict) -> dict:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > 1_500_000:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Снимок ассистента больше 1,5 МБ. Оставьте каноническими только нужные справочники.",
        )
    return {
        **snapshot,
        "version": hashlib.sha256(encoded).hexdigest(),
        "published_at": datetime.now(UTC).isoformat(),
    }


async def _build_snapshot(db: AsyncSession, assistant: Assistant) -> dict:
    runtime_policy = await _build_runtime_policy(db, assistant)
    prompts = list(
        (
            await db.execute(
                select(PromptVersion).where(
                    PromptVersion.assistant_id == assistant.id,
                    PromptVersion.status == "active",
                )
            )
        ).scalars()
    )
    active_roles = [prompt.role for prompt in prompts]
    duplicate_roles = sorted({role for role in active_roles if active_roles.count(role) > 1})
    if duplicate_roles:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Найдено несколько активных промптов одной роли: " + ", ".join(duplicate_roles),
        )
    active_prompts = {
        prompt.role: {
            "id": prompt.id,
            "version": prompt.version,
            "system_prompt": prompt.system_prompt,
            "target_family": prompt.target_family,
        }
        for prompt in prompts
    }
    if "tutor" not in active_prompts:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Перед публикацией активируйте промпт режима «Разбор со студентом».",
        )

    grader_model_id = getattr(assistant, "default_grader_model_id", None)
    generator_model_id = getattr(assistant, "default_generator_model_id", None) or grader_model_id
    model_roles = {
        "tutor": (generator_model_id, "модели разбора"),
        "generator": (generator_model_id, "модели генерации"),
        "grader": (grader_model_id, "модели проверки"),
    }
    for role, (model_id, label) in model_roles.items():
        prompt = active_prompts.get(role)
        if prompt is None or not model_id:
            continue
        model = await db.get(ModelEntry, model_id)
        expected_family = str(getattr(model, "family", "") or "").strip().casefold()
        prompt_family = str(prompt.get("target_family") or "").strip().casefold()
        if expected_family and prompt_family and expected_family != prompt_family:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Активный промпт роли «{role}» рассчитан на {prompt_family}, "
                f"а выбранная {label} — на {expected_family}.",
            )

    if getattr(assistant, "grading_enabled", False) and "grader" not in active_prompts:
        raise HTTPException(422, "Проверка работ включена, но нет активного промпта «Проверка решений». Активируйте его перед публикацией.")

    sheets = list(
        (
            await db.execute(
                select(ReferenceSheet)
                .where(
                    ReferenceSheet.assistant_id == assistant.id,
                    ReferenceSheet.is_canonical.is_(True),
                    ReferenceSheet.visibility == "student",
                )
                .order_by(ReferenceSheet.ord, ReferenceSheet.created_at)
            )
        ).scalars()
    )
    empty_sheets = [sheet.title for sheet in sheets if not sheet.content_markdown.strip()]
    if empty_sheets:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Пустые студенческие справочники нельзя публиковать: " + ", ".join(empty_sheets[:5]),
        )
    snapshot = {
        "schema_version": 1,
        "assistant": {
            "id": assistant.id,
            "name": assistant.name,
            "discipline": assistant.discipline,
            "description": assistant.description,
            "audience": assistant.audience,
            "language": assistant.language,
            "topics": assistant.topics or [],
            "criteria": assistant.criteria or [],
            "nuances": assistant.nuances or [],
            "runtime_policy": runtime_policy,
            "grading_enabled": bool(getattr(assistant, "grading_enabled", False)) and "grader" in active_prompts,
        },
        "prompts": active_prompts,
        "reference_sheets": [
            {
                "id": sheet.id,
                "title": sheet.title,
                "kind": sheet.kind,
                "description": sheet.description,
                "content_markdown": sheet.content_markdown,
            }
            for sheet in sheets
        ],
    }
    return _seal_snapshot(snapshot)


def _publication_scope(assistant_id: str, course_id: str) -> str:
    return f"course-publish:{assistant_id}:{course_id}"


async def _build_publication_preflight(db: AsyncSession, assistant: Assistant, course: Course) -> dict:
    blockers: list[dict] = []
    warnings: list[dict] = []
    external_course_id = course.external_course_id.strip()
    if not external_course_id:
        blockers.append(
            {
                "severity": "blocker",
                "code": "course_unbound",
                "title": "Курс не привязан к Picrete",
                "message": "Выберите целевой курс до review.",
                "field": "external_course_id",
            }
        )
    settings = get_settings()
    if not settings.picrete_api_url or not settings.picrete_integration_token:
        blockers.append(
            {
                "severity": "blocker",
                "code": "integration_unconfigured",
                "title": "Связь с Picrete не настроена",
                "message": "Администратор должен задать URL API и integration token.",
                "field": "integration",
            }
        )

    snapshot = None
    try:
        snapshot = await _build_snapshot(db, assistant)
    except HTTPException as exc:
        blockers.append(
            {
                "severity": "blocker",
                "code": "snapshot_incomplete",
                "title": "Снимок ассистента не готов",
                "message": str(exc.detail),
                "field": "assistant",
            }
        )

    if not assistant.description.strip():
        warnings.append(
            {
                "severity": "warning",
                "code": "description_empty",
                "title": "Нет описания для студента",
                "message": "В карточке курса будет непонятно, с чем помогает ассистент.",
                "field": "description",
            }
        )
    if not (assistant.topics or []):
        warnings.append(
            {
                "severity": "warning",
                "code": "topics_empty",
                "title": "Не перечислены темы",
                "message": "Проверьте границы предметной области ассистента.",
                "field": "topics",
            }
        )
    if not (assistant.criteria or []):
        warnings.append(
            {
                "severity": "warning",
                "code": "criteria_empty",
                "title": "Нет критериев оценивания",
                "message": "Режим разбора доступен, но проверка работ не имеет явной шкалы.",
                "field": "criteria",
            }
        )

    if snapshot is not None and snapshot["assistant"].get("grading_enabled"):
        reviewed = list((await db.execute(
            select(PlaygroundResult).join(PlaygroundRun).where(
                PlaygroundRun.assistant_id == assistant.id,
                PlaygroundResult.status == "completed",
                PlaygroundResult.rating >= 4,
            ).order_by(PlaygroundRun.created_at.desc()).limit(100)
        )).scalars())
        tested = any(
            (r.output or {}).get("_studio", {}).get("snapshot_version") == snapshot["version"]
            and (r.output or {}).get("_studio", {}).get("course_id") == external_course_id
            for r in reviewed
        )
        if not tested:
            blockers.append({"severity": "blocker", "code": "grading_not_reviewed",
                             "title": "Проверьте текущую версию на задаче из банка курса",
                             "message": "Playground → Банк курса → Черновик: проверьте ответ и отметьте «Проверка корректна». После изменения настроек повторите прогон.",
                             "field": "grading"})

    digest = ""
    token = ""
    preview: dict = {}
    if snapshot is not None:
        digest = hashlib.sha256(f"{external_course_id}:{snapshot['version']}".encode()).hexdigest()
        token = create_review_token(
            scope=_publication_scope(assistant.id, course.id),
            digest=digest,
            secret_key=get_settings().secret_key,
        )
        preview = {
            "assistant_name": assistant.name,
            "discipline": assistant.discipline,
            "description": assistant.description,
            "audience": assistant.audience,
            "topics": list(assistant.topics or []),
            "reference_sheets": [sheet["title"] for sheet in snapshot["reference_sheets"]],
            "tutor_prompt_version": snapshot["prompts"]["tutor"]["version"],
            "model_id": snapshot["assistant"]["runtime_policy"]["tutor_model_id"],
            "target_course_id": external_course_id,
        }
    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "review_token": token,
        "digest": digest,
        "preview": preview,
        "snapshot": snapshot,
    }


@router.post("/assistants/{assistant_id}/courses/{course_id}/publish/preflight")
async def preflight_course_assistant(
    assistant_id: str,
    course_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    assistant, course = await _course_or_404(db, assistant_id, course_id)
    result = await _build_publication_preflight(db, assistant, course)
    result.pop("snapshot", None)
    return result


@router.post("/assistants/{assistant_id}/courses/{course_id}/publish")
async def publish_course_assistant(
    assistant_id: str,
    course_id: str,
    body: PublishReviewRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    settings = get_settings()
    _ensure_configured()
    assistant, course = await _course_or_404(db, assistant_id, course_id)
    preflight = await _build_publication_preflight(db, assistant, course)
    if preflight["blockers"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Публикация остановлена: " + preflight["blockers"][0]["message"],
        )
    if preflight["warnings"] and not body.acknowledge_warnings:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Перед публикацией подтвердите предупреждения preflight.",
        )
    if not verify_review_token(
        body.review_token,
        scope=_publication_scope(assistant.id, course.id),
        digest=preflight["digest"],
        secret_key=get_settings().secret_key,
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Снимок изменился или review устарел. Просмотрите student preview ещё раз.",
        )

    external_course_id = course.external_course_id.strip()
    snapshot = preflight["snapshot"]
    url = (
        f"{settings.picrete_api_url.rstrip('/')}/api/v1/internal/studio/"
        f"course-assistants/{external_course_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            response = await client.put(
                url,
                headers=_picrete_headers(),
                json=snapshot,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Picrete временно недоступен. Снимок не опубликован — повторите попытку.",
        ) from exc

    if not response.is_success:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail or f"Picrete отклонил публикацию (HTTP {response.status_code}).",
        )
    result = response.json()
    course.published_version = snapshot["version"]
    course.published_at = datetime.now(UTC)
    await db.commit()
    return {
        "ok": True,
        "version": snapshot["version"],
        "published_at": result.get("synced_at", course.published_at.isoformat()),
        "assistant_name": assistant.name,
        "course_id": external_course_id,
    }


class BankPreviewRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    student_text: str = Field(min_length=1, max_length=30000)
    mode: str = "draft"


class TaskBankImportRequest(BaseModel):
    source_code: str = Field(default="studio_fizicheskaya_himiya", min_length=1, max_length=128)
    source_title: str = Field(default="Физическая химия", min_length=1, max_length=256)
    version: str = Field(default="1.0", min_length=1, max_length=64)


@router.post("/assistants/{assistant_id}/courses/{course_id}/task-bank/import")
async def import_course_task_bank(
    assistant_id: str,
    course_id: str,
    body: TaskBankImportRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Push the already reviewed Studio task bank into the bound Picrete course."""

    _, course = await _course_or_404(db, assistant_id, course_id)
    tasks = list(
        (
            await db.execute(
                select(GeneratedTask)
                .where(
                    GeneratedTask.assistant_id == assistant_id,
                    GeneratedTask.status.in_(("validated", "approved")),
                )
                .order_by(GeneratedTask.created_at)
            )
        ).scalars()
    )
    not_ready = [task.id for task in tasks if not task_is_export_ready(task)]
    if not tasks:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нет готовых задач для импорта в Picrete")
    if not_ready:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Импорт остановлен: {len(not_ready)} задач не прошли ручную или автоматическую проверку",
        )
    payload = build_bank_export(
        tasks,
        source_code=body.source_code.strip(),
        source_title=body.source_title.strip(),
        version=body.version.strip(),
    )
    return await _picrete_request("POST", course, "task-bank/import", json=payload)


async def _picrete_request(method: str, course: Course, path: str, **kwargs) -> dict:
    _ensure_configured()
    if not course.external_course_id.strip():
        raise HTTPException(422, "Сначала привяжите курс к Picrete во вкладке «Курсы»")
    url = f"{get_settings().picrete_api_url.rstrip('/')}/api/v1/internal/studio/courses/{course.external_course_id}/{path}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
            response = await client.request(method, url, headers=_picrete_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Picrete не завершил запрос. Повторите попытку.") from exc
    if not response.is_success:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise HTTPException(502, detail or f"Picrete вернул HTTP {response.status_code}")
    return response.json()


@router.get("/assistants/{assistant_id}/courses/{course_id}/task-bank")
async def course_task_bank(assistant_id: str, course_id: str, q: str = "", skip: int = 0,
                          db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    _, course = await _course_or_404(db, assistant_id, course_id)
    return await _picrete_request("GET", course, "task-bank", params={"q": q[:200], "skip": max(0, skip)})


@router.post("/assistants/{assistant_id}/courses/{course_id}/grading-preview")
async def course_grading_preview(assistant_id: str, course_id: str, body: BankPreviewRequest,
                                 db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    assistant, course = await _course_or_404(db, assistant_id, course_id)
    if body.mode not in ("draft", "published"):
        raise HTTPException(422, "Выберите черновик или опубликованную версию")
    snapshot = await _build_snapshot(db, assistant) if body.mode == "draft" else None
    if snapshot and not snapshot["assistant"]["grading_enabled"]:
        raise HTTPException(422, "Создайте и активируйте промпт «Проверка решений»")
    result = await _picrete_request("POST", course, "grading-preview", json={
        "task_id": body.task_id, "student_text": body.student_text, "snapshot": snapshot,
    })
    output = result["output"]
    metadata = output.get("_metadata", {})
    # Store the exact runtime snapshot/provenance, independent of later profile edits.
    output["_studio"] = {"course_id": course.external_course_id, "task_id": result["task_id"],
                         "task_number": result["task_number"], "mode": body.mode,
                         "snapshot_version": result["snapshot_version"], "snapshot": snapshot}
    run = PlaygroundRun(assistant_id=assistant.id, prompt_version_id=snapshot["prompts"]["grader"]["id"] if snapshot else None,
                        task_text=result["task_text"], reference_solution=result["reference_solution"],
                        rubric=result["rubric"], max_score=result["max_score"], ocr_text=body.student_text,
                        images=[], created_by=user.id)
    db.add(run)
    await db.flush()
    row = PlaygroundResult(run_id=run.id, provider_name="Picrete · production engine",
                           model_id=metadata.get("model", ""), status="completed", output=output,
                           duration_ms=int(metadata.get("duration_seconds", 0)*1000),
                           tokens_total=metadata.get("tokens_used"))
    db.add(row)
    await db.commit()
    result["run_id"] = run.id
    result["result_id"] = row.id
    return result


@router.get("/assistants/{assistant_id}/courses/{course_id}/task-bank/{item_id}/images/{image_id}")
async def course_bank_image(assistant_id: str, course_id: str, item_id: str, image_id: str,
                            db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    _, course = await _course_or_404(db, assistant_id, course_id)
    _ensure_configured()
    if not course.external_course_id:
        raise HTTPException(422, "Курс не привязан")
    url = f"{get_settings().picrete_api_url.rstrip('/')}/api/v1/internal/studio/courses/{course.external_course_id}/task-bank/{item_id}/images/{image_id}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=_picrete_headers())
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Изображение недоступно") from exc
    if not r.is_success:
        raise HTTPException(404, "Изображение не найдено")
    return Response(r.content, media_type=r.headers.get("content-type", "image/png"), headers={"Cache-Control":"private, no-store"})
