import asyncio
import json

import pytest

from app.llm.client import LlmResult
from app.models import ModelEntry, Provider
from app.services import grading
from app.services.grading_contract import GradingContractError, validate_grading_output


RUBRIC = [
    {"criterion_name": "Метод", "max_score": 3, "description": ""},
    {"criterion_name": "Расчёты", "max_score": 2, "description": ""},
]


def grade_output() -> dict:
    return {
        "unreadable": False,
        "unreadable_reason": None,
        "total_score": 4,
        "max_score": 5,
        "criteria_scores": [
            {"criterion_name": "Метод", "score": 3, "max_score": 3, "comment": ""},
            {"criterion_name": "Расчёты", "score": 1, "max_score": 2, "comment": ""},
        ],
        "confidence": 0.9,
        "needs_teacher_review": False,
    }


def test_valid_output_preserves_a_consistent_grade() -> None:
    output = grade_output()
    validated = validate_grading_output(output, RUBRIC, 5)
    assert validated == output
    assert validated is not output


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda value: value["criteria_scores"].pop(), "пропущены"),
        (
            lambda value: value["criteria_scores"].append(
                {"criterion_name": "Оформление", "score": 0, "max_score": 0, "comment": ""}
            ),
            "лишние",
        ),
        (
            lambda value: value["criteria_scores"].append(value["criteria_scores"][0].copy()),
            "повторно",
        ),
    ],
)
def test_rejects_a_changed_criterion_set(change, message: str) -> None:
    output = grade_output()
    change(output)
    with pytest.raises(GradingContractError, match=message):
        validate_grading_output(output, RUBRIC, 5)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda value: value.update(max_score=10), "max_score ответа изменён"),
        (lambda value: value["criteria_scores"][0].update(max_score=4), "максимум критерия"),
        (lambda value: value["criteria_scores"][1].update(score=3), "вне диапазона"),
        (lambda value: value.update(total_score=3), "не равен сумме"),
    ],
)
def test_rejects_score_integrity_violations(change, message: str) -> None:
    output = grade_output()
    change(output)
    with pytest.raises(GradingContractError, match=message):
        validate_grading_output(output, RUBRIC, 5)


@pytest.mark.parametrize("field", ["confidence", "needs_teacher_review", "unreadable"])
def test_rejects_missing_review_contract_fields(field: str) -> None:
    output = grade_output()
    output.pop(field)
    with pytest.raises(GradingContractError, match=field):
        validate_grading_output(output, RUBRIC, 5)


def test_low_confidence_is_deterministically_routed_to_teacher_review() -> None:
    output = grade_output()
    output["confidence"] = 0.55
    validated = validate_grading_output(output, RUBRIC, 5)
    assert validated["needs_teacher_review"] is True
    assert "уверенность модели" in validated["contract_review_reasons"][0]


def test_unreadable_work_requires_reason_and_teacher_review() -> None:
    output = grade_output()
    output.update(unreadable=True, unreadable_reason="Фрагмент листа обрезан")
    validated = validate_grading_output(output, RUBRIC, 5)
    assert validated["needs_teacher_review"] is True


def test_rejects_inconsistent_source_rubric() -> None:
    output = grade_output()
    output["max_score"] = 10
    with pytest.raises(GradingContractError, match="исходная рубрика неконсистентна"):
        validate_grading_output(output, RUBRIC, 10)


def test_empty_rubric_never_yields_an_automatic_grade() -> None:
    with pytest.raises(GradingContractError, match="rubric_not_configured"):
        validate_grading_output(grade_output(), [], 5)


def test_run_grading_never_completes_with_a_contract_violation(monkeypatch) -> None:
    invalid = grade_output()
    invalid["total_score"] = 5

    async def fake_chat(*args, **kwargs) -> LlmResult:
        return LlmResult(text=json.dumps(invalid), duration_ms=17, tokens_total=23)

    monkeypatch.setattr(grading.llm, "chat", fake_chat)
    outcome = asyncio.run(
        grading.run_grading(
            Provider(name="test", base_url="https://example.invalid"),
            ModelEntry(model_id="test-model", provider_id="provider"),
            "system",
            "task",
            "solution",
            RUBRIC,
            5,
            "student work",
        )
    )

    assert outcome.output == invalid
    assert "не прошёл контракт проверки" in outcome.error
