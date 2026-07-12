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
