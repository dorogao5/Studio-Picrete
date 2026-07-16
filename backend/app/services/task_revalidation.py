from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Assistant, GeneratedTask, GenerationBatch, TaskTemplate, utcnow
from app.services.assistant_profile import build_assistant_profile
from app.services.task_approval import task_is_export_ready
from app.services.task_evidence import evidence_matches_task
from app.services.taskgen import (
    _resolve_batch_model,
    build_generation_grounding,
    build_grounding_meta,
    load_reference_sheets,
    merge_template_params,
    sheets_to_text,
    validation_contract_for_task,
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


def _generated_candidate_should_be_discarded(task: GeneratedTask, validation: dict) -> bool:
    """Keep failed model candidates out of the teacher's exception queue."""

    return validation.get("verdict") != "validated" and bool(str(task.model_used or "").strip() or task.batch_id)


async def _revalidate_task(
    db: AsyncSession,
    *,
    batch: GenerationBatch,
    task: GeneratedTask,
    solver_model_entry_id: str,
    discipline_context: str,
) -> str:
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
    contract = validation_contract_for_task(task, merged)
    solver_provider, solver_model = await _resolve_batch_model(db, solver_model_entry_id)
    grounding_query = contract["kb_query"] or task.topic
    sheet_ids = contract["sheet_ids"] or None
    sheets = await load_reference_sheets(db, batch.assistant_id, sheet_ids)
    grounding_text = await build_generation_grounding(
        db,
        batch.assistant_id,
        sheet_ids=sheet_ids,
        query=grounding_query,
    )
    grounding_meta = await build_grounding_meta(
        db,
        sheets,
        grounding_text,
        grounding_query,
        assistant_id=batch.assistant_id,
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
        discipline_context=discipline_context,
        topic=task.topic,
        chemistry_facts=(task.grounding or {}).get("chemistry_facts"),
        chemistry_facts_source=str((task.grounding or {}).get("chemistry_facts_source") or ""),
        extract_chemistry_facts_if_missing=True,
        grounding_sheets=[*grounding_meta["sheets"], *grounding_meta.get("kb_sources", [])],
    )
    validation = dict(validation)
    validation.pop("approval", None)
    await db.refresh(task)
    if not evidence_matches_task(validation, task):
        return "attention"
    if _generated_candidate_should_be_discarded(task, validation):
        validation["candidate_disposition"] = "discarded_on_revalidation"
        task.status = "rejected"
        disposition = "discarded"
    else:
        task.status = validation["verdict"]
        disposition = "ready" if task.status == "validated" else "attention"
    task.validation = validation
    task.approved = False
    if disposition == "ready" and not task_is_export_ready(task):
        validation["reasons"] = [
            *(validation.get("reasons") or []),
            "Перепроверка не сформировала полный экспортный evidence-контракт",
        ]
        task.validation = validation
        task.status = "needs_review"
        disposition = "attention"
    await db.commit()
    return disposition


async def _execute_revalidation_batch(db: AsyncSession, batch: GenerationBatch) -> None:
    params = batch.params or {}
    task_ids = [str(value) for value in params.get("task_ids") or []]
    solver_model_entry_id = str(params.get("solver_model_entry_id") or "")
    if not task_ids or not solver_model_entry_id:
        raise ValueError("Партия перепроверки не содержит задач или контрольной модели")

    assistant = (await db.execute(select(Assistant).where(Assistant.id == batch.assistant_id))).scalar_one_or_none()
    if assistant is None:
        raise ValueError("Дисциплина партии перепроверки больше не существует")
    discipline_context = build_assistant_profile(assistant)

    ready = 0
    discarded = 0
    attention = 0
    total = len(task_ids)
    for index, task_id in enumerate(task_ids, start=1):
        await _set_progress(
            db,
            batch,
            stage=f"Перепроверка {index}/{total}",
            done=index - 1,
            total=total,
        )
        task = (
            await db.execute(
                select(GeneratedTask).where(
                    GeneratedTask.assistant_id == batch.assistant_id,
                    GeneratedTask.id == task_id,
                )
            )
        ).scalar_one_or_none()
        if task is None:
            discarded += 1
            batch.generated_count = index
            await db.commit()
            continue
        if task_is_export_ready(task):
            disposition = "ready"
        else:
            disposition = await _revalidate_task(
                db,
                batch=batch,
                task=task,
                solver_model_entry_id=solver_model_entry_id,
                discipline_context=discipline_context,
            )
        if disposition == "ready":
            ready += 1
        elif disposition == "discarded":
            discarded += 1
        else:
            attention += 1
        batch.validated_count = ready
        batch.generated_count = index
        await db.commit()

    batch.status = "completed"
    batch.finished_at = utcnow()
    batch.params = {
        **(batch.params or {}),
        "quality_summary": {
            "ready_count": ready,
            "discarded_count": discarded,
            "attention_count": attention,
        },
    }
    batch.progress = {
        "stage": f"Готовы: {ready}; исключены: {discarded}; требуют внимания: {attention}",
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
