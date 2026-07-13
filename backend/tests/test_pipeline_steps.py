import pytest
from fastapi import HTTPException

from app.api.pipelines import _validate_steps


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
    _validate_steps(
        [
            {"type": "ocr", "config": {}},
            {"type": "grade", "config": {"model_entry_id": "pro-a"}},
            {"type": "grade", "config": {"model_entry_id": "pro-b"}},
            {"type": "consensus", "config": {}},
        ]
    )


def test_consensus_requires_distinct_graders() -> None:
    with pytest.raises(HTTPException, match="разные модели"):
        _validate_steps(
            [
                {"type": "grade", "config": {"model_entry_id": "pro"}},
                {"type": "grade", "config": {"model_entry_id": "pro"}},
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
    ],
)
def test_rejects_structurally_invalid_steps(steps: list, message: str) -> None:
    with pytest.raises(HTTPException, match=message):
        _validate_steps(steps)
