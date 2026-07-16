import pytest
from pydantic import ValidationError

from app.schemas import TaskTemplateCreate
from app.services.chemistry_checks import ConductometryCheck, GravimetryCheck
from app.services.chemistry_facts import (
    chemistry_admission_evidence,
    normalize_chemistry_facts,
    required_check_ids,
)
from app.services.chemistry_validation import CheckState, ChemistryTask


def _gravimetry_facts(**changes: object) -> dict:
    facts = {
        "analyte_stoichiometric_coefficient": 2,
        "weighing_form_stoichiometric_coefficient": 1,
        "analyte_molar_mass": "55.845 g/mol",
        "weighing_form_molar_mass": "159.687 g/mol",
        "gravimetric_factor": 0.69943,
        "weighing_form_mass": "0.5000 g",
        "analyte_mass": "0.34972 g",
    }
    facts.update(changes)
    return {"gravimetry": facts}


def _gravimetry_task(**changes: object) -> ChemistryTask:
    values = {
        "discipline": "Аналитическая химия",
        "statement": (
            "После прокаливания получили m(Fe2O3) = 0.5000 g. "
            "Используйте M(Fe) = 55.845 g/mol и M(Fe2O3) = 159.687 g/mol."
        ),
        "facts": _gravimetry_facts(),
    }
    values.update(changes)
    return ChemistryTask(**values)


def _conductometry_facts(**changes: object) -> dict:
    facts = {
        "resistance": "1.000 kΩ",
        "conductance": "1.000 mS",
        "cell_constant": "1.000 cm^-1",
        "conductivity": "1.000 mS/cm",
    }
    facts.update(changes)
    return {"conductometry": facts}


def _conductometry_task(**changes: object) -> ChemistryTask:
    values = {
        "discipline": "Аналитическая химия",
        "statement": "Измерено R = 1.000 kΩ; постоянная ячейки K_cell = 1.000 cm^-1.",
        "facts": _conductometry_facts(),
    }
    values.update(changes)
    return ChemistryTask(**values)


def test_gravimetry_checks_factor_and_mass_chain() -> None:
    result = GravimetryCheck().evaluate(_gravimetry_task())

    assert result.state == CheckState.PASS
    assert result.evidence["expected_gravimetric_factor"] == pytest.approx(
        2 * 55.845 / 159.687
    )
    assert result.evidence["expected_analyte_mass_kg"] == pytest.approx(0.00034972, rel=1e-4)
    assert result.evidence["rounding_audit"]["gravimetric_factor"]["status"] == "pass"


@pytest.mark.parametrize(
    ("factor", "expected_at_precision"),
    [
        (0.20315, "0.20314"),
        (0.2032, "0.2031"),
        (0.3622, "0.3621"),
    ],
)
def test_gravimetry_rejects_close_but_impossibly_rounded_factor(
    factor: float, expected_at_precision: str
) -> None:
    if factor == 0.3622:
        statement = (
            "Получено m(G)=1.0000 g. Используйте M(X)=36.2084 g/mol "
            "и M(G)=100.000 g/mol; стехиометрия 1:1."
        )
        facts = _gravimetry_facts(
            analyte_stoichiometric_coefficient=1,
            analyte_molar_mass="36.2084 g/mol",
            weighing_form_molar_mass="100.000 g/mol",
            gravimetric_factor=factor,
            weighing_form_mass="1.0000 g",
            analyte_mass="0.36208 g",
        )
    else:
        statement = (
            "Получено m(Ni(Dm)2)=0.2234 g. Используйте M(Ni)=58.69 g/mol "
            "и M(Ni(Dm)2)=288.91 g/mol; стехиометрия 1:1."
        )
        facts = _gravimetry_facts(
            analyte_stoichiometric_coefficient=1,
            analyte_molar_mass="58.69 g/mol",
            weighing_form_molar_mass="288.91 g/mol",
            gravimetric_factor=factor,
            weighing_form_mass="0.2234 g",
            analyte_mass="0.04538 g",
        )

    result = GravimetryCheck().evaluate(_gravimetry_task(statement=statement, facts=facts))

    assert result.state == CheckState.FAIL
    factor_error = next(
        error for error in result.evidence["rounding_errors"] if error["field"] == "gravimetric_factor"
    )
    assert factor_error["expected_at_declared_precision"] == expected_at_precision


def test_gravimetry_accepts_correct_rounding_at_input_precision_or_better() -> None:
    statement = (
        "Получено m(Ni(Dm)2)=0.2234 g. Используйте M(Ni)=58.69 g/mol "
        "и M(Ni(Dm)2)=288.91 g/mol; стехиометрия 1:1."
    )
    four_digits = _gravimetry_facts(
        analyte_stoichiometric_coefficient=1,
        analyte_molar_mass="58.69 g/mol",
        weighing_form_molar_mass="288.91 g/mol",
        gravimetric_factor=0.2031,
        weighing_form_mass="0.2234 g",
        analyte_mass="0.04538 g",
    )
    five_digits = _gravimetry_facts(
        analyte_stoichiometric_coefficient=1,
        analyte_molar_mass="58.69 g/mol",
        weighing_form_molar_mass="288.91 g/mol",
        gravimetric_factor=0.20314,
        weighing_form_mass="0.2234 g",
        analyte_mass="0.045382 g",
    )

    assert (
        GravimetryCheck().evaluate(_gravimetry_task(statement=statement, facts=four_digits)).state
        == CheckState.PASS
    )
    assert (
        GravimetryCheck().evaluate(_gravimetry_task(statement=statement, facts=five_digits)).state
        == CheckState.PASS
    )


@pytest.mark.parametrize(
    "facts",
    [
        _gravimetry_facts(analyte_stoichiometric_coefficient=1),
        _gravimetry_facts(gravimetric_factor=0.5, analyte_mass="0.2500 g"),
        _gravimetry_facts(analyte_mass="0.5000 g"),
    ],
)
def test_gravimetry_rejects_adversarial_consistent_mutations(facts: dict) -> None:
    result = GravimetryCheck().evaluate(_gravimetry_task(facts=facts))

    assert result.state == CheckState.FAIL


def test_gravimetry_requires_units_finite_values_and_positive_magnitudes() -> None:
    missing_unit = GravimetryCheck().evaluate(
        _gravimetry_task(facts=_gravimetry_facts(analyte_molar_mass=55.845))
    )
    non_finite = GravimetryCheck().evaluate(
        _gravimetry_task(facts=_gravimetry_facts(gravimetric_factor="NaN"))
    )
    negative = GravimetryCheck().evaluate(
        _gravimetry_task(
            facts=_gravimetry_facts(
                gravimetric_factor=-0.69944,
                weighing_form_mass="-0.5000 g",
                analyte_mass="0.34972 g",
            )
        )
    )

    assert missing_unit.state == CheckState.INDETERMINATE
    assert missing_unit.evidence["missing_or_invalid"] == ["analyte_molar_mass"]
    assert non_finite.state == CheckState.INDETERMINATE
    assert negative.state == CheckState.FAIL
    assert set(negative.evidence["non_positive"]) == {
        "gravimetric_factor",
        "weighing_form_mass",
    }


def test_gravimetry_rejects_inputs_hidden_from_student() -> None:
    result = GravimetryCheck().evaluate(
        _gravimetry_task(statement="После прокаливания получили m(Fe2O3) = 0.5000 g.")
    )

    assert result.state == CheckState.FAIL
    assert set(result.evidence["hidden_inputs"]) == {
        "analyte_molar_mass",
        "weighing_form_molar_mass",
    }


def test_conductometry_cross_checks_resistance_cell_constant_and_conductivity() -> None:
    result = ConductometryCheck().evaluate(_conductometry_task())

    assert result.state == CheckState.PASS
    assert result.evidence["expected_conductance_s"] == pytest.approx(1e-3)
    assert result.evidence["expected_conductivity_s_per_m"] == pytest.approx(0.1)


@pytest.mark.parametrize(
    "facts",
    [
        _conductometry_facts(conductance="2.000 mS", conductivity="2.000 mS/cm"),
        _conductometry_facts(conductivity="0.100 mS/cm"),
    ],
)
def test_conductometry_rejects_adversarial_chain_mutations(facts: dict) -> None:
    result = ConductometryCheck().evaluate(_conductometry_task(facts=facts))

    assert result.state == CheckState.FAIL


def test_conductometry_requires_units_finite_values_and_positive_magnitudes() -> None:
    missing_unit = ConductometryCheck().evaluate(
        _conductometry_task(facts=_conductometry_facts(resistance=1000))
    )
    non_finite = ConductometryCheck().evaluate(
        _conductometry_task(facts=_conductometry_facts(conductivity="NaN S/m"))
    )
    negative = ConductometryCheck().evaluate(
        _conductometry_task(
            statement="Измерено R = -1.000 kΩ; постоянная ячейки K_cell = -1.000 cm^-1.",
            facts=_conductometry_facts(
                resistance="-1.000 kΩ",
                conductance="-1.000 mS",
                cell_constant="-1.000 cm^-1",
                conductivity="1.000 mS/cm",
            ),
        )
    )

    assert missing_unit.state == CheckState.INDETERMINATE
    assert missing_unit.evidence["missing_or_invalid"] == ["resistance"]
    assert non_finite.state == CheckState.INDETERMINATE
    assert negative.state == CheckState.FAIL
    assert set(negative.evidence["non_positive"]) == {
        "resistance",
        "conductance",
        "cell_constant",
    }


def test_conductometry_rejects_hidden_cell_constant() -> None:
    result = ConductometryCheck().evaluate(
        _conductometry_task(statement="Измерено сопротивление R = 1.000 kΩ.")
    )

    assert result.state == CheckState.FAIL
    assert result.evidence["hidden_inputs"] == ["cell_constant"]


@pytest.mark.parametrize("check_id", ["analytical.gravimetry", "analytical.conductometry"])
def test_template_schema_accepts_new_checks(check_id: str) -> None:
    template = TaskTemplateCreate(name="Продвинутая аналитическая задача", chemistry_check=check_id)

    assert template.chemistry_check == check_id


def test_template_schema_still_rejects_unknown_analytical_check() -> None:
    with pytest.raises(ValidationError):
        TaskTemplateCreate(name="Задача", chemistry_check="analytical.unverified")


def test_fact_registry_and_admission_require_explicit_new_check() -> None:
    facts = _gravimetry_facts()

    assert normalize_chemistry_facts(facts) == facts
    assert required_check_ids("analytical.gravimetry", facts) == {"analytical.gravimetry"}
    evidence = chemistry_admission_evidence(
        discipline="Аналитическая химия",
        statement=_gravimetry_task().statement,
        reference_solution="F=0.69943; m(Fe)=0.34972 g.",
        answer="0.34972 g",
        topic="Гравиметрия",
        facts=facts,
        facts_source="test",
        chemistry_check="analytical.gravimetry",
    )

    assert evidence["admission_effect"] == "pass"
    assert evidence["required_not_passed"] == []
