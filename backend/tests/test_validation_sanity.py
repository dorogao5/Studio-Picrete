import math

import pytest

from app.services.validation import sanity_check


def _task(**overrides: object) -> dict:
    task = {
        "statement": "Рассчитайте количество вещества по приведённым в условии данным.",
        "reference_solution": "Используем материальный баланс и получаем полный ответ.",
        "answer": "0.100 mol",
        "max_score": 10,
        "rubric": [{"criterion_name": "Материальный баланс", "max_score": 10}],
    }
    task.update(overrides)
    return task


def test_sanity_accepts_well_formed_positive_rubric() -> None:
    assert sanity_check(_task())["issues"] == []


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, "not-a-number", None])
def test_sanity_rejects_nonpositive_or_nonfinite_max_score(value: object) -> None:
    issues = sanity_check(_task(max_score=value))["issues"]
    assert any("Максимальный балл" in issue for issue in issues)


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, "bad", None])
def test_sanity_rejects_nonpositive_or_nonfinite_criterion_score(value: object) -> None:
    issues = sanity_check(
        _task(rubric=[{"criterion_name": "Материальный баланс", "max_score": value}])
    )["issues"]
    assert any("Балл критерия" in issue for issue in issues)


def test_sanity_rejects_malformed_unnamed_and_duplicate_criteria() -> None:
    issues = sanity_check(
        _task(
            max_score=3,
            rubric=[
                "не объект",
                {"criterion_name": "", "max_score": 1},
                {"criterion_name": "Баланс", "max_score": 1},
                {"criterion_name": "баланс", "max_score": 1},
            ],
        )
    )["issues"]

    assert any("должен быть объектом" in issue for issue in issues)
    assert any("не задано название" in issue for issue in issues)
    assert any("повторяется" in issue for issue in issues)
