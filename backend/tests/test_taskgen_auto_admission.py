import asyncio
from types import SimpleNamespace

from app.services import taskgen


class FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    async def execute(self, _statement):
        class Scalars:
            def all(self):
                return []

        class Result:
            def scalars(self):
                return Scalars()

        return Result()

    async def commit(self) -> None:
        self.commits += 1


def _task(task_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        statement=f"Полное условие задачи {task_id} с достаточным количеством исходных данных.",
        reference_solution="Подробное эталонное решение с полным финальным ответом.",
        answer="m = 5 г",
        rubric=[{"criterion_name": "Расчёт", "max_score": 10}],
        max_score=10,
        grounding={"data_used": []},
        status="draft",
        validation={},
        approved=False,
    )


def test_failed_candidates_are_discarded_while_green_tasks_are_ready(monkeypatch) -> None:
    calls = 0

    async def fake_validation(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"verdict": "validated", "reasons": []}
        return {"verdict": "needs_review", "reasons": ["Ответы разошлись"]}

    async def quiet_progress(*_args, **_kwargs):
        return None

    monkeypatch.setattr(taskgen, "run_validation", fake_validation)
    monkeypatch.setattr(taskgen, "_set_progress", quiet_progress)
    ready = _task("ready")
    discarded = _task("discarded")
    batch = SimpleNamespace(id="batch", assistant_id="assistant", validated_count=0)
    merged = {
        "answer_format": "numeric",
        "tolerance_pct": 2,
        "validation_solver": True,
        "validation_data_check": True,
    }

    asyncio.run(
        taskgen._validate_batch(
            FakeDb(),
            batch,
            [ready, discarded],
            merged,
            SimpleNamespace(name="DeepSeek"),
            SimpleNamespace(model_id="deepseek-v4-pro"),
            "",
            "",
        )
    )

    assert batch.validated_count == 1
    assert ready.status == "validated"
    assert ready.approved is False
    assert discarded.status == "rejected"
    assert discarded.approved is False
    assert discarded.validation["candidate_disposition"] == "discarded"
