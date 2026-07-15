from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.assistants import get_assistant_or_404
from app.db import get_db
from app.models import GeneratedTask, Pipeline, PipelineRun, User
from app.schemas import PipelineCreate, PipelineOut, PipelineRunOut, PipelineRunRequest, PipelineUpdate
from app.security import get_current_user
from app.services.pipeline import PipelineError, execute_pipeline, preflight_pipeline, validate_pipeline_steps

router = APIRouter(tags=["pipelines"])


def _validate_steps(steps: list) -> list[dict]:
    try:
        return validate_pipeline_steps(steps)
    except PipelineError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err)) from err


@router.get("/assistants/{assistant_id}/pipelines", response_model=list[PipelineOut])
async def list_pipelines(
    assistant_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> list[Pipeline]:
    await get_assistant_or_404(assistant_id, db)
    return list(
        (
            await db.execute(
                select(Pipeline).where(Pipeline.assistant_id == assistant_id).order_by(Pipeline.created_at)
            )
        ).scalars()
    )


@router.post("/assistants/{assistant_id}/pipelines", response_model=PipelineOut)
async def create_pipeline(
    assistant_id: str, body: PipelineCreate, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> Pipeline:
    await get_assistant_or_404(assistant_id, db)
    steps = _validate_steps(body.steps)
    pipeline = Pipeline(assistant_id=assistant_id, name=body.name, description=body.description, steps=steps)
    db.add(pipeline)
    await db.commit()
    await db.refresh(pipeline)
    return pipeline


async def _get_pipeline(db: AsyncSession, assistant_id: str, pipeline_id: str) -> Pipeline:
    pipeline = (
        await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id, Pipeline.assistant_id == assistant_id))
    ).scalar_one_or_none()
    if pipeline is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пайплайн не найден")
    return pipeline


@router.patch("/assistants/{assistant_id}/pipelines/{pipeline_id}", response_model=PipelineOut)
async def update_pipeline(
    assistant_id: str,
    pipeline_id: str,
    body: PipelineUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Pipeline:
    pipeline = await _get_pipeline(db, assistant_id, pipeline_id)
    payload = body.model_dump(exclude_unset=True)
    if "steps" in payload:
        payload["steps"] = _validate_steps(payload["steps"])
    for field, value in payload.items():
        setattr(pipeline, field, value)
    await db.commit()
    await db.refresh(pipeline)
    return pipeline


@router.delete("/assistants/{assistant_id}/pipelines/{pipeline_id}")
async def delete_pipeline(
    assistant_id: str, pipeline_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> dict:
    pipeline = await _get_pipeline(db, assistant_id, pipeline_id)
    await db.delete(pipeline)
    await db.commit()
    return {"ok": True}


@router.post("/assistants/{assistant_id}/pipelines/{pipeline_id}/run", response_model=PipelineRunOut)
async def run_pipeline(
    assistant_id: str,
    pipeline_id: str,
    body: PipelineRunRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PipelineRun:
    pipeline = await _get_pipeline(db, assistant_id, pipeline_id)
    _validate_steps(pipeline.steps)

    run_input = body.model_dump()
    if body.task_id:
        task = (
            await db.execute(
                select(GeneratedTask).where(
                    GeneratedTask.id == body.task_id, GeneratedTask.assistant_id == assistant_id
                )
            )
        ).scalar_one_or_none()
        if task is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
        run_input.update(
            task_text=task.statement,
            reference_solution=task.reference_solution,
            rubric=task.rubric,
            max_score=task.max_score,
        )
    if not run_input.get("task_text"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Не задано условие задачи (task_id или task_text)")

    try:
        plan = await preflight_pipeline(db, pipeline, run_input)
    except PipelineError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err)) from err

    run = PipelineRun(pipeline_id=pipeline_id, input=run_input, created_by=user.id)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await execute_pipeline(db, pipeline, run, plan)
    await db.refresh(run)
    return run


@router.get("/assistants/{assistant_id}/pipelines/{pipeline_id}/runs", response_model=list[PipelineRunOut])
async def list_runs(
    assistant_id: str, pipeline_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> list[PipelineRun]:
    await _get_pipeline(db, assistant_id, pipeline_id)
    return list(
        (
            await db.execute(
                select(PipelineRun)
                .where(PipelineRun.pipeline_id == pipeline_id)
                .order_by(PipelineRun.started_at.desc())
                .limit(20)
            )
        ).scalars()
    )
