from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.assistants import get_assistant_or_404, resolve_model
from app.db import get_db
from app.llm import client as llm
from app.models import GeneratedTask, PromptVersion, TaskTemplate, User
from app.schemas import (
    GeneratedTaskOut,
    GeneratedTaskUpdate,
    TaskGenerateRequest,
    TaskTemplateCreate,
    TaskTemplateOut,
)
from app.security import get_current_user
from app.services.taskgen import generate_tasks

router = APIRouter(tags=["tasks"], dependencies=[Depends(get_current_user)])


@router.get("/assistants/{assistant_id}/templates", response_model=list[TaskTemplateOut])
async def list_templates(assistant_id: str, db: AsyncSession = Depends(get_db)) -> list[TaskTemplate]:
    await get_assistant_or_404(assistant_id, db)
    return list(
        (
            await db.execute(
                select(TaskTemplate).where(TaskTemplate.assistant_id == assistant_id).order_by(TaskTemplate.created_at)
            )
        ).scalars()
    )


@router.post("/assistants/{assistant_id}/templates", response_model=TaskTemplateOut)
async def create_template(
    assistant_id: str, body: TaskTemplateCreate, db: AsyncSession = Depends(get_db)
) -> TaskTemplate:
    await get_assistant_or_404(assistant_id, db)
    template = TaskTemplate(assistant_id=assistant_id, **body.model_dump())
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/assistants/{assistant_id}/templates/{template_id}")
async def delete_template(assistant_id: str, template_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    template = (
        await db.execute(
            select(TaskTemplate).where(TaskTemplate.id == template_id, TaskTemplate.assistant_id == assistant_id)
        )
    ).scalar_one_or_none()
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
    await db.delete(template)
    await db.commit()
    return {"ok": True}


@router.get("/assistants/{assistant_id}/tasks", response_model=list[GeneratedTaskOut])
async def list_tasks(assistant_id: str, db: AsyncSession = Depends(get_db)) -> list[GeneratedTask]:
    await get_assistant_or_404(assistant_id, db)
    return list(
        (
            await db.execute(
                select(GeneratedTask)
                .where(GeneratedTask.assistant_id == assistant_id)
                .order_by(GeneratedTask.created_at.desc())
            )
        ).scalars()
    )


@router.post("/assistants/{assistant_id}/tasks/generate", response_model=list[GeneratedTaskOut])
async def generate(
    assistant_id: str, body: TaskGenerateRequest, db: AsyncSession = Depends(get_db)
) -> list[GeneratedTask]:
    assistant = await get_assistant_or_404(assistant_id, db)
    provider, model = await resolve_model(db, body.model_entry_id)

    topic, difficulty, instructions, example, template_id = body.topic, body.difficulty, body.instructions, "", None
    if body.template_id:
        template = (
            await db.execute(
                select(TaskTemplate).where(
                    TaskTemplate.id == body.template_id, TaskTemplate.assistant_id == assistant_id
                )
            )
        ).scalar_one_or_none()
        if template is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
        template_id = template.id
        topic = body.topic or template.topic
        difficulty = template.difficulty if body.difficulty == "medium" else body.difficulty
        instructions = "\n".join(filter(None, [template.instructions, body.instructions]))
        example = template.example

    system_prompt = None
    if body.prompt_version_id:
        prompt = (
            await db.execute(
                select(PromptVersion).where(
                    PromptVersion.id == body.prompt_version_id, PromptVersion.assistant_id == assistant_id
                )
            )
        ).scalar_one_or_none()
        if prompt is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Версия промпта не найдена")
        system_prompt = prompt.system_prompt
    else:
        active = (
            await db.execute(
                select(PromptVersion)
                .where(
                    PromptVersion.assistant_id == assistant_id,
                    PromptVersion.role == "generator",
                    PromptVersion.status == "active",
                )
                .order_by(PromptVersion.version.desc())
            )
        ).scalars().first()
        if active:
            system_prompt = active.system_prompt

    existing = (
        await db.execute(
            select(GeneratedTask.statement)
            .where(GeneratedTask.assistant_id == assistant_id)
            .order_by(GeneratedTask.created_at.desc())
            .limit(8)
        )
    ).scalars().all()

    try:
        tasks_data = await generate_tasks(
            provider,
            model,
            assistant,
            system_prompt,
            topic,
            difficulty,
            body.count,
            instructions,
            example,
            list(existing),
            temperature=body.temperature,
        )
    except llm.LlmError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err))

    created: list[GeneratedTask] = []
    for item in tasks_data:
        if not isinstance(item, dict) or not item.get("statement"):
            continue
        task = GeneratedTask(
            assistant_id=assistant_id,
            template_id=template_id,
            statement=str(item.get("statement", "")),
            reference_solution=str(item.get("reference_solution", "")),
            rubric=item.get("rubric") or [],
            max_score=float(item.get("max_score") or 10),
            difficulty=str(item.get("difficulty") or difficulty),
            topic=str(item.get("topic") or topic),
            model_used=f"{provider.name}/{model.model_id}",
        )
        db.add(task)
        created.append(task)
    if not created:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Модель не вернула ни одной валидной задачи")
    await db.commit()
    for task in created:
        await db.refresh(task)
    return created


@router.patch("/assistants/{assistant_id}/tasks/{task_id}", response_model=GeneratedTaskOut)
async def update_task(
    assistant_id: str, task_id: str, body: GeneratedTaskUpdate, db: AsyncSession = Depends(get_db)
) -> GeneratedTask:
    task = (
        await db.execute(
            select(GeneratedTask).where(GeneratedTask.id == task_id, GeneratedTask.assistant_id == assistant_id)
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/assistants/{assistant_id}/tasks/{task_id}")
async def delete_task(assistant_id: str, task_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    task = (
        await db.execute(
            select(GeneratedTask).where(GeneratedTask.id == task_id, GeneratedTask.assistant_id == assistant_id)
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    await db.delete(task)
    await db.commit()
    return {"ok": True}
