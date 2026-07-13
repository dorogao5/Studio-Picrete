
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.assistants import get_assistant_or_404
from app.db import get_db
from app.models import Assistant, GeneratedTask, PromptVersion, TaskTemplate, User
from app.schemas import PromptPreviewRequest, PromptPreviewResponse
from app.security import get_current_user
from app.services import taskgen
from app.services.assistant_profile import with_assistant_profile
from app.services.contracts import GENERATION_JSON_CONTRACT
from app.services.grading import build_grading_user_message
from app.services.grounding import build_grounding_block
from app.services.tutor import FALLBACK_TUTOR_PROMPT, build_tutor_context, flatten_dialog

router = APIRouter(tags=["preview"])

SAMPLE_STUDENT_MESSAGE = "Объясните, где у меня ошибка в решении"
GRADER_NO_PROMPT_PLACEHOLDER = "(промпт роли grader не задан — создайте или сгенерируйте версию)"


async def _resolve_system_prompt(
    db: AsyncSession, assistant: Assistant, role: str, prompt_version_id: str | None
) -> str:
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
        if prompt.role != role:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Версия промпта относится к другой роли")
        return prompt.system_prompt
    active = (
        await db.execute(
            select(PromptVersion)
            .where(
                PromptVersion.assistant_id == assistant.id,
                PromptVersion.role == role,
                PromptVersion.status == "active",
            )
            .order_by(PromptVersion.version.desc())
        )
    ).scalars().first()
    if active is not None:
        return active.system_prompt
    if role == "generator":
        return taskgen.FALLBACK_GENERATOR_PROMPT.format(
            discipline=assistant.discipline, contract=GENERATION_JSON_CONTRACT
        )
    if role == "tutor":
        return FALLBACK_TUTOR_PROMPT.format(discipline=assistant.discipline)
    return GRADER_NO_PROMPT_PLACEHOLDER


def _build_generation_message(template: TaskTemplate | None, grounding: str, existing_statements: list[str]) -> str:
    merged = taskgen.merge_template_params(template, topic="", difficulty="", instructions="")
    return taskgen.build_generation_user_message(
        topic=merged["topic"],
        difficulty=merged["difficulty"],
        count=3,
        task_kind=merged["task_kind"],
        answer_format=merged["answer_format"],
        instructions=merged["instructions"],
        grounding=grounding,
        rubric=merged["rubric"],
        example_tasks=merged["example_tasks"],
        existing_statements=existing_statements,
    )


@router.post("/assistants/{assistant_id}/prompt-preview", response_model=PromptPreviewResponse)
async def prompt_preview(
    assistant_id: str,
    body: PromptPreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PromptPreviewResponse:
    assistant = await get_assistant_or_404(assistant_id, db)
    system_prompt = await _resolve_system_prompt(db, assistant, body.role, body.prompt_version_id)
    system_prompt = with_assistant_profile(system_prompt, assistant)

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

    if body.role == "generator":
        template = None
        if body.template_id:
            template = (
                await db.execute(
                    select(TaskTemplate).where(
                        TaskTemplate.id == body.template_id, TaskTemplate.assistant_id == assistant.id
                    )
                )
            ).scalar_one_or_none()
            if template is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
        merged = taskgen.merge_template_params(template, topic="", difficulty="", instructions="")
        sheet_ids = merged["sheet_ids"]
        query = merged["kb_query"] or merged["topic"]
        grounding = await build_grounding_block(db, assistant.id, sheet_ids=sheet_ids, query=query)
        existing_statements = list(
            (
                await db.execute(
                    select(GeneratedTask.statement)
                    .where(GeneratedTask.assistant_id == assistant.id)
                    .order_by(GeneratedTask.created_at.desc())
                    .limit(8)
                )
            ).scalars()
        )
        user_message = _build_generation_message(template, grounding, existing_statements)
    elif body.role == "tutor":
        query_source = task.statement if task else (body.student_work or SAMPLE_STUDENT_MESSAGE)
        grounding = await build_grounding_block(
            db,
            assistant.id,
            query=query_source[:200],
            allowed_visibilities=("student",),
        )
        context = build_tutor_context(task, body.student_work, grounding)
        user_message = flatten_dialog([{"role": "user", "content": SAMPLE_STUDENT_MESSAGE}], context)
    else:
        task_text = task.statement if task else "(условие задачи)"
        query = (task.statement if task else "")[:200] or body.ocr_text[:200]
        grounding = await build_grounding_block(db, assistant.id, query=query)
        user_message = build_grading_user_message(
            task_text,
            task.reference_solution if task else "",
            task.rubric if task else assistant.criteria,
            task.max_score if task else 10.0,
            body.ocr_text,
            grounding=grounding,
        )

    return PromptPreviewResponse(system_prompt=system_prompt, user_message=user_message)
