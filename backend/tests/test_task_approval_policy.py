import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import tasks as tasks_api
from app.schemas import GeneratedTaskUpdate, TaskExportRequest
from app.services.validation import VALIDATION_POLICY_VERSION


class FakeDb:
    def __init__(self, tasks=None):
        self.tasks = tasks or []
        self.commits = 0

    async def execute(self, _statement):
        values = self.tasks

        class Result:
            def scalars(self):
                return values

        return Result()

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None


def generated_task(*, status="needs_review", validation=None, approved=False):
    return SimpleNamespace(
        id="task-1",
        status=status,
        approved=approved,
        validation=validation or {"verdict": status},
        statement="Условие",
        reference_solution="Решение",
        answer="Ответ",
        rubric=[{"criterion_name": "Решение", "max_score": 5}],
        max_score=5,
    )


def call_update(monkeypatch, task, body):
    async def fake_get(*_args):
        return task

    monkeypatch.setattr(tasks_api, "_get_task_or_404", fake_get)
    db = FakeDb()
    result = asyncio.run(
        tasks_api.update_task(
            "assistant",
            task.id,
            GeneratedTaskUpdate(**body),
            db,
            SimpleNamespace(id="teacher-1"),
        )
    )
    return result, db


def test_unvalidated_task_requires_teacher_reason(monkeypatch) -> None:
    task = generated_task()
    with pytest.raises(HTTPException, match="причину ручного одобрения"):
        call_update(monkeypatch, task, {"status": "approved"})


def test_teacher_override_is_recorded(monkeypatch) -> None:
    task = generated_task()
    result, db = call_update(
        monkeypatch,
        task,
        {"status": "approved", "approval_reason": "Проверено вручную по методичке"},
    )

    assert result.status == "approved"
    assert result.approved is True
    assert result.validation["approval"]["basis"] == "teacher_override"
    assert result.validation["approval"]["reviewed_by"] == "teacher-1"
    assert db.commits == 1


def test_current_policy_validation_can_be_approved_directly(monkeypatch) -> None:
    task = generated_task(
        status="validated",
        validation={
            "verdict": "validated",
            "policy_version": VALIDATION_POLICY_VERSION,
            "model_policy": {"decision_capable": True},
        },
    )
    result, _db = call_update(monkeypatch, task, {"status": "approved"})

    assert result.validation["approval"]["basis"] == "policy_validated"


def test_editing_content_invalidates_previous_approval(monkeypatch) -> None:
    task = generated_task(status="approved", validation={"approval": {"basis": "teacher_override"}}, approved=True)
    result, _db = call_update(monkeypatch, task, {"answer": "Исправленный ответ"})

    assert result.answer == "Исправленный ответ"
    assert result.status == "draft"
    assert result.approved is False
    assert result.validation == {}


def test_explicit_export_rejects_unapproved_tasks(monkeypatch) -> None:
    task = generated_task()

    async def fake_assistant(*_args):
        return SimpleNamespace(discipline="Химия")

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    with pytest.raises(HTTPException, match="сначала одобрите"):
        asyncio.run(
            tasks_api.export_tasks(
                "assistant",
                TaskExportRequest(task_ids=[task.id], mode="bank"),
                FakeDb([task]),
            )
        )
