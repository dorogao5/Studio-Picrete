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
}


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
        return [{"statement": f"Задача {len(requested)}"}]

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
            return [{"statement": "Единственная валидная задача"}]
        return [{"statement": ""}]

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
