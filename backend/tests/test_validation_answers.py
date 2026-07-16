import pytest

from app.services.validation import compare_answers, extract_number_groups


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


def test_identifier_digits_are_not_treated_as_numeric_outputs() -> None:
    result = compare_answers(
        "c₂ = 0.125 моль/л; n₁ = n₂ = 0.125 моль; V₁ = 100 мл",
        "c2 = 0.125 моль/л; n1 = n2 = 0.125 моль; V1 = 100 мл",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"
    assert 2.0 not in result["solver_numbers"]


def test_latex_compound_unit_matches_unicode_display_unit() -> None:
    result = compare_answers(
        "S = 229 м²/г",
        r"S = 229\ \text{м}^2/\text{г}",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"


def test_reference_solution_matches_unit_split_by_latex_math_delimiters() -> None:
    result = compare_answers(
        "99.7 м²/г; значение соответствует типичному диапазону 10–1000 м²/г.",
        r"""
        $S_{уд} \approx 9.9727 \times 10^1 = 99.727$ м$^2$/г.
        После округления: $S_{уд} \approx 99.7$ м$^2$/г.
        Значение около $100$ м$^2$/г лежит в интервале $10$–$1000$ м$^2$/г.
        """,
        tolerance_pct=0.5,
        context="Оцените диапазон 10–1000 м$^2$/г.",
        allow_extra_numbers=True,
    )

    assert result["verdict"] == "match"
    assert result["matched_count"] == result["required_count"] == 1
    assert result["missing_reference_units"] == []


def test_latex_text_subscript_and_escaped_percent_match_plain_result() -> None:
    result = compare_answers(
        "w_Al = 31.73 %",
        r"$результат\ w_{\mathrm{Al}} = 31.73$\%",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"


def test_latex_display_delimiters_do_not_obscure_numeric_answer() -> None:
    result = compare_answers(
        "S = 99.7 м²/г",
        r"\(S = 99.7\ \text{м}^2/\text{г}\)",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"


def test_explicit_quantity_labels_prevent_same_unit_value_swaps() -> None:
    result = compare_answers(
        "m_start = 5 г; m_end = 3 г",
        "m_start = 3 г; m_end = 5 г",
        tolerance_pct=2,
    )

    assert result["verdict"] != "match"


def test_latex_and_unicode_numeric_subscripts_bind_the_same_quantities() -> None:
    reference = (
        "$c_2 = 0.150$ моль/л; $n_1 = 0.0300$ моль; $n_2 = 0.0300$ моль; "
        "для приготовления 100.0 мл 0.300 моль/л раствора требуется 25.0 мл исходного раствора."
    )
    solver = "c₂ = 0.150 моль/л; n₁ = 0.0300 моль; n₂ = 0.0300 моль; V исходного = 25.0 мл"
    statement = (
        "Для разбавления водного раствора хлорида калия взяли $V_1 = 25.0$ мл исходного раствора с "
        "концентрацией $c_1 = 1.20$ моль/л и довели объём дистиллированной водой до $V_2 = 200.0$ мл. "
        "Считая, что количество растворённого вещества сохраняется, вычислите концентрацию $c_2$ полученного раствора. "
        "Проверьте, что количество вещества до и после разбавления одинаково, вычислив $n_1$ и $n_2$. Выполните обратную проверку: "
        "рассчитайте, какой объём исходного раствора потребуется для приготовления $100.0$ мл раствора с концентрацией $0.300$ моль/л."
    )
    result = compare_answers(reference, solver, tolerance_pct=0.5, context=statement)

    assert result["verdict"] == "match"
    assert result["matched_count"] == result["required_count"] == 4

    chained_verifier = (
        "c₂ = 0.150 моль/л; n₁ = n₂ = 0.0300 моль; объём исходного раствора для обратной проверки: 25.0 мл"
    )
    chained_result = compare_answers(reference, chained_verifier, tolerance_pct=0.5, context=statement)
    assert chained_result["verdict"] == "match"
    assert chained_result["matched_count"] == chained_result["required_count"] == 4

    missing_reverse_volume = compare_answers(
        reference,
        "c₂ = 0.150 моль/л; n₁ = 0.0300 моль; n₂ = 0.0300 моль",
        tolerance_pct=0.5,
        context=statement,
    )
    assert missing_reverse_volume["verdict"] == "incomplete"
    assert missing_reverse_volume["missing_reference_numbers"] == [25.0]


def test_labeled_output_equal_to_context_value_remains_required() -> None:
    result = compare_answers(
        "c_2=0.150 моль/л; V_исх=25.0 мл",
        "c₂=0.150 моль/л",
        tolerance_pct=0.5,
        context="Исходные данные: V_1=25.0 мл.",
    )

    assert result["verdict"] == "incomplete"
    assert result["required_count"] == 2
    assert result["missing_reference_numbers"] == [25.0]


def test_labeled_solver_input_from_context_is_not_an_unexpected_output() -> None:
    result = compare_answers(
        "c_2=0.150 моль/л",
        "c_1=1.20 моль/л; c_2=0.150 моль/л",
        tolerance_pct=0.5,
        context="Исходная концентрация c_1=1.20 моль/л.",
    )

    assert result["verdict"] == "match"
    assert result["unexpected_solver_numbers"] == []


def test_numeric_subscript_binding_still_rejects_swapped_concentrations() -> None:
    result = compare_answers(
        "$c_1=0.100$ моль/л; $c_2=0.150$ моль/л",
        "c₁=0.150 моль/л; c₂=0.100 моль/л",
        tolerance_pct=0.5,
    )

    assert result["verdict"] != "match"


@pytest.mark.parametrize(
    ("reference", "solver"),
    [
        (
            "c₂=0.150 моль/л; n₁=0.0300 моль; n₂=0.0300 моль; V=25.0 мл",
            "c₂=0.150 моль/л; n₁ = n₂ = 0.0300 моль; V=25.0 мл",
        ),
        (
            "c₂=0.150 моль/л; n₁ = n₂ = 0.0300 моль; V=25.0 мл",
            "c₂=0.150 моль/л; n₁=0.0300 моль; n₂=0.0300 моль; V=25.0 мл",
        ),
    ],
)
def test_chained_equal_labels_expand_to_separate_required_outputs(reference: str, solver: str) -> None:
    result = compare_answers(reference, solver, tolerance_pct=0.5)

    assert result["verdict"] == "match"
    assert result["matched_count"] == result["required_count"] == 4


@pytest.mark.parametrize(
    "expression",
    [
        "n=c V=0.0300 моль",
        "n₁+n₂=0.0300 моль",
        "n₁/n₂=0.0300 моль",
        "n+n₁=n₂=0.0300 моль",
        "n=c V=n₁=n₂=0.0300 моль",
    ],
)
def test_arbitrary_formula_operands_are_not_expanded_as_equal_labels(expression: str) -> None:
    assert extract_number_groups(expression) == [[0.03]]


@pytest.mark.parametrize(
    "solver",
    [
        "n₁ = n₂ = 0.0300 г",
        "n₁ = n₂ = 0.0400 моль",
    ],
)
def test_chained_equal_labels_preserve_unit_and_value_protection(solver: str) -> None:
    result = compare_answers(
        "n₁=0.0300 моль; n₂=0.0300 моль",
        solver,
        tolerance_pct=0.5,
    )

    assert result["verdict"] != "match"


def test_gravimetric_factor_and_mass_fraction_notation_variants_match() -> None:
    result = compare_answers(
        r"$F_g = 0.2032;~ m(\mathrm{Ni}) = 0.04538~\text{г};~ w(\mathrm{Ni}) = 9.08~\%$",
        "Fg = 0.2031, m(Ni) = 0.04538 г, ω(Ni) = 9.076%",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"
    assert result["matched_count"] == result["required_count"] == 3
    assert result["reference_units"] == ["%", "г"]


def test_element_subscript_labels_match_parenthesized_analytical_notation() -> None:
    result = compare_answers(
        r"$F_g=0.2032;~m(\mathrm{Ni})=0.04538~\text{г};~w(\mathrm{Ni})=9.08~\%$",
        "гравиметрический фактор F_g = 0.2031, масса никеля m_Ni = 0.04538 г, массовая доля никеля ω_Ni = 9.076%",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"
    assert result["matched_count"] == result["required_count"] == 3


def test_unambiguous_russian_quantity_names_bind_to_chemical_formula_labels() -> None:
    result = compare_answers(
        "F_g = 0.3621; m_MgO = 0.09052 г; w_MgO = 22.63%",
        "F_g = 0.3621; масса MgO = 0.09052 г; массовая доля MgO = 22.63%",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"
    assert result["matched_count"] == result["required_count"] == 3


def test_russian_analyte_names_with_sample_context_bind_to_element_labels() -> None:
    result = compare_answers(
        r"$F_g = 0.2783$; $m(\mathrm{P}) = 0.1703\ \text{г}$; $w(\mathrm{P}) = 22.71\%$.",
        (
            "Гравиметрический фактор F_g = 0.2783; масса фосфора в навеске = 0.1703 г; "
            "массовая доля фосфора в удобрении = 22.71%."
        ),
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"
    assert result["matched_count"] == result["required_count"] == 3


def test_russian_analyte_name_binding_preserves_element_identity() -> None:
    result = compare_answers(
        "m(P) = 0.1703 г; w(P) = 22.71%",
        "масса азота в навеске = 0.1703 г; массовая доля азота в образце = 22.71%",
        tolerance_pct=0.5,
    )

    assert result["verdict"] != "match"
    assert result["matched_count"] == 0


def test_species_charge_caret_is_optional_inside_quantity_label() -> None:
    result = compare_answers(
        r"$Q=73.1\ \text{Кл};~n(e^-)=7.58\cdot10^{-4}\ \text{моль};~m(Ag)=8.18\cdot10^{-2}\ \text{г}$",
        "Q=73.1 Кл; n(e-)=7.58×10⁻⁴ моль; m(Ag)=0.0818 г",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"
    assert result["matched_count"] == result["required_count"] == 3


def test_named_quantity_binding_preserves_the_compound_identity() -> None:
    result = compare_answers(
        "m_MgO = 0.09052 г; w_MgO = 22.63%",
        "масса CaO = 0.09052 г; массовая доля CaO = 22.63%",
        tolerance_pct=0.5,
    )

    assert result["verdict"] != "match"
    assert result["matched_count"] == 0


@pytest.mark.parametrize("name", ["образца", "sample", "rate"])
def test_free_prose_mass_name_does_not_infer_a_compound_label(name: str) -> None:
    result = compare_answers(
        f"m_{name} = 0.09052 г",
        f"масса {name} = 0.09052 г",
        tolerance_pct=0.5,
    )

    assert result["verdict"] != "match"


@pytest.mark.parametrize("suffix", ["rate", "Max", "NI", "Nickel"])
def test_omega_alias_rejects_non_element_suffixes(suffix: str) -> None:
    result = compare_answers(
        f"w_{suffix}=9.08 %",
        f"ω_{suffix}=9.08 %",
        tolerance_pct=0.5,
    )

    assert result["verdict"] != "match"


@pytest.mark.parametrize(
    ("reference", "solver"),
    [("F_g=0.2031", "Fg=0.2031"), ("Fg=0.2031", "F_g=0.2031")],
)
def test_gravimetric_factor_label_normalization_is_symmetric_and_dimensionless(reference: str, solver: str) -> None:
    result = compare_answers(reference, solver, tolerance_pct=0.5)

    assert result["verdict"] == "match"
    assert result["reference_units"] == result["solver_units"] == []


def test_latex_spacing_does_not_hide_a_wrong_mass_unit() -> None:
    result = compare_answers(
        r"$F_g=0.2032;~m(\mathrm{Ni})=0.04538~\text{г};~w(\mathrm{Ni})=9.08~\%$",
        "Fg=0.2032; m(Ni)=0.04538 мг; ω(Ni)=9.08%",
        tolerance_pct=0.5,
    )

    assert result["verdict"] != "match"
    assert result["missing_reference_units"] == ["г"]


def test_mass_fraction_alias_keeps_element_binding_when_values_are_swapped() -> None:
    result = compare_answers(
        "w(Ni)=10 %; w(Co)=20 %",
        "ω(Ni)=20 %; ω(Co)=10 %",
        tolerance_pct=0.5,
    )

    assert result["verdict"] != "match"


def test_mass_fraction_alias_does_not_collapse_indexed_angular_frequency() -> None:
    result = compare_answers(
        "w_0=10 с",
        "ω_0=10 с",
        tolerance_pct=0.5,
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


def test_coarser_parenthetical_rendering_of_same_quantity_is_not_an_extra_output() -> None:
    result = compare_answers(
        "S_уд = 183 м²/г",
        "S_уд = 1.8·10² м²/г (183 м²/г)",
        tolerance_pct=0.5,
    )

    assert result["verdict"] == "match"
    assert result["unexpected_solver_numbers"] == []
    assert result["rounded_solver_duplicates"] == [180.0]


@pytest.mark.parametrize(
    "solver",
    [
        "S_уд = 1.7·10² м²/г (183 м²/г)",
        "S_уд = 1.8·10² м²/кг (183 м²/г)",
        "S_rough = 1.8·10² м²/г; S_уд = 183 м²/г",
        "x = 1.8·10² м²/г (183 м²/г)",
        "S_уд = 180 м²/г (183 м²/г)",
    ],
)
def test_unrelated_or_unproven_extra_number_is_not_accepted_as_rounding(solver: str) -> None:
    result = compare_answers("S_уд = 183 м²/г", solver, tolerance_pct=0.5)

    assert result["verdict"] != "match"
    assert result["unexpected_solver_numbers"]


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
