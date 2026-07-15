import math
import mimetypes
import re
import time
from copy import deepcopy
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
GRADE_ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
GRADE_ROLE_LABELS = {
    "primary": "Основная проверка",
    "auditor": "Независимый аудит",
}


class PipelineError(Exception):
    pass


@dataclass(frozen=True)
class GradeStepPlan:
    provider: Provider
    model: ModelEntry
    prompt: PromptVersion
    model_use: ModelUse
    role: str = "primary"
    role_label: str = GRADE_ROLE_LABELS["primary"]


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


def _legacy_grade_role(grade_index: int) -> str:
    if grade_index == 0:
        return "primary"
    if grade_index == 1:
        return "auditor"
    return f"reviewer_{grade_index + 1}"


def grade_role_label(role: str) -> str:
    if role in GRADE_ROLE_LABELS:
        return GRADE_ROLE_LABELS[role]
    reviewer = re.fullmatch(r"reviewer_(\d+)", role)
    if reviewer:
        return f"Дополнительная проверка {reviewer.group(1)}"
    return role.replace("_", " ").capitalize()


def _grade_role_instruction(role: str) -> str:
    label = grade_role_label(role)
    if role == "primary":
        instruction = (
            "Решите задачу и оцените работу с нуля по рубрике. Не предполагайте, какой балл "
            "должен получиться, и обоснуйте каждое снятие баллов конкретным фрагментом решения."
        )
    elif role == "auditor":
        instruction = (
            "Проведите независимый аудит с нуля: перепроверьте химическую модель, уравнения, "
            "размерности, численные расчёты и применение каждого критерия. Не пытайтесь угадать "
            "или усреднить вердикт основного проверяющего."
        )
    else:
        instruction = (
            "Проведите независимую проверку с нуля и фиксируйте только выводы, которые следуют "
            "из работы студента, условия и рубрики."
        )
    return f"Роль в независимой проверке: {label}.\n{instruction}"


def _system_prompt_for_grade_role(system_prompt: str, role: str) -> str:
    return f"{system_prompt.rstrip()}\n\n---\n{_grade_role_instruction(role)}"


def validate_pipeline_steps(steps: list) -> list[dict]:
    if not isinstance(steps, list) or not steps:
        raise PipelineError("В пайплайне должен быть хотя бы один шаг проверки LLM")

    normalized_steps = deepcopy(steps)
    grade_count = 0
    ocr_count = 0
    consensus_seen = False
    allowed_config = {
        "ocr": set(),
        "grade": {"model_entry_id", "prompt_version_id", "temperature", "role"},
        "consensus": {"disagreement_threshold_pct"},
    }
    for index, step in enumerate(normalized_steps):
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
            raw_role = config.get("role")
            if raw_role is None:
                role = _legacy_grade_role(grade_count)
            elif not isinstance(raw_role, str) or not raw_role.strip():
                raise PipelineError(f"{label}: role должен быть непустой строкой")
            else:
                role = raw_role.strip().casefold()
            if not GRADE_ROLE_PATTERN.fullmatch(role):
                raise PipelineError(
                    f"{label}: role должен начинаться с латинской буквы и содержать только a-z, 0-9 и _"
                )
            config["role"] = role
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
        grader_roles = [step["config"]["role"] for step in normalized_steps if step["type"] == "grade"]
        if len(set(grader_roles)) != len(grader_roles):
            raise PipelineError("Для консенсуса назначьте каждой проверке уникальную роль")
    return normalized_steps


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
    steps = validate_pipeline_steps(pipeline.steps)
    if not isinstance(run_input, dict):
        raise PipelineError("Входные данные пайплайна должны быть объектом")
    if not isinstance(run_input.get("task_text"), str) or not run_input["task_text"].strip():
        raise PipelineError("Не задано условие задачи")
    try:
        validate_grading_request(run_input.get("rubric"), run_input.get("max_score"))
    except GradingContractError as err:
        raise PipelineError(f"Проверка не запущена: {err}") from err
    _validate_ocr_input(steps, run_input)

    assistant = (await db.execute(select(Assistant).where(Assistant.id == pipeline.assistant_id))).scalar_one_or_none()
    if assistant is None:
        raise PipelineError("Дисциплина пайплайна не найдена")

    grades: dict[int, GradeStepPlan] = {}
    for index, step in enumerate(steps):
        if step["type"] != "grade":
            continue
        config = step["config"]
        provider, model = await _resolve_model(db, config["model_entry_id"])
        try:
            model_use = require_decision_model(model)
        except ModelUsePolicyError as err:
            raise PipelineError(f"Шаг {index + 1}: {err}") from err
        prompt = await _resolve_grader_prompt(db, pipeline.assistant_id, config.get("prompt_version_id"))
        role = config["role"]
        grades[index] = GradeStepPlan(
            provider=provider,
            model=model,
            prompt=prompt,
            model_use=model_use,
            role=role,
            role_label=grade_role_label(role),
        )
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


def _consensus_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PipelineError(f"{field}: проверяющий не вернул числовое значение")
    number = float(value)
    if not math.isfinite(number):
        raise PipelineError(f"{field}: проверяющий вернул неконечное число")
    return number


def _score_spread_pct(spread: float, maximum: float) -> float:
    if maximum > 0:
        return spread / maximum * 100
    return 0.0 if math.isclose(spread, 0.0, rel_tol=0.0, abs_tol=1e-9) else 100.0


def _requests_substantive_review(result: dict) -> bool:
    if not result.get("needs_teacher_review"):
        return False
    if result.get("unreadable"):
        return True
    contract_reasons = result.get("contract_review_reasons")
    if not isinstance(contract_reasons, list) or not contract_reasons:
        return True
    concrete_reasons = [
        reason
        for reason in contract_reasons
        if not (isinstance(reason, str) and "уверенность модели" in reason.casefold())
    ]
    return bool(concrete_reasons)


def _run_consensus_step(steps_log: list[dict], config: dict) -> dict:
    grades = _collect_grades(steps_log)
    if len(grades) < 2:
        raise PipelineError("Для консенсуса нужно минимум два успешных шага «Проверка»")

    threshold_pct = float(config.get("disagreement_threshold_pct", 20))
    scores: list[dict] = []
    criterion_vectors: list[dict[str, tuple[float, float]]] = []
    seen_roles: set[str] = set()
    criterion_order: list[str] | None = None
    for index, grade_output in enumerate(grades):
        result = grade_output["result"]
        role = str(grade_output.get("role") or _legacy_grade_role(index))
        if role in seen_roles:
            raise PipelineError("Консенсус получил несколько результатов одной роли")
        seen_roles.add(role)
        role_label = str(grade_output.get("role_label") or grade_role_label(role))
        total_score = _consensus_number(result.get("total_score"), f"{role_label}.total_score")
        max_score = _consensus_number(result.get("max_score"), f"{role_label}.max_score")

        criteria = result.get("criteria_scores")
        if not isinstance(criteria, list) or not criteria:
            raise PipelineError(f"{role_label}: нет вектора баллов по критериям")
        vector: dict[str, tuple[float, float]] = {}
        ordered_names: list[str] = []
        public_vector: list[dict] = []
        for criterion_index, criterion in enumerate(criteria):
            if not isinstance(criterion, dict):
                raise PipelineError(f"{role_label}.criteria_scores[{criterion_index}]: ожидался объект")
            name = criterion.get("criterion_name")
            if not isinstance(name, str) or not name.strip():
                raise PipelineError(f"{role_label}.criteria_scores[{criterion_index}]: не задано название")
            name = name.strip()
            if name in vector:
                raise PipelineError(f"{role_label}: критерий «{name}» возвращён повторно")
            score = _consensus_number(criterion.get("score"), f"{role_label}.{name}.score")
            criterion_max = _consensus_number(criterion.get("max_score"), f"{role_label}.{name}.max_score")
            vector[name] = (score, criterion_max)
            ordered_names.append(name)
            public_vector.append({"criterion_name": name, "score": score, "max_score": criterion_max})

        if criterion_order is None:
            criterion_order = ordered_names
        elif set(criterion_order) != set(ordered_names):
            raise PipelineError("Проверяющие вернули разные наборы критериев; консенсус невозможен")
        criterion_vectors.append(vector)
        scores.append(
            {
                "role": role,
                "role_label": role_label,
                "model": grade_output.get("model", ""),
                "total_score": total_score,
                "max_score": max_score,
                "criteria_scores": public_vector,
            }
        )

    max_scores = [score["max_score"] for score in scores]
    if any(not math.isclose(value, max_scores[0], rel_tol=0.0, abs_tol=1e-6) for value in max_scores[1:]):
        raise PipelineError("Проверяющие вернули разные максимальные баллы; консенсус невозможен")
    totals = [score["total_score"] for score in scores]
    average_score = sum(totals) / len(totals)
    total_spread = max(totals) - min(totals)
    total_spread_pct = _score_spread_pct(total_spread, max_scores[0])

    criterion_comparison: list[dict] = []
    review_reasons: list[str] = []
    if total_spread_pct > threshold_pct:
        review_reasons.append(f"Итоговые баллы расходятся на {total_spread_pct:.1f}% при пороге {threshold_pct:g}%")
    for name in criterion_order or []:
        maxima = [vector[name][1] for vector in criterion_vectors]
        if any(not math.isclose(value, maxima[0], rel_tol=0.0, abs_tol=1e-6) for value in maxima[1:]):
            raise PipelineError(f"Проверяющие вернули разные максимумы критерия «{name}»")
        values = [vector[name][0] for vector in criterion_vectors]
        spread = max(values) - min(values)
        spread_pct = _score_spread_pct(spread, maxima[0])
        disagreement = spread_pct > threshold_pct
        role_scores = [
            {"role": score["role"], "role_label": score["role_label"], "score": value}
            for score, value in zip(scores, values, strict=True)
        ]
        criterion_comparison.append(
            {
                "criterion_name": name,
                "max_score": maxima[0],
                "scores": role_scores,
                "average_score": round(sum(values) / len(values), 2),
                "spread": round(spread, 2),
                "spread_pct": round(spread_pct, 1),
                "disagreement": disagreement,
            }
        )
        if disagreement:
            review_reasons.append(
                f"По критерию «{name}» баллы расходятся на {spread_pct:.1f}% при пороге {threshold_pct:g}%"
            )

    for grade_output, score in zip(grades, scores, strict=True):
        if _requests_substantive_review(grade_output["result"]):
            review_reasons.append(f"{score['role_label']}: требуется разбор преподавателя")

    return {
        "scores": scores,
        "criterion_comparison": criterion_comparison,
        "average_score": round(average_score, 2),
        # Keep the original keys for existing clients while exposing their precise meaning.
        "spread": round(total_spread, 2),
        "spread_pct": round(total_spread_pct, 1),
        "total_spread": round(total_spread, 2),
        "total_spread_pct": round(total_spread_pct, 1),
        "disagreement_threshold_pct": threshold_pct,
        "review_reasons": review_reasons,
        "needs_teacher_review": bool(review_reasons),
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
        steps = validate_pipeline_steps(pipeline.steps)
        for index, step in enumerate(steps):
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
                entry["role"] = grade_plan.role
                entry["role_label"] = grade_plan.role_label
                if grounding is None:
                    grounding = await build_grounding_block(
                        db, pipeline.assistant_id, query=str(run_input.get("task_text", ""))[:200]
                    )
                outcome = await grading.run_grading(
                    provider,
                    model,
                    _system_prompt_for_grade_role(prompt.system_prompt, grade_plan.role),
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
                    entry["output"] = {
                        "error": outcome.error,
                        "model": f"{provider.name}/{model.model_id}",
                        "role": grade_plan.role,
                        "role_label": grade_plan.role_label,
                    }
                    failed_grades.append(f"шаг {index + 1}: {outcome.error}")
                else:
                    entry["output"] = {
                        "result": outcome.output,
                        "model": f"{provider.name}/{model.model_id}",
                        "role": grade_plan.role,
                        "role_label": grade_plan.role_label,
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
