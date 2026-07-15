import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


SCORE_TOLERANCE = 1e-6


class GradingContractError(ValueError):
    """Raised when a model response cannot be used as a trustworthy grade."""


@dataclass(frozen=True)
class _ExpectedCriterion:
    name: str
    max_score: float


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GradingContractError(f"{field}: ожидалось конечное число")
    number = float(value)
    if not math.isfinite(number):
        raise GradingContractError(f"{field}: ожидалось конечное число")
    return number


def _same_score(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=SCORE_TOLERANCE)


def _expected_criteria(rubric: list) -> list[_ExpectedCriterion]:
    expected: list[_ExpectedCriterion] = []
    seen: set[str] = set()
    for index, criterion in enumerate(rubric):
        if not isinstance(criterion, dict):
            raise GradingContractError(f"rubric[{index}]: критерий должен быть объектом")
        raw_name = criterion.get("criterion_name", criterion.get("name"))
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise GradingContractError(f"rubric[{index}].criterion_name: не задано название")
        name = raw_name.strip()
        if name in seen:
            raise GradingContractError(f"rubric: критерий «{name}» задан повторно")
        maximum = _number(criterion.get("max_score"), f"rubric[{index}].max_score")
        if maximum < 0:
            raise GradingContractError(f"rubric[{index}].max_score: значение не может быть отрицательным")
        seen.add(name)
        expected.append(_ExpectedCriterion(name=name, max_score=maximum))
    return expected


def validate_grading_request(rubric: list, max_score: float) -> None:
    """Reject an unusable teacher rubric before spending a model request."""
    if not isinstance(rubric, list):
        raise GradingContractError("rubric: ожидался список критериев")
    expected_max = _number(max_score, "max_score задания")
    if expected_max < 0:
        raise GradingContractError("max_score задания: значение не может быть отрицательным")
    expected = _expected_criteria(rubric)
    if not expected:
        raise GradingContractError("rubric_not_configured: автоматическая оценка без рубрики запрещена")
    rubric_max = sum(criterion.max_score for criterion in expected)
    if not _same_score(rubric_max, expected_max):
        raise GradingContractError(
            f"исходная рубрика неконсистентна: сумма {rubric_max:g}, max_score {expected_max:g}"
        )


def validate_grading_output(output: Any, rubric: list, max_score: float) -> dict:
    """Return a safe grade or reject a structurally/arithmeticly invalid one."""

    if not isinstance(output, dict):
        raise GradingContractError("корень ответа: ожидался JSON-объект")
    validate_grading_request(rubric, max_score)
    expected_max = _number(max_score, "max_score задания")
    expected = _expected_criteria(rubric)
    expected_by_name = {criterion.name: criterion for criterion in expected}

    returned_max = _number(output.get("max_score"), "max_score ответа")
    if not _same_score(returned_max, expected_max):
        raise GradingContractError(f"max_score ответа изменён моделью: {returned_max:g} вместо {expected_max:g}")

    total_score = _number(output.get("total_score"), "total_score")
    if total_score < -SCORE_TOLERANCE or total_score > expected_max + SCORE_TOLERANCE:
        raise GradingContractError(f"total_score {total_score:g} вне диапазона 0..{expected_max:g}")

    criteria_scores = output.get("criteria_scores")
    if not isinstance(criteria_scores, list):
        raise GradingContractError("criteria_scores: ожидался список")

    returned_by_name: dict[str, tuple[int, dict]] = {}
    criterion_total = 0.0
    for index, criterion in enumerate(criteria_scores):
        if not isinstance(criterion, dict):
            raise GradingContractError(f"criteria_scores[{index}]: критерий должен быть объектом")
        raw_name = criterion.get("criterion_name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise GradingContractError(f"criteria_scores[{index}].criterion_name: не задано название")
        name = raw_name.strip()
        if name in returned_by_name:
            raise GradingContractError(f"criteria_scores: критерий «{name}» возвращён повторно")
        score = _number(criterion.get("score"), f"criteria_scores[{index}].score")
        criterion_max = _number(criterion.get("max_score"), f"criteria_scores[{index}].max_score")
        if score < -SCORE_TOLERANCE or score > criterion_max + SCORE_TOLERANCE:
            raise GradingContractError(f"criteria_scores[{index}].score: {score:g} вне диапазона 0..{criterion_max:g}")
        returned_by_name[name] = (index, criterion)
        criterion_total += score

    if expected:
        missing = [criterion.name for criterion in expected if criterion.name not in returned_by_name]
        extra = [name for name in returned_by_name if name not in expected_by_name]
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append("пропущены: " + ", ".join(f"«{name}»" for name in missing))
            if extra:
                details.append("лишние: " + ", ".join(f"«{name}»" for name in extra))
            raise GradingContractError("набор критериев не совпадает с рубрикой (" + "; ".join(details) + ")")
        for name, expected_criterion in expected_by_name.items():
            index, returned = returned_by_name[name]
            returned_criterion_max = _number(returned.get("max_score"), f"criteria_scores[{index}].max_score")
            if not _same_score(returned_criterion_max, expected_criterion.max_score):
                raise GradingContractError(
                    f"максимум критерия «{name}» изменён моделью: "
                    f"{returned_criterion_max:g} вместо {expected_criterion.max_score:g}"
                )

    if not _same_score(total_score, criterion_total):
        raise GradingContractError(
            f"total_score {total_score:g} не равен сумме баллов по критериям {criterion_total:g}"
        )

    confidence = _number(output.get("confidence"), "confidence")
    if confidence < 0 or confidence > 1:
        raise GradingContractError("confidence: значение должно находиться в диапазоне 0..1")
    needs_teacher_review = output.get("needs_teacher_review")
    if not isinstance(needs_teacher_review, bool):
        raise GradingContractError("needs_teacher_review: ожидалось логическое значение")
    unreadable = output.get("unreadable")
    if not isinstance(unreadable, bool):
        raise GradingContractError("unreadable: ожидалось логическое значение")
    if unreadable:
        reason = output.get("unreadable_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise GradingContractError("unreadable_reason: у нечитаемой работы требуется причина")

    safe_output = deepcopy(output)
    forced_review_reasons: list[str] = []
    if unreadable and not needs_teacher_review:
        forced_review_reasons.append("работа отмечена как нечитаемая")
    if forced_review_reasons:
        safe_output["needs_teacher_review"] = True
        safe_output["contract_review_reasons"] = forced_review_reasons
    return safe_output
