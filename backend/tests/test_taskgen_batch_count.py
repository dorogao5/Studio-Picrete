import asyncio
from types import SimpleNamespace

from app.services import taskgen


MERGED = {
    "topic": "Тема",
    "difficulty": "medium",
    "task_kind": "calculation",
    "answer_format": "numeric",
    "instructions": "",
    "example_tasks": [],
    "chemistry_check": "auto",
}


def valid_item(statement: str) -> dict:
    return {"statement": statement, "data_used": [], "chemistry_facts": {}}


def run_collection(count: int):
    return taskgen._generate_batch_items(
        SimpleNamespace(name="provider"),
        SimpleNamespace(model_id="model"),
        SimpleNamespace(discipline="chemistry"),
        "prompt",
        merged=MERGED,
        params={"temperature": 0.2},
        count=count,
        grounding_text="",
        existing_statements=[],
    )


def test_refills_items_missing_from_model_chunks(monkeypatch) -> None:
    requested: list[int] = []

    async def one_at_a_time(*args, count: int, **kwargs) -> list[dict]:
        requested.append(count)
        return [valid_item(f"Задача {len(requested)}")]

    monkeypatch.setattr(taskgen, "generate_tasks", one_at_a_time)
    items, errors = asyncio.run(run_collection(4))

    assert len(items) == 4
    assert requested == [2, 2, 2, 1]
    assert errors == []


def test_refill_attempts_are_bounded_and_short_batch_is_failed(monkeypatch) -> None:
    calls = 0

    async def mostly_invalid(*args, **kwargs) -> list[dict]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [valid_item("Единственная валидная задача")]
        return [valid_item("")]

    monkeypatch.setattr(taskgen, "generate_tasks", mostly_invalid)
    items, errors = asyncio.run(run_collection(3))

    assert len(items) == 1
    assert calls == 5  # ceil(3 / 2) обязательных порций + 3 попытки восполнения
    assert len(errors) == 4

    batch = SimpleNamespace(status="running", error="", progress={}, finished_at=None)
    taskgen._mark_batch_finished(
        batch,
        requested_count=3,
        generated_count=len(items),
        generation_errors=errors,
    )
    assert batch.status == "failed"
    assert "готово 1 из 3" in batch.error
    assert batch.progress == {"stage": "Неполная партия", "done": 1, "total": 3}
    assert batch.finished_at is not None


def test_exact_batch_is_the_only_successful_completion() -> None:
    batch = SimpleNamespace(status="running", error="old", progress={}, finished_at=None)

    taskgen._mark_batch_finished(
        batch,
        requested_count=3,
        generated_count=3,
        generation_errors=[],
    )

    assert batch.status == "completed"
    assert batch.error == ""
    assert batch.progress == {"stage": "Готово", "done": 3, "total": 3}


def test_refills_items_missing_structured_evidence(monkeypatch) -> None:
    calls = 0

    async def missing_then_complete(*args, **kwargs) -> list[dict]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [{"statement": "Нет evidence"}]
        return [valid_item("Полный evidence-контракт")]

    monkeypatch.setattr(taskgen, "generate_tasks", missing_then_complete)
    items, errors = asyncio.run(run_collection(1))

    assert [item["statement"] for item in items] == ["Полный evidence-контракт"]
    assert calls == 2
    assert any("data_used" in error for error in errors)


def test_shared_call_budget_caps_initial_and_refill_waves(monkeypatch) -> None:
    calls = 0

    async def one_at_a_time(*args, **kwargs) -> list[dict]:
        nonlocal calls
        calls += 1
        return [valid_item(f"Кандидат {calls}")]

    async def collect() -> tuple[list[dict], list[dict], list[str], taskgen._GenerationCallBudget]:
        budget = taskgen._GenerationCallBudget(limit=3)
        initial, initial_errors = await taskgen._generate_batch_items(
            SimpleNamespace(name="provider"),
            SimpleNamespace(model_id="model"),
            SimpleNamespace(discipline="chemistry"),
            "prompt",
            merged=MERGED,
            params={"temperature": 0.2},
            count=2,
            grounding_text="",
            existing_statements=[],
            call_budget=budget,
        )
        refill, refill_errors = await taskgen._generate_batch_items(
            SimpleNamespace(name="provider"),
            SimpleNamespace(model_id="model"),
            SimpleNamespace(discipline="chemistry"),
            "prompt",
            merged=MERGED,
            params={"temperature": 0.2},
            count=2,
            grounding_text="",
            existing_statements=[item["statement"] for item in initial],
            call_budget=budget,
        )
        return initial, refill, initial_errors + refill_errors, budget

    monkeypatch.setattr(taskgen, "generate_tasks", one_at_a_time)
    initial, refill, errors, budget = asyncio.run(collect())

    assert len(initial) == 2
    assert len(refill) == 1
    assert calls == 3
    assert budget.used == budget.limit == 3
    assert any("Исчерпан общий бюджет" in error for error in errors)


def test_generation_call_limit_is_based_on_whole_candidate_budget() -> None:
    assert taskgen._generation_call_limit(15) == 11
    assert taskgen._generation_call_limit(30) == 18
    assert taskgen._generation_call_limit(40) == 23
