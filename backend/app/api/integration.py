import hashlib
import json
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import Assistant, Course, ModelEntry, PromptVersion, ReferenceSheet, User
from app.schemas import PublishReviewRequest
from app.security import get_current_user
from app.services.content_preflight import create_review_token, verify_review_token
from app.services.model_policy import current_model_use_policy

router = APIRouter(tags=["integration"])


def _picrete_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().picrete_integration_token}"}


def _ensure_configured() -> None:
    settings = get_settings()
    if not settings.picrete_api_url or not settings.picrete_integration_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Связь с Picrete ещё не настроена администратором платформы.",
        )


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
    model = await db.get(ModelEntry, assistant.default_grader_model_id)
    if model is None or not model.enabled:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Основная модель проверки не найдена или отключена.",
        )
    use = current_model_use_policy().classify(model)
    if not use.decision_capable:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Модель {model.model_id} нельзя опубликовать для работы со студентами: {use.reason}.",
        )
    return {
        "policy_version": use.policy_version,
        # Пока Studio не хранит отдельную tutor-модель: student-facing tutor использует
        # ту же decision-grade модель, что и итоговая проверка.
        "tutor_model_id": model.model_id,
        "decision_model_id": model.model_id,
        "tier": use.tier,
        "allowed_uses": ["student_tutor", "task_validation", "grading"],
    }


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
