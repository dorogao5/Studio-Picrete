import math
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Assistant, ModelEntry, Pipeline, PipelineRun, PromptVersion, Provider, utcnow
from app.services import grading, ocr
from app.services.grading_contract import GradingContractError, validate_grading_request
from app.services.grounding import build_grounding_block
from app.services.model_policy import ModelUse, ModelUsePolicyError, require_decision_model

STEP_TYPES = ("ocr", "grade", "consensus")


class PipelineError(Exception):
    pass


@dataclass(frozen=True)
class GradeStepPlan:
    provider: Provider
    model: ModelEntry
    prompt: PromptVersion
    model_use: ModelUse


@dataclass(frozen=True)
class PipelinePlan:
    assistant: Assistant
    grades: dict[int, GradeStepPlan]


def _bounded_number(value: object, *, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PipelineError(f"{field}: ожидалось число от {minimum:g} до {maximum:g}")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise PipelineError(f"{field}: ожидалось число от {minimum:g} до {maximum:g}")
    return number


def validate_pipeline_steps(steps: list) -> None:
    if not isinstance(steps, list) or not steps:
        raise PipelineError("В пайплайне должен быть хотя бы один шаг проверки LLM")

    grade_count = 0
    ocr_count = 0
    consensus_seen = False
    allowed_config = {
        "ocr": set(),
        "grade": {"model_entry_id", "prompt_version_id", "temperature"},
        "consensus": {"disagreement_threshold_pct"},
    }
    for index, step in enumerate(steps):
        label = f"Шаг {index + 1}"
        if not isinstance(step, dict) or step.get("type") not in STEP_TYPES:
            raise PipelineError(f"{label}: type должен быть одним из {STEP_TYPES}")
        if "title" in step and not isinstance(step["title"], str):
            raise PipelineError(f"{label}: title должен быть строкой")
        config = step.get("config")
        if not isinstance(config, dict):
            raise PipelineError(f"{label}: config должен быть объектом")

        step_type = step["type"]
        unexpected = sorted(set(config) - allowed_config[step_type])
        if unexpected:
            raise PipelineError(f"{label}: неизвестные параметры config: {', '.join(unexpected)}")
        if consensus_seen:
            raise PipelineError("После консенсуса нельзя добавлять другие шаги")

        if step_type == "ocr":
            ocr_count += 1
            if ocr_count > 1 or grade_count > 0:
                raise PipelineError("Шаг OCR может быть только один и должен идти до проверок")
        elif step_type == "grade":
            model_id = config.get("model_entry_id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise PipelineError(f"{label}: выберите модель-проверщик")
            prompt_id = config.get("prompt_version_id")
            if prompt_id is not None and (not isinstance(prompt_id, str) or not prompt_id.strip()):
                raise PipelineError(f"{label}: prompt_version_id должен быть непустой строкой")
            if "temperature" in config:
                _bounded_number(config["temperature"], field=f"{label}.temperature", minimum=0, maximum=2)
            grade_count += 1
        elif step_type == "consensus":
            if grade_count < 2:
                raise PipelineError("Для консенсуса добавьте минимум две проверки LLM")
            if index != len(steps) - 1:
                raise PipelineError("Консенсус должен быть последним шагом")
            if "disagreement_threshold_pct" in config:
                _bounded_number(
                    config["disagreement_threshold_pct"],
                    field=f"{label}.disagreement_threshold_pct",
                    minimum=0,
                    maximum=100,
                )
            consensus_seen = True

    if grade_count == 0:
        raise PipelineError("В пайплайне должен быть хотя бы один шаг проверки LLM")
    if consensus_seen:
        grader_ids = [step["config"]["model_entry_id"] for step in steps if step["type"] == "grade"]
        if len(set(grader_ids)) != len(grader_ids):
            raise PipelineError("Для консенсуса выберите разные модели-проверщики")


async def _resolve_model(db: AsyncSession, model_entry_id: str) -> tuple[Provider, ModelEntry]:
    model = (await db.execute(select(ModelEntry).where(ModelEntry.id == model_entry_id))).scalar_one_or_none()
    if model is None:
        raise PipelineError(f"Модель {model_entry_id} не найдена")
    if not model.enabled:
        raise PipelineError(f"Модель {model.model_id} отключена")
    provider = (await db.execute(select(Provider).where(Provider.id == model.provider_id))).scalar_one_or_none()
    if provider is None or not provider.enabled:
        raise PipelineError(f"Провайдер модели {model.model_id} недоступен")
    if provider.purpose != "production":
        raise PipelineError(f"Провайдер модели {model.model_id} не предназначен для проверки работ")
    return provider, model


async def _resolve_grader_prompt(db: AsyncSession, assistant_id: str, prompt_version_id: str | None) -> PromptVersion:
    if prompt_version_id:
        prompt = (
            await db.execute(
                select(PromptVersion).where(
                    PromptVersion.id == prompt_version_id,
                    PromptVersion.assistant_id == assistant_id,
                    PromptVersion.role == "grader",
                )
            )
        ).scalar_one_or_none()
        if prompt is None:
            raise PipelineError(f"Промпт проверки {prompt_version_id} не найден у этой дисциплины")
        if prompt.assistant_id != assistant_id or prompt.role != "grader":
            raise PipelineError(f"Промпт проверки {prompt_version_id} не принадлежит этой дисциплине")
        return prompt
    prompt = (
        (
            await db.execute(
                select(PromptVersion)
                .where(
                    PromptVersion.assistant_id == assistant_id,
                    PromptVersion.role == "grader",
                    PromptVersion.status == "active",
                )
                .order_by(PromptVersion.version.desc())
            )
        )
        .scalars()
        .first()
    )
    if prompt is None:
        raise PipelineError("У ассистента нет активного промпта проверки — создайте его на вкладке «Промпты»")
    if prompt.assistant_id != assistant_id or prompt.role != "grader" or prompt.status != "active":
        raise PipelineError("Активный промпт проверки настроен некорректно")
    return prompt


def _validate_ocr_input(steps: list[dict], run_input: dict) -> None:
    ocr_text = run_input.get("ocr_text")
    if ocr_text is not None and not isinstance(ocr_text, str):
        raise PipelineError("ocr_text должен быть строкой")
    if isinstance(ocr_text, str) and ocr_text.strip():
        return

    has_ocr = any(step["type"] == "ocr" for step in steps)
    image_ids = run_input.get("image_ids")
    if not has_ocr:
        raise PipelineError("Нет OCR-текста: добавьте шаг OCR или передайте ocr_text")
    if not isinstance(image_ids, list) or not image_ids:
        raise PipelineError("Для шага OCR нужны изображения или готовый ocr_text")

    uploads_dir = get_settings().uploads_dir
    for index, image_id in enumerate(image_ids):
        if not isinstance(image_id, str) or not image_id.strip():
            raise PipelineError(f"image_ids[{index}]: ожидалось имя загруженного файла")
        path = uploads_dir / Path(image_id).name
        if not path.exists() or not path.is_file():
            raise PipelineError(f"Файл {image_id} не найден")


async def preflight_pipeline(db: AsyncSession, pipeline: Pipeline, run_input: dict) -> PipelinePlan:
    validate_pipeline_steps(pipeline.steps)
    if not isinstance(run_input, dict):
        raise PipelineError("Входные данные пайплайна должны быть объектом")
    if not isinstance(run_input.get("task_text"), str) or not run_input["task_text"].strip():
        raise PipelineError("Не задано условие задачи")
    try:
        validate_grading_request(run_input.get("rubric"), run_input.get("max_score"))
    except GradingContractError as err:
        raise PipelineError(f"Проверка не запущена: {err}") from err
    _validate_ocr_input(pipeline.steps, run_input)

    assistant = (await db.execute(select(Assistant).where(Assistant.id == pipeline.assistant_id))).scalar_one_or_none()
    if assistant is None:
        raise PipelineError("Дисциплина пайплайна не найдена")

    grades: dict[int, GradeStepPlan] = {}
    for index, step in enumerate(pipeline.steps):
        if step["type"] != "grade":
            continue
        config = step["config"]
        provider, model = await _resolve_model(db, config["model_entry_id"])
        try:
            model_use = require_decision_model(model)
        except ModelUsePolicyError as err:
            raise PipelineError(f"Шаг {index + 1}: {err}") from err
        prompt = await _resolve_grader_prompt(db, pipeline.assistant_id, config.get("prompt_version_id"))
        grades[index] = GradeStepPlan(provider=provider, model=model, prompt=prompt, model_use=model_use)
    return PipelinePlan(assistant=assistant, grades=grades)


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


async def execute_pipeline(
    db: AsyncSession, pipeline: Pipeline, run: PipelineRun, plan: PipelinePlan | None = None
) -> None:
    run_input = run.input
    steps_log: list[dict] = []
    ocr_text = run_input.get("ocr_text", "")
    grounding: str | None = None
    failed_grades: list[str] = []

    try:
        plan = plan or await preflight_pipeline(db, pipeline, run_input)
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
                grade_plan = plan.grades[index]
                provider, model, prompt = grade_plan.provider, grade_plan.model, grade_plan.prompt
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
                    assistant=plan.assistant,
                )
                if outcome.error:
                    entry["status"] = "failed"
                    entry["output"] = {"error": outcome.error, "model": f"{provider.name}/{model.model_id}"}
                    failed_grades.append(f"шаг {index + 1}: {outcome.error}")
                else:
                    entry["output"] = {
                        "result": outcome.output,
                        "model": f"{provider.name}/{model.model_id}",
                        "prompt_version": prompt.version,
                        "model_policy": grade_plan.model_use.as_dict(),
                        "tokens_total": outcome.tokens_total,
                    }
            elif step_type == "consensus":
                entry["output"] = _run_consensus_step(steps_log, config)
            else:
                raise PipelineError(f"Неизвестный тип шага: {step_type}")

            entry["duration_ms"] = int((time.monotonic() - started) * 1000)
            steps_log.append(entry)

        if failed_grades:
            run.status = "failed"
            run.error = "Не завершены шаги проверки: " + " | ".join(failed_grades)
        else:
            run.status = "completed"
            run.error = ""
    except (PipelineError, ocr.OcrError) as err:
        run.status = "failed"
        run.error = str(err)
    except Exception as err:  # unexpected failures must not leave a persisted run in "running"
        run.status = "failed"
        run.error = f"Внутренняя ошибка пайплайна ({type(err).__name__}): {err}"
    finally:
        run.steps_log = steps_log
        run.finished_at = utcnow()
        await db.commit()
