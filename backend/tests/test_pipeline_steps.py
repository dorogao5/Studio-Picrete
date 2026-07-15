import pytest
from fastapi import HTTPException

from app.api.pipelines import _validate_steps
from app.services.pipeline import _run_consensus_step


def test_consensus_requires_two_graders() -> None:
    with pytest.raises(HTTPException, match="минимум две проверки"):
        _validate_steps(
            [
                {"type": "ocr", "config": {}},
                {"type": "grade", "config": {"model_entry_id": "pro"}},
                {"type": "consensus", "config": {}},
            ]
        )


def test_valid_single_grader_pipeline_does_not_fake_consensus() -> None:
    _validate_steps(
        [
            {"type": "ocr", "config": {}},
            {"type": "grade", "config": {"model_entry_id": "pro"}},
        ]
    )


def test_valid_consensus_has_two_preceding_graders_and_is_last() -> None:
    normalized = _validate_steps(
        [
            {"type": "ocr", "config": {}},
            {"type": "grade", "config": {"model_entry_id": "pro"}},
            {"type": "grade", "config": {"model_entry_id": "pro"}},
            {"type": "consensus", "config": {}},
        ]
    )
    assert [step["config"]["role"] for step in normalized if step["type"] == "grade"] == [
        "primary",
        "auditor",
    ]


def test_consensus_requires_distinct_roles_not_distinct_models() -> None:
    with pytest.raises(HTTPException, match="уникальную роль"):
        _validate_steps(
            [
                {"type": "grade", "config": {"model_entry_id": "pro-a", "role": "auditor"}},
                {"type": "grade", "config": {"model_entry_id": "pro-b", "role": "auditor"}},
                {"type": "consensus", "config": {}},
            ]
        )


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (None, "хотя бы один шаг"),
        ([], "хотя бы один шаг"),
        ([{"type": "grade"}], "config должен быть объектом"),
        ([{"type": "grade", "config": "bad"}], "config должен быть объектом"),
        ([{"type": "grade", "config": {}}], "выберите модель"),
        (
            [{"type": "grade", "config": {"model_entry_id": "pro", "temperature": 2.1}}],
            "temperature",
        ),
        (
            [
                {"type": "grade", "config": {"model_entry_id": "pro-a"}},
                {"type": "grade", "config": {"model_entry_id": "pro-b"}},
                {"type": "consensus", "config": {"disagreement_threshold_pct": 101}},
            ],
            "disagreement_threshold_pct",
        ),
        (
            [{"type": "grade", "config": {"model_entry_id": "pro", "model_id": "typo"}}],
            "неизвестные параметры",
        ),
        (
            [{"type": "grade", "config": {"model_entry_id": "pro", "role": "Независимый аудитор"}}],
            "role должен начинаться",
        ),
    ],
)
def test_rejects_structurally_invalid_steps(steps: list, message: str) -> None:
    with pytest.raises(HTTPException, match=message):
        _validate_steps(steps)


def _grade_entry(
    role: str,
    method_score: float,
    calculation_score: float,
    *,
    needs_teacher_review: bool = False,
    contract_review_reasons: list[str] | None = None,
) -> dict:
    result = {
        "total_score": method_score + calculation_score,
        "max_score": 10,
        "criteria_scores": [
            {"criterion_name": "Метод", "score": method_score, "max_score": 5},
            {"criterion_name": "Расчёты", "score": calculation_score, "max_score": 5},
        ],
        "confidence": 0.1,
        "needs_teacher_review": needs_teacher_review,
        "unreadable": False,
    }
    if contract_review_reasons is not None:
        result["contract_review_reasons"] = contract_review_reasons
    return {
        "type": "grade",
        "status": "completed",
        "output": {
            "model": "DeepSeek/deepseek-v4-pro",
            "role": role,
            "role_label": "Основная проверка" if role == "primary" else "Независимый аудит",
            "result": result,
        },
    }


def test_consensus_detects_opposite_criterion_vectors_with_equal_totals() -> None:
    output = _run_consensus_step(
        [
            _grade_entry("primary", 5, 0),
            _grade_entry("auditor", 0, 5),
        ],
        {"disagreement_threshold_pct": 20},
    )

    assert output["spread"] == 0
    assert output["needs_teacher_review"] is True
    assert [criterion["spread_pct"] for criterion in output["criterion_comparison"]] == [100, 100]
    assert "confidence" not in output["scores"][0]
    assert output["scores"][1]["role_label"] == "Независимый аудит"


def test_consensus_does_not_use_model_self_confidence_as_a_decision_signal() -> None:
    confidence_only_reason = ["уверенность модели 0.10 ниже порога 0.80"]
    output = _run_consensus_step(
        [
            _grade_entry(
                "primary",
                4,
                4,
                needs_teacher_review=True,
                contract_review_reasons=confidence_only_reason,
            ),
            _grade_entry("auditor", 4, 4),
        ],
        {},
    )

    assert output["needs_teacher_review"] is False
    assert output["review_reasons"] == []


def test_consensus_preserves_a_concrete_review_request() -> None:
    output = _run_consensus_step(
        [
            _grade_entry("primary", 4, 4, needs_teacher_review=True),
            _grade_entry("auditor", 4, 4),
        ],
        {},
    )

    assert output["needs_teacher_review"] is True
    assert output["review_reasons"] == ["Основная проверка: требуется разбор преподавателя"]
