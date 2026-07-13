import asyncio
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import tasks as tasks_api
from app.schemas import GenerationBatchRequest
from app.services import taskgen
from app.services.taskgen import GenerationError


class BatchDb:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None

    async def refresh(self, value):
        if value.id is None:
            value.id = "batch-1"


def _batch_body(prompt_version_id: str | None) -> GenerationBatchRequest:
    return GenerationBatchRequest(
        model_entry_id="model-entry-1",
        prompt_version_id=prompt_version_id,
        count=2,
    )


def _call_create_batch(monkeypatch, *, requested_id: str | None, resolved_prompt) -> tuple[object, list]:
    requested: list[str | None] = []

    async def fake_assistant(*_args):
        return SimpleNamespace(id="assistant-1")

    async def fake_model(*_args):
        return SimpleNamespace(name="DeepSeek"), SimpleNamespace(model_id="deepseek-v4-pro")

    async def fake_prompt(_db, _assistant_id, prompt_version_id):
        requested.append(prompt_version_id)
        return resolved_prompt

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    monkeypatch.setattr(tasks_api, "resolve_model", fake_model)
    monkeypatch.setattr(tasks_api, "resolve_generator_prompt_version", fake_prompt)
    db = BatchDb()
    batch = asyncio.run(
        tasks_api.create_batch(
            "assistant-1",
            _batch_body(requested_id),
            BackgroundTasks(),
            db,
            SimpleNamespace(id="teacher-1"),
        )
    )
    return batch, requested


def test_batch_freezes_current_active_prompt_id(monkeypatch) -> None:
    batch, requested = _call_create_batch(
        monkeypatch,
        requested_id=None,
        resolved_prompt=SimpleNamespace(id="active-generator-v7"),
    )

    assert requested == [None]
    assert batch.params["prompt_version_id"] == "active-generator-v7"


def test_batch_preserves_explicit_prompt_id(monkeypatch) -> None:
    batch, requested = _call_create_batch(
        monkeypatch,
        requested_id="chosen-generator-v3",
        resolved_prompt=SimpleNamespace(id="chosen-generator-v3"),
    )

    assert requested == ["chosen-generator-v3"]
    assert batch.params["prompt_version_id"] == "chosen-generator-v3"


def test_batch_keeps_builtin_fallback_when_no_active_prompt(monkeypatch) -> None:
    batch, requested = _call_create_batch(monkeypatch, requested_id=None, resolved_prompt=None)

    assert requested == [None]
    assert batch.params["prompt_version_id"] is None


def test_missing_explicit_prompt_still_returns_not_found(monkeypatch) -> None:
    async def fake_assistant(*_args):
        return SimpleNamespace(id="assistant-1")

    async def fake_model(*_args):
        return SimpleNamespace(name="DeepSeek"), SimpleNamespace(model_id="deepseek-v4-pro")

    async def missing_prompt(*_args):
        raise GenerationError("Версия промпта не найдена")

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    monkeypatch.setattr(tasks_api, "resolve_model", fake_model)
    monkeypatch.setattr(tasks_api, "resolve_generator_prompt_version", missing_prompt)

    with pytest.raises(HTTPException, match="Версия промпта не найдена") as error:
        asyncio.run(
            tasks_api.create_batch(
                "assistant-1",
                _batch_body("missing-prompt"),
                BackgroundTasks(),
                BatchDb(),
                SimpleNamespace(id="teacher-1"),
            )
        )

    assert error.value.status_code == 404


class PromptResult:
    def __init__(self, prompt):
        self.prompt = prompt

    def scalar_one_or_none(self):
        return self.prompt

    def scalars(self):
        return self

    def first(self):
        return self.prompt


class PromptDb:
    def __init__(self, prompt):
        self.prompt = prompt

    async def execute(self, _statement):
        return PromptResult(self.prompt)


def test_prompt_resolver_returns_active_record_and_none_fallback() -> None:
    active = SimpleNamespace(id="active-v4", system_prompt="system")

    resolved = asyncio.run(taskgen.resolve_generator_prompt_version(PromptDb(active), "assistant-1", None))
    missing = asyncio.run(taskgen.resolve_generator_prompt_version(PromptDb(None), "assistant-1", None))

    assert resolved is active
    assert missing is None
