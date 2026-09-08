import hashlib
import json
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from fastapi.responses import Response
from sqlalchemy.orm import selectinload
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import Assistant, Course, ModelEntry, PromptVersion, ReferenceSheet, User, PlaygroundRun, PlaygroundResult
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
                             "title": "Проверьте текущую версию на задаче из банка",
                             "message": "Playground → Банк Picrete · Свиридов → Черновик: проверьте ответ и отметьте «Проверка корректна». После изменения настроек повторите прогон.",
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
