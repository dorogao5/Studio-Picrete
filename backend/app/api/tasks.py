from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.assistants import get_assistant_or_404, resolve_model
from app.db import get_db
from app.llm import client as llm
from app.models import GeneratedTask, GenerationBatch, TaskTemplate, User, utcnow
from app.schemas import (
    GeneratedTaskOut,
    GeneratedTaskUpdate,
    GenerationBatchOut,
    GenerationBatchRequest,
    RevalidateRequest,
    TaskExportRequest,
    TaskGenerateRequest,
    TaskTemplateCreate,
    TaskTemplateOut,
    TaskTemplateUpdate,
)
from app.security import get_current_user
from app.services.export import build_bank_export, build_variants_export
from app.services.task_approval import has_complete_approval, validation_is_current_decision
from app.services.taskgen import (
    GenerationError,
    build_generation_grounding,
    build_grounding_meta,
    generate_tasks,
    load_reference_sheets,
    merge_template_params,
    resolve_generator_prompt,
    resolve_generator_prompt_version,
    run_batch,
    sheets_to_text,
    task_from_item,
)
from app.services.validation import VALIDATION_POLICY_VERSION, run_validation

router = APIRouter(tags=["tasks"], dependencies=[Depends(get_current_user)])


async def _get_template_or_404(db: AsyncSession, assistant_id: str, template_id: str) -> TaskTemplate:
    template = (
        await db.execute(
            select(TaskTemplate).where(TaskTemplate.id == template_id, TaskTemplate.assistant_id == assistant_id)
        )
    ).scalar_one_or_none()
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
    return template


async def _get_task_or_404(db: AsyncSession, assistant_id: str, task_id: str) -> GeneratedTask:
    task = (
        await db.execute(
            select(GeneratedTask).where(GeneratedTask.id == task_id, GeneratedTask.assistant_id == assistant_id)
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    return task


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


@router.patch("/assistants/{assistant_id}/templates/{template_id}", response_model=TaskTemplateOut)
async def update_template(
    assistant_id: str, template_id: str, body: TaskTemplateUpdate, db: AsyncSession = Depends(get_db)
) -> TaskTemplate:
    template = await _get_template_or_404(db, assistant_id, template_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(template, field, value)
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/assistants/{assistant_id}/templates/{template_id}")
async def delete_template(assistant_id: str, template_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    template = await _get_template_or_404(db, assistant_id, template_id)
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

    template = None
    if body.template_id:
        template = await _get_template_or_404(db, assistant_id, body.template_id)
    merged = merge_template_params(
        template, topic=body.topic, difficulty=body.difficulty, instructions=body.instructions
    )
    try:
        system_prompt = await resolve_generator_prompt(db, assistant_id, body.prompt_version_id)
    except GenerationError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err))

    grounding_query = merged["kb_query"] or merged["topic"]
    sheets = await load_reference_sheets(db, assistant_id, merged["sheet_ids"])
    grounding_text = await build_generation_grounding(
        db, assistant_id, sheet_ids=merged["sheet_ids"], query=grounding_query
    )

    existing = (
        await db.execute(
            select(GeneratedTask.statement)
            .where(GeneratedTask.assistant_id == assistant_id)
            .order_by(GeneratedTask.created_at.desc())
            .limit(8)
        )
    ).scalars().all()

    try:
        items = await generate_tasks(
            provider,
            model,
            assistant,
            system_prompt,
            topic=merged["topic"],
            difficulty=merged["difficulty"],
            count=body.count,
            task_kind=merged["task_kind"],
            answer_format=merged["answer_format"],
            instructions=merged["instructions"],
            grounding=grounding_text,
            rubric=merged["rubric"],
            example_tasks=merged["example_tasks"],
            existing_statements=list(existing),
            temperature=body.temperature,
        )
    except llm.LlmError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err))

    grounding_meta = build_grounding_meta(sheets, grounding_text, grounding_query)
    created: list[GeneratedTask] = []
    for item in items:
        task = task_from_item(
            item,
            assistant_id=assistant_id,
            template_id=template.id if template else None,
            batch_id=None,
            topic=merged["topic"],
            difficulty=merged["difficulty"],
            model_used=f"{provider.name}/{model.model_id}",
            grounding_meta=grounding_meta,
            template_rubric=merged["rubric"],
        )
        if task is not None:
            db.add(task)
            created.append(task)
    if len(created) != body.count:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Модель вернула {len(created)} из {body.count} валидных задач. Неполный набор не сохранён — повторите генерацию.",
        )
    await db.commit()
    for task in created:
        await db.refresh(task)
    return created


@router.post("/assistants/{assistant_id}/tasks/batches", response_model=GenerationBatchOut)
async def create_batch(
    assistant_id: str,
    body: GenerationBatchRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GenerationBatch:
    await get_assistant_or_404(assistant_id, db)
    provider, model = await resolve_model(db, body.model_entry_id)
    if body.solver_model_entry_id:
        await resolve_model(db, body.solver_model_entry_id)
    if body.template_id:
        await _get_template_or_404(db, assistant_id, body.template_id)
    try:
        prompt_version = await resolve_generator_prompt_version(db, assistant_id, body.prompt_version_id)
    except GenerationError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err))

    params = body.model_dump()
    # Freeze the active version before the background job is queued. If no active
    # generator prompt exists, None intentionally preserves the built-in fallback.
    params["prompt_version_id"] = prompt_version.id if prompt_version else None

    batch = GenerationBatch(
        assistant_id=assistant_id,
        template_id=body.template_id,
        status="running",
        params=params,
        model_used=f"{provider.name}/{model.model_id}",
        requested_count=body.count,
        progress={"stage": "В очереди", "done": 0, "total": body.count},
        created_by=user.id,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    background.add_task(run_batch, batch.id)
    return batch


@router.get("/assistants/{assistant_id}/tasks/batches", response_model=list[GenerationBatchOut])
async def list_batches(
    assistant_id: str, limit: int = 10, db: AsyncSession = Depends(get_db)
) -> list[GenerationBatch]:
    await get_assistant_or_404(assistant_id, db)
    return list(
        (
            await db.execute(
                select(GenerationBatch)
                .where(GenerationBatch.assistant_id == assistant_id)
                .order_by(GenerationBatch.created_at.desc())
                .limit(min(max(limit, 1), 50))
            )
        ).scalars()
    )


@router.get("/assistants/{assistant_id}/tasks/batches/{batch_id}", response_model=GenerationBatchOut)
async def get_batch(assistant_id: str, batch_id: str, db: AsyncSession = Depends(get_db)) -> GenerationBatch:
    batch = (
        await db.execute(
            select(GenerationBatch).where(
                GenerationBatch.id == batch_id, GenerationBatch.assistant_id == assistant_id
            )
        )
    ).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Партия не найдена")
    return batch


@router.post("/assistants/{assistant_id}/tasks/{task_id}/revalidate", response_model=GeneratedTaskOut)
async def revalidate_task(
    assistant_id: str, task_id: str, body: RevalidateRequest, db: AsyncSession = Depends(get_db)
) -> GeneratedTask:
    assistant = await get_assistant_or_404(assistant_id, db)
    task = await _get_task_or_404(db, assistant_id, task_id)

    template = None
    if task.template_id:
        template = (
            await db.execute(
                select(TaskTemplate).where(
                    TaskTemplate.id == task.template_id, TaskTemplate.assistant_id == assistant_id
                )
            )
        ).scalar_one_or_none()
    merged = merge_template_params(template, topic=task.topic, difficulty=task.difficulty, instructions="")

    solver_entry_id = (
        body.solver_model_entry_id
        or assistant.default_grader_model_id
        or assistant.default_generator_model_id
    )

    solver_provider = solver_model = None
    if merged["validation_solver"]:
        if not solver_entry_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Не удалось определить модель-решатель — передайте solver_model_entry_id",
            )
        solver_provider, solver_model = await resolve_model(db, solver_entry_id)

    grounding_query = merged["kb_query"] or task.topic
    sheets = await load_reference_sheets(db, assistant_id, merged["sheet_ids"])
    grounding_text = await build_generation_grounding(
        db, assistant_id, sheet_ids=merged["sheet_ids"], query=grounding_query
    )
    existing = (
        await db.execute(
            select(GeneratedTask.statement)
            .where(GeneratedTask.assistant_id == assistant_id, GeneratedTask.id != task.id)
            .order_by(GeneratedTask.created_at.desc())
            .limit(50)
        )
    ).scalars().all()

    validation = await run_validation(
        statement=task.statement,
        reference_answer=task.answer,
        rubric=task.rubric,
        max_score=task.max_score,
        answer_format=merged["answer_format"],
        tolerance_pct=merged["tolerance_pct"],
        grounding=grounding_text,
        sheets_text=sheets_to_text(sheets),
        existing_statements=list(existing),
        data_used=(task.grounding or {}).get("data_used", []),
        solver_provider=solver_provider,
        solver_model=solver_model,
        run_solver=merged["validation_solver"],
        run_data=merged["validation_data_check"],
    )
    task.validation = validation
    task.status = validation["verdict"]
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/assistants/{assistant_id}/tasks/export")
async def export_tasks(assistant_id: str, body: TaskExportRequest, db: AsyncSession = Depends(get_db)) -> dict:
    assistant = await get_assistant_or_404(assistant_id, db)
    query = select(GeneratedTask).where(GeneratedTask.assistant_id == assistant_id)
    if body.task_ids:
        query = query.where(GeneratedTask.id.in_(body.task_ids))
    else:
        query = query.where(or_(GeneratedTask.approved.is_(True), GeneratedTask.status == "approved"))
    tasks = list((await db.execute(query.order_by(GeneratedTask.created_at))).scalars())
    if not tasks:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Нет задач для экспорта — одобрите задачи или передайте task_ids"
        )
    if body.task_ids:
        requested_ids = set(body.task_ids)
        if len(tasks) != len(requested_ids):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Часть выбранных задач не найдена")
    unapproved = [task for task in tasks if not task.approved or task.status != "approved"]
    if unapproved:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Экспорт остановлен: сначала одобрите задачи ({len(unapproved)})",
        )
    unaudited = [task for task in tasks if not has_complete_approval(task.validation)]
    if unaudited:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Экспорт остановлен: подтвердите одобрение по текущей политике ({len(unaudited)})",
        )
    source_title = body.source_title or assistant.discipline
    if body.mode == "bank":
        return build_bank_export(tasks, source_code=body.source_code, source_title=source_title, version=body.version)
    template_ids = {task.template_id for task in tasks if task.template_id}
    tolerance_by_template: dict[str, float] = {}
    if template_ids:
        templates = (
            await db.execute(select(TaskTemplate).where(TaskTemplate.id.in_(template_ids)))
        ).scalars()
        tolerance_by_template = {template.id: template.numeric_tolerance_pct for template in templates}
    return build_variants_export(tasks, tolerance_by_template)


@router.patch("/assistants/{assistant_id}/tasks/{task_id}", response_model=GeneratedTaskOut)
async def update_task(
    assistant_id: str,
    task_id: str,
    body: GeneratedTaskUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GeneratedTask:
    task = await _get_task_or_404(db, assistant_id, task_id)
    data = {field: value for field, value in body.model_dump(exclude_unset=True).items() if value is not None}
    approval_reason = str(data.pop("approval_reason", "")).strip()
    content_fields = {"statement", "reference_solution", "answer", "rubric", "max_score"}
    changes_content = bool(content_fields.intersection(data))
    requested_status = data.get("status")
    if requested_status is None and "approved" in data:
        requested_status = "approved" if data["approved"] else "draft"

    if requested_status in {"validated", "needs_review"}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Статусы автопроверки меняются только после запуска проверки",
        )
    if changes_content and requested_status == "approved":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Сначала сохраните изменения и запустите автопроверку, затем одобрите задачу",
        )

    if changes_content:
        data["validation"] = {}
        data["status"] = "draft"
        data["approved"] = False
    elif requested_status == "approved":
        validation = dict(task.validation) if isinstance(task.validation, dict) else {}
        policy_validated = validation_is_current_decision(validation)
        if not policy_validated and len(approval_reason) < 10:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Задача не подтверждена текущей политикой: укажите причину ручного одобрения (не менее 10 символов)",
            )
        validation["approval"] = {
            "basis": "policy_validated" if policy_validated else "teacher_override",
            "reviewed_by": user.id,
            "reviewed_at": utcnow().isoformat(),
            "reason": approval_reason,
            "policy_version": VALIDATION_POLICY_VERSION,
        }
        data["validation"] = validation
        data["status"] = "approved"
        data["approved"] = True
    elif requested_status is not None:
        validation = dict(task.validation) if isinstance(task.validation, dict) else {}
        validation.pop("approval", None)
        data["validation"] = validation
        data["status"] = requested_status
        data["approved"] = False

    for field, value in data.items():
        setattr(task, field, value)
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/assistants/{assistant_id}/tasks/{task_id}")
async def delete_task(assistant_id: str, task_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    task = await _get_task_or_404(db, assistant_id, task_id)
    await db.delete(task)
    await db.commit()
    return {"ok": True}
