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

    assert result["verdict"] == "incomplete"
    assert result["missing_reference_units"] == ["г"]


def test_equivalent_litre_notation_is_normalized() -> None:
    result = compare_answers("V = 2 л", "V = 2 дм^3", tolerance_pct=2)

    assert result["verdict"] == "match"

    reverse = compare_answers("V = 2 дм^3", "V = 2 л", tolerance_pct=2)
    assert reverse["verdict"] == "match"
