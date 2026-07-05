import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.assistants import get_assistant_or_404, resolve_model
from app.config import get_settings
from app.db import get_db
from app.models import GeneratedTask, PlaygroundResult, PlaygroundRun, PromptVersion, User
from app.schemas import CompareRequest, FeedbackRequest, OcrResponse, PlaygroundResultOut, PlaygroundRunOut
from app.security import get_current_user
from app.services import grading, ocr
from app.services.grounding import build_grounding_block

router = APIRouter(prefix="/playground", tags=["playground"])

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


@router.post("/ocr", response_model=OcrResponse)
async def run_ocr(
    files: list[UploadFile] = File(...), _: User = Depends(get_current_user)
) -> OcrResponse:
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Загрузите хотя бы один файл")
    uploads_dir = get_settings().uploads_dir
    pages: list[str] = []
    image_ids: list[str] = []
    for file in files:
        mime = file.content_type or "image/jpeg"
        if mime not in ALLOWED_MIME:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Неподдерживаемый тип файла: {mime}")
        content = await file.read()
        if len(content) > 15 * 1024 * 1024:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Файл больше 15 МБ")
        suffix = Path(file.filename or "page.jpg").suffix or ".jpg"
        image_id = f"{uuid.uuid4().hex}{suffix}"
        (uploads_dir / image_id).write_bytes(content)
        image_ids.append(image_id)
        try:
            markdown = await ocr.run_datalab_ocr(file.filename or image_id, content, mime)
        except ocr.OcrError as err:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err))
        pages.append(markdown)
    return OcrResponse(ocr_text="\n\n---\n\n".join(pages), image_ids=image_ids)


@router.post("/compare", response_model=PlaygroundRunOut)
async def compare(
    body: CompareRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> PlaygroundRun:
    assistant = await get_assistant_or_404(body.assistant_id, db)

    task_text, reference_solution, rubric, max_score = (
        body.task_text,
        body.reference_solution,
        body.rubric,
        body.max_score,
    )
    if body.task_id:
        task = (
            await db.execute(
                select(GeneratedTask).where(
                    GeneratedTask.id == body.task_id, GeneratedTask.assistant_id == assistant.id
                )
            )
        ).scalar_one_or_none()
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
        task_text, reference_solution, rubric, max_score = (
            task.statement,
            task.reference_solution,
            task.rubric,
            task.max_score,
        )
    if not task_text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Не задано условие задачи")
    if not body.ocr_text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нет текста решения (OCR)")

    if body.prompt_version_id:
        prompt = (
            await db.execute(
                select(PromptVersion).where(
                    PromptVersion.id == body.prompt_version_id, PromptVersion.assistant_id == assistant.id
                )
            )
        ).scalar_one_or_none()
        if prompt is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Версия промпта не найдена")
    else:
        prompt = (
            await db.execute(
                select(PromptVersion)
                .where(
                    PromptVersion.assistant_id == assistant.id,
                    PromptVersion.role == "grader",
                    PromptVersion.status == "active",
                )
                .order_by(PromptVersion.version.desc())
            )
        ).scalars().first()
        if prompt is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "У ассистента нет активного промпта проверки — создайте и активируйте его",
            )

    resolved = []
    for model_entry_id in body.model_entry_ids:
        resolved.append(await resolve_model(db, model_entry_id))

    grounding = ""
    if body.include_reference:
        grounding = await build_grounding_block(db, assistant.id, query=task_text[:200] or body.ocr_text[:200])

    run = PlaygroundRun(
        assistant_id=assistant.id,
        prompt_version_id=prompt.id,
        task_text=task_text,
        reference_solution=reference_solution,
        rubric=rubric,
        max_score=max_score,
        ocr_text=body.ocr_text,
        images=body.image_ids,
        created_by=user.id,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    async def grade_one(provider, model):
        return await grading.run_grading(
            provider,
            model,
            prompt.system_prompt,
            task_text,
            reference_solution,
            rubric,
            max_score,
            body.ocr_text,
            grounding=grounding,
            temperature=body.temperature,
        )

    outcomes = await asyncio.gather(*(grade_one(p, m) for p, m in resolved))

    for (provider, model), outcome in zip(resolved, outcomes):
        db.add(
            PlaygroundResult(
                run_id=run.id,
                provider_name=provider.name,
                model_id=model.model_id,
                status="failed" if outcome.error else "completed",
                output=outcome.output,
                raw_text=outcome.raw_text[:20000],
                error=outcome.error,
                duration_ms=outcome.duration_ms,
                tokens_total=outcome.tokens_total,
            )
        )
    await db.commit()

    return (
        await db.execute(
            select(PlaygroundRun).options(selectinload(PlaygroundRun.results)).where(PlaygroundRun.id == run.id)
        )
    ).scalar_one()


@router.get("/runs", response_model=list[PlaygroundRunOut])
async def list_runs(
    assistant_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> list[PlaygroundRun]:
    return list(
        (
            await db.execute(
                select(PlaygroundRun)
                .options(selectinload(PlaygroundRun.results))
                .where(PlaygroundRun.assistant_id == assistant_id)
                .order_by(PlaygroundRun.created_at.desc())
                .limit(30)
            )
        ).scalars()
    )


@router.post("/results/{result_id}/feedback", response_model=PlaygroundResultOut)
async def leave_feedback(
    result_id: str, body: FeedbackRequest, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> PlaygroundResult:
    result = (
        await db.execute(select(PlaygroundResult).where(PlaygroundResult.id == result_id))
    ).scalar_one_or_none()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Результат не найден")
    if body.rating is not None:
        result.rating = body.rating
    if body.comment is not None:
        result.feedback_comment = body.comment
    if body.is_winner is not None:
        result.is_winner = body.is_winner
        if body.is_winner:
            siblings = (
                await db.execute(
                    select(PlaygroundResult).where(
                        PlaygroundResult.run_id == result.run_id, PlaygroundResult.id != result.id
                    )
                )
            ).scalars()
            for sibling in siblings:
                sibling.is_winner = False
    await db.commit()
    await db.refresh(result)
    return result
