from dataclasses import dataclass

import pytest

from app.services.chemistry_checks import (
    BetCheck,
    CalibrationCheck,
    DilutionCheck,
    DlvoCheck,
    FaradayCheck,
    ReactionBalanceCheck,
    ScaleCompatibilityCheck,
    SmoluchowskiCheck,
    StoichiometryCheck,
    TitrationCheck,
    UnitConsistencyCheck,
)
from app.services.chemistry_validation import (
    CheckResult,
    CheckState,
    ChemistryCheckRegistry,
    ChemistryDiscipline,
    ChemistryTask,
    CHEMISTRY_VALIDATION_VERSION,
    validate_chemistry_task,
)


def test_unit_check_validates_conversion_and_rejects_wrong_one() -> None:
    valid = UnitConsistencyCheck().evaluate(
        ChemistryTask("Неорганическая химия", "Переведите объём.", answer="V = 25 mL = 0.025 L")
    )
    invalid = UnitConsistencyCheck().evaluate(
        ChemistryTask("Неорганическая химия", "Переведите объём.", answer="V = 25 mL = 0.25 L")
    )

    assert valid.state == CheckState.PASS
    assert invalid.state == CheckState.FAIL
    assert invalid.evidence["issues"][0]["reason"] == "conversion_value_mismatch"


def test_unit_check_requires_unit_for_unambiguously_dimensional_answer() -> None:
    result = UnitConsistencyCheck().evaluate(
        ChemistryTask(
            "Аналитическая химия",
            "Рассчитайте концентрацию аналита после разбавления.",
            answer="Ответ: 0.025",
        )
    )

    assert result.state == CheckState.FAIL
    assert result.evidence["issues"] == [
        {"reason": "dimensional_answer_without_unit", "answer": "Ответ: 0.025"}
    ]


def test_reaction_check_ignores_unbalanced_prompt_and_checks_reference_answer() -> None:
    result = ReactionBalanceCheck().evaluate(
        ChemistryTask(
            "Неорганическая химия",
            "Расставьте коэффициенты: H2 + O2 -> H2O.",
            reference_solution="Итог: 2H2 + O2 -> 2H2O",
        )
    )

    assert result.state == CheckState.PASS


def test_scale_check_rejects_pauling_values_in_mulliken_energy_formula() -> None:
    result = ScaleCompatibilityCheck().evaluate(
        ChemistryTask(
            "Неорганическая химия",
            "Вычислите сродство по формуле Малликена E = 2χ - I.",
            reference_solution=(
                "Берём χ(C)=2,5 по шкале Полинга. Применяем формулу Малликена: "
                "E = 2·2,5 - 11,26 эВ."
            ),
        )
    )

    assert result.state == CheckState.FAIL
    assert "нельзя подставлять" in result.message


def test_scale_check_allows_explicit_noncomputational_comparison() -> None:
    result = ScaleCompatibilityCheck().evaluate(
        ChemistryTask(
            "Неорганическая химия",
            "Сравните определения электроотрицательности по Полингу и Малликену.",
            reference_solution="Шкалы построены по разным исходным величинам и напрямую не смешиваются.",
        )
    )

    assert result.state == CheckState.PASS


def test_structured_stoichiometry_finds_limiting_reagent_and_product() -> None:
    task = ChemistryTask(
        "Неорганическая химия",
        "Определите выход воды.",
        facts={
            "stoichiometry": {
                "reaction": "2H2 + O2 -> 2H2O",
                "reactant_amounts": {"H2": "1.00 mol", "O2": "1.00 mol"},
                "target_species": "H2O",
                "target_amount": "1.00 mol",
                "limiting_reagent": "H2",
            }
        },
    )

    result = StoichiometryCheck().evaluate(task)

    assert result.state == CheckState.PASS
    assert result.evidence["limiting_reagents"] == ["H2"]
    assert result.evidence["expected_mol"] == pytest.approx(1.0)


def test_stoichiometry_rejects_product_not_supported_by_limiting_reagent() -> None:
    task = ChemistryTask(
        "Неорганическая химия",
        "Определите выход воды.",
        facts={
            "stoichiometry": {
                "reaction": "2H2 + O2 -> 2H2O",
                "reactant_amounts": {"H2": "1.00 mol", "O2": "1.00 mol"},
                "target_species": "H2O",
                "target_amount": "2.00 mol",
            }
        },
    )

    assert StoichiometryCheck().evaluate(task).state == CheckState.FAIL


def test_structured_stoichiometry_matches_ionic_species_by_charge() -> None:
    result = StoichiometryCheck().evaluate(
        ChemistryTask(
            "Неорганическая химия",
            "Восстановление железа(III)",
            facts={
                "stoichiometry": {
                    "reaction": "Fe^3+ + e- -> Fe^2+",
                    "reactant_amounts": {"Fe^3+": "1.00 mol", "e-": "2.00 mol"},
                    "target_species": "Fe^2+",
                    "target_amount": "1.00 mol",
                    "limiting_reagent": "Fe^3+",
                }
            },
        )
    )

    assert result.state == CheckState.PASS
    assert result.evidence["limiting_reagents"] == ["Fe^3+"]


def test_stoichiometry_accepts_explicit_excess_medium_species() -> None:
    result = StoichiometryCheck().evaluate(
        ChemistryTask(
            "Неорганическая химия",
            "К дихромату и иодиду добавили H+ в явном избытке.",
            facts={
                "stoichiometry": {
                    "reaction": "Cr2O7^2- + 6I^- + 14H+ -> 2Cr^3+ + 3I2 + 7H2O",
                    "reactant_amounts": {"Cr2O7^2-": "0.00500 mol", "I^-": "0.0100 mol"},
                    "excess_reactants": ["H+"],
                    "target_species": "I2",
                    "target_amount": "0.00500 mol",
                    "limiting_reagent": "I^-",
                }
            },
        )
    )

    assert result.state == CheckState.PASS
    assert result.evidence["limiting_reagents"] == ["I^-"]
    assert result.evidence["excess_reactants"] == ["H+"]


@pytest.mark.parametrize(
    ("excess_reactants", "statement"),
    [
        ([], "К дихромату и иодиду добавили кислоту."),
        (["H+"], "К дихромату и иодиду добавили кислоту."),
    ],
)
def test_stoichiometry_does_not_assume_an_unquantified_reagent_is_excess(
    excess_reactants: list[str], statement: str
) -> None:
    result = StoichiometryCheck().evaluate(
        ChemistryTask(
            "Неорганическая химия",
            statement,
            facts={
                "stoichiometry": {
                    "reaction": "Cr2O7^2- + 6I^- + 14H+ -> 2Cr^3+ + 3I2 + 7H2O",
                    "reactant_amounts": {"Cr2O7^2-": "0.00500 mol", "I^-": "0.0100 mol"},
                    "excess_reactants": excess_reactants,
                    "target_species": "I2",
                    "target_amount": "0.00500 mol",
                }
            },
        )
    )

    assert result.state == CheckState.INDETERMINATE


def test_dilution_material_balance_works_across_unit_scales() -> None:
    result = DilutionCheck().evaluate(
        ChemistryTask(
            "Аналитическая химия",
            "Разбавление стандартного раствора",
            answer="c1 = 0.100 mol/L; V1 = 25.0 mL; c2 = 0.0100 mol/L; V2 = 250 mL",
        )
    )

    assert result.state == CheckState.PASS


def test_titration_uses_explicit_stoichiometric_coefficients() -> None:
    result = TitrationCheck().evaluate(
        ChemistryTask(
            "Аналитическая химия",
            "Титрование серной кислоты щёлочью",
            facts={
                "titration": {
                    "analyte": {
                        "concentration": "0.0100 mol/L",
                        "volume": "25.00 mL",
                        "stoichiometric_coefficient": 1,
                    },
                    "titrant": {
                        "concentration": "0.0200 mol/L",
                        "volume": "25.00 mL",
                        "stoichiometric_coefficient": 2,
                    },
                }
            },
        )
    )

    assert result.state == CheckState.PASS


def test_faraday_cross_checks_charge_electrons_and_deposited_mass() -> None:
    result = FaradayCheck().evaluate(
        ChemistryTask(
            "Аналитическая химия",
            "Кулонометрическое определение серебра; F = 96485.33212 C/mol.",
            facts={
                "faraday": {
                    "current": "1.000 A",
                    "time": "96485.33212 s",
                    "charge": "96485.33212 C",
                    "electron_amount": "1.000 mol",
                    "mass": "107.8682 g",
                    "molar_mass": "107.8682 g/mol",
                    "electrons": 1,
                    "current_efficiency": 1.0,
                    "faraday_constant": "96485.33212 C/mol",
                }
            },
        )
    )

    assert result.state == CheckState.PASS
    assert {item["relation"] for item in result.evidence["checks"]} == {
        "Q=It",
        "n_e=Q/F",
        "m=M Q eta/(zF)",
    }


def test_calibration_warns_on_mathematically_correct_extrapolation() -> None:
    result = CalibrationCheck().evaluate(
        ChemistryTask(
            "Аналитическая химия",
            "Градуировочная зависимость",
            facts={
                "calibration": {
                    "slope": 2.0,
                    "intercept": 0.5,
                    "signal": 5.5,
                    "concentration": 2.5,
                    "calibration_range": [0.0, 2.0],
                }
            },
        )
    )

    assert result.state == CheckState.WARNING
    assert result.evidence["inside_calibration_range"] is False


def test_bet_checks_slope_intercept_and_pressure_window() -> None:
    result = BetCheck().evaluate(
        ChemistryTask(
            "Коллоидная химия",
            "Линеаризация BET",
            facts={
                "bet": {
                    "slope": 0.4,
                    "intercept": 0.1,
                    "monolayer_capacity": 2.0,
                    "bet_constant": 5.0,
                    "relative_pressures": [0.05, 0.15, 0.30],
                }
            },
        )
    )

    assert result.state == CheckState.PASS


def _bet_surface_facts(**overrides: object) -> dict[str, object]:
    facts: dict[str, object] = {
        "variant": "surface_area",
        "monolayer_amount_per_mass": "1.000 mmol/g",
        "molecular_cross_section": "0.162 nm2",
        "avogadro_constant": "6.02214076e23 1/mol",
        "specific_surface": "97.5587 m2/g",
    }
    facts.update(overrides)
    return facts


def _bet_surface_task(**overrides: object) -> ChemistryTask:
    return ChemistryTask(
        "Коллоидная химия",
        "a_m = 1.000 mmol/g; s0 = 0.162 nm2; постоянная Авогадро "
        "N_A = 6.02214076e23 1/mol. Определите Ssp.",
        facts={"bet": _bet_surface_facts(**overrides)},
    )


def test_bet_surface_area_converts_mmol_per_g_and_nm2_to_m2_per_g() -> None:
    result = BetCheck().evaluate(_bet_surface_task())

    assert result.state == CheckState.PASS
    assert result.evidence["expected_specific_surface_m2_per_kg"] == pytest.approx(97558.680312)
    assert result.evidence["actual_specific_surface_m2_per_kg"] == pytest.approx(97558.7)


def test_bet_surface_area_rejects_avogadro_constant_hidden_from_student() -> None:
    task = _bet_surface_task()
    hidden = ChemistryTask(
        task.discipline,
        "a_m = 1.000 mmol/g; s0 = 0.162 nm2. Определите Ssp.",
        facts=task.facts,
    )

    result = BetCheck().evaluate(hidden)

    assert result.state == CheckState.FAIL
    assert "скрыто" in result.message


def test_bet_surface_area_rejects_unit_scale_mutation() -> None:
    result = BetCheck().evaluate(_bet_surface_task(specific_surface="97.5587 m2/kg"))

    assert result.state == CheckState.FAIL
    assert result.evidence["relative_error"] == pytest.approx(0.999)


def test_bet_surface_area_rejects_negative_physical_quantity() -> None:
    result = BetCheck().evaluate(_bet_surface_task(monolayer_amount_per_mass="-1.000 mmol/g"))

    assert result.state == CheckState.FAIL
    assert result.evidence["non_positive"] == ["monolayer_amount_per_mass"]


def test_bet_surface_area_rejects_cross_section_mutation() -> None:
    result = BetCheck().evaluate(_bet_surface_task(molecular_cross_section="1.62 nm2"))

    assert result.state == CheckState.FAIL
    assert result.evidence["relative_error"] > 0.8


def test_bet_surface_area_fails_closed_without_units_or_variant() -> None:
    no_unit = BetCheck().evaluate(_bet_surface_task(molecular_cross_section=0.162))
    no_variant = BetCheck().evaluate(_bet_surface_task(variant=None))

    assert no_unit.state == CheckState.INDETERMINATE
    assert no_unit.evidence["invalid_fields"] == ["molecular_cross_section"]
    assert no_variant.state == CheckState.INDETERMINATE


def test_smoluchowski_requires_thin_double_layer_for_applicability_claim() -> None:
    facts = {
        "smoluchowski": {
            "mobility": "3.475e-8 m2/(V*s)",
            "viscosity": "1.000 mPa*s",
            "relative_permittivity": 78.5,
            "vacuum_permittivity": "8.8541878128e-12 F/m",
            "zeta": "0.0500 V",
            "kappa_a": 5,
            "claims_applicable": True,
        }
    }

    result = SmoluchowskiCheck().evaluate(
        ChemistryTask(
            "Коллоидная химия",
            "Электрофорез; ε0 = 8.8541878128e-12 F/m.",
            facts=facts,
        )
    )

    assert result.state == CheckState.FAIL
    assert result.evidence["kappa_a"] == 5


def test_smoluchowski_rejects_constant_hidden_from_student() -> None:
    facts = {
        "smoluchowski": {
            "mobility": "3.475e-8 m2/(V*s)",
            "viscosity": "1.000 mPa*s",
            "relative_permittivity": 78.5,
            "vacuum_permittivity": "8.8541878128e-12 F/m",
            "zeta": "0.0500 V",
        }
    }

    result = SmoluchowskiCheck().evaluate(
        ChemistryTask("Коллоидная химия", "Рассчитайте ζ-потенциал.", facts=facts)
    )

    assert result.state == CheckState.FAIL
    assert "не дано студенту" in result.message


def test_dlvo_checks_debye_length_and_derjaguin_geometry() -> None:
    result = DlvoCheck().evaluate(
        ChemistryTask(
            "Коллоидная химия",
            "Оценка двойного электрического слоя",
            answer="κ^-1 = 3.04 nm; h/a = 0.020; приближение Дерягина допустимо.",
            facts={
                "dlvo": {
                    "debye_model": "water_1_1_25c",
                    "ionic_strength": "0.0100 mol/L",
                    "debye_length": "3.04 nm",
                    "particle_radius": "100 nm",
                    "separation": "2 nm",
                    "claims_derjaguin": True,
                }
            },
        )
    )

    assert result.state == CheckState.PASS
    assert result.evidence["expected_debye_length_nm"] == pytest.approx(3.04)


@pytest.mark.parametrize(
    ("answer", "claim"),
    [
        ("κ^-1 = 3.04 nm; приближение Дерягина допустимо.", True),
        ("κ^-1 = 3.04 nm; h/a = 0.020; приближение Дерягина недопустимо.", True),
        ("κ^-1 = 3.04 nm; h/a = 0.020; приближение Дерягина допустимо.", False),
    ],
)
def test_dlvo_requires_ratio_and_consistent_applicability_in_short_answer(answer: str, claim: bool) -> None:
    result = DlvoCheck().evaluate(
        ChemistryTask(
            "Коллоидная химия",
            "Оценка двойного электрического слоя",
            answer=answer,
            facts={
                "dlvo": {
                    "debye_model": "water_1_1_25c",
                    "ionic_strength": "0.0100 mol/L",
                    "debye_length": "3.04 nm",
                    "particle_radius": "100 nm",
                    "separation": "2 nm",
                    "claims_derjaguin": claim,
                }
            },
        )
    )

    assert result.state == CheckState.FAIL


def test_dlvo_refuses_hidden_medium_assumption() -> None:
    result = DlvoCheck().evaluate(
        ChemistryTask(
            "Коллоидная химия",
            "Длина Дебая",
            facts={"dlvo": {"ionic_strength": "0.0100 mol/L", "debye_length": "3.04 nm"}},
        )
    )

    assert result.state == CheckState.INDETERMINATE


def test_stoichiometry_rejects_negative_amounts_even_when_ratios_match() -> None:
    result = StoichiometryCheck().evaluate(
        ChemistryTask(
            "Неорганическая химия",
            "Некорректные отрицательные количества",
            facts={
                "stoichiometry": {
                    "reaction": "H2 + Cl2 -> 2HCl",
                    "reactant_amounts": {"H2": "-1 mol", "Cl2": "-1 mol"},
                    "target_species": "HCl",
                    "target_amount": "-2 mol",
                }
            },
        )
    )

    assert result.state == CheckState.FAIL


def test_dilution_rejects_negative_values_even_when_products_match() -> None:
    result = DilutionCheck().evaluate(
        ChemistryTask(
            "Аналитическая химия",
            "Некорректное разбавление",
            facts={
                "dilution": {
                    "c1": "-1 mol/L",
                    "v1": "-1 L",
                    "c2": "-0.5 mol/L",
                    "v2": "-2 L",
                }
            },
        )
    )

    assert result.state == CheckState.FAIL


def test_titration_rejects_negative_concentrations_and_volumes() -> None:
    side = {
        "concentration": "-1 mol/L",
        "volume": "-1 L",
        "stoichiometric_coefficient": 1,
    }
    result = TitrationCheck().evaluate(
        ChemistryTask(
            "Аналитическая химия",
            "Некорректное титрование",
            facts={"titration": {"analyte": side, "titrant": side}},
        )
    )

    assert result.state == CheckState.FAIL


def test_smoluchowski_rejects_negative_viscosity() -> None:
    result = SmoluchowskiCheck().evaluate(
        ChemistryTask(
            "Коллоидная химия",
            "ε0 = 8.8541878128e-12 F/m.",
            facts={
                "smoluchowski": {
                    "mobility": "3.475e-8 m2/(V*s)",
                    "viscosity": "-1.000 mPa*s",
                    "relative_permittivity": 78.5,
                    "vacuum_permittivity": "8.8541878128e-12 F/m",
                    "zeta": "-0.0500 V",
                }
            },
        )
    )

    assert result.state == CheckState.FAIL


def test_faraday_rejects_negative_time_and_charge_magnitudes() -> None:
    result = FaradayCheck().evaluate(
        ChemistryTask(
            "Аналитическая химия",
            "Некорректные величины закона Фарадея; F = 96485.33212 C/mol.",
            facts={
                "faraday": {
                    "current": "1 A",
                    "time": "-10 s",
                    "charge": "-10 C",
                    "electron_amount": "-0.0001036427 mol",
                    "faraday_constant": "96485.33212 C/mol",
                }
            },
        )
    )

    assert result.state == CheckState.FAIL


def test_faraday_rejects_constant_hidden_from_student() -> None:
    result = FaradayCheck().evaluate(
        ChemistryTask(
            "Аналитическая химия",
            "Через раствор пропустили Q = 96485 C. Определите количество электронов.",
            facts={
                "faraday": {
                    "charge": "96485 C",
                    "electron_amount": "1 mol",
                    "faraday_constant": "96485 C/mol",
                }
            },
        )
    )

    assert result.state == CheckState.FAIL
    assert "скрыто" in result.message


def test_dlvo_rejects_negative_geometry_even_when_ratio_is_small() -> None:
    result = DlvoCheck().evaluate(
        ChemistryTask(
            "Коллоидная химия",
            "Некорректная геометрия",
            facts={
                "dlvo": {
                    "particle_radius": "-10 nm",
                    "separation": "-0.01 nm",
                    "claims_derjaguin": True,
                }
            },
        )
    )

    assert result.state == CheckState.FAIL


@dataclass(frozen=True)
class BrokenCheck:
    check_id: str = "test.broken"
    disciplines: frozenset[ChemistryDiscipline] = frozenset()

    def evaluate(self, task: ChemistryTask) -> CheckResult:
        raise RuntimeError("parser exploded")


def test_registry_fails_closed_when_a_check_crashes() -> None:
    registry = ChemistryCheckRegistry()
    registry.register(BrokenCheck())

    report = registry.run(ChemistryTask("Неорганическая химия", "Задача"))

    assert report.deterministic_pass is False
    assert report.blocking_failures[0].state == CheckState.ERROR
    assert report.to_dict()["blocking_codes"] == ["test.broken"]
    assert report.to_dict()["validation_version"] == CHEMISTRY_VALIDATION_VERSION


def test_default_report_does_not_claim_pass_without_deterministic_coverage() -> None:
    report = validate_chemistry_task(ChemistryTask("Неорганическая химия", "Объясните окраску комплекса."))

    assert report.applicable_count == 0
    assert report.deterministic_pass is False


def test_warning_prevents_deterministic_green_status() -> None:
    registry = ChemistryCheckRegistry()

    @dataclass(frozen=True)
    class WarningCheck:
        check_id: str = "test.warning"
        disciplines: frozenset[ChemistryDiscipline] = frozenset()

        def evaluate(self, task: ChemistryTask) -> CheckResult:
            return CheckResult(self.check_id, CheckState.WARNING, "borderline")

    registry.register(WarningCheck())
    report = registry.run(ChemistryTask("Коллоидная химия", "Задача"))

    assert report.deterministic_pass is False
    assert report.to_dict()["warning_codes"] == ["test.warning"]
