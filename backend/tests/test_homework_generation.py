"""Homework integration contract without any paid generation or production database."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import integration
from app.models import GenerationBatch, TaskTemplate


class Db:
    def __init__(self):
        self.batch = None
        self.tasks = []
        self.template = SimpleNamespace(id="template", assistant_id="assistant", topic="Equilibrium", difficulty="hard")

    async def get(self, model, _id):
        return self.batch if model is GenerationBatch else self.template if model is TaskTemplate else None

    def add(self, batch):
        self.batch = batch

    async def commit(self):
        pass

    async def scalars(self, _query):
        return SimpleNamespace(all=lambda: self.tasks)


def setup(monkeypatch):
    db = Db()
    from app.services import taskgen
    monkeypatch.setattr(taskgen, "merge_template_params", lambda _template, **kwargs: kwargs)
    monkeypatch.setattr(integration, "get_settings", lambda: SimpleNamespace(picrete_integration_token="secret"))
    async def assistant(*_args):
        return SimpleNamespace(id="assistant", generation_policy="single_verifier", default_generator_model_id="gen")
    async def model(*_args):
        return SimpleNamespace(name="provider"), SimpleNamespace(id="model", model_id="model")
    async def prompt(*_args):
        return None
    monkeypatch.setattr(integration, "get_assistant_or_404", assistant)
    monkeypatch.setattr(integration, "uses_single_verifier", lambda _: True)
    monkeypatch.setattr(integration, "task_verifier_model_id", lambda _: "verifier")
    monkeypatch.setattr(integration, "resolve_model", model)
    monkeypatch.setattr(integration, "require_decision_model", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(integration, "resolve_generator_prompt_version", prompt)
    monkeypatch.setattr(integration, "task_is_export_ready", lambda t: t.status == "validated")
    body = integration.HomeworkBatchRequest(assistant_id="assistant", template_id="template", batch_id="a" * 32, count=2)
    return db, body


def task(statement, status="validated"):
    return SimpleNamespace(statement=statement, status=status, reference_solution="Compute using the formula", answer="4",
                           images=[], rubric=[{"criterion_name": "Result", "max_score": 10}], max_score=10,
                           validation={}, template_id="template", topic="Equilibrium")


def test_start_poll_is_idempotent_and_uses_exact_blueprint(monkeypatch):
    db, body = setup(monkeypatch)
    background = BackgroundTasks()
    async def run():
        first = await integration.homework_batch(body, background, "Bearer secret", db)
        second = await integration.homework_batch(body, background, "Bearer secret", db)
        assert first["status"] == second["status"] == "running"
        assert len(background.tasks) == 1
        assert db.batch.template_id == "template"
        assert db.batch.params["difficulty"] == "hard"
        assert db.batch.params["count"] == 2
        db.batch.status = "completed"
        db.tasks = [task("Calculate 2 + 2"), task("Calculate 3 + 1")]
        ready = await integration.homework_batch(body, background, "Bearer secret", db)
        assert ready["status"] == "ready"
        assert len(ready["tasks"]) == 2
        assert ready["tasks"][0]["reference_solution"]
        assert len(background.tasks) == 1
    asyncio.run(run())


def test_rejects_unverified_and_duplicate_variants_keeps_ready_subset(monkeypatch):
    db, body = setup(monkeypatch)
    async def run():
        background = BackgroundTasks()
        await integration.homework_batch(body, background, "Bearer secret", db)
        db.batch.status = "completed"
        db.tasks = [task("Calculate  2 + 2"), task("calculate 2 + 2"), task("Calculate 3 + 1", "needs_review")]
        result = await integration.homework_batch(body, background, "Bearer secret", db)
        assert result["status"] == "failed"
        assert result["ready_count"] == 1
        assert len(result["tasks"]) == 1
    asyncio.run(run())


def test_batch_key_cannot_be_reused_for_another_blueprint(monkeypatch):
    db, body = setup(monkeypatch)
    async def run():
        await integration.homework_batch(body, BackgroundTasks(), "Bearer secret", db)
        body.template_id = "other"
        with pytest.raises(HTTPException) as exc:
            await integration.homework_batch(body, BackgroundTasks(), "Bearer secret", db)
        assert exc.value.status_code == 409
    asyncio.run(run())


def test_internal_token_and_template_ownership(monkeypatch):
    db, body = setup(monkeypatch)
    async def run():
        with pytest.raises(HTTPException) as exc:
            await integration.homework_batch(body, BackgroundTasks(), "wrong", db)
        assert exc.value.status_code == 401
        db.template.assistant_id = "another-course"
        with pytest.raises(HTTPException) as exc:
            await integration.homework_batch(body, BackgroundTasks(), "Bearer secret", db)
        assert exc.value.status_code == 422
        assert db.batch is None
    asyncio.run(run())
