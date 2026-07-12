from app.models import ReferenceSheet
from app.services.grounding import _rank_sheets_for_query, _select_sheets


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


def test_empty_query_and_explicit_selection_keep_database_order() -> None:
    first = _sheet(title="Первый", order=1)
    second = _sheet(title="Второй", order=2)
    sheets = [first, second]

    assert _select_sheets(sheets, query="", query_aware=True) == sheets
    assert _select_sheets(sheets, query="несовпадающий запрос", query_aware=False) == sheets
