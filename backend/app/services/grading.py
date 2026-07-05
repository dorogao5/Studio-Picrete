import json
from dataclasses import dataclass

from app.llm import client as llm
from app.models import ModelEntry, Provider


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
) -> str:
    rubric_json = json.dumps(rubric, ensure_ascii=False, indent=2) if rubric else "(рубрика не задана)"
    return f"""Задача:
{task_text}

Эталонное решение:
{reference_solution or "(не задано — проверяйте по собственному решению, посчитанному самостоятельно)"}

Критерии оценивания (максимум {max_score} баллов):
{rubric_json}

OCR-расшифровка решения студента:
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
    temperature: float = 0.1,
) -> GradeOutcome:
    user_message = build_grading_user_message(task_text, reference_solution, rubric, max_score, ocr_text)
    try:
        result = await llm.chat(provider, model, system_prompt, user_message, temperature=temperature, json_mode=True)
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
    return GradeOutcome(
        output=parsed,
        raw_text=result.text,
        duration_ms=result.duration_ms,
        tokens_total=result.tokens_total,
    )
