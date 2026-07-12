import json

import pytest

from app.services.kb import _chemrag_index_to_markdown


def test_chemrag_index_preserves_text_and_marks_ai_figure_descriptions() -> None:
    payload = [
        {
            "page": 2,
            "markdown": "## Получение газа\n\nТекст.\n\n![](images/0.jpg)",
            "figure_metadata": [{"ai_description": "Схема прибора для получения газа."}],
        },
        {"page": 1, "markdown": "Титульная страница", "figure_metadata": []},
    ]

    markdown, page_count = _chemrag_index_to_markdown(json.dumps(payload).encode())

    assert page_count == 2
    assert markdown.index("# Страница 1") < markdown.index("# Страница 2")
    assert "## Получение газа" in markdown
    assert "images/0.jpg" not in markdown
    assert "Автоматические описания ниже требуют сверки" in markdown
    assert "Схема прибора для получения газа" in markdown


def test_chemrag_index_rejects_arbitrary_json() -> None:
    with pytest.raises(ValueError, match="массив страниц"):
        _chemrag_index_to_markdown(b'{"items": []}')
