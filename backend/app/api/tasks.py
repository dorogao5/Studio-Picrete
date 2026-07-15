from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
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
    RevalidationBatchRequest,
    TaskExportRequest,
    TaskGenerateRequest,
    TaskTemplateCreate,
    TaskTemplateOut,
    TaskTemplateUpdate,
)
from app.security import get_current_user
from app.services.assistant_profile import build_assistant_profile
from app.services.export import build_bank_export, build_variants_export
from app.services.evidence_invalidation import invalidate_task_evidence
from app.services.model_policy import ModelUsePolicyError, require_decision_model
from app.services.task_approval import task_is_export_ready
from app.services.task_evidence import (
    APPROVAL_SCHEMA_VERSION,
    evidence_matches_task,
    task_content_fingerprint,
)
from app.services.task_revalidation import run_revalidation_batch
from app.services.taskgen import (
    GenerationError,
    build_generation_grounding,
    build_grounding_meta,
    build_validation_contract,
    generate_tasks,
    load_reference_sheets,
    merge_template_params,
    resolve_generator_prompt,
    resolve_generator_prompt_version,
    run_batch,
    sheets_to_text,
    task_from_item,
    validation_contract_for_task,
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
    await invalidate_task_evidence(
        db,
        assistant_id,
        template_id=template_id,
        reason="Изменился шаблон задачи — автоматические доказательства нужно пересобрать",
    )
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/assistants/{assistant_id}/templates/{template_id}")
async def delete_template(assistant_id: str, template_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    template = await _get_template_or_404(db, assistant_id, template_id)
    await invalidate_task_evidence(
        db,
        assistant_id,
        template_id=template_id,
        reason="Шаблон задачи удалён — автоматические доказательства нужно пересобрать",
    )
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
        (
            await db.execute(
                select(GeneratedTask.statement)
                .where(GeneratedTask.assistant_id == assistant_id)
                .order_by(GeneratedTask.created_at.desc())
                .limit(8)
            )
        )
        .scalars()
        .all()
    )

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
            chemistry_check=merged["chemistry_check"],
        )
    except llm.LlmError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err))

    grounding_meta = await build_grounding_meta(db, sheets, grounding_text, grounding_query)
    validation_contract = build_validation_contract(merged, grounding_meta)
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
            validation_contract=validation_contract,
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
    assistant = await get_assistant_or_404(assistant_id, db)
    if not body.validate_tasks:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Партия банка задач всегда проходит автоматический контроль; для экспериментов используйте песочницу",
        )
    provider, model = await resolve_model(db, body.model_entry_id)
    solver_entry_id = (
        body.solver_model_entry_id or getattr(assistant, "default_grader_model_id", None) or body.model_entry_id
    )
    solver_model = model
    if solver_entry_id != body.model_entry_id:
        _, solver_model = await resolve_model(db, solver_entry_id)
    try:
        require_decision_model(solver_model)
    except ModelUsePolicyError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err))
    if body.template_id:
        await _get_template_or_404(db, assistant_id, body.template_id)
    try:
        prompt_version = await resolve_generator_prompt_version(db, assistant_id, body.prompt_version_id)
    except GenerationError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err))

    params = body.model_dump()
    params["solver_model_entry_id"] = solver_entry_id
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
async def list_batches(assistant_id: str, limit: int = 10, db: AsyncSession = Depends(get_db)) -> list[GenerationBatch]:
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


@router.post(
    "/assistants/{assistant_id}/tasks/revalidation-batches",
    response_model=GenerationBatchOut,
)
async def create_revalidation_batch(
    assistant_id: str,
    body: RevalidationBatchRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GenerationBatch:
    assistant = await get_assistant_or_404(assistant_id, db)
    running = list(
        (
            await db.execute(
                select(GenerationBatch).where(
                    GenerationBatch.assistant_id == assistant_id,
                    GenerationBatch.status == "running",
                )
            )
        ).scalars()
    )
    for batch in running:
        if (batch.params or {}).get("operation") == "revalidation":
            return batch

    solver_entry_id = (
        body.solver_model_entry_id or assistant.default_grader_model_id or assistant.default_generator_model_id
    )
    if not solver_entry_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Не настроена контрольная модель для автоматической перепроверки",
        )
    provider, model = await resolve_model(db, solver_entry_id)
    try:
        require_decision_model(model)
    except ModelUsePolicyError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err))

    query = select(GeneratedTask).where(
        GeneratedTask.assistant_id == assistant_id,
        GeneratedTask.status != "rejected",
    )
    requested_ids = list(dict.fromkeys(body.task_ids))
    if requested_ids:
        query = query.where(GeneratedTask.id.in_(requested_ids))
    tasks = list((await db.execute(query.order_by(GeneratedTask.created_at))).scalars())
    if requested_ids and len(tasks) != len(requested_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Часть выбранных задач не найдена")
    tasks = [task for task in tasks if not task_is_export_ready(task)]
    if not tasks:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Все выбранные задачи уже готовы по текущей политике",
        )
    if len(tasks) > 100:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "За один запуск можно перепроверить не более 100 задач",
        )

    params = {
        "operation": "revalidation",
        "task_ids": [task.id for task in tasks],
        "solver_model_entry_id": model.id,
    }
    batch = GenerationBatch(
        assistant_id=assistant_id,
        status="running",
        params=params,
        model_used=f"{provider.name}/{model.model_id}",
        requested_count=len(tasks),
        progress={"stage": "В очереди на перепроверку", "done": 0, "total": len(tasks)},
        created_by=user.id,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    background.add_task(run_revalidation_batch, batch.id)
    return batch


@router.get("/assistants/{assistant_id}/tasks/batches/{batch_id}", response_model=GenerationBatchOut)
async def get_batch(assistant_id: str, batch_id: str, db: AsyncSession = Depends(get_db)) -> GenerationBatch:
    batch = (
        await db.execute(
            select(GenerationBatch).where(GenerationBatch.id == batch_id, GenerationBatch.assistant_id == assistant_id)
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
    contract = validation_contract_for_task(task, merged)

    solver_entry_id = (
        body.solver_model_entry_id or assistant.default_grader_model_id or assistant.default_generator_model_id
    )

    solver_provider = solver_model = None
    if contract["validation_solver"]:
        if not solver_entry_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Не удалось определить модель-решатель — передайте solver_model_entry_id",
            )
        solver_provider, solver_model = await resolve_model(db, solver_entry_id)
        try:
            require_decision_model(solver_model)
        except ModelUsePolicyError as err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err))

    grounding_query = contract["kb_query"] or task.topic
    sheet_ids = contract["sheet_ids"] or None
    sheets = await load_reference_sheets(db, assistant_id, sheet_ids)
    grounding_text = await build_generation_grounding(
        db, assistant_id, sheet_ids=sheet_ids, query=grounding_query
    )
    grounding_meta = await build_grounding_meta(db, sheets, grounding_text, grounding_query)
    existing = (
        (
            await db.execute(
                select(GeneratedTask.statement)
                .where(GeneratedTask.assistant_id == assistant_id, GeneratedTask.id != task.id)
                .order_by(GeneratedTask.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    validation = await run_validation(
        statement=task.statement,
        reference_solution=task.reference_solution,
        reference_answer=task.answer,
        rubric=task.rubric,
        max_score=task.max_score,
        answer_format=contract["answer_format"],
        tolerance_pct=contract["tolerance_pct"],
        grounding=grounding_text,
        sheets_text=sheets_to_text(sheets),
        existing_statements=list(existing),
        data_used=(task.grounding or {}).get("data_used"),
        solver_provider=solver_provider,
        solver_model=solver_model,
        run_solver=contract["validation_solver"],
        run_data=contract["validation_data_check"],
        validation_config=contract,
        discipline_context=build_assistant_profile(assistant),
        topic=task.topic,
        chemistry_facts=(task.grounding or {}).get("chemistry_facts"),
        chemistry_facts_source=str((task.grounding or {}).get("chemistry_facts_source") or ""),
        extract_chemistry_facts_if_missing=True,
        grounding_sheets=grounding_meta["sheets"],
    )
    validation = dict(validation)
    validation.pop("approval", None)
    await db.refresh(task)
    if not evidence_matches_task(validation, task):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Задача изменилась во время проверки. Изменения сохранены; запустите проверку ещё раз",
        )
    task.validation = validation
    task.status = validation["verdict"]
    task.approved = False
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
        query = query.where(GeneratedTask.status.in_(("validated", "approved")))
    tasks = list((await db.execute(query.order_by(GeneratedTask.created_at))).scalars())
    if not tasks:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Нет готовых задач для экспорта — запустите автоматическую проверку или передайте task_ids",
        )
    if body.task_ids:
        requested_ids = set(body.task_ids)
        if len(tasks) != len(requested_ids):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Часть выбранных задач не найдена")
    not_ready = [task for task in tasks if not task_is_export_ready(task)]
    if not_ready:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Экспорт остановлен: {len(not_ready)} задач требуют автоматической перепроверки или решения преподавателя",
        )
    source_title = body.source_title or assistant.discipline
    if body.mode == "bank":
        return build_bank_export(tasks, source_code=body.source_code, source_title=source_title, version=body.version)
    template_ids = {task.template_id for task in tasks if task.template_id}
    tolerance_by_template: dict[str, float] = {}
    if template_ids:
        templates = (await db.execute(select(TaskTemplate).where(TaskTemplate.id.in_(template_ids)))).scalars()
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
        if (
            task.status != "needs_review"
            or validation.get("verdict") != "needs_review"
            or validation.get("policy_version") != VALIDATION_POLICY_VERSION
            or not evidence_matches_task(validation, task)
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Ручное исключение доступно только после актуальной автоматической проверки этой версии задачи",
            )
        if len(approval_reason) < 10:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Ручное принятие — исключение из автоматической политики: укажите причину (не менее 10 символов)",
            )
        approval_config = validation.get("validation_config")
        validation["approval"] = {
            "basis": "teacher_override",
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "reviewed_by": user.id,
            "reviewed_at": utcnow().isoformat(),
            "reason": approval_reason,
            "validation_config": approval_config,
            "content_fingerprint": task_content_fingerprint(task, approval_config),
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
