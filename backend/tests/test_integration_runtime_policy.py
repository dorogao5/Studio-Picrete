import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import integration
from app.services.model_policy import ModelUsePolicy


class FakeDb:
    def __init__(self, model, query_results: list[list] | None = None):
        self.model = model
        self.requested_id = None
        self.query_results = list(query_results or [])

    async def get(self, _model_class, model_id):
        self.requested_id = model_id
        return self.model

    async def execute(self, _statement):
        values = self.query_results.pop(0)

        class Result:
            def scalars(self):
                return values

        return Result()


def _policy() -> ModelUsePolicy:
    return ModelUsePolicy(
        version="model-use-v1:test",
        decision_model_ids=frozenset({"deepseek-v4-pro"}),
        advisory_model_ids=frozenset({"deepseek-v4-flash"}),
    )


def test_runtime_policy_resolves_actual_default_model_id(monkeypatch) -> None:
    model = SimpleNamespace(model_id="deepseek-v4-pro", enabled=True)
    db = FakeDb(model)
    assistant = SimpleNamespace(default_grader_model_id="entry-42")
    monkeypatch.setattr(integration, "current_model_use_policy", _policy)

    runtime = asyncio.run(integration._build_runtime_policy(db, assistant))

    assert db.requested_id == "entry-42"
    assert runtime == {
        "policy_version": "model-use-v1:test",
        "tutor_model_id": "deepseek-v4-pro",
        "decision_model_id": "deepseek-v4-pro",
        "tier": "decision",
        "allowed_uses": ["student_tutor", "task_validation", "grading"],
    }


def test_advisory_default_cannot_be_published(monkeypatch) -> None:
    db = FakeDb(SimpleNamespace(model_id="deepseek-v4-flash", enabled=True))
    assistant = SimpleNamespace(default_grader_model_id="entry-flash")
    monkeypatch.setattr(integration, "current_model_use_policy", _policy)

    with pytest.raises(HTTPException, match="нельзя опубликовать") as error:
        asyncio.run(integration._build_runtime_policy(db, assistant))

    assert error.value.status_code == 422


def test_runtime_policy_is_part_of_immutable_snapshot_hash() -> None:
    base = {
        "schema_version": 1,
        "assistant": {
            "id": "assistant-1",
            "runtime_policy": {
                "policy_version": "model-use-v1:test",
                "tutor_model_id": "deepseek-v4-pro",
                "decision_model_id": "deepseek-v4-pro",
                "tier": "decision",
                "allowed_uses": ["student_tutor"],
            },
        },
        "prompts": {},
        "reference_sheets": [],
    }
    changed = {
        **base,
        "assistant": {
            **base["assistant"],
            "runtime_policy": {
                **base["assistant"]["runtime_policy"],
                "tutor_model_id": "another-model",
            },
        },
    }

    first = integration._seal_snapshot(base)
    second = integration._seal_snapshot(changed)

    assert first["version"] != second["version"]
    assert first["assistant"]["runtime_policy"]["tutor_model_id"] == "deepseek-v4-pro"


def test_built_snapshot_contains_nested_runtime_contract(monkeypatch) -> None:
    model = SimpleNamespace(model_id="deepseek-v4-pro", enabled=True)
    prompt = SimpleNamespace(
        role="tutor",
        id="prompt-1",
        version=3,
        system_prompt="Помогайте студенту",
        target_family="deepseek",
    )
    db = FakeDb(model, query_results=[[prompt], []])
    assistant = SimpleNamespace(
        id="assistant-1",
        name="Ассистент",
        discipline="Химия",
        description="",
        audience="1 курс",
        language="ru",
        topics=[],
        criteria=[],
        nuances=[],
        default_grader_model_id="entry-42",
    )
    monkeypatch.setattr(integration, "current_model_use_policy", _policy)

    snapshot = asyncio.run(integration._build_snapshot(db, assistant))

    assert snapshot["assistant"]["runtime_policy"]["policy_version"] == "model-use-v1:test"
    assert snapshot["assistant"]["runtime_policy"]["tutor_model_id"] == "deepseek-v4-pro"
    assert len(snapshot["version"]) == 64
