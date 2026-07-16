import asyncio
from types import SimpleNamespace

import pytest

from app.services.task_evidence import evidence_matches_task
from app.services.validation import data_check, run_validation, source_lineage_check


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


def test_validation_fingerprint_uses_the_task_evidence_canonical_form() -> None:
    task = SimpleNamespace(
        statement="Объясните выбор индикатора.",
        reference_solution="Интервал перехода должен попадать в скачок титрования.",
        answer="Индикатор выбирают по положению интервала перехода.",
        rubric=[{"criterion_name": "Обоснование", "max_score": 1}],
        max_score=1,
        grounding={"data_used": [], "chemistry_facts": None},
    )
    validation = asyncio.run(
        run_validation(
            statement=task.statement,
            reference_solution=task.reference_solution,
            reference_answer=task.answer,
            rubric=task.rubric,
            max_score=task.max_score,
            answer_format="text",
            tolerance_pct=0,
            grounding="",
            sheets_text="",
            existing_statements=[],
            data_used=[],
            run_solver=False,
            run_data=True,
        )
    )

    assert evidence_matches_task(validation, task)


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


def test_source_lineage_rejects_reference_sheet_without_source_document() -> None:
    result = source_lineage_check(
        [{"sheet_title": "Сиротская карточка", "values": ["K = 1"]}],
        [{"id": "sheet-1", "title": "Сиротская карточка", "source_document_id": ""}],
        "### Сиротская карточка\nK = 1",
    )

    assert result["status"] == "warn"
    assert result["unbound_sources"] == ["Сиротская карточка"]


def test_source_lineage_accepts_exact_kb_header_bound_to_trusted_document() -> None:
    title = "Коллоидная химия · курс лекций [материал курса] — ЛЕКЦИЯ 6"
    result = source_lineage_check(
        [{"sheet_title": title, "values": ["s0 = 0.162 нм²"]}],
        [
            {
                "title": title,
                "source_document_id": "doc-1",
                "source_document_exists": True,
                "source_authority": "course_lecture",
                "source_version": "2026-r3",
                "source_kind": "kb_chunk",
            }
        ],
        f"### {title}\ns0 = 0.162 нм²",
    )

    assert result["status"] == "ok"
    assert result["unbound_sources"] == []


def test_source_lineage_accepts_exact_kb_heading_copied_with_markdown_marker() -> None:
    title = "Задачник Свиридова [справочный источник] — § 14. p-Элементы V группы"
    result = source_lineage_check(
        [{"sheet_title": f"### {title}", "values": ["NO + KMnO4 = MnO2 + KNO3"]}],
        [
            {
                "title": title,
                "source_document_id": "doc-sviridov",
                "source_document_exists": True,
                "source_authority": "reference",
                "source_version": "launch-2026",
                "source_kind": "kb_chunk",
            }
        ],
        f"### {title}\nNO + KMnO4 = MnO2 + KNO3",
    )

    assert result["status"] == "ok"
    assert result["unbound_sources"] == []


@pytest.mark.parametrize("copied", ["## {title}", "### {title} — продолжение"])
def test_source_lineage_does_not_fuzz_markdown_prefixed_kb_titles(copied: str) -> None:
    title = "Задачник Свиридова [справочный источник] — § 14"
    rendered = copied.format(title=title)
    result = source_lineage_check(
        [{"sheet_title": rendered, "values": ["NO + KMnO4 = MnO2 + KNO3"]}],
        [
            {
                "title": title,
                "source_document_id": "doc-sviridov",
                "source_document_exists": True,
                "source_authority": "reference",
                "source_version": "launch-2026",
                "source_kind": "kb_chunk",
            }
        ],
        f"### {title}\nNO + KMnO4 = MnO2 + KNO3",
    )

    assert result["status"] == "warn"


def test_source_lineage_accepts_rendered_reference_sheet_heading() -> None:
    base_title = "LAB-04 · Расчёты для растворов"
    rendered_title = f"{base_title} (Формулы, справочный источник)"
    result = source_lineage_check(
        [{"sheet_title": rendered_title, "values": ["c = n/V"]}],
        [
            {
                "title": base_title,
                "source_document_id": "doc-lab-04",
                "source_document_exists": True,
                "source_authority": "reference",
                "source_version": "2026-r3",
            }
        ],
        f"## СПРАВОЧНЫЕ МАТЕРИАЛЫ КУРСА\n### {rendered_title}\nc = n/V",
    )

    assert result["status"] == "ok"
    assert result["unbound_sources"] == []


def test_source_lineage_rendered_alias_requires_exact_grounding_heading() -> None:
    base_title = "LAB-04 · Расчёты для растворов"
    rendered_title = f"{base_title} (Формулы, справочный источник)"
    sheet = {
        "title": base_title,
        "source_document_id": "doc-lab-04",
        "source_document_exists": True,
        "source_authority": "reference",
        "source_version": "2026-r3",
    }

    absent = source_lineage_check(
        [{"sheet_title": rendered_title, "values": ["c = n/V"]}],
        [sheet],
        f"### {base_title}\nc = n/V",
    )
    substring = source_lineage_check(
        [{"sheet_title": rendered_title, "values": ["c = n/V"]}],
        [sheet],
        f"### {rendered_title} — дополнение\nc = n/V",
    )

    assert absent["status"] == "warn"
    assert absent["unbound_sources"] == [rendered_title]
    assert substring["status"] == "warn"
    assert substring["unbound_sources"] == [rendered_title]


def test_source_lineage_rendered_alias_does_not_override_authority_or_document_checks() -> None:
    base_title = "LAB-04 · Расчёты для растворов"
    rendered_title = f"{base_title} (Формулы, справочный источник)"
    data_used = [{"sheet_title": rendered_title, "values": ["c = n/V"]}]
    provenance = f"### {rendered_title}\nc = n/V"

    for metadata in (
        {
            "title": base_title,
            "source_document_id": "missing",
            "source_document_exists": False,
            "source_authority": "reference",
            "source_version": "2026-r3",
        },
        {
            "title": base_title,
            "source_document_id": "doc-lab-04",
            "source_document_exists": True,
            "source_authority": "unverified",
            "source_version": "2026-r3",
        },
        {
            "title": base_title,
            "source_document_id": "doc-lab-04",
            "source_document_exists": True,
            "source_authority": "course_lecture",
            "source_version": "2026-r3",
        },
    ):
        result = source_lineage_check(data_used, [metadata], provenance)
        assert result["status"] == "warn"
        assert result["unbound_sources"] == [rendered_title]


def test_source_lineage_does_not_accept_title_substring_as_document_lineage() -> None:
    result = source_lineage_check(
        [{"sheet_title": "Учебник [материал курса] — раздел", "values": ["F = 96485"]}],
        [],
        "### Учебник [материал курса] — раздел\nF = 96485",
    )

    assert result["status"] == "warn"
    assert result["unbound_sources"] == ["Учебник [материал курса] — раздел"]
    assert result["kb_sources"] == []


def test_source_lineage_accepts_only_existing_verified_document_metadata() -> None:
    result = source_lineage_check(
        [{"sheet_title": "ANA-03", "values": ["F = 96485"]}],
        [
            {
                "title": "ANA-03",
                "source_document_id": "doc-1",
                "source_document_exists": True,
                "source_authority": "reference",
            }
        ],
    )

    assert result["status"] == "ok"
    assert result["unbound_sources"] == []


def test_source_lineage_rejects_dangling_or_unverified_document() -> None:
    data_used = [{"sheet_title": "Источник", "values": ["K = 1"]}]
    dangling = source_lineage_check(
        data_used,
        [
            {
                "title": "Источник",
                "source_document_id": "missing",
                "source_document_exists": False,
                "source_authority": "reference",
            }
        ],
    )
    unverified = source_lineage_check(
        data_used,
        [
            {
                "title": "Источник",
                "source_document_id": "doc-1",
                "source_document_exists": True,
                "source_authority": "unverified",
            }
        ],
    )

    assert dangling["status"] == "warn"
    assert unverified["status"] == "warn"


def test_malformed_provenance_is_never_treated_as_empty_and_valid() -> None:
    for malformed in ("garbage", {}, [7], [{"values": ["1.0"]}]):
        data = data_check("Полное условие задачи с исходными данными.", "", data_used=malformed)
        lineage = source_lineage_check(malformed, [])

        assert data["status"] == "invalid"
        assert lineage["status"] == "invalid"
