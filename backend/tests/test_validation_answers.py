import pytest

from app.services.validation import compare_answers


def test_numeric_comparison_requires_every_reference_output() -> None:
    result = compare_answers(
        "0.62 эВ; 59.82 кДж/моль",
        "59.82 кДж/моль",
        tolerance_pct=2,
    )

    assert result["verdict"] == "incomplete"
    assert result["matched_count"] == 1
    assert result["required_count"] == 2
    assert result["missing_reference_numbers"] == [0.62]


def test_numeric_comparison_matches_all_outputs_one_to_one() -> None:
    result = compare_answers(
        "x = 40.29%; s = 0.099%; RSD = 0.25%; интервал ±0.12%",
        "RSD 0.25%; интервал ±0.12%; среднее 40.29%; s 0.099%",
        tolerance_pct=2,
    )

    assert result["verdict"] == "match"
    assert result["matched_count"] == result["required_count"] == 4


def test_condition_numbers_do_not_count_as_missing_outputs() -> None:
    result = compare_answers(
        "Ответ: 0.140 мкг/л",
        "0.140 мкг/л",
        tolerance_pct=2,
        context="Из 200.0 мл получили 20.00 мл; сигнал 3450.",
    )

    assert result["verdict"] == "match"


def test_lexically_similar_text_never_auto_approves_semantic_answer() -> None:
    result = compare_answers(
        "Повышение температуры смещает равновесие эндотермической реакции вправо.",
        "Повышение температуры не смещает равновесие эндотермической реакции вправо.",
        tolerance_pct=2,
    )

    assert result["similarity"] > 0.4
    assert result["verdict"] == "uncertain"


def test_equal_number_with_wrong_unit_never_matches() -> None:
    result = compare_answers("m = 5 г", "m = 5 кг", tolerance_pct=2)

    assert result["verdict"] != "match"
    assert result["missing_reference_units"] == ["г"]


def test_units_are_bound_to_their_quantities_not_compared_as_a_global_set() -> None:
    result = compare_answers(
        "m = 5 г; V = 3 мл",
        "m = 5 мл; V = 3 г",
        tolerance_pct=2,
    )

    assert result["verdict"] != "match"


def test_coulombs_never_match_seconds() -> None:
    result = compare_answers("Q = 225 Кл", "Q = 225 с", tolerance_pct=2)

    assert result["verdict"] != "match"


def test_unexpected_final_quantity_prevents_automatic_match() -> None:
    result = compare_answers("m = 5 г", "m = 5 г; V = 3 мл", tolerance_pct=2)

    assert result["verdict"] != "match"
    assert result["unexpected_solver_numbers"] == [3.0]


def test_intermediate_numbers_are_allowed_inside_reference_solution() -> None:
    result = compare_answers(
        "m = 5 г",
        "Сначала находим n = 0.1 моль. Затем получаем m = 5 г.",
        tolerance_pct=2,
        allow_extra_numbers=True,
    )

    assert result["verdict"] == "match"
    assert result["unexpected_solver_numbers"] == [0.1]
    assert result["extra_numbers_allowed"] is True


def test_explicit_quantity_labels_prevent_same_unit_value_swaps() -> None:
    result = compare_answers(
        "m_start = 5 г; m_end = 3 г",
        "m_start = 3 г; m_end = 5 г",
        tolerance_pct=2,
    )

    assert result["verdict"] != "match"


@pytest.mark.parametrize(
    ("reference", "solver"),
    [
        ("pH = 2; среда кислая", "pH = 2; среда щелочная"),
        ("ζ = -60 мВ; приближение применимо", "ζ = -60 мВ; приближение неприменимо"),
    ],
)
def test_numeric_match_does_not_hide_contradictory_text_claim(reference: str, solver: str) -> None:
    result = compare_answers(reference, solver, tolerance_pct=2)

    assert result["verdict"] != "match"
    assert result["missing_text_claims"]


def test_equivalent_litre_notation_is_normalized() -> None:
    result = compare_answers("V = 2 л", "V = 2 дм^3", tolerance_pct=2)

    assert result["verdict"] == "match"

    reverse = compare_answers("V = 2 дм^3", "V = 2 л", tolerance_pct=2)
    assert reverse["verdict"] == "match"


def test_explicit_rounded_alternative_is_one_required_output() -> None:
    result = compare_answers(
        "Среднее: 10.4975 см (или 10.498 см). Абсолютная погрешность: 0.0025 см. Относительная: 0.024%.",
        "Среднее 10.4975 см; абсолютная погрешность 0.0025 см; относительная 0.024%.",
        tolerance_pct=0.01,
    )

    assert result["verdict"] == "match"
    assert result["reference_number_groups"] == [[10.4975, 10.498], [0.0025], [0.024]]
    assert result["matched_count"] == result["required_count"] == 3
    assert result["missing_reference_numbers"] == []


@pytest.mark.parametrize("connector", ["или", "либо", "or", "OR"])
def test_supported_connectors_form_numeric_alternative_group(connector: str) -> None:
    result = compare_answers(
        f"x = 1.234 {connector} 1.23 г; y = 7 мл",
        "x = 1.234 г; y = 7 мл",
        tolerance_pct=0.01,
    )

    assert result["verdict"] == "match"
    assert result["reference_number_groups"] == [[1.234, 1.23], [7.0]]
    assert result["required_count"] == 2


def test_numeric_alternative_does_not_make_other_outputs_optional() -> None:
    result = compare_answers(
        "x = 1.00 или 1.01; y = 5; z = 7",
        "x = 1.00; y = 5",
        tolerance_pct=0.01,
    )

    assert result["verdict"] == "incomplete"
    assert result["matched_count"] == 2
    assert result["required_count"] == 3
    assert result["missing_reference_numbers"] == [7.0]


def test_words_between_connector_and_number_do_not_collapse_distinct_outputs() -> None:
    result = compare_answers(
        "x = 1 или отдельный результат y = 2",
        "x = 1",
        tolerance_pct=0.01,
    )

    assert result["verdict"] == "incomplete"
    assert result["required_count"] == 2


def test_alternative_number_still_requires_reference_unit() -> None:
    result = compare_answers(
        "Среднее: 10.4975 см (или 10.498 см)",
        "Среднее: 10.4975 мм",
        tolerance_pct=0.01,
    )

    assert result["verdict"] != "match"
    assert result["missing_reference_units"] == ["см"]


def test_equivalent_voltage_representations_are_one_required_group() -> None:
    result = compare_answers(
        "ζ = 0.020 V (20 mV)",
        "ζ = 20 mV",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"
    assert result["reference_number_groups"] == [[0.02, 20.0]]
    assert result["matched_count"] == result["required_count"] == 1
    assert result["missing_reference_units"] == []


@pytest.mark.parametrize(
    ("reference", "solver"),
    [
        ("ζ = 0.020 V", "ζ = 20 mV"),
        ("ζ = 20 mV", "ζ = 0.020 V"),
        ("ζ = 0.020 V и 20 mV", "ζ = 20 mV"),
    ],
)
def test_si_prefix_conversion_matches_single_representation(reference: str, solver: str) -> None:
    result = compare_answers(reference, solver, tolerance_pct=0.5)

    assert result["verdict"] == "match"
    assert result["matched_count"] == result["required_count"] == 1


def test_distinct_faraday_outputs_remain_three_required_quantities() -> None:
    result = compare_answers(
        "Q = 225 C; n = 2.25e-3 mol; m = 243 mg",
        "Q = 225 C; n = 2.25e-3 mol; m = 243 mg",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"
    assert result["reference_number_groups"] == [[225.0], [0.00225], [243.0]]
    assert result["required_count"] == 3


def test_equal_normalized_values_of_different_quantities_are_not_grouped() -> None:
    result = compare_answers(
        "ζ = 0.020 V; отдельное напряжение Δφ = 20 mV",
        "ζ = 0.020 V",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "incomplete"
    assert result["reference_number_groups"] == [[0.02], [20.0]]
    assert result["matched_count"] == 1
    assert result["required_count"] == 2


def test_voltage_is_not_equated_to_unknown_compound_unit() -> None:
    result = compare_answers("ζ = 0.020 V", "градиент = 20 mV/cm", tolerance_pct=0.5)

    assert result["verdict"] != "match"
    assert result["missing_reference_units"] == ["в"]


def test_trace_values_do_not_match_through_a_large_absolute_floor() -> None:
    result = compare_answers("c = 1e-12 mol/L", "c = 9e-10 mol/L", tolerance_pct=2)

    assert result["verdict"] != "match"


def test_same_number_with_different_mobility_scale_does_not_match() -> None:
    result = compare_answers(
        "μ = 1e-8 m²/(V·s)",
        "μ = 1e-8 cm²/(V·s)",
        tolerance_pct=2,
    )

    assert result["verdict"] != "match"


def test_same_number_with_current_instead_of_charge_does_not_match() -> None:
    result = compare_answers("Q = 5 C", "I = 5 A", tolerance_pct=2)

    assert result["verdict"] != "match"


def test_equivalent_launch_units_are_converted_before_comparison() -> None:
    result = compare_answers("μ = 1e-8 m²/(V·s)", "μ = 1e-4 cm²/(V·s)", tolerance_pct=2)

    assert result["verdict"] == "match"


@pytest.mark.parametrize(
    ("reference", "solver"),
    [
        ("η = 2 Pa·s", "η = 2 mPa·s"),
        ("S = 10 m²/g", "S = 10 m²/kg"),
        ("n = 1 mol", "n = 1 mmol"),
    ],
)
def test_same_numeral_with_different_si_scale_is_not_equal(reference: str, solver: str) -> None:
    assert compare_answers(reference, solver, tolerance_pct=2)["verdict"] != "match"


@pytest.mark.parametrize(
    ("reference", "solver"),
    [
        ("η = 2 Pa·s", "η = 2000 mPa·s"),
        ("S = 10 m²/g", "S = 10000 m²/kg"),
        ("n = 1 mol", "n = 1000 mmol"),
        ("m = 1 g", "m = 0.001 kg"),
    ],
)
def test_equivalent_si_scaled_values_match(reference: str, solver: str) -> None:
    assert compare_answers(reference, solver, tolerance_pct=2)["verdict"] == "match"
