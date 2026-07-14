from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import GeneratedTask, GenerationBatch, TaskTemplate, utcnow
from app.services.task_approval import task_is_export_ready
from app.services.taskgen import (
    _resolve_batch_model,
    build_generation_grounding,
    load_reference_sheets,
    merge_template_params,
    sheets_to_text,
)
from app.services.validation import run_validation


async def _set_progress(
    db: AsyncSession,
    batch: GenerationBatch,
    *,
    stage: str,
    done: int,
    total: int,
) -> None:
    batch.progress = {"stage": stage, "done": done, "total": total}
    await db.commit()


async def _revalidate_task(
    db: AsyncSession,
    *,
    batch: GenerationBatch,
    task: GeneratedTask,
    solver_model_entry_id: str,
) -> bool:
    template = None
    if task.template_id:
        template = (
            await db.execute(
                select(TaskTemplate).where(
                    TaskTemplate.id == task.template_id,
                    TaskTemplate.assistant_id == batch.assistant_id,
                )
            )
        ).scalar_one_or_none()
    merged = merge_template_params(template, topic=task.topic, difficulty=task.difficulty, instructions="")
    solver_provider, solver_model = await _resolve_batch_model(db, solver_model_entry_id)
    grounding_query = merged["kb_query"] or task.topic
    sheets = await load_reference_sheets(db, batch.assistant_id, merged["sheet_ids"])
    grounding_text = await build_generation_grounding(
        db,
        batch.assistant_id,
        sheet_ids=merged["sheet_ids"],
        query=grounding_query,
    )
    existing = (
        (
            await db.execute(
                select(GeneratedTask.statement)
                .where(
                    GeneratedTask.assistant_id == batch.assistant_id,
                    GeneratedTask.id != task.id,
                    GeneratedTask.status != "rejected",
                )
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
    validation = dict(validation)
    validation.pop("approval", None)
    task.validation = validation
    task.status = validation["verdict"]
    task.approved = False
    await db.commit()
    return task_is_export_ready(task)


async def _execute_revalidation_batch(db: AsyncSession, batch: GenerationBatch) -> None:
    params = batch.params or {}
    task_ids = [str(value) for value in params.get("task_ids") or []]
    solver_model_entry_id = str(params.get("solver_model_entry_id") or "")
    if not task_ids or not solver_model_entry_id:
        raise ValueError("Партия перепроверки не содержит задач или контрольной модели")

    tasks = list(
        (
            await db.execute(
                select(GeneratedTask).where(
                    GeneratedTask.assistant_id == batch.assistant_id,
                    GeneratedTask.id.in_(task_ids),
                )
            )
        ).scalars()
    )
    by_id = {task.id: task for task in tasks}
    ordered = [by_id[task_id] for task_id in task_ids if task_id in by_id]
    if len(ordered) != len(task_ids):
        raise ValueError("Часть задач партии перепроверки больше не существует")

    ready = 0
    total = len(ordered)
    for index, task in enumerate(ordered, start=1):
        await _set_progress(
            db,
            batch,
            stage=f"Перепроверка {index}/{total}",
            done=index - 1,
            total=total,
        )
        if await _revalidate_task(
            db,
            batch=batch,
            task=task,
            solver_model_entry_id=solver_model_entry_id,
        ):
            ready += 1
        batch.validated_count = ready
        batch.generated_count = index
        await db.commit()

    batch.status = "completed"
    batch.finished_at = utcnow()
    batch.progress = {
        "stage": f"Готово: {ready}; требуют внимания: {total - ready}",
        "done": total,
        "total": total,
    }
    await db.commit()


async def run_revalidation_batch(batch_id: str) -> None:
    async with SessionLocal() as db:
        batch = (await db.execute(select(GenerationBatch).where(GenerationBatch.id == batch_id))).scalar_one_or_none()
        if batch is None:
            return
        try:
            await _execute_revalidation_batch(db, batch)
        except Exception as err:
            await db.rollback()
            batch.status = "failed"
            batch.error = str(err)
            batch.finished_at = utcnow()
            await db.commit()
