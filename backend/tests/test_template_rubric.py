import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from app import main
from app.models import TaskTemplate
from app.schemas import TaskTemplateCreate, TaskTemplateUpdate
from app.services.taskgen import (
    build_generation_user_message,
    merge_batch_template_params,
    merge_template_params,
    task_from_item,
)


RUBRIC = [
    {"criterion_name": "Выбор метода", "max_score": 3, "description": "Обоснован подход"},
    {"criterion_name": "Расчёт", "max_score": 5, "description": "Верные формулы и числа"},
    {"criterion_name": "Ответ", "max_score": 2, "description": "Есть единицы измерения"},
]


def test_template_rubric_accepts_empty_fallback_and_normalizes_text() -> None:
    assert TaskTemplateCreate(name="Без контракта").rubric == []

    created = TaskTemplateCreate(
        name="Расчёт",
        rubric=[
            {"criterion_name": "  Метод  ", "max_score": 4, "description": "  Обоснование  "},
            {"criterion_name": "Ответ", "max_score": 6, "description": ""},
        ],
    )

    assert created.rubric[0].criterion_name == "Метод"
    assert created.rubric[0].description == "Обоснование"


@pytest.mark.parametrize(
    ("rubric", "message"),
    [
        ([{"criterion_name": "   ", "max_score": 10}], "string_too_short"),
        ([{"criterion_name": "Метод", "max_score": 0}], "greater_than"),
        ([{"criterion_name": "Метод", "max_score": 9}], "ровно 10"),
        (
            [
                {"criterion_name": "Метод", "max_score": 5},
                {"criterion_name": " метод ", "max_score": 5},
            ],
            "не должны повторяться",
        ),
    ],
)
def test_template_rubric_rejects_invalid_contract(rubric: list[dict], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        TaskTemplateCreate(name="Некорректная рубрика", rubric=rubric)


def test_template_rubric_update_uses_the_same_contract() -> None:
    with pytest.raises(ValidationError, match="ровно 10"):
        TaskTemplateUpdate(rubric=[{"criterion_name": "Ответ", "max_score": 7}])


def test_merge_template_params_carries_exact_rubric_and_empty_fallback() -> None:
    template = TaskTemplate(
        name="Шаблон",
        rubric=RUBRIC,
        topic="Растворы",
        difficulty="medium",
        instructions="",
        task_kind="calculation",
        answer_format="numeric",
        numeric_tolerance_pct=2,
        reference_sheet_ids=[],
        example_tasks=[],
        kb_query="",
        validation_solver=True,
        validation_data_check=True,
    )

    merged = merge_template_params(template, topic="", difficulty="", instructions="")

    assert merged["rubric"] == RUBRIC
    assert merge_template_params(None, topic="", difficulty="", instructions="")["rubric"] == []


def test_batch_empty_difficulty_preserves_hard_template_contract() -> None:
    template = TaskTemplate(
        name="Продвинутая задача",
        rubric=RUBRIC,
        topic="Кулонометрия",
        difficulty="hard",
        instructions="Интегрировать кусочно-заданный ток",
        task_kind="calculation",
        answer_format="numeric",
        numeric_tolerance_pct=0.5,
        reference_sheet_ids=[],
        example_tasks=[],
        kb_query="заряд по профилю тока",
        validation_solver=True,
        validation_data_check=True,
    )

    merged = merge_batch_template_params(
        template,
        {"topic": "", "difficulty": "", "instructions": ""},
    )

    assert merged["difficulty"] == "hard"
    assert merged["topic"] == "Кулонометрия"
    assert merged["instructions"] == "Интегрировать кусочно-заданный ток"


def test_generation_message_marks_template_rubric_as_an_exact_contract() -> None:
    message = build_generation_user_message(topic="Растворы", difficulty="medium", count=1, rubric=RUBRIC)

    assert "Рубрика преподавателя (обязательный контракт)" in message
    assert '"criterion_name": "Выбор метода"' in message
    assert "ТОЧНО теми же criterion_name, max_score и description" in message


def test_generated_task_preserves_template_rubric_instead_of_model_output() -> None:
    model_rubric = [{"criterion_name": "Общее впечатление", "max_score": 99, "description": ""}]

    task = task_from_item(
        {
            "statement": "Условие",
            "rubric": model_rubric,
            "max_score": 99,
            "data_used": [],
            "chemistry_facts": {},
        },
        assistant_id="assistant-1",
        template_id="template-1",
        batch_id=None,
        topic="Растворы",
        difficulty="medium",
        model_used="DeepSeek/deepseek-v4",
        grounding_meta={},
        template_rubric=RUBRIC,
    )

    assert task is not None
    assert task.rubric == RUBRIC
    assert task.rubric is not RUBRIC
    assert task.max_score == 10


def test_generated_task_keeps_model_rubric_for_legacy_empty_template() -> None:
    model_rubric = [{"criterion_name": "Решение", "max_score": 7, "description": ""}]

    task = task_from_item(
        {
            "statement": "Условие",
            "rubric": model_rubric,
            "max_score": 7,
            "data_used": [],
            "chemistry_facts": {},
        },
        assistant_id="assistant-1",
        template_id="template-1",
        batch_id=None,
        topic="Растворы",
        difficulty="medium",
        model_used="DeepSeek/deepseek-v4",
        grounding_meta={},
        template_rubric=[],
    )

    assert task is not None
    assert task.rubric == model_rubric
    assert task.max_score == 7


def test_generated_task_without_explicit_data_provenance_is_rejected() -> None:
    task = task_from_item(
        {"statement": "Условие", "rubric": [], "max_score": 10},
        assistant_id="assistant-1",
        template_id=None,
        batch_id=None,
        topic="Растворы",
        difficulty="medium",
        model_used="DeepSeek/deepseek-v4-pro",
        grounding_meta={},
    )

    assert task is None


def test_generated_task_rejects_malformed_data_provenance() -> None:
    task = task_from_item(
        {
            "statement": "Условие",
            "rubric": [],
            "max_score": 10,
            "data_used": [{"sheet_title": "Карточка без перечисленных значений", "values": []}],
        },
        assistant_id="assistant-1",
        template_id=None,
        batch_id=None,
        topic="Растворы",
        difficulty="medium",
        model_used="DeepSeek/deepseek-v4-pro",
        grounding_meta={},
    )

    assert task is None


def test_generated_task_requires_explicit_structured_chemistry_facts() -> None:
    task = task_from_item(
        {"statement": "Условие", "rubric": [], "max_score": 10, "data_used": []},
        assistant_id="assistant-1",
        template_id=None,
        batch_id=None,
        topic="Растворы",
        difficulty="medium",
        model_used="DeepSeek/deepseek-v4-pro",
        grounding_meta={},
    )

    assert task is None


def test_required_subject_check_requires_its_fact_block() -> None:
    task = task_from_item(
        {
            "statement": "Рассчитайте концентрацию после разбавления",
            "rubric": [],
            "max_score": 10,
            "data_used": [],
            "chemistry_facts": {},
        },
        assistant_id="assistant-1",
        template_id=None,
        batch_id=None,
        topic="Растворы",
        difficulty="medium",
        model_used="DeepSeek/deepseek-v4-pro",
        grounding_meta={},
        validation_contract={
            "answer_format": "numeric",
            "validation_solver": True,
            "validation_data_check": True,
            "chemistry_check": "chemistry.dilution",
        },
    )

    assert task is None


def test_sqlite_startup_backfills_rubric_for_existing_templates(monkeypatch) -> None:
    async def run() -> tuple[set[str], str]:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "CREATE TABLE task_templates (id VARCHAR(32) PRIMARY KEY, name VARCHAR(256) NOT NULL)"
            )
            await conn.exec_driver_sql("INSERT INTO task_templates (id, name) VALUES ('legacy-1', 'Старый шаблон')")
            monkeypatch.setattr(
                main,
                "SQLITE_COLUMN_BACKFILL",
                {"task_templates": {"rubric": "JSON NOT NULL DEFAULT '[]'"}},
            )
            await main.ensure_sqlite_columns(conn)
            columns = {row[1] for row in await conn.exec_driver_sql("PRAGMA table_info(task_templates)")}
            row = (await conn.exec_driver_sql("SELECT rubric FROM task_templates WHERE id = 'legacy-1'")).one()
        await engine.dispose()
        return columns, row[0]

    columns, rubric = asyncio.run(run())

    assert "rubric" in columns
    assert rubric == "[]"


def test_postgres_startup_declares_jsonb_rubric_backfill() -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def exec_driver_sql(self, statement: str) -> None:
            self.statements.append(statement)

    connection = FakeConnection()
    asyncio.run(main.ensure_postgres_columns(connection))

    rubric_statement = next(statement for statement in connection.statements if "task_templates" in statement)
    assert "ADD COLUMN IF NOT EXISTS rubric JSONB NOT NULL" in rubric_statement
    assert "DEFAULT '[]'::jsonb" in rubric_statement
