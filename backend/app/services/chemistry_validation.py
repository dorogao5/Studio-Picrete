"""Extensible, fail-closed deterministic validation for chemistry tasks.

The admission policy consumes this toolkit as one evidence layer. A green
deterministic report is never sufficient on its own: source closure, two
independent solutions, the subject critic and rubric integrity remain required.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol


CHEMISTRY_VALIDATION_VERSION = "chemistry-evidence-v2"


class ChemistryDiscipline(StrEnum):
    GENERAL = "general_inorganic"
    ANALYTICAL = "analytical"
    COLLOID = "colloid"
    UNKNOWN = "unknown"


class CheckState(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    INDETERMINATE = "indeterminate"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


def infer_discipline(value: str | ChemistryDiscipline) -> ChemistryDiscipline:
    if isinstance(value, ChemistryDiscipline):
        return value
    normalized = (value or "").casefold()
    if any(token in normalized for token in ("аналит", "analytical")):
        return ChemistryDiscipline.ANALYTICAL
    if any(token in normalized for token in ("коллоид", "colloid", "поверхност")):
        return ChemistryDiscipline.COLLOID
    if any(token in normalized for token in ("неорган", "общая хим", "inorganic", "general chem")):
        return ChemistryDiscipline.GENERAL
    return ChemistryDiscipline.UNKNOWN


@dataclass(frozen=True)
class ChemistryTask:
    discipline: ChemistryDiscipline | str
    statement: str
    reference_solution: str = ""
    answer: str = ""
    topic: str = ""
    # Structured facts are optional.  They let a DeepSeek extraction pass hand
    # exact quantities to deterministic checks; every physical measurement must
    # be ``{"value": number, "unit": string}`` or ``"number unit"`` and no
    # check trusts an LLM confidence score.  Built-in block names are:
    # stoichiometry, dilution, titration, gravimetry, conductometry, faraday,
    # calibration, bet, smoluchowski and dlvo. Individual checks document their
    # required fields.
    facts: Mapping[str, Any] = field(default_factory=dict)

    @property
    def normalized_discipline(self) -> ChemistryDiscipline:
        return infer_discipline(self.discipline)

    @property
    def full_text(self) -> str:
        return "\n".join(filter(None, (self.statement, self.reference_solution, self.answer)))

    @property
    def solution_text(self) -> str:
        return "\n".join(filter(None, (self.reference_solution, self.answer)))


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    state: CheckState
    message: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.state in {CheckState.FAIL, CheckState.ERROR}

    @property
    def conclusive(self) -> bool:
        return self.state not in {CheckState.INDETERMINATE, CheckState.NOT_APPLICABLE, CheckState.ERROR}

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        value["blocking"] = self.blocking
        return value


class ChemistryCheck(Protocol):
    check_id: str
    disciplines: frozenset[ChemistryDiscipline]

    def evaluate(self, task: ChemistryTask) -> CheckResult: ...


@dataclass(frozen=True)
class ChemistryValidationReport:
    discipline: ChemistryDiscipline
    results: tuple[CheckResult, ...]

    @property
    def blocking_failures(self) -> tuple[CheckResult, ...]:
        return tuple(result for result in self.results if result.blocking)

    @property
    def indeterminate_checks(self) -> tuple[CheckResult, ...]:
        return tuple(result for result in self.results if result.state == CheckState.INDETERMINATE)

    @property
    def warning_checks(self) -> tuple[CheckResult, ...]:
        return tuple(result for result in self.results if result.state == CheckState.WARNING)

    @property
    def applicable_count(self) -> int:
        return sum(result.state != CheckState.NOT_APPLICABLE for result in self.results)

    @property
    def deterministic_pass(self) -> bool:
        """True only when at least one check ran and every applicable check concluded safely.

        This is evidence for a wider admission policy, not an auto-admission
        decision: source grounding, independent solving and pedagogical checks
        remain outside this toolkit.
        """

        if self.applicable_count == 0:
            return False
        return not self.blocking_failures and not self.indeterminate_checks and not self.warning_checks

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_version": CHEMISTRY_VALIDATION_VERSION,
            "discipline": self.discipline.value,
            "deterministic_pass": self.deterministic_pass,
            "applicable_count": self.applicable_count,
            "blocking_codes": [result.check_id for result in self.blocking_failures],
            "indeterminate_codes": [result.check_id for result in self.indeterminate_checks],
            "warning_codes": [result.check_id for result in self.warning_checks],
            "results": [result.to_dict() for result in self.results],
        }


class ChemistryCheckRegistry:
    def __init__(self) -> None:
        self._checks: dict[str, ChemistryCheck] = {}

    def register(self, check: ChemistryCheck) -> None:
        if check.check_id in self._checks:
            raise ValueError(f"chemistry check {check.check_id!r} is already registered")
        self._checks[check.check_id] = check

    def available(self) -> tuple[str, ...]:
        return tuple(self._checks)

    def run(
        self,
        task: ChemistryTask,
        *,
        selected_checks: set[str] | None = None,
    ) -> ChemistryValidationReport:
        discipline = task.normalized_discipline
        results: list[CheckResult] = []
        for check_id, check in self._checks.items():
            if selected_checks is not None and check_id not in selected_checks:
                continue
            if check.disciplines and discipline not in check.disciplines:
                continue
            try:
                result = check.evaluate(task)
            except Exception as exc:  # fail closed at the plugin boundary
                result = CheckResult(
                    check_id=check_id,
                    state=CheckState.ERROR,
                    message="Внутренняя ошибка детерминированной химической проверки.",
                    evidence={"exception_type": type(exc).__name__, "exception": str(exc)[:300]},
                )
            if result.check_id != check_id:
                raise ValueError(f"check {check_id!r} returned result for {result.check_id!r}")
            results.append(result)
        return ChemistryValidationReport(discipline=discipline, results=tuple(results))


def default_chemistry_registry() -> ChemistryCheckRegistry:
    # Late import keeps individual checks reusable without an import cycle.
    from app.services.chemistry_checks import DEFAULT_CHECKS

    registry = ChemistryCheckRegistry()
    for check in DEFAULT_CHECKS:
        registry.register(check)
    return registry


def validate_chemistry_task(
    task: ChemistryTask,
    *,
    registry: ChemistryCheckRegistry | None = None,
    selected_checks: set[str] | None = None,
) -> ChemistryValidationReport:
    return (registry or default_chemistry_registry()).run(task, selected_checks=selected_checks)
