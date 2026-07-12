import json
from dataclasses import dataclass

from app.llm import client as llm
from app.models import Assistant, ModelEntry, Provider
from app.services.assistant_profile import with_assistant_profile
from app.services.grading_contract import GradingContractError, validate_grading_output


@dataclass
class GradeOutcome:
    output: dict | None
    raw_text: str
    duration_ms: int
    tokens_total: int | None
    error: str = ""


def build_grading_user_message(
    task_text: str,
    reference_solution: str,
    rubric: list,
    max_score: float,
    ocr_text: str,
    grounding: str = "",
) -> str:
    rubric_json = json.dumps(rubric, ensure_ascii=False, indent=2) if rubric else "(рубрика не задана)"
    grounding_block = (
        f"Справочные материалы курса (используйте ТОЛЬКО эти данные):\n{grounding}\n\n" if grounding else ""
    )
    return f"""Задача:
{task_text}

Эталонное решение:
{reference_solution or "(не задано — проверяйте по собственному решению, посчитанному самостоятельно)"}

Критерии оценивания (максимум {max_score} баллов):
{rubric_json}

{grounding_block}OCR-расшифровка решения студента:
{ocr_text}

Выполните проверку строго по критериям. Ответ — строго JSON по контракту из инструкции."""


async def run_grading(
    provider: Provider,
    model: ModelEntry,
    system_prompt: str,
    task_text: str,
    reference_solution: str,
    rubric: list,
    max_score: float,
    ocr_text: str,
    grounding: str = "",
    temperature: float = 0.1,
    assistant: Assistant | None = None,
) -> GradeOutcome:
    user_message = build_grading_user_message(
        task_text, reference_solution, rubric, max_score, ocr_text, grounding=grounding
    )
    try:
        result = await llm.chat(
            provider,
            model,
            with_assistant_profile(system_prompt, assistant),
            user_message,
            temperature=temperature,
            json_mode=True,
        )
    except llm.LlmError as err:
        return GradeOutcome(output=None, raw_text="", duration_ms=0, tokens_total=None, error=str(err))
    try:
        parsed = llm.extract_json(result.text)
    except llm.LlmError as err:
        return GradeOutcome(
            output=None,
            raw_text=result.text,
            duration_ms=result.duration_ms,
            tokens_total=result.tokens_total,
            error=str(err),
        )
    try:
        validated = validate_grading_output(parsed, rubric, max_score)
    except GradingContractError as err:
        return GradeOutcome(
            output=parsed if isinstance(parsed, dict) else None,
            raw_text=result.text,
            duration_ms=result.duration_ms,
            tokens_total=result.tokens_total,
            error=f"Ответ модели не прошёл контракт проверки: {err}",
        )
    return GradeOutcome(
        output=validated,
        raw_text=result.text,
        duration_ms=result.duration_ms,
        tokens_total=result.tokens_total,
    )
