"""The deliberately small admission path for the physical-chemistry trainer.

Physical chemistry tasks are not sent through the generic chemistry-facts,
classifier and three-agent critic chain.  Qwen creates the task; one
independent DeepSeek verifier checks it and, when possible, returns a complete
repair of the same task.  The caller applies that repair to the existing row
instead of buying a replacement generation.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm import client as llm
from app.models import Assistant, GeneratedTask, ModelEntry, Provider
from app.services.model_policy import current_model_use_policy
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
    values = (assistant.discipline, assistant.name)
    normalized = " ".join(str(value or "").casefold() for value in values)
    return "физичес" in normalized and "хим" in normalized


def _task_payload(task: GeneratedTask) -> dict[str, Any]:
    grounding = task.grounding if isinstance(task.grounding, dict) else {}
    return {
        "statement": task.statement,
        "reference_solution": task.reference_solution,
        "answer": task.answer,
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
    if not all(str(result.get(key) or "").strip() for key in ("statement", "reference_solution", "answer")):
        return None
    if not isinstance(result.get("rubric"), list) or not isinstance(result.get("data_used"), list):
        return None
    result["chemistry_facts"] = {}
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
    prompt_context = {
        "discipline_profile": discipline_context,
        "canonical_grounding": grounding,
        "task": original,
    }
    reasons: list[str] = []
    correction: dict[str, Any] | None = None
    verifier_report: dict[str, Any] = {"status": "error", "comparison": {"verdict": "uncertain"}}
    model_use = current_model_use_policy().classify(model)
    try:
        result = await llm.chat(
            provider,
            model,
            PHYSICAL_CHEMISTRY_VERIFIER_PROMPT,
            json.dumps(prompt_context, ensure_ascii=False),
            temperature=0.1,
            json_mode=True,
            thinking="enabled",
        )
        response = llm.extract_json(result.text)
    except llm.LlmError as error:
        reasons.append(f"Верификатор не завершил проверку: {error}")
        response = {}

    if isinstance(response, dict):
        issues = [str(issue).strip() for issue in response.get("issues") or [] if str(issue).strip()]
        verdict = str(response.get("verdict") or "fail").strip().casefold()
        correction = _normalized_correction(response.get("corrected_task"), original)
        if verdict == "pass":
            if issues:
                reasons.extend(issues)
            else:
                verifier_report = {
                    "status": "match",
                    "comparison": {"verdict": "match", "basis": "single_independent_verifier"},
                    "issues": [],
                    "solution": str((response.get("verification") or {}).get("solution") or ""),
                    "answer": str((response.get("verification") or {}).get("answer") or ""),
                    "solution_truncated": False,
                }
        elif correction is not None:
            verifier_report = {
                "status": "match",
                "comparison": {"verdict": "match", "basis": "single_independent_verifier_repair"},
                "issues": issues,
                "solution": str((response.get("verification") or {}).get("solution") or ""),
                "answer": str((response.get("verification") or {}).get("answer") or ""),
                "solution_truncated": False,
            }
        else:
            reasons.extend(issues or ["Верификатор обнаружил несогласованность, но не вернул безопасное исправление"])
            verifier_report = {
                "status": "mismatch",
                "comparison": {"verdict": "mismatch", "basis": "single_independent_verifier"},
                "issues": issues,
            }
    else:
        reasons.append("Верификатор вернул ответ не по JSON-контракту")

    validated = verifier_report.get("status") == "match" and not reasons
    if correction is not None and verifier_report.get("status") == "match":
        validated = True
    validation: dict[str, Any] = {
        "policy_version": PHYSICAL_CHEMISTRY_VALIDATION_POLICY_VERSION,
        "validation_config": config,
        "content_fingerprint": task_content_fingerprint(task, config),
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
            "applied": correction is not None,
            "issues": (verifier_report.get("issues") or []) if correction is not None else [],
        },
    }
    return validation, correction
