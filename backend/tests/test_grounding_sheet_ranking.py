import asyncio

from app.models import KnowledgeDocument, ReferenceSheet
from app.services import grounding
from app.services.grounding import (
    AUTO_SHEET_TOP_K,
    _rank_sheets_for_query,
    _select_sheets,
    build_grounding_block,
)
from app.services.taskgen import build_validation_contract


def _sheet(*, title: str, description: str = "", content: str = "", order: int = 0) -> ReferenceSheet:
    return ReferenceSheet(
        title=title,
        description=description,
        content_markdown=content,
        ord=order,
        kind="other",
    )


def test_short_term_matches_token_boundary_not_substring() -> None:
    analytics = _sheet(title="Прикладная аналитика", content="Метрики аналитики")
    infrared = _sheet(title="ИК-спектроскопия", content="Характеристические полосы поглощения")

    selected = _rank_sheets_for_query([analytics, infrared], "Что показывает ИК?")

    assert selected == [infrared]


def test_relevant_sheet_ranks_ahead_of_weakly_matching_canonical_sheet() -> None:
    unrelated = _sheet(
        title="Хроматографическое определение",
        content="Аналитическое определение компонентов смеси",
        order=1,
    )
    relevant = _sheet(
        title="Титриметрическое определение хлоридов",
        description="Расчёт содержания хлоридов методом титрования",
        content="Точка эквивалентности и объём титранта",
        order=20,
    )

    selected = _rank_sheets_for_query(
        [unrelated, relevant], "Титриметрическое определение содержания хлоридов"
    )

    assert selected == [relevant, unrelated]


def test_order_is_the_tie_break_for_equal_relevance() -> None:
    later = _sheet(title="Растворы: пример B", content="молярная концентрация", order=9)
    earlier = _sheet(title="Растворы: пример A", content="молярная концентрация", order=2)

    selected = _rank_sheets_for_query([later, earlier], "молярная концентрация раствора")

    assert selected == [earlier, later]


def test_query_aware_ranking_has_stable_strict_top_k_amid_noise() -> None:
    relevant = [
        _sheet(
            title=f"Растворы — справочник {index}",
            content="молярная концентрация раствора",
            order=index,
        )
        for index in range(AUTO_SHEET_TOP_K + 4)
    ]
    noise = [_sheet(title=f"Шум {index}", content="другая тема") for index in range(20)]

    selected = _rank_sheets_for_query(
        [*reversed(relevant), *noise], "молярная концентрация раствора"
    )

    assert len(selected) == AUTO_SHEET_TOP_K
    assert selected == relevant[:AUTO_SHEET_TOP_K]


def test_equal_rank_and_order_preserve_input_order_for_unsaved_sheets() -> None:
    first = _sheet(title="Растворы A", content="концентрация", order=1)
    second = _sheet(title="Растворы B", content="концентрация", order=1)

    assert _rank_sheets_for_query([second, first], "концентрация") == [second, first]


def test_empty_query_and_explicit_selection_keep_database_order() -> None:
    first = _sheet(title="Первый", order=1)
    second = _sheet(title="Второй", order=2)
    sheets = [first, second]

    assert _select_sheets(sheets, query="", query_aware=True) == sheets
    assert _select_sheets(sheets, query="несовпадающий запрос", query_aware=False) == sheets


class _ScalarsResult:
    def __init__(self, rows: list[ReferenceSheet]) -> None:
        self._rows = rows

    def scalars(self) -> "_ScalarsResult":
        return self

    def all(self) -> list[ReferenceSheet]:
        return self._rows


class _RowsResult:
    def __init__(self, rows: list[tuple[str, str, str]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, str, str]]:
        return self._rows


class _GroundingDb:
    def __init__(
        self,
        sheets: list[ReferenceSheet],
        documents: list[KnowledgeDocument],
    ) -> None:
        self._results = [
            _ScalarsResult(sheets),
            _RowsResult([(doc.id, doc.title, doc.authority) for doc in documents]),
        ]

    async def execute(self, _statement):
        return self._results.pop(0)


def _sourced_sheet(*, sheet_id: str, title: str, document_id: str) -> ReferenceSheet:
    return ReferenceSheet(
        id=sheet_id,
        assistant_id="assistant-1",
        title=title,
        content_markdown=f"Содержание: {title}",
        source_document_id=document_id,
        visibility="teacher_only",
        is_canonical=True,
        kind="data_table",
    )


def test_automatic_grounding_excludes_sheet_from_unverified_source() -> None:
    verified = KnowledgeDocument(
        id="doc-verified", assistant_id="assistant-1", title="Учебник", authority="reference"
    )
    unverified = KnowledgeDocument(
        id="doc-unverified", assistant_id="assistant-1", title="Черновик OCR", authority="unverified"
    )
    trusted_sheet = _sourced_sheet(
        sheet_id="sheet-trusted", title="Проверенная таблица", document_id=verified.id
    )
    draft_sheet = _sourced_sheet(
        sheet_id="sheet-draft", title="Непроверенная таблица", document_id=unverified.id
    )

    result = asyncio.run(
        build_grounding_block(
            _GroundingDb([trusted_sheet, draft_sheet], [verified, unverified]),
            "assistant-1",
            include_kb=False,
        )
    )

    assert trusted_sheet.title in result
    assert draft_sheet.title not in result


def test_automatic_grounding_excludes_sheet_without_existing_source_document() -> None:
    missing = _sourced_sheet(
        sheet_id="sheet-missing",
        title="Карточка с потерянным источником",
        document_id="does-not-exist",
    )

    result = asyncio.run(
        build_grounding_block(
            _GroundingDb([missing], []),
            "assistant-1",
            include_kb=False,
        )
    )

    assert missing.title not in result


def test_automatic_kb_grounding_requests_only_verified_authorities(monkeypatch) -> None:
    captured: dict = {}

    async def fake_search(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(grounding, "search_chunks", fake_search)
    result = asyncio.run(
        build_grounding_block(
            _GroundingDb([], []),
            "assistant-1",
            query="закон Фарадея",
        )
    )

    assert result == ""
    assert captured["allowed_authorities"] == ("course_policy", "course_lecture", "reference")


def test_explicit_teacher_selection_keeps_unverified_sheet_and_is_not_top_k_capped() -> None:
    unverified = KnowledgeDocument(
        id="doc-unverified", assistant_id="assistant-1", title="Черновик OCR", authority="unverified"
    )
    sheets = [
        _sourced_sheet(
            sheet_id=f"sheet-{index}",
            title=f"Явно выбранная таблица {index}",
            document_id=unverified.id,
        )
        for index in range(AUTO_SHEET_TOP_K + 3)
    ]

    result = asyncio.run(
        build_grounding_block(
            _GroundingDb(sheets, [unverified]),
            "assistant-1",
            sheet_ids=[sheet.id for sheet in sheets],
            query="таблица",
            include_kb=False,
        )
    )

    assert all(sheet.title in result for sheet in sheets)


def test_validation_contract_never_resurrects_selected_but_unrendered_sheets() -> None:
    contract = build_validation_contract(
        {
            "answer_format": "numeric",
            "tolerance_pct": 2,
            "validation_solver": True,
            "validation_data_check": True,
            "sheet_ids": ["selected-but-omitted"],
            "kb_query": "",
            "task_kind": "calculation",
            "chemistry_check": "chemistry.dilution",
        },
        {"sheets": [], "query": "разбавление"},
    )

    assert contract["sheet_ids"] == []
