import asyncio

from app.services.validation import data_check, run_validation


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


def test_kb_chunk_provenance_from_generation_grounding_is_accepted() -> None:
    chunk_title = (
        "Аналитическая химия · качественный анализ, гравиметрия, электроанализ и СЗМ "
        "[материал курса] — Аналитическая химия: выверенные основы расчёта и интерпретации › "
        "ANA-03. Закон Фарадея и кулонометрический расчёт › Количество вещества"
    )
    grounding = f"""## СПРАВОЧНЫЕ МАТЕРИАЛЫ КУРСА
### ANA-03 · Закон Фарадея и кулонометрия (Формулы, материал курса)
Q=It

## ВЫДЕРЖКИ ИЗ МАТЕРИАЛОВ КУРСА (контекст, терминология)
### {chunk_title}
Рекомендуемое значение: F = 96485 Кл·моль⁻¹.
"""

    validation = asyncio.run(
        run_validation(
            statement="Рассчитайте количество вещества по закону Фарадея для заданного заряда.",
            reference_answer="n = 2.25e-3 моль",
            rubric=[{"criterion_name": "Расчёт", "max_score": 1}],
            max_score=1,
            answer_format="numeric",
            tolerance_pct=0.5,
            grounding=grounding,
            # This intentionally lacks the KB chunk title and reproduces production.
            sheets_text="ANA-03 · Закон Фарадея и кулонометрия\nQ=It; F=96485 Кл·моль⁻¹",
            existing_statements=[],
            data_used=[{"sheet_title": chunk_title, "values": ["F = 96485 Кл·моль⁻¹"]}],
            run_solver=False,
            run_data=True,
        )
    )

    assert validation["data"] == {"status": "ok", "unknown_numbers": [], "unknown_sources": []}


def test_grounding_provenance_still_rejects_fabricated_value_and_source() -> None:
    grounding = """## ВЫДЕРЖКИ ИЗ МАТЕРИАЛОВ КУРСА
### Реальный источник — ANA-03
F = 96485 Кл·моль⁻¹
"""

    result = data_check(
        "Используйте постоянную Фарадея.",
        grounding,
        data_used=[
            {"sheet_title": "Реальный источник — ANA-03", "values": ["F = 12345 Кл·моль⁻¹"]},
            {"sheet_title": "Выдуманный источник", "values": ["F = 96485 Кл·моль⁻¹"]},
        ],
    )

    assert result == {
        "status": "warn",
        "unknown_numbers": ["12345"],
        "unknown_sources": ["Выдуманный источник"],
    }
