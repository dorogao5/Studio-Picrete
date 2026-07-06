from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.assistants import get_assistant_or_404, resolve_model
from app.db import get_db
from app.llm import client as llm
from app.models import Assistant, GeneratedTask, PromptVersion, TutorRun, User
from app.schemas import TutorChatRequest, TutorChatResponse, TutorFeedbackRequest, TutorRunOut
from app.security import get_current_user
from app.services.grounding import build_grounding_block
from app.services.tutor import FALLBACK_TUTOR_PROMPT, build_tutor_context, flatten_dialog, run_tutor_reply

router = APIRouter(tags=["tutor"])


async def _resolve_tutor_prompt(
    db: AsyncSession, assistant: Assistant, prompt_version_id: str | None
) -> PromptVersion | None:
    if prompt_version_id:
        prompt = (
            await db.execute(
                select(PromptVersion).where(
                    PromptVersion.id == prompt_version_id, PromptVersion.assistant_id == assistant.id
                )
            )
        ).scalar_one_or_none()
        if prompt is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Версия промпта не найдена")
        if prompt.role != "tutor":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Выбранная версия промпта не для роли «Разбор»")
        return prompt
    return (
        await db.execute(
            select(PromptVersion)
            .where(
                PromptVersion.assistant_id == assistant.id,
                PromptVersion.role == "tutor",
                PromptVersion.status == "active",
            )
            .order_by(PromptVersion.version.desc())
        )
    ).scalars().first()


@router.post("/assistants/{assistant_id}/tutor/chat", response_model=TutorChatResponse)
async def tutor_chat(
    assistant_id: str,
    body: TutorChatRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TutorChatResponse:
    assistant = await get_assistant_or_404(assistant_id, db)
    if body.messages[-1].role != "user":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Последнее сообщение должно быть от студента")
    provider, model = await resolve_model(db, body.model_entry_id)
    prompt = await _resolve_tutor_prompt(db, assistant, body.prompt_version_id)
    system_prompt = prompt.system_prompt if prompt else FALLBACK_TUTOR_PROMPT.format(discipline=assistant.discipline)

    task = None
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

    query_source = (
        task.statement
        if task
        else " ".join(part for part in (body.student_work, body.messages[-1].content) if part)
    )
    query = query_source[:200]
    grounding = await build_grounding_block(db, assistant.id, query=query)
    context = build_tutor_context(task, body.student_work, grounding)
    user_message = flatten_dialog([m.model_dump() for m in body.messages], context)

    try:
        result = await run_tutor_reply(provider, model, system_prompt, user_message)
    except llm.LlmError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err))

    if body.run_id:
        run = (
            await db.execute(select(TutorRun).where(TutorRun.id == body.run_id, TutorRun.assistant_id == assistant.id))
        ).scalar_one_or_none()
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Сессия разбора не найдена")
    else:
        run = TutorRun(assistant_id=assistant.id, created_by=user.id)
        db.add(run)

    if body.task_id:
        run.task_id = body.task_id
    run.prompt_version_id = prompt.id if prompt else None
    run.provider_name = provider.name
    run.model_id = model.model_id
    run.student_work = body.student_work
    run.messages = [m.model_dump() for m in body.messages] + [{"role": "assistant", "content": result.text}]
    await db.commit()
    await db.refresh(run)
    return TutorChatResponse(run=TutorRunOut.model_validate(run), reply=result.text)


@router.get("/assistants/{assistant_id}/tutor/runs", response_model=list[TutorRunOut])
async def tutor_runs(
    assistant_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[TutorRun]:
    await get_assistant_or_404(assistant_id, db)
    return list(
        (
            await db.execute(
                select(TutorRun)
                .where(TutorRun.assistant_id == assistant_id)
                .order_by(TutorRun.updated_at.desc())
                .limit(limit)
            )
        ).scalars()
    )


@router.post("/tutor/runs/{run_id}/feedback", response_model=TutorRunOut)
async def tutor_feedback(
    run_id: str,
    body: TutorFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> TutorRun:
    run = (await db.execute(select(TutorRun).where(TutorRun.id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сессия разбора не найдена")
    if body.rating is not None:
        run.rating = body.rating
    if body.comment is not None:
        run.comment = body.comment
    await db.commit()
    await db.refresh(run)
    return run
