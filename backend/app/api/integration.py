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
from app.security import get_current_user
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


@router.post("/assistants/{assistant_id}/courses/{course_id}/publish")
async def publish_course_assistant(
    assistant_id: str,
    course_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    settings = get_settings()
    _ensure_configured()
    assistant, course = await _course_or_404(db, assistant_id, course_id)
    external_course_id = course.external_course_id.strip()
    if not external_course_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Укажите ID курса в Picrete перед публикацией.",
        )

    snapshot = await _build_snapshot(db, assistant)
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
