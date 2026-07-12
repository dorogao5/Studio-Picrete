from app.services.validation import data_check


def test_self_contained_task_numbers_are_not_reference_claims() -> None:
    result = data_check(
        "Раствор 200.00 мл содержит сигнал 3450 при коэффициенте 2.45e3.",
        "Ядро аналитики · Градуировка\n$c_1V_1=c_2V_2$",
        data_used=[],
    )

    assert result == {"status": "ok", "unknown_numbers": [], "unknown_sources": []}


def test_claimed_reference_value_and_title_are_verified() -> None:
    sheets = "Таблица курса · Константы\n$K = 2.45\\cdot10^3$"
    result = data_check(
        "Используйте $K = 2.45\\cdot10^3$.",
        sheets,
        data_used=[{"sheet_title": "Таблица курса · Константы", "values": ["K = 2.45·10^3"]}],
    )

    assert result == {"status": "ok", "unknown_numbers": [], "unknown_sources": []}


def test_fabricated_reference_claim_is_flagged() -> None:
    result = data_check(
        "Используйте $K = 17.2$.",
        "Таблица курса · Константы\n$K = 12.0$",
        data_used=[{"sheet_title": "Несуществующая таблица", "values": ["K = 17.2"]}],
    )

    assert result == {
        "status": "warn",
        "unknown_numbers": ["17.2"],
        "unknown_sources": ["Несуществующая таблица"],
    }
