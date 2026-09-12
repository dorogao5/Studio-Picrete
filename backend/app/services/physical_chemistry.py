"""The deliberately small admission path for the physical-chemistry trainer.

Physical chemistry tasks are not sent through the generic chemistry-facts,
classifier and three-agent critic chain.  Qwen creates the task; one
independent DeepSeek verifier checks it and, when possible, returns a complete
repair of the same task.  The caller applies that repair to the existing row
instead of buying a replacement generation.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

from app.llm import client as llm
from app.config import get_settings
from app.models import Assistant, GeneratedTask, ModelEntry, Provider
from app.services.model_policy import current_model_use_policy
from app.services.contracts import ESSENTIAL_TOOLS_INSTRUCTION, PHYSICAL_VERIFIER_RESPONSE_SCHEMA
from app.services.task_evidence import normalize_validation_config, task_content_fingerprint

PHYSICAL_CHEMISTRY_VALIDATION_POLICY_VERSION = "physchem-single-verifier-v1"

PHYSICAL_CHEMISTRY_VERIFIER_PROMPT = r"""Вы — независимый верификатор задач по физической химии.
Проверяйте задачу с нуля, не доверяя эталонному решению. Это единственная автоматическая
проверка физхимической задачи: не вызывайте и не имитируйте классификатор, второй решатель
или предметного критика. Проверьте самодостаточность условия, выбор физической модели,
вывод формул, размерности, знаки, арифметику, химическую осмысленность и соответствие
всех подпунктов эталонному ответу. Эквивалентные алгебраические записи и нормальное
округление не являются ошибками.

Канонические правила курса:
- Используйте обозначения и порядок вывода из приложенного контекста курса.
- Для формальной кинетики используйте v или v_0, k, [A], [B] и явно указывайте порядок.
- Для Аррениуса используйте k(T)=A exp(-E_a/(RT)), ln k=ln A-E_a/(RT), температуры только в K.
- Для последовательной реакции используйте A -> B -> P и k_1, k_2; не подменяйте её одной стадией.
- Для теории столкновений различайте мономолекулярное разложение (k в с^-1) и бимолекулярную
  газовую реакцию. Зависимость частоты столкновений T^(1/2) нельзя выдавать как объяснение
  мономолекулярной кинетики без этого ограничения.
- Для Михаэлиса–Ментен используйте r=r_max[S]/(K_M+[S]), r_max=k_2[E]_0 и
  K_M=(k_2+k_-1)/k_1; режимы [S] << K_M и [S] >> K_M называйте предельными только
  при действительно малом/большом отношении.

Если найдена ошибка, не отклоняйте задачу и не создавайте другой сюжет. Верните в corrected_task
полную исправленную версию того же задания: statement, reference_solution, answer, rubric,
max_score, difficulty, topic, data_used и chemistry_facts (для физхимии chemistry_facts={} ).
Меняйте только то, что нужно для исправления; все числа после изменения пересчитайте.
Если исправить безопасно нельзя, corrected_task должен быть null, а verdict — fail.

Ответьте строго одним JSON без markdown:
{
  "verdict": "pass" или "fail",
  "issues": ["конкретные проверяемые замечания"],
  "corrected_task": null или {"statement":"...","reference_solution":"...","answer":"...",
    "rubric":[],"max_score":10,"difficulty":"easy|medium|hard","topic":"...",
    "data_used":[],"chemistry_facts":{}},
  "verification": {"solution":"краткая независимая проверка по шагам", "answer":"..."}
}
"""


def is_physical_chemistry(assistant: Assistant) -> bool:
    values = (getattr(assistant, "discipline", ""), getattr(assistant, "name", ""))
    normalized = " ".join(str(value or "").casefold() for value in values)
    return "физичес" in normalized and "хим" in normalized


PHYSICAL_TOOLS_VERIFIER_PROMPT = """Вы — единственный независимый verifier задачи по физической химии.
Проверьте модель, вывод, единицы, вычисления и все подпункты, используя доступные инструменты.
На pass всегда верните полный verified_task той же задачи, даже если исправлений не требуется.
Сохраните все корректные абзацы исходного reference_solution, все подпункты, условие и данные;
исправляйте только необходимое. reference_solution — полный самодостаточный учебный эталон,
answer — чистые ответы на все вопросы. Не заменяйте задачу и не меняйте выбранную тему.
verification.solution и verification.answer — только диагностика, они не сохраняются как эталон.
Для бимолекулярных столкновений r=k[A][B], k=P A_coll(T)exp(-E_0/(RT)), A_exp=P A_coll(T).
Не подменяйте k предэкспонентой в законе скорости: r не равно A_exp[A][B] без барьерного множителя.
Если безопасно проверить или исправить нельзя, верните fail и verified_task=null.
Ответ строго JSON по схеме: verdict, issues, verified_task, verification.
verified_task содержит statement, reference_solution, answer, images, rubric, max_score,
difficulty, topic, data_used и chemistry_facts={}. Не возвращайте сокращённый объект или инструкции замены.
"""


def physical_verifier_prompt(system_prompt: str | None, *, essential_tools: bool = False) -> str:
    """Use the editable prompt verbatim; domain rules are fallback content only."""
    return system_prompt if system_prompt and system_prompt.strip() else (
        PHYSICAL_TOOLS_VERIFIER_PROMPT if essential_tools else PHYSICAL_CHEMISTRY_VERIFIER_PROMPT)


def physical_json_schema_enabled(assistant: Assistant | None, provider: Provider | None, model: ModelEntry) -> bool:
    return (
        getattr(model, "model_id", None) in {
            value.strip() for value in get_settings().json_schema_model_ids.split(",") if value.strip()
        }
        and assistant is not None and is_physical_chemistry(assistant)
        and getattr(provider, "kind", "") == "yandex"
        and getattr(model, "family", "") == "qwen"
        and getattr(model, "supports_json", False) is True
    )


def task_verifier_model_id(assistant: Assistant) -> str | None:
    if is_physical_chemistry(assistant):
        return getattr(assistant, "verifier_model_id", None)
    return getattr(assistant, "verifier_model_id", None) or getattr(assistant, "default_grader_model_id", None)


def assistant_tools_enabled(assistant, role):
    return getattr(assistant, f"{role}_tools_enabled", False) is True


def student_grading_model_use(assistant: Assistant, model: ModelEntry):
    use = current_model_use_policy().classify(model)
    if (is_physical_chemistry(assistant) and model.family == "qwen" and use.explicitly_configured):
        # Permission applies to student grading only, never task admission.
        return replace(use, tier="decision", decision_capable=True,
                       reason="Qwen разрешён для проверки работ студентов по физической химии")
    return use


def _task_payload(task: GeneratedTask) -> dict[str, Any]:
    grounding = task.grounding if isinstance(task.grounding, dict) else {}
    return {
        "statement": task.statement,
        "reference_solution": task.reference_solution,
        "answer": task.answer,
        "images": list(task.images or []),
        "rubric": task.rubric or [],
        "max_score": task.max_score,
        "difficulty": task.difficulty,
        "topic": task.topic,
        "data_used": grounding.get("data_used") or [],
        "chemistry_facts": {},
    }


def _normalized_correction(candidate: object, original: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    result = dict(candidate)
    if "reference_solution" not in result and result.get("solution"):
        result["reference_solution"] = result["solution"]
    for key in ("rubric", "max_score", "difficulty", "topic", "data_used", "chemistry_facts"):
        if key not in result:
            result[key] = original[key]
    if not all(isinstance(result.get(key), str) and result[key].strip()
               for key in ("statement", "reference_solution", "answer")):
        return None
    if not isinstance(result.get("rubric"), list) or not isinstance(result.get("data_used"), list):
        return None
    result["chemistry_facts"] = {}
    # Grouping metadata belongs to the selected task, not the verifier's wording.
    result["topic"] = original["topic"]
    return result


async def run_physical_validation(
    *,
    task: GeneratedTask,
    provider: Provider,
    model: ModelEntry,
    grounding: str,
    discipline_context: str,
    answer_format: str = "numeric",
    tolerance_pct: float = 2.0,
    system_prompt: str | None = None,
    essential_tools: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    config = normalize_validation_config(
        {
            "answer_format": answer_format or "numeric",
            "tolerance_pct": tolerance_pct,
            "validation_solver": False,
            "validation_data_check": False,
            "task_kind": "calculation",
            "chemistry_check": "off",
        }
    )
    original = _task_payload(task)
    fingerprint = task_content_fingerprint(task, config)
    prompt_context = {
        "discipline_profile": discipline_context,
        "canonical_grounding": grounding,
        "task": original,
    }
    reasons: list[str] = []
    correction: dict[str, Any] | None = None
    verifier_report: dict[str, Any] = {"status": "error", "comparison": {"verdict": "uncertain"}}
    model_use = current_model_use_policy().classify(model)
    calculation_audit = None
    try:
        result = await llm.chat(
            provider,
            model,
            physical_verifier_prompt(system_prompt, essential_tools=essential_tools) + (ESSENTIAL_TOOLS_INSTRUCTION if essential_tools else ""),
            json.dumps(prompt_context, ensure_ascii=False),
            temperature=0.1,
            json_mode=True,
            thinking="enabled",
            **({"essential_tools": True, "initial_tool_choice": "required",
                "response_schema": PHYSICAL_VERIFIER_RESPONSE_SCHEMA}
               if essential_tools else {}),
            **({"reasoning_effort": "high"}
               if getattr(provider, "kind", "") == "yandex" and getattr(model, "family", "") == "deepseek" else {}),
        )
        calculation_audit = result.raw if essential_tools else None
        response = llm.extract_json(result.text)
    except llm.LlmError as error:
        if essential_tools:
            calculation_audit = error.raw
        reasons.append(f"Верификатор не завершил проверку: {error}")
        response = {}

    if isinstance(response, dict):
        issues = [str(issue).strip() for issue in response.get("issues") or [] if str(issue).strip()]
        verdict = str(response.get("verdict") or "fail").strip().casefold()
        verification = response.get("verification")
        verification = verification if isinstance(verification, dict) else {}
        if essential_tools:
            candidate = response.get("verified_task")
            # Structural contract only: diagnostics never become a task patch.
            required = set(original) | {"images"}
            if (verdict == "pass" and isinstance(candidate, dict) and required <= candidate.keys()
                    and isinstance(candidate.get("images"), list)):
                correction = _normalized_correction(candidate, original)
        else:
            correction = _normalized_correction(response.get("corrected_task"), original)
        if correction is not None and verdict in {"pass", "fail"}:
            verifier_report = {
                "status": "match",
                "comparison": {"verdict": "match", "basis": "single_independent_verifier_repair"},
                "issues": issues,
                "solution": str(verification.get("solution") or ""),
                "answer": str(verification.get("answer") or ""),
                "solution_truncated": False,
            }
        elif not essential_tools and verdict == "pass" and response.get("corrected_task") is None:
            # The verdict is semantic; issues may contain harmless notes.
            verifier_report = {
                "status": "match",
                "comparison": {"verdict": "match", "basis": "single_independent_verifier"},
                "issues": issues,
                "solution": str(verification.get("solution") or ""),
                "answer": str(verification.get("answer") or ""),
                "solution_truncated": False,
            }
        else:
            if essential_tools and verdict == "pass":
                reasons.append("Верификатор не вернул полный verified_task по контракту")
            reasons.extend(issues or ["Верификатор обнаружил несогласованность, но не вернул безопасное исправление"])
            verifier_report = {
                "status": "mismatch",
                "comparison": {"verdict": "mismatch", "basis": "single_independent_verifier"},
                "issues": issues,
            }
    else:
        reasons.append("Верификатор вернул ответ не по JSON-контракту")

    validated = verifier_report.get("status") == "match" and not reasons
    if not validated:
        correction = None
    validation: dict[str, Any] = {
        "policy_version": PHYSICAL_CHEMISTRY_VALIDATION_POLICY_VERSION,
        "validation_config": config,
        "content_fingerprint": fingerprint,
        "model_policy": model_use.as_dict(),
        "solver": {"status": "skipped", "reason": "Физхимия: используется один независимый verifier"},
        "verifier": verifier_report,
        "cross_comparison": {"verdict": "skipped", "reason": "Не используется в физхимии"},
        "critic": {"status": "skipped", "reason": "Не используется в физхимии"},
        "chemistry": {"validation_version": "not_applicable", "admission_effect": "not_applicable"},
        "reference_solution_check": {"verdict": "match", "basis": "single_independent_verifier"}
        if validated
        else {"verdict": "uncertain"},
        "data": {"status": "skipped", "unknown_numbers": [], "unknown_sources": []},
        "source_lineage": {"status": "skipped", "unbound_sources": []},
        "sanity": {"issues": []},
        "dedup": {"duplicate": False},
        "answer_format": config["answer_format"],
        "verdict": "validated" if validated else "needs_review",
        "reasons": reasons,
        "correction": {
            "applied": False,
            "issues": (verifier_report.get("issues") or []) if correction is not None else [],
            **({"before": deepcopy(original)} if correction is not None and correction != original else {}),
        },
    }
    if calculation_audit is not None:
        validation["calculation_audit"] = calculation_audit
    return validation, correction
