"""Built-in deterministic checks for the launch chemistry disciplines."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any

from app.services.chemistry_equations import (
    ChemistryParseError,
    check_reaction_balance,
    parse_species,
    reaction_candidates,
)
from app.services.chemistry_units import (
    AssignedMeasurement,
    Dimension,
    Measurement,
    close_si,
    extract_assigned_measurements,
    extract_measurements,
    normalize_label,
    parse_measurement,
)
from app.services.chemistry_validation import (
    CheckResult,
    CheckState,
    ChemistryDiscipline,
    ChemistryTask,
)


FARADAY_CONSTANT = 96485.33212
AVOGADRO_CONSTANT = 6.02214076e23
VACUUM_PERMITTIVITY = 8.8541878128e-12
DEFAULT_RELATIVE_TOLERANCE = 0.01
_NUMBER_RE = r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d+)?"


def _relative_error(actual: float, expected: float) -> float:
    scale = max(abs(actual), abs(expected), 1e-30)
    return abs(actual - expected) / scale


def _float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _numeric_literals(text: str) -> list[float]:
    values: list[float] = []
    for match in re.finditer(_NUMBER_RE, text or ""):
        try:
            value = float(match.group(0).replace(",", "."))
        except ValueError:
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _decimal_literal(value: object) -> Decimal | None:
    """Return the declared decimal, preserving the precision written by the generator."""

    if isinstance(value, Mapping):
        value = value.get("value")
    if isinstance(value, bool):
        return None
    match = re.search(_NUMBER_RE, str(value))
    if match is None:
        return None
    try:
        parsed = Decimal(match.group(0).replace(",", "."))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _significant_digits(value: object) -> int | None:
    parsed = _decimal_literal(value)
    if parsed is None:
        return None
    digits = parsed.as_tuple().digits
    return len(digits) if any(digits) else 1


def _rounding_audit(
    actual_value: object,
    expected_value: Decimal,
    *,
    minimum_significant_digits: int,
) -> dict[str, Any]:
    """Prove that a derived literal is a correctly rounded view of the exact result.

    A broad relative tolerance is useful for comparing independently presented
    answers, but it cannot distinguish 0.20315 from the correctly rounded
    0.20314.  Here the exponent written by the generator is the rounding
    contract: more displayed digits are welcome, but every displayed digit must
    agree with the deterministic calculation.
    """

    actual = _decimal_literal(actual_value)
    if actual is None:
        return {"status": "invalid_literal"}
    significant_digits = _significant_digits(actual_value) or 0
    try:
        with localcontext() as context:
            context.prec = 50
            quantum = Decimal(1).scaleb(actual.as_tuple().exponent)
            expected_rounded = expected_value.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return {"status": "invalid_precision"}
    matches = actual == expected_rounded and significant_digits >= minimum_significant_digits
    return {
        "status": "pass" if matches else "fail",
        "actual": str(actual),
        "expected_at_declared_precision": str(expected_rounded),
        "declared_significant_digits": significant_digits,
        "minimum_significant_digits": minimum_significant_digits,
    }


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _facts(task: ChemistryTask, key: str) -> Mapping[str, Any] | None:
    return _mapping(task.facts.get(key)) if isinstance(task.facts, Mapping) else None


def _assigned(task: ChemistryTask) -> list[AssignedMeasurement]:
    return extract_assigned_measurements(task.full_text)


def _find_assigned(
    values: list[AssignedMeasurement],
    aliases: set[str],
    dimensions: set[Dimension] | None = None,
) -> Measurement | None:
    normalized_aliases = {normalize_label(alias) for alias in aliases}
    for value in reversed(values):
        if value.normalized_label not in normalized_aliases:
            continue
        if dimensions is not None and value.measurement.dimension not in dimensions:
            continue
        return value.measurement
    return None


def _fact_measurement(
    facts: Mapping[str, Any],
    names: tuple[str, ...],
    dimensions: set[Dimension] | None = None,
) -> Measurement | None:
    for name in names:
        if name not in facts:
            continue
        value = parse_measurement(facts[name])
        if value is not None and (dimensions is None or value.dimension in dimensions):
            return value
    return None


def _measurement_is_stated(statement: str, expected: Measurement, *, tolerance: float = 0.002) -> bool:
    """Prove that an input quantity was visible to the student.

    Structured facts are generated metadata, not a source of new task data.  A
    deterministic check that relies on a physical input therefore requires the
    same dimension and value in the actual statement.
    """

    return any(
        value.dimension == expected.dimension
        and close_si(value.si_value, expected.si_value, relative_tolerance=tolerance)
        for value in extract_measurements(statement)
    )


def _result(check_id: str, state: CheckState, message: str, **evidence: Any) -> CheckResult:
    return CheckResult(check_id=check_id, state=state, message=message, evidence=evidence)


@dataclass(frozen=True)
class ReactionBalanceCheck:
    check_id: str = "chemistry.reaction_balance"
    disciplines: frozenset[ChemistryDiscipline] = frozenset()

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        candidates = reaction_candidates(task.solution_text)
        if not candidates:
            return _result(self.check_id, CheckState.NOT_APPLICABLE, "В эталонном ответе нет химического уравнения.")
        parsed: list[dict[str, Any]] = []
        parse_errors: list[dict[str, str]] = []
        for equation in candidates:
            try:
                balance = check_reaction_balance(equation)
            except ChemistryParseError as exc:
                parse_errors.append({"equation": equation, "error": str(exc)})
                continue
            parsed.append(balance.as_dict())
        if parse_errors:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Не все уравнения удалось разобрать однозначно; автоматический пропуск запрещён.",
                equations=parsed,
                parse_errors=parse_errors,
            )
        broken = [item for item in parsed if not item["balanced"]]
        if broken:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "В эталонном решении нарушен баланс атомов или заряда.",
                equations=parsed,
                unbalanced=broken,
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Все распознанные уравнения сохраняют атомы и суммарный заряд.",
            equations=parsed,
        )


@dataclass(frozen=True)
class UnitConsistencyCheck:
    check_id: str = "chemistry.units"
    disciplines: frozenset[ChemistryDiscipline] = frozenset()

    _chain_re = re.compile(
        rf"(?P<left>{_NUMBER_RE}\s*[^\s=≈,;]+)\s*(?:=|≈)\s*"
        rf"(?P<right>{_NUMBER_RE}\s*[^\s=≈,;]+)"
    )
    _dimension_request_re = re.compile(
        r"(?:рассчитайте|вычислите|определите|найдите).{0,80}"
        r"(?:массу|объ[её]м|концентрац|заряд|силу тока|время|потенциал|"
        r"дзета-потенциал|удельную поверхность|подвижност)",
        re.IGNORECASE | re.DOTALL,
    )

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        issues: list[dict[str, Any]] = []
        conversions: list[dict[str, Any]] = []
        for match in self._chain_re.finditer(task.full_text):
            left = parse_measurement(match.group("left"))
            right = parse_measurement(match.group("right"))
            if left is None or right is None:
                continue
            record = {
                "left": match.group("left"),
                "right": match.group("right"),
                "left_dimension": left.dimension.value,
                "right_dimension": right.dimension.value,
            }
            conversions.append(record)
            # A local ``number unit = number unit`` match may be a fragment of a
            # longer calculation (for example a denominator followed by the
            # result), so only same-dimension pairs are treated as conversions.
            # Cross-dimension reuse of an assigned symbol is checked below.
            if left.dimension != right.dimension:
                continue
            if not close_si(left.si_value, right.si_value, relative_tolerance=0.005):
                issues.append(
                    {
                        **record,
                        "reason": "conversion_value_mismatch",
                        "left_si": left.si_value,
                        "right_si": right.si_value,
                    }
                )

        dimensions_by_label: dict[str, set[Dimension]] = {}
        sources_by_label: dict[str, list[str]] = {}
        for value in _assigned(task):
            # Case is significant for conventional chemistry notation: ``m`` is
            # mass while ``M`` is molar mass.  Only an exactly reused label is a
            # dimensional contradiction.
            exact_label = re.sub(r"[^A-Za-zА-Яа-яЁё0-9]+", "", value.label)
            dimensions_by_label.setdefault(exact_label, set()).add(value.measurement.dimension)
            sources_by_label.setdefault(exact_label, []).append(value.measurement.source)
        for label, dimensions in dimensions_by_label.items():
            if len(dimensions) > 1:
                issues.append(
                    {
                        "reason": "same_label_different_dimensions",
                        "label": label,
                        "dimensions": sorted(dimension.value for dimension in dimensions),
                        "sources": sources_by_label[label],
                    }
                )

        answer = task.answer.strip()
        answer_has_number = re.search(_NUMBER_RE, answer) is not None
        answer_is_bare_number = re.fullmatch(rf"(?:ответ\s*:\s*)?{_NUMBER_RE}\s*", answer, re.IGNORECASE) is not None
        if (
            answer_has_number
            and answer_is_bare_number
            and not extract_measurements(answer)
            and self._dimension_request_re.search(task.statement)
        ):
            issues.append({"reason": "dimensional_answer_without_unit", "answer": answer})

        if issues:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Обнаружена размерностная ошибка или некорректный перевод единиц.",
                issues=issues,
                checked_conversions=conversions,
            )
        if not conversions and not dimensions_by_label:
            return _result(
                self.check_id,
                CheckState.NOT_APPLICABLE,
                "Нет однозначных присваиваний или переводов единиц для детерминированной проверки.",
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Явные единицы и переводы размерностно согласованы.",
            checked_conversions=conversions,
            assigned_labels=sorted(dimensions_by_label),
        )


@dataclass(frozen=True)
class ScaleCompatibilityCheck:
    check_id: str = "chemistry.scale_compatibility"
    disciplines: frozenset[ChemistryDiscipline] = frozenset()

    _mulliken_formula_re = re.compile(
        r"(?:малликен|mulliken).{0,180}(?:2\s*(?:χ|x|\\chi)|(?:χ|x|\\chi)\s*=\s*\(?\s*[ia].{0,20}[ia])",
        re.IGNORECASE | re.DOTALL,
    )
    _substitution_re = re.compile(r"(?:примен|подстав|рассчит|вычисл)", re.IGNORECASE)

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        text = task.full_text.casefold()
        has_mulliken = "малликен" in text or "mulliken" in text
        has_pauling = "полинг" in text or "pauling" in text
        if not (has_mulliken and has_pauling):
            return _result(
                self.check_id,
                CheckState.NOT_APPLICABLE,
                "Одновременное использование шкал Полинга и Малликена не найдено.",
            )
        solution = task.solution_text
        direct_calculation = bool(
            self._mulliken_formula_re.search(task.full_text) and self._substitution_re.search(solution)
        )
        if direct_calculation:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Безразмерные электроотрицательности Полинга нельзя подставлять в энергетическую формулу Малликена.",
                incompatible_scales=["Pauling", "Mulliken"],
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Обе шкалы названы, но прямой подстановки значений Полинга в формулу Малликена не найдено.",
        )


@dataclass(frozen=True)
class StoichiometryCheck:
    check_id: str = "chemistry.stoichiometry"
    disciplines: frozenset[ChemistryDiscipline] = frozenset(
        {ChemistryDiscipline.GENERAL, ChemistryDiscipline.ANALYTICAL}
    )

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        facts = _facts(task, "stoichiometry")
        if facts is None:
            return self._evaluate_amount_concentration_relation(task)
        reaction = str(facts.get("reaction") or "").strip()
        amounts = _mapping(facts.get("reactant_amounts"))
        target_species = str(facts.get("target_species") or "").strip()
        target_amount = parse_measurement(facts.get("target_amount"), expected=Dimension.AMOUNT)
        if not reaction or amounts is None or not target_species or target_amount is None:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Структурированные данные стехиометрии неполны или содержат величины без единиц.",
                required=["reaction", "reactant_amounts", "target_species", "target_amount"],
            )
        if target_amount.si_value <= 0:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Количество целевого вещества должно быть положительным.",
                target_amount_mol=target_amount.si_value,
            )
        try:
            balance = check_reaction_balance(reaction)
        except ChemistryParseError as exc:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Не удалось однозначно разобрать стехиометрическое уравнение.",
                error=str(exc),
            )
        if not balance.balanced:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Расчёт опирается на несбалансированное уравнение реакции.",
                reaction=balance.as_dict(),
            )

        def species_key(raw: str) -> tuple[tuple[tuple[str, int], ...], int]:
            parsed = parse_species(raw)
            return tuple(sorted(parsed.atoms.items())), parsed.charge

        def term_key(term: Any) -> tuple[tuple[tuple[str, int], ...], int]:
            return tuple(sorted(term.species.atoms.items())), term.species.charge

        def descriptor(term: Any) -> str:
            if term.species.formula in {"e-", "e+"}:
                return term.species.formula
            charge = term.species.charge
            if not charge:
                return term.species.formula
            sign = "+" if charge > 0 else "-"
            magnitude = "" if abs(charge) == 1 else str(abs(charge))
            return f"{term.species.formula}^{magnitude}{sign}"

        excess_raw = facts.get("excess_reactants") or []
        if not isinstance(excess_raw, list) or any(not str(item).strip() for item in excess_raw):
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "excess_reactants должен быть массивом формул явно избыточных реагентов.",
            )
        try:
            excess_keys = {species_key(str(item)) for item in excess_raw}
        except ChemistryParseError as exc:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Не удалось разобрать формулу явно избыточного реагента.",
                error=str(exc),
            )
        if excess_keys and "избыт" not in task.full_text.casefold():
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Избыточный реагент должен быть явно обозначен в условии или решении.",
                excess_reactants=[str(item) for item in excess_raw],
            )

        try:
            target_key = species_key(target_species)
        except ChemistryParseError as exc:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Не удалось разобрать формулу целевого вещества.",
                error=str(exc),
            )
        target_term = next(
            (term for term in (*balance.products, *balance.reactants) if term_key(term) == target_key),
            None,
        )
        if target_term is None:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Целевое вещество отсутствует в уравнении реакции.",
                target_species=target_species,
            )
        extents: dict[str, float] = {}
        for term in balance.reactants:
            raw_amount = None
            amount_label = descriptor(term)
            for supplied_formula, supplied_amount in amounts.items():
                try:
                    matches = species_key(str(supplied_formula)) == term_key(term)
                except ChemistryParseError:
                    matches = False
                if matches:
                    raw_amount = supplied_amount
                    amount_label = str(supplied_formula)
                    break
            parsed_amount = parse_measurement(raw_amount, expected=Dimension.AMOUNT)
            if parsed_amount is None:
                if term_key(term) in excess_keys:
                    continue
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Для всех реагентов нужны количества вещества с явными единицами.",
                    missing_reactant=descriptor(term),
                )
            if parsed_amount.si_value <= 0:
                return _result(
                    self.check_id,
                    CheckState.FAIL,
                    "Количество каждого реагента должно быть положительным.",
                    reactant=amount_label,
                    amount_mol=parsed_amount.si_value,
                )
            extents[amount_label] = parsed_amount.si_value / float(term.coefficient)
        if not extents:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Нужен хотя бы один количественно заданный реагент.",
            )
        limiting_extent = min(extents.values())
        expected_amount = limiting_extent * float(target_term.coefficient)
        error = _relative_error(target_amount.si_value, expected_amount)
        limiting = sorted(formula for formula, extent in extents.items() if close_si(extent, limiting_extent))
        claimed_limiting = str(facts.get("limiting_reagent") or "").strip()
        claimed_is_limiting = claimed_limiting in limiting
        if claimed_limiting and not claimed_is_limiting:
            try:
                claimed_key = species_key(claimed_limiting)
                claimed_is_limiting = any(
                    species_key(formula) == claimed_key for formula in limiting
                )
            except ChemistryParseError:
                claimed_is_limiting = False
        if claimed_limiting and not claimed_is_limiting:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Неверно определён лимитирующий реагент.",
                expected_limiting=limiting,
                claimed_limiting=claimed_limiting,
                extents=extents,
            )
        if error > DEFAULT_RELATIVE_TOLERANCE:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Количество целевого вещества не следует из коэффициентов и лимитирующего реагента.",
                expected_mol=expected_amount,
                actual_mol=target_amount.si_value,
                relative_error=error,
                limiting_reagents=limiting,
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Лимитирующий реагент и теоретическое количество продукта согласованы.",
            expected_mol=expected_amount,
            actual_mol=target_amount.si_value,
            limiting_reagents=limiting,
            excess_reactants=[str(item) for item in excess_raw],
        )

    def _evaluate_amount_concentration_relation(self, task: ChemistryTask) -> CheckResult:
        values = _assigned(task)
        amount = _find_assigned(values, {"n"}, {Dimension.AMOUNT})
        concentration = _find_assigned(values, {"c"}, {Dimension.AMOUNT_CONCENTRATION})
        volume = _find_assigned(values, {"V"}, {Dimension.VOLUME})
        if not all((amount, concentration, volume)):
            return _result(
                self.check_id,
                CheckState.NOT_APPLICABLE,
                "Нет полного набора n, c и V либо структурированных стехиометрических фактов.",
            )
        expected = concentration.si_value * volume.si_value
        error = _relative_error(amount.si_value, expected)
        if error > DEFAULT_RELATIVE_TOLERANCE:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Нарушено соотношение n = cV.",
                expected_mol=expected,
                actual_mol=amount.si_value,
                relative_error=error,
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Количество вещества согласовано с молярной концентрацией и объёмом.",
            expected_mol=expected,
            actual_mol=amount.si_value,
        )


@dataclass(frozen=True)
class DilutionCheck:
    check_id: str = "chemistry.dilution"
    disciplines: frozenset[ChemistryDiscipline] = frozenset(
        {ChemistryDiscipline.GENERAL, ChemistryDiscipline.ANALYTICAL}
    )

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        facts = _facts(task, "dilution")
        allowed_concentration = {Dimension.AMOUNT_CONCENTRATION, Dimension.MASS_CONCENTRATION}
        if facts is not None:
            c1 = _fact_measurement(facts, ("c1", "initial_concentration"), allowed_concentration)
            v1 = _fact_measurement(facts, ("v1", "aliquot_volume"), {Dimension.VOLUME})
            c2 = _fact_measurement(facts, ("c2", "final_concentration"), allowed_concentration)
            v2 = _fact_measurement(facts, ("v2", "final_volume"), {Dimension.VOLUME})
        else:
            assigned = _assigned(task)
            c1 = _find_assigned(assigned, {"c1", "c_1"}, allowed_concentration)
            v1 = _find_assigned(assigned, {"V1", "V_1"}, {Dimension.VOLUME})
            c2 = _find_assigned(assigned, {"c2", "c_2"}, allowed_concentration)
            v2 = _find_assigned(assigned, {"V2", "V_2"}, {Dimension.VOLUME})
        values = (c1, v1, c2, v2)
        if not all(values):
            if facts is not None:
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Для проверки разбавления нужны c1, V1, c2, V2 с явными единицами.",
                )
            return _result(self.check_id, CheckState.NOT_APPLICABLE, "Полная схема разбавления c1V1=c2V2 не найдена.")
        assert c1 and v1 and c2 and v2
        non_positive = [
            name
            for name, value in (("c1", c1), ("v1", v1), ("c2", c2), ("v2", v2))
            if value.si_value <= 0
        ]
        if non_positive:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Концентрации и объёмы в схеме разбавления должны быть положительными.",
                non_positive=non_positive,
            )
        if c1.dimension != c2.dimension:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "До и после разбавления использованы разные типы концентрации.",
                c1_dimension=c1.dimension.value,
                c2_dimension=c2.dimension.value,
            )
        left = c1.si_value * v1.si_value
        right = c2.si_value * v2.si_value
        error = _relative_error(left, right)
        if error > DEFAULT_RELATIVE_TOLERANCE:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Нарушен материальный баланс разбавления c1V1 = c2V2.",
                initial_amount=left,
                final_amount=right,
                relative_error=error,
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Материальный баланс разбавления выполнен.",
            initial_amount=left,
            final_amount=right,
        )


@dataclass(frozen=True)
class TitrationCheck:
    check_id: str = "analytical.titration"
    disciplines: frozenset[ChemistryDiscipline] = frozenset({ChemistryDiscipline.ANALYTICAL})

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        facts = _facts(task, "titration")
        if facts is None:
            return _result(
                self.check_id,
                CheckState.NOT_APPLICABLE,
                "Титриметрическая проверка требует явных стехиометрических факторов в structured facts.",
            )
        analyte = _mapping(facts.get("analyte"))
        titrant = _mapping(facts.get("titrant"))
        if analyte is None or titrant is None:
            return _result(self.check_id, CheckState.INDETERMINATE, "Не заданы стороны титрования analyte/titrant.")

        def equivalents(side: Mapping[str, Any]) -> tuple[float, str] | str | None:
            concentration = _fact_measurement(
                side, ("concentration", "c"), {Dimension.AMOUNT_CONCENTRATION}
            )
            volume = _fact_measurement(side, ("volume", "v"), {Dimension.VOLUME})
            if concentration is None or volume is None:
                return None
            if concentration.si_value <= 0 or volume.si_value <= 0:
                return "non_positive"
            amount = concentration.si_value * volume.si_value
            factor = _float(side.get("equivalent_factor"))
            coefficient = _float(side.get("stoichiometric_coefficient"))
            if factor is not None and factor > 0:
                return amount * factor, "equivalent_factor"
            if coefficient is not None and coefficient > 0:
                return amount / coefficient, "stoichiometric_coefficient"
            return None

        analyte_eq = equivalents(analyte)
        titrant_eq = equivalents(titrant)
        if analyte_eq == "non_positive" or titrant_eq == "non_positive":
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Концентрации и объёмы титрования должны быть положительными.",
            )
        if analyte_eq is None or titrant_eq is None:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Для каждой стороны нужны c, V и equivalent_factor либо stoichiometric_coefficient.",
            )
        assert not isinstance(analyte_eq, str) and not isinstance(titrant_eq, str)
        error = _relative_error(analyte_eq[0], titrant_eq[0])
        if error > DEFAULT_RELATIVE_TOLERANCE:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Количество эквивалентов в точке эквивалентности не совпадает.",
                analyte_extent=analyte_eq[0],
                titrant_extent=titrant_eq[0],
                relative_error=error,
                conventions={"analyte": analyte_eq[1], "titrant": titrant_eq[1]},
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Стехиометрия точки эквивалентности согласована.",
            analyte_extent=analyte_eq[0],
            titrant_extent=titrant_eq[0],
            conventions={"analyte": analyte_eq[1], "titrant": titrant_eq[1]},
        )


@dataclass(frozen=True)
class GravimetryCheck:
    check_id: str = "analytical.gravimetry"
    disciplines: frozenset[ChemistryDiscipline] = frozenset({ChemistryDiscipline.ANALYTICAL})

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        facts = _facts(task, "gravimetry")
        if facts is None:
            return _result(
                self.check_id,
                CheckState.NOT_APPLICABLE,
                "Гравиметрическая проверка ожидает полный mass chain в structured facts.",
            )

        analyte_coefficient = _float(facts.get("analyte_stoichiometric_coefficient"))
        weighing_form_coefficient = _float(facts.get("weighing_form_stoichiometric_coefficient"))
        analyte_molar_mass = _fact_measurement(
            facts,
            ("analyte_molar_mass",),
            {Dimension.MOLAR_MASS},
        )
        weighing_form_molar_mass = _fact_measurement(
            facts,
            ("weighing_form_molar_mass",),
            {Dimension.MOLAR_MASS},
        )
        gravimetric_factor = _float(facts.get("gravimetric_factor"))
        weighing_form_mass = _fact_measurement(
            facts,
            ("weighing_form_mass",),
            {Dimension.MASS},
        )
        analyte_mass = _fact_measurement(facts, ("analyte_mass",), {Dimension.MASS})

        parsed_fields: dict[str, object | None] = {
            "analyte_stoichiometric_coefficient": analyte_coefficient,
            "weighing_form_stoichiometric_coefficient": weighing_form_coefficient,
            "analyte_molar_mass": analyte_molar_mass,
            "weighing_form_molar_mass": weighing_form_molar_mass,
            "gravimetric_factor": gravimetric_factor,
            "weighing_form_mass": weighing_form_mass,
            "analyte_mass": analyte_mass,
        }
        missing_or_invalid = [name for name, value in parsed_fields.items() if value is None]
        if missing_or_invalid:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Нужен полный гравиметрический mass chain; массы и молярные массы должны иметь единицы.",
                missing_or_invalid=missing_or_invalid,
            )

        assert analyte_coefficient is not None
        assert weighing_form_coefficient is not None
        assert analyte_molar_mass is not None
        assert weighing_form_molar_mass is not None
        assert gravimetric_factor is not None
        assert weighing_form_mass is not None
        assert analyte_mass is not None

        non_positive = [
            name
            for name, value in (
                ("analyte_stoichiometric_coefficient", analyte_coefficient),
                ("weighing_form_stoichiometric_coefficient", weighing_form_coefficient),
                ("analyte_molar_mass", analyte_molar_mass.si_value),
                ("weighing_form_molar_mass", weighing_form_molar_mass.si_value),
                ("gravimetric_factor", gravimetric_factor),
                ("weighing_form_mass", weighing_form_mass.si_value),
                ("analyte_mass", analyte_mass.si_value),
            )
            if value <= 0
        ]
        if non_positive:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Коэффициенты, массы, молярные массы и гравиметрический фактор должны быть положительными.",
                non_positive=non_positive,
            )

        hidden_inputs = [
            name
            for name, value in (
                ("analyte_molar_mass", analyte_molar_mass),
                ("weighing_form_molar_mass", weighing_form_molar_mass),
                ("weighing_form_mass", weighing_form_mass),
            )
            if not _measurement_is_stated(task.statement, value)
        ]
        if hidden_inputs:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Исходные физические величины гравиметрии скрыты от студента или расходятся с условием.",
                hidden_inputs=hidden_inputs,
            )

        expected_factor = (
            analyte_coefficient * analyte_molar_mass.si_value
            / (weighing_form_coefficient * weighing_form_molar_mass.si_value)
        )
        expected_analyte_mass = weighing_form_mass.si_value * expected_factor

        # Decimal-place correctness is stricter than the broad physical-value
        # tolerance below.  It catches a recurrent generator failure where the
        # value is numerically close but the last printed digit is impossible
        # (for example 58.69/288.91 -> 0.20315 instead of 0.20314, followed by
        # the wrong four-significant-digit answer 0.2032 instead of 0.2031).
        coefficient_a_decimal = _decimal_literal(facts.get("analyte_stoichiometric_coefficient"))
        coefficient_f_decimal = _decimal_literal(facts.get("weighing_form_stoichiometric_coefficient"))
        analyte_molar_mass_decimal = Decimal(str(analyte_molar_mass.si_value))
        weighing_form_molar_mass_decimal = Decimal(str(weighing_form_molar_mass.si_value))
        weighing_form_mass_decimal = Decimal(str(weighing_form_mass.si_value))
        with localcontext() as context:
            context.prec = 50
            assert coefficient_a_decimal is not None
            assert coefficient_f_decimal is not None
            exact_factor = (
                coefficient_a_decimal
                * analyte_molar_mass_decimal
                / (coefficient_f_decimal * weighing_form_molar_mass_decimal)
            )
            exact_analyte_mass = weighing_form_mass_decimal * exact_factor

        factor_input_precisions = [
            digits
            for digits in (
                _significant_digits(facts.get("analyte_molar_mass")),
                _significant_digits(facts.get("weighing_form_molar_mass")),
            )
            if digits is not None
        ]
        factor_minimum_digits = min(factor_input_precisions) if factor_input_precisions else 1
        factor_rounding = _rounding_audit(
            facts.get("gravimetric_factor"),
            exact_factor,
            minimum_significant_digits=factor_minimum_digits,
        )

        raw_analyte_mass = _decimal_literal(facts.get("analyte_mass"))
        actual_analyte_mass_si = Decimal(str(analyte_mass.si_value))
        if raw_analyte_mass is None or raw_analyte_mass == 0:
            mass_rounding = {"status": "invalid_literal"}
        else:
            # Express the exact SI result in the unit used by the generated
            # literal so that its written decimal exponent remains meaningful.
            unit_scale = actual_analyte_mass_si / raw_analyte_mass
            exact_mass_in_declared_unit = exact_analyte_mass / unit_scale
            weighing_mass_digits = _significant_digits(facts.get("weighing_form_mass"))
            mass_minimum_digits = min(
                factor_minimum_digits,
                weighing_mass_digits if weighing_mass_digits is not None else factor_minimum_digits,
            )
            mass_rounding = _rounding_audit(
                facts.get("analyte_mass"),
                exact_mass_in_declared_unit,
                minimum_significant_digits=mass_minimum_digits,
            )

        rounding_errors = [
            {"field": field, **audit}
            for field, audit in (
                ("gravimetric_factor", factor_rounding),
                ("analyte_mass", mass_rounding),
            )
            if audit.get("status") != "pass"
        ]
        if rounding_errors:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Вычисляемые величины гравиметрии округлены арифметически неверно или с недостаточной точностью.",
                rounding_errors=rounding_errors,
                exact_gravimetric_factor=str(exact_factor),
                exact_analyte_mass_kg=str(exact_analyte_mass),
            )

        factor_error = _relative_error(gravimetric_factor, expected_factor)
        mass_error = _relative_error(analyte_mass.si_value, expected_analyte_mass)
        claimed_chain_error = _relative_error(
            analyte_mass.si_value,
            weighing_form_mass.si_value * gravimetric_factor,
        )
        evidence = {
            "expected_gravimetric_factor": expected_factor,
            "actual_gravimetric_factor": gravimetric_factor,
            "expected_analyte_mass_kg": expected_analyte_mass,
            "actual_analyte_mass_kg": analyte_mass.si_value,
            "factor_relative_error": factor_error,
            "mass_relative_error": mass_error,
            "claimed_chain_relative_error": claimed_chain_error,
            "rounding_audit": {
                "gravimetric_factor": factor_rounding,
                "analyte_mass": mass_rounding,
            },
        }
        if max(factor_error, mass_error, claimed_chain_error) > DEFAULT_RELATIVE_TOLERANCE:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Гравиметрический фактор или цепочка m(аналита)=F·m(весовой формы) неверны.",
                **evidence,
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Гравиметрический фактор и цепочка масс согласованы со стехиометрией.",
            **evidence,
        )


@dataclass(frozen=True)
class ConductometryCheck:
    check_id: str = "analytical.conductometry"
    disciplines: frozenset[ChemistryDiscipline] = frozenset({ChemistryDiscipline.ANALYTICAL})

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        facts = _facts(task, "conductometry")
        if facts is None:
            return _result(
                self.check_id,
                CheckState.NOT_APPLICABLE,
                "Кондуктометрическая проверка ожидает R, G, постоянную ячейки и проводимость.",
            )

        resistance = _fact_measurement(facts, ("resistance",), {Dimension.RESISTANCE})
        conductance = _fact_measurement(facts, ("conductance",), {Dimension.CONDUCTANCE})
        cell_constant = _fact_measurement(facts, ("cell_constant",), {Dimension.INVERSE_LENGTH})
        conductivity = _fact_measurement(facts, ("conductivity",), {Dimension.CONDUCTIVITY})
        parsed_fields = {
            "resistance": resistance,
            "conductance": conductance,
            "cell_constant": cell_constant,
            "conductivity": conductivity,
        }
        missing_or_invalid = [name for name, value in parsed_fields.items() if value is None]
        if missing_or_invalid:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Для проверки нужны R, G, K_cell и κ с явными совместимыми единицами.",
                missing_or_invalid=missing_or_invalid,
            )

        assert resistance is not None
        assert conductance is not None
        assert cell_constant is not None
        assert conductivity is not None
        non_positive = [
            name
            for name, value in parsed_fields.items()
            if value is not None and value.si_value <= 0
        ]
        if non_positive:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "R, G, постоянная ячейки и удельная проводимость должны быть положительными.",
                non_positive=non_positive,
            )

        statement_values = extract_measurements(task.statement)
        electrical_input_is_stated = any(
            candidate.dimension in {Dimension.RESISTANCE, Dimension.CONDUCTANCE}
            and (
                (candidate.dimension == Dimension.RESISTANCE and close_si(candidate.si_value, resistance.si_value))
                or (candidate.dimension == Dimension.CONDUCTANCE and close_si(candidate.si_value, conductance.si_value))
            )
            for candidate in statement_values
        )
        cell_constant_is_stated = _measurement_is_stated(task.statement, cell_constant)
        hidden_inputs = []
        if not electrical_input_is_stated:
            hidden_inputs.append("resistance_or_conductance")
        if not cell_constant_is_stated:
            hidden_inputs.append("cell_constant")
        if hidden_inputs:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Исходные R/G или постоянная ячейки скрыты от студента или расходятся с условием.",
                hidden_inputs=hidden_inputs,
            )

        expected_conductance = 1.0 / resistance.si_value
        expected_conductivity = cell_constant.si_value * conductance.si_value
        conductance_error = _relative_error(conductance.si_value, expected_conductance)
        conductivity_error = _relative_error(conductivity.si_value, expected_conductivity)
        evidence = {
            "expected_conductance_s": expected_conductance,
            "actual_conductance_s": conductance.si_value,
            "expected_conductivity_s_per_m": expected_conductivity,
            "actual_conductivity_s_per_m": conductivity.si_value,
            "conductance_relative_error": conductance_error,
            "conductivity_relative_error": conductivity_error,
        }
        if max(conductance_error, conductivity_error) > DEFAULT_RELATIVE_TOLERANCE:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Нарушена связь G=1/R или κ=K_cell·G.",
                **evidence,
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Сопротивление, проводимость и постоянная ячейки размерностно и численно согласованы.",
            **evidence,
        )


@dataclass(frozen=True)
class FaradayCheck:
    check_id: str = "analytical.faraday"
    disciplines: frozenset[ChemistryDiscipline] = frozenset({ChemistryDiscipline.ANALYTICAL})

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        facts = _facts(task, "faraday")
        assigned = _assigned(task)
        topic_text = f"{task.topic}\n{task.statement}".casefold()
        applicable = facts is not None or any(token in topic_text for token in ("фараде", "электролиз", "кулонометр"))
        if not applicable:
            return _result(self.check_id, CheckState.NOT_APPLICABLE, "Электрохимический расчёт Фарадея не найден.")

        if facts is not None:
            current = _fact_measurement(facts, ("current", "i"), {Dimension.CURRENT})
            duration = _fact_measurement(facts, ("time", "t"), {Dimension.TIME})
            charge = _fact_measurement(facts, ("charge", "q"), {Dimension.CHARGE})
            electron_amount = _fact_measurement(facts, ("electron_amount", "n_e"), {Dimension.AMOUNT})
            mass = _fact_measurement(facts, ("mass", "deposited_mass"), {Dimension.MASS})
            molar_mass = _fact_measurement(facts, ("molar_mass",), {Dimension.MOLAR_MASS})
            faraday_constant = _fact_measurement(
                facts,
                ("faraday_constant", "F"),
                {Dimension.CHARGE_PER_AMOUNT},
            )
            electrons_raw = facts.get("electrons") if "electrons" in facts else facts.get("z")
            electrons = _float(electrons_raw)
            efficiency = _float(facts.get("current_efficiency"))

            measurement_fields = (
                (("current", "i"), current),
                (("time", "t"), duration),
                (("charge", "q"), charge),
                (("electron_amount", "n_e"), electron_amount),
                (("mass", "deposited_mass"), mass),
                (("molar_mass",), molar_mass),
                (("faraday_constant", "F"), faraday_constant),
            )
            invalid_fields = [
                "/".join(names)
                for names, parsed in measurement_fields
                if any(name in facts for name in names) and parsed is None
            ]
            if any(name in facts for name in ("electrons", "z")) and electrons is None:
                invalid_fields.append("electrons/z")
            if "current_efficiency" in facts and efficiency is None:
                invalid_fields.append("current_efficiency")
            if invalid_fields:
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Некоторые величины блока faraday не разобраны или не имеют требуемых единиц.",
                    invalid_fields=invalid_fields,
                )

            non_positive_measurements = [
                name
                for name, value in (
                    ("current", current),
                    ("time", duration),
                    ("charge", charge),
                    ("electron_amount", electron_amount),
                    ("mass", mass),
                    ("molar_mass", molar_mass),
                    ("faraday_constant", faraday_constant),
                )
                if value is not None and value.si_value <= 0
            ]
            if non_positive_measurements:
                return _result(
                    self.check_id,
                    CheckState.FAIL,
                    "Величины закона Фарадея должны быть положительными модулями.",
                    non_positive=non_positive_measurements,
                )

            qit_intended = any(name in facts for name in ("current", "i", "time", "t"))
            if qit_intended and not all((current, duration, charge)):
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Для заявленной связи Q=It нужны ток, время и заряд.",
                )
            electron_relation_intended = any(name in facts for name in ("electron_amount", "n_e"))
            if electron_relation_intended and not all((charge, electron_amount, faraday_constant)):
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Для заявленной связи n_e=Q/F нужны заряд, количество электронов и явно заданная F.",
                )
            mass_relation_intended = any(
                name in facts
                for name in ("mass", "deposited_mass", "molar_mass", "electrons", "z", "current_efficiency")
            )
            if mass_relation_intended and (
                not all((charge, mass, molar_mass, faraday_constant)) or electrons is None
            ):
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Для расчёта осаждённой массы нужны Q, m, M, z и явно заданная F.",
                )
            if mass_relation_intended and electrons <= 0:
                return _result(
                    self.check_id,
                    CheckState.FAIL,
                    "Число электронов z в законе Фарадея должно быть положительным.",
                    electrons=electrons,
                )
        else:
            current = _find_assigned(assigned, {"I"}, {Dimension.CURRENT})
            duration = _find_assigned(assigned, {"t"}, {Dimension.TIME})
            charge = _find_assigned(assigned, {"Q"}, {Dimension.CHARGE})
            electron_amount = _find_assigned(assigned, {"ne", "n_e"}, {Dimension.AMOUNT})
            mass = molar_mass = None
            electrons = efficiency = faraday_constant = None

        uses_faraday_constant = electron_amount is not None or mass is not None
        if uses_faraday_constant:
            if faraday_constant is None:
                return _result(
                    self.check_id,
                    CheckState.FAIL,
                    "Постоянная Фарадея нужна для решения, но её численное значение не дано студенту.",
                )
            stated_constants = [
                value
                for value in extract_measurements(task.statement)
                if value.dimension == Dimension.CHARGE_PER_AMOUNT
            ]
            constant_error = _relative_error(faraday_constant.si_value, FARADAY_CONSTANT)
            if constant_error > 0.005 or not any(
                close_si(value.si_value, faraday_constant.si_value, relative_tolerance=0.002)
                for value in stated_constants
            ):
                return _result(
                    self.check_id,
                    CheckState.FAIL,
                    "Значение F неверно, скрыто от студента или расходится со structured facts.",
                    expected_faraday_constant=FARADAY_CONSTANT,
                    actual_faraday_constant=faraday_constant.si_value,
                )

        checks: list[dict[str, Any]] = []
        if current and duration and charge:
            expected = current.si_value * duration.si_value
            checks.append(
                {
                    "relation": "Q=It",
                    "expected": expected,
                    "actual": charge.si_value,
                    "relative_error": _relative_error(charge.si_value, expected),
                }
            )
        if charge and electron_amount:
            assert faraday_constant is not None
            expected = charge.si_value / faraday_constant.si_value
            checks.append(
                {
                    "relation": "n_e=Q/F",
                    "expected": expected,
                    "actual": electron_amount.si_value,
                    "relative_error": _relative_error(electron_amount.si_value, expected),
                }
            )
        if charge and mass and molar_mass and electrons and electrons > 0:
            assert faraday_constant is not None
            eta = efficiency if efficiency is not None else 1.0
            if not 0 < eta <= 1:
                return _result(
                    self.check_id,
                    CheckState.FAIL,
                    "Выход по току должен лежать в интервале (0, 1].",
                    current_efficiency=eta,
                )
            expected = molar_mass.si_value * charge.si_value * eta / (electrons * faraday_constant.si_value)
            checks.append(
                {
                    "relation": "m=M Q eta/(zF)",
                    "expected": expected,
                    "actual": mass.si_value,
                    "relative_error": _relative_error(mass.si_value, expected),
                }
            )
        if not checks:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Тема Фарадея распознана, но нет полного набора величин хотя бы для одной проверяемой связи.",
            )
        failed = [item for item in checks if item["relative_error"] > DEFAULT_RELATIVE_TOLERANCE]
        if failed:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Электрохимические величины не согласуются с законом Фарадея.",
                checks=checks,
                failed=failed,
                faraday_constant=faraday_constant.si_value if faraday_constant is not None else None,
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Проверяемые связи закона Фарадея выполнены.",
            checks=checks,
            faraday_constant=faraday_constant.si_value if faraday_constant is not None else None,
        )


@dataclass(frozen=True)
class CalibrationCheck:
    check_id: str = "analytical.calibration"
    disciplines: frozenset[ChemistryDiscipline] = frozenset({ChemistryDiscipline.ANALYTICAL})

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        facts = _facts(task, "calibration")
        if facts is None:
            return _result(
                self.check_id,
                CheckState.NOT_APPLICABLE,
                "Калибровочная проверка принимает точные coefficients/signal/concentration в structured facts.",
            )
        slope = _float(facts.get("slope"))
        intercept = _float(facts.get("intercept"))
        signal = _float(facts.get("signal"))
        concentration = _float(facts.get("concentration"))
        if None in (slope, intercept, signal, concentration) or slope == 0:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Нужны конечные slope, intercept, signal и concentration; slope не может быть нулём.",
            )
        expected = (signal - intercept) / slope
        error = _relative_error(concentration, expected)
        evidence: dict[str, Any] = {
            "expected_concentration": expected,
            "actual_concentration": concentration,
            "relative_error": error,
        }
        if concentration < 0:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Рассчитанная концентрация не может быть отрицательной.",
                **evidence,
            )
        calibration_range = facts.get("calibration_range")
        if calibration_range is not None:
            if not isinstance(calibration_range, (list, tuple)) or len(calibration_range) != 2:
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "calibration_range должен содержать ровно две конечные границы.",
                    **evidence,
                )
            low, high = _float(calibration_range[0]), _float(calibration_range[1])
            if low is None or high is None:
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Границы calibration_range должны быть конечными числами.",
                    **evidence,
                )
            evidence["inside_calibration_range"] = min(low, high) <= concentration <= max(low, high)
        if error > DEFAULT_RELATIVE_TOLERANCE:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Концентрация неверно восстановлена из калибровочной зависимости y=ax+b.",
                **evidence,
            )
        if evidence.get("inside_calibration_range") is False:
            return _result(
                self.check_id,
                CheckState.WARNING,
                "Расчёт верен, но результат получен экстраполяцией за калибровочный диапазон.",
                **evidence,
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Обратный расчёт по калибровочной прямой выполнен корректно.",
            **evidence,
        )


@dataclass(frozen=True)
class BetCheck:
    check_id: str = "colloid.bet"
    disciplines: frozenset[ChemistryDiscipline] = frozenset({ChemistryDiscipline.COLLOID})

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        facts = _facts(task, "bet")
        if facts is None:
            return _result(
                self.check_id,
                CheckState.NOT_APPLICABLE,
                "BET-проверка ожидает линейные параметры либо variant=surface_area в structured facts.",
            )

        variant = facts.get("variant")
        surface_fields = {
            "monolayer_amount_per_mass",
            "molecular_cross_section",
            "avogadro_constant",
            "specific_surface",
        }
        if variant == "surface_area":
            return self._evaluate_surface_area(task, facts)
        if variant not in (None, "linear"):
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Неизвестный variant блока bet; допустимы linear и surface_area.",
                variant=variant,
            )
        if any(field in facts for field in surface_fields):
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Для расчёта удельной поверхности нужен явный variant=surface_area.",
            )
        return self._evaluate_linear(facts)

    def _evaluate_linear(self, facts: Mapping[str, Any]) -> CheckResult:
        slope = _float(facts.get("slope"))
        intercept = _float(facts.get("intercept"))
        capacity = _float(facts.get("monolayer_capacity"))
        constant = _float(facts.get("bet_constant"))
        if None in (slope, intercept, capacity, constant) or intercept == 0 or slope + intercept == 0:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Недостаточно конечных параметров линейной формы BET либо знаменатель равен нулю.",
            )
        if intercept <= 0:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Отрезок линейной формы BET должен быть положительным.",
                intercept=intercept,
            )
        expected_capacity = 1.0 / (slope + intercept)
        expected_constant = 1.0 + slope / intercept
        capacity_error = _relative_error(capacity, expected_capacity)
        constant_error = _relative_error(constant, expected_constant)
        evidence: dict[str, Any] = {
            "expected_monolayer_capacity": expected_capacity,
            "actual_monolayer_capacity": capacity,
            "expected_bet_constant": expected_constant,
            "actual_bet_constant": constant,
            "capacity_relative_error": capacity_error,
            "constant_relative_error": constant_error,
        }
        if capacity <= 0 or constant <= 0 or max(capacity_error, constant_error) > DEFAULT_RELATIVE_TOLERANCE:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Параметры BET не следуют из наклона и отрезка линейной зависимости.",
                **evidence,
            )
        pressure_ratios = facts.get("relative_pressures")
        if pressure_ratios is not None:
            if not isinstance(pressure_ratios, list):
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "relative_pressures должен быть массивом чисел.",
                    **evidence,
                )
            values = [value for item in pressure_ratios if (value := _float(item)) is not None]
            if len(values) != len(pressure_ratios):
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Все значения relative_pressures должны быть конечными числами.",
                    **evidence,
                )
            evidence["relative_pressures"] = values
            if any(value <= 0 or value >= 1 for value in values):
                return _result(
                    self.check_id,
                    CheckState.FAIL,
                    "Относительное давление p/p0 в BET должно лежать строго между 0 и 1.",
                    **evidence,
                )
            if any(value < 0.05 or value > 0.35 for value in values):
                return _result(
                    self.check_id,
                    CheckState.WARNING,
                    "Алгебра BET верна, но часть точек вне типового линейного диапазона 0.05–0.35.",
                    **evidence,
                )
        return _result(self.check_id, CheckState.PASS, "Линейные параметры BET согласованы.", **evidence)

    def _evaluate_surface_area(
        self,
        task: ChemistryTask,
        facts: Mapping[str, Any],
    ) -> CheckResult:
        required_fields = {
            "monolayer_amount_per_mass": Dimension.AMOUNT_PER_MASS,
            "molecular_cross_section": Dimension.AREA,
            "avogadro_constant": Dimension.RECIPROCAL_AMOUNT,
            "specific_surface": Dimension.SPECIFIC_SURFACE,
        }
        parsed = {
            field: parse_measurement(facts.get(field), expected=dimension)
            for field, dimension in required_fields.items()
        }
        invalid_fields = [field for field, value in parsed.items() if value is None]
        if invalid_fields:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Для Ssp=am·NA·s0 нужны все четыре величины с явными единицами.",
                invalid_fields=invalid_fields,
                required={field: dimension.value for field, dimension in required_fields.items()},
            )

        monolayer = parsed["monolayer_amount_per_mass"]
        cross_section = parsed["molecular_cross_section"]
        avogadro = parsed["avogadro_constant"]
        specific_surface = parsed["specific_surface"]
        assert monolayer is not None
        assert cross_section is not None
        assert avogadro is not None
        assert specific_surface is not None

        non_positive = [field for field, value in parsed.items() if value is not None and value.si_value <= 0]
        if non_positive:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Ёмкость монослоя, площадь молекулы, NA и удельная поверхность должны быть положительными.",
                non_positive=non_positive,
            )

        stated_constants = [
            value
            for value in extract_measurements(task.statement)
            if value.dimension == Dimension.RECIPROCAL_AMOUNT
        ]
        statement_names_constant = bool(
            re.search(r"(?:авогад|N\s*(?:_?\{?\s*A\s*\}?|ₐ))", task.statement, re.IGNORECASE)
        )
        constant_error = _relative_error(avogadro.si_value, AVOGADRO_CONSTANT)
        stated_match = any(
            close_si(value.si_value, avogadro.si_value, relative_tolerance=0.002)
            for value in stated_constants
        )
        if constant_error > 0.005 or not statement_names_constant or not stated_match:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Значение NA неверно, скрыто от студента или расходится со structured facts.",
                expected_avogadro_constant=AVOGADRO_CONSTANT,
                actual_avogadro_constant=avogadro.si_value,
                stated_match=stated_match,
            )

        expected_surface = monolayer.si_value * avogadro.si_value * cross_section.si_value
        error = _relative_error(specific_surface.si_value, expected_surface)
        evidence = {
            "variant": "surface_area",
            "relation": "Ssp=am*NA*s0",
            "monolayer_amount_mol_per_kg": monolayer.si_value,
            "molecular_cross_section_m2": cross_section.si_value,
            "avogadro_constant_per_mol": avogadro.si_value,
            "expected_specific_surface_m2_per_kg": expected_surface,
            "actual_specific_surface_m2_per_kg": specific_surface.si_value,
            "relative_error": error,
        }
        if error > DEFAULT_RELATIVE_TOLERANCE:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Удельная поверхность не согласуется с Ssp=am·NA·s0 после перевода в SI.",
                **evidence,
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Удельная поверхность и переводы am, s0 и Ssp согласованы.",
            **evidence,
        )


@dataclass(frozen=True)
class SmoluchowskiCheck:
    check_id: str = "colloid.smoluchowski"
    disciplines: frozenset[ChemistryDiscipline] = frozenset({ChemistryDiscipline.COLLOID})

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        facts = _facts(task, "smoluchowski")
        if facts is None:
            return _result(
                self.check_id,
                CheckState.NOT_APPLICABLE,
                "Проверка Смолуховского ожидает mobility, viscosity, permittivity и zeta в structured facts.",
            )
        mobility = _fact_measurement(facts, ("mobility",), {Dimension.MOBILITY})
        viscosity = _fact_measurement(facts, ("viscosity",), {Dimension.VISCOSITY})
        zeta = _fact_measurement(facts, ("zeta", "zeta_potential"), {Dimension.VOLTAGE})
        vacuum_permittivity = _fact_measurement(
            facts,
            ("vacuum_permittivity", "epsilon_0"),
            {Dimension.PERMITTIVITY},
        )
        relative_permittivity = _float(facts.get("relative_permittivity"))
        if (
            mobility is None
            or viscosity is None
            or zeta is None
            or vacuum_permittivity is None
            or relative_permittivity is None
            or relative_permittivity <= 0
        ):
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Для расчёта ζ нужны подвижность, вязкость, εr, ε0 и ζ с явными единицами.",
            )
        if viscosity.si_value <= 0 or vacuum_permittivity.si_value <= 0:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Вязкость и электрическая постоянная должны быть положительными.",
                viscosity_si=viscosity.si_value,
                vacuum_permittivity_si=vacuum_permittivity.si_value,
            )
        stated_constants = [
            value
            for value in extract_measurements(task.statement)
            if value.dimension == Dimension.PERMITTIVITY
        ]
        if not stated_constants:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Электрическая постоянная ε0 нужна для решения, но её численное значение не дано студенту в условии.",
            )
        constant_error = _relative_error(vacuum_permittivity.si_value, VACUUM_PERMITTIVITY)
        if constant_error > 0.002 or not any(
            close_si(value.si_value, vacuum_permittivity.si_value, relative_tolerance=0.002)
            for value in stated_constants
        ):
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Заданное значение ε0 неверно или не совпадает со структурированными данными.",
                expected_epsilon_0=VACUUM_PERMITTIVITY,
                actual_epsilon_0=vacuum_permittivity.si_value,
            )
        expected = mobility.si_value * viscosity.si_value / (
            relative_permittivity * vacuum_permittivity.si_value
        )
        error = _relative_error(zeta.si_value, expected)
        evidence: dict[str, Any] = {
            "expected_zeta_v": expected,
            "actual_zeta_v": zeta.si_value,
            "relative_error": error,
            "relation": "zeta=eta*mobility/(epsilon_r*epsilon_0)",
            "vacuum_permittivity": vacuum_permittivity.si_value,
        }
        if error > DEFAULT_RELATIVE_TOLERANCE:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "ζ-потенциал не согласуется с приближением Смолуховского.",
                **evidence,
            )
        kappa_a = _float(facts.get("kappa_a"))
        claims_applicable = facts.get("claims_applicable")
        if "kappa_a" in facts and kappa_a is None:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "kappa_a должен быть конечным безразмерным числом.",
                **evidence,
            )
        if claims_applicable is not None and not isinstance(claims_applicable, bool):
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "claims_applicable должен быть логическим значением.",
                **evidence,
            )
        if claims_applicable is not None and kappa_a is None:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Для проверки claims_applicable требуется kappa_a.",
                **evidence,
            )
        if kappa_a is not None:
            evidence["kappa_a"] = kappa_a
            if kappa_a <= 0:
                return _result(
                    self.check_id,
                    CheckState.FAIL,
                    "Безразмерный параметр κa должен быть положительным.",
                    **evidence,
                )
            if claims_applicable is True and kappa_a <= 10:
                return _result(
                    self.check_id,
                    CheckState.FAIL,
                    "Заявлено приближение Смолуховского при κa, недостаточном для тонкого двойного слоя.",
                    **evidence,
                )
            if claims_applicable is True and kappa_a < 50:
                return _result(
                    self.check_id,
                    CheckState.WARNING,
                    "Расчёт верен, но κa не демонстрирует уверенно асимптотический режим κa≫1.",
                    **evidence,
                )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Расчёт по Смолуховскому и доступные условия применимости согласованы.",
            **evidence,
        )


@dataclass(frozen=True)
class DlvoCheck:
    check_id: str = "colloid.dlvo"
    disciplines: frozenset[ChemistryDiscipline] = frozenset({ChemistryDiscipline.COLLOID})

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        facts = _facts(task, "dlvo")
        if facts is None:
            return _result(
                self.check_id,
                CheckState.NOT_APPLICABLE,
                "DLVO-проверка ожидает ionic_strength/debye_length или условия применимости в structured facts.",
            )
        evidence: dict[str, Any] = {}
        failures: list[str] = []
        warnings: list[str] = []
        performed = False

        ionic_strength = _fact_measurement(
            facts, ("ionic_strength",), {Dimension.AMOUNT_CONCENTRATION}
        )
        debye_length = _fact_measurement(facts, ("debye_length",), {Dimension.LENGTH})
        if "ionic_strength" in facts and ionic_strength is None:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "ionic_strength не распознана как молярная концентрация с единицей.",
            )
        if "debye_length" in facts and debye_length is None:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "debye_length не распознана как длина с единицей.",
            )
        if (ionic_strength is None) != (debye_length is None):
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Для проверки длины Дебая нужны одновременно ionic_strength и debye_length.",
            )
        if ionic_strength and debye_length:
            if facts.get("debye_model") != "water_1_1_25c":
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Формула 0.304/sqrt(I) допустима только при явно заданной модели water_1_1_25c.",
                )
            performed = True
            ionic_strength_mol_l = ionic_strength.si_value / 1000.0
            if ionic_strength_mol_l <= 0:
                failures.append("ionic_strength_non_positive")
            elif debye_length.si_value <= 0:
                failures.append("debye_length_non_positive")
            else:
                # 25 °C water, monovalent electrolyte.  Other media/valences must
                # provide a dedicated model rather than silently use this shortcut.
                expected_nm = 0.304 / math.sqrt(ionic_strength_mol_l)
                actual_nm = debye_length.si_value * 1e9
                error = _relative_error(actual_nm, expected_nm)
                evidence.update(
                    expected_debye_length_nm=expected_nm,
                    actual_debye_length_nm=actual_nm,
                    debye_relative_error=error,
                )
                if error > 0.03:
                    failures.append("debye_length_mismatch")

        particle_radius = _fact_measurement(facts, ("particle_radius",), {Dimension.LENGTH})
        separation = _fact_measurement(facts, ("separation",), {Dimension.LENGTH})
        if "particle_radius" in facts and particle_radius is None:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "particle_radius не распознан как длина с единицей.",
            )
        if "separation" in facts and separation is None:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "separation не распознано как длина с единицей.",
            )
        claims_derjaguin = facts.get("claims_derjaguin")
        if claims_derjaguin is not None and not isinstance(claims_derjaguin, bool):
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "claims_derjaguin должен быть логическим значением.",
            )
        if claims_derjaguin is not None and (particle_radius is None or separation is None):
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "Для проверки приближения Дерягина нужны particle_radius и separation.",
            )
        if particle_radius and separation:
            if not isinstance(claims_derjaguin, bool):
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Для геометрической проверки нужен явный boolean claims_derjaguin.",
                )
            performed = True
            if particle_radius.si_value <= 0:
                failures.append("particle_radius_non_positive")
            if separation.si_value <= 0:
                failures.append("separation_non_positive")
            if particle_radius.si_value > 0 and separation.si_value > 0:
                ratio = separation.si_value / particle_radius.si_value
                evidence["separation_to_radius"] = ratio
                answer_numbers = _numeric_literals(task.answer)
                ratio_in_answer = any(_relative_error(value, ratio) <= 0.01 for value in answer_numbers)
                answer_text = task.answer.casefold()
                negative_conclusion = re.search(r"(?:не\s+(?:допуст|примен)|недопуст|непримен)", answer_text)
                positive_conclusion = re.search(r"(?:допуст|примен)", answer_text)
                conclusion_matches = (
                    claims_derjaguin is True and positive_conclusion is not None and negative_conclusion is None
                ) or (claims_derjaguin is False and negative_conclusion is not None)
                evidence.update(
                    ratio_present_in_answer=ratio_in_answer,
                    applicability_conclusion_matches=conclusion_matches,
                )
                if not ratio_in_answer:
                    failures.append("answer_missing_separation_to_radius")
                if not conclusion_matches:
                    failures.append("answer_applicability_conclusion_mismatch")
                if claims_derjaguin is True and ratio > 0.1:
                    failures.append("derjaguin_requires_h_much_less_than_radius")
                elif claims_derjaguin is True and ratio > 0.05:
                    warnings.append("derjaguin_borderline_geometry")

        sufficiency_claim = facts.get("claims_dlvo_sufficient")
        non_dlvo_forces = facts.get("non_dlvo_forces_present")
        if sufficiency_claim is not None or non_dlvo_forces is not None:
            if not isinstance(sufficiency_claim, bool) or not isinstance(non_dlvo_forces, bool):
                return _result(
                    self.check_id,
                    CheckState.INDETERMINATE,
                    "Условия claims_dlvo_sufficient/non_dlvo_forces_present должны быть парой boolean.",
                )
            performed = True
            if sufficiency_claim and non_dlvo_forces:
                failures.append("classical_dlvo_ignores_declared_non_dlvo_forces")
        evidence["failures"] = failures
        evidence["warnings"] = warnings
        if failures:
            return _result(
                self.check_id,
                CheckState.FAIL,
                "Численный результат или заявленные условия применимости DLVO неконсистентны.",
                **evidence,
            )
        if warnings:
            return _result(
                self.check_id,
                CheckState.WARNING,
                "Расчёт не опровергнут, но геометрическое приближение DLVO погранично.",
                **evidence,
            )
        if not performed:
            return _result(
                self.check_id,
                CheckState.INDETERMINATE,
                "DLVO facts не содержат ни проверяемой длины Дебая, ни условий применимости.",
            )
        return _result(
            self.check_id,
            CheckState.PASS,
            "Проверяемые численные связи и условия применимости DLVO согласованы.",
            **evidence,
        )


DEFAULT_CHECKS = (
    UnitConsistencyCheck(),
    ScaleCompatibilityCheck(),
    ReactionBalanceCheck(),
    StoichiometryCheck(),
    DilutionCheck(),
    TitrationCheck(),
    GravimetryCheck(),
    ConductometryCheck(),
    FaradayCheck(),
    CalibrationCheck(),
    BetCheck(),
    SmoluchowskiCheck(),
    DlvoCheck(),
)
