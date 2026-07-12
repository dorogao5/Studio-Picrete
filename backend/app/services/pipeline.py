import mimetypes
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Assistant, ModelEntry, Pipeline, PipelineRun, PromptVersion, Provider, utcnow
from app.services import grading, ocr
from app.services.grounding import build_grounding_block

STEP_TYPES = ("ocr", "grade", "consensus")


class PipelineError(Exception):
    pass


async def _resolve_model(db: AsyncSession, model_entry_id: str) -> tuple[Provider, ModelEntry]:
    model = (await db.execute(select(ModelEntry).where(ModelEntry.id == model_entry_id))).scalar_one_or_none()
    if model is None:
        raise PipelineError(f"Модель {model_entry_id} не найдена")
    provider = (await db.execute(select(Provider).where(Provider.id == model.provider_id))).scalar_one_or_none()
    if provider is None or not provider.enabled:
        raise PipelineError(f"Провайдер модели {model.model_id} недоступен")
    return provider, model


async def _resolve_grader_prompt(db: AsyncSession, assistant_id: str, prompt_version_id: str | None) -> PromptVersion:
    if prompt_version_id:
        prompt = (
            await db.execute(select(PromptVersion).where(PromptVersion.id == prompt_version_id))
        ).scalar_one_or_none()
        if prompt is None:
            raise PipelineError(f"Версия промпта {prompt_version_id} не найдена")
        return prompt
    prompt = (
        await db.execute(
            select(PromptVersion)
            .where(
                PromptVersion.assistant_id == assistant_id,
                PromptVersion.role == "grader",
                PromptVersion.status == "active",
            )
            .order_by(PromptVersion.version.desc())
        )
    ).scalars().first()
    if prompt is None:
        raise PipelineError("У ассистента нет активного промпта проверки — создайте его на вкладке «Промпты»")
    return prompt


async def _run_ocr_step(run_input: dict) -> dict:
    if run_input.get("ocr_text"):
        return {"ocr_text": run_input["ocr_text"], "source": "provided"}
    image_ids: list[str] = run_input.get("image_ids") or []
    if not image_ids:
        raise PipelineError("Для шага OCR нужны изображения или готовый ocr_text")
    uploads_dir = get_settings().uploads_dir
    pages: list[str] = []
    for image_id in image_ids:
        path = uploads_dir / Path(image_id).name
        if not path.exists():
            raise PipelineError(f"Файл {image_id} не найден")
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        markdown = await ocr.run_datalab_ocr(path.name, path.read_bytes(), mime)
        pages.append(markdown)
    return {"ocr_text": "\n\n---\n\n".join(pages), "pages": len(pages), "source": "datalab"}


def _collect_grades(steps_log: list[dict]) -> list[dict]:
    grades = []
    for entry in steps_log:
        if entry["type"] == "grade" and entry["status"] == "completed" and entry["output"].get("result"):
            grades.append(entry["output"])
    return grades


def _run_consensus_step(steps_log: list[dict], config: dict) -> dict:
    grades = _collect_grades(steps_log)
    if len(grades) < 2:
        raise PipelineError("Для консенсуса нужно минимум два успешных шага «Проверка»")
    scores = []
    for grade_output in grades:
        result = grade_output["result"]
        scores.append(
            {
                "model": grade_output.get("model", ""),
                "total_score": result.get("total_score"),
                "max_score": result.get("max_score"),
                "confidence": result.get("confidence"),
            }
        )
    numeric = [s["total_score"] for s in scores if isinstance(s["total_score"], (int, float))]
    if not numeric:
        raise PipelineError("Ни один проверяющий не вернул числовой total_score")
    avg = sum(numeric) / len(numeric)
    spread = max(numeric) - min(numeric)
    max_score = next((s["max_score"] for s in scores if s["max_score"]), None)
    threshold_pct = float(config.get("disagreement_threshold_pct", 20))
    spread_pct = (spread / max_score * 100) if max_score else 0.0
    return {
        "scores": scores,
        "average_score": round(avg, 2),
        "spread": round(spread, 2),
        "spread_pct": round(spread_pct, 1),
        "needs_teacher_review": spread_pct > threshold_pct
        or any(g["result"].get("needs_teacher_review") for g in grades),
    }


async def execute_pipeline(db: AsyncSession, pipeline: Pipeline, run: PipelineRun) -> None:
    run_input = run.input
    steps_log: list[dict] = []
    ocr_text = run_input.get("ocr_text", "")
    grounding: str | None = None

    try:
        assistant = (
            await db.execute(select(Assistant).where(Assistant.id == pipeline.assistant_id))
        ).scalar_one_or_none()
        if assistant is None:
            raise PipelineError("Дисциплина пайплайна не найдена")
        for index, step in enumerate(pipeline.steps):
            step_type = step.get("type")
            config = step.get("config") or {}
            started = time.monotonic()
            entry: dict = {"index": index, "type": step_type, "title": step.get("title", ""), "status": "completed"}

            if step_type == "ocr":
                output = await _run_ocr_step(run_input)
                ocr_text = output["ocr_text"]
                entry["output"] = output
            elif step_type == "grade":
                if not ocr_text:
                    raise PipelineError("Нет OCR-текста: добавьте шаг OCR перед проверкой или передайте ocr_text")
                provider, model = await _resolve_model(db, config.get("model_entry_id", ""))
                prompt = await _resolve_grader_prompt(db, pipeline.assistant_id, config.get("prompt_version_id"))
                if grounding is None:
                    grounding = await build_grounding_block(
                        db, pipeline.assistant_id, query=str(run_input.get("task_text", ""))[:200]
                    )
                outcome = await grading.run_grading(
                    provider,
                    model,
                    prompt.system_prompt,
                    run_input.get("task_text", ""),
                    run_input.get("reference_solution", ""),
                    run_input.get("rubric", []),
                    run_input.get("max_score", 10),
                    ocr_text,
                    grounding=grounding,
                    temperature=float(config.get("temperature", 0.1)),
                    assistant=assistant,
                )
                if outcome.error:
                    entry["status"] = "failed"
                    entry["output"] = {"error": outcome.error, "model": f"{provider.name}/{model.model_id}"}
                else:
                    entry["output"] = {
                        "result": outcome.output,
                        "model": f"{provider.name}/{model.model_id}",
                        "prompt_version": prompt.version,
                        "tokens_total": outcome.tokens_total,
                    }
            elif step_type == "consensus":
                entry["output"] = _run_consensus_step(steps_log, config)
            else:
                raise PipelineError(f"Неизвестный тип шага: {step_type}")

            entry["duration_ms"] = int((time.monotonic() - started) * 1000)
            steps_log.append(entry)

        run.status = "completed"
    except (PipelineError, ocr.OcrError) as err:
        run.status = "failed"
        run.error = str(err)

    run.steps_log = steps_log
    run.finished_at = utcnow()
    await db.commit()
