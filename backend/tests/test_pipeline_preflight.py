import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import pipelines as pipelines_api
from app.models import ModelEntry, Provider
from app.schemas import PipelineRunRequest
from app.services import grading, pipeline
from app.services.model_policy import current_model_use_policy


RUBRIC = [{"criterion_name": "Решение", "max_score": 5}]


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class AssistantDb:
    def __init__(self, assistant):
        self.assistant = assistant
        self.commits = 0

    async def execute(self, _statement):
        return ScalarResult(self.assistant)

    async def commit(self):
        self.commits += 1


def run_input() -> dict:
    return {
        "task_text": "Условие",
        "reference_solution": "Решение",
        "rubric": RUBRIC,
        "max_score": 5,
        "ocr_text": "Работа студента",
        "image_ids": [],
    }


def test_all_grade_steps_are_preflighted_before_ocr_or_llm(monkeypatch) -> None:
    assistant = SimpleNamespace(id="assistant")
    provider = SimpleNamespace(name="DeepSeek")
    model = SimpleNamespace(model_id="deepseek-v4-pro")
    prompt = SimpleNamespace(system_prompt="Проверяющий", version=1)
    resolved: list[str] = []
    external_calls: list[str] = []

    async def fake_resolve_model(_db, model_id: str):
        resolved.append(model_id)
        if model_id == "missing":
            raise pipeline.PipelineError("Модель missing не найдена")
        return provider, model

    async def fake_resolve_prompt(*_args):
        return prompt

    async def fake_ocr(*_args):
        external_calls.append("ocr")
        return {"ocr_text": "text"}

    async def fake_grading(*_args, **_kwargs):
        external_calls.append("llm")
        raise AssertionError("LLM must not run")

    monkeypatch.setattr(pipeline, "_resolve_model", fake_resolve_model)
    monkeypatch.setattr(pipeline, "_resolve_grader_prompt", fake_resolve_prompt)
    monkeypatch.setattr(pipeline, "_run_ocr_step", fake_ocr)
    monkeypatch.setattr(pipeline.grading, "run_grading", fake_grading)

    configured = SimpleNamespace(
        assistant_id="assistant",
        steps=[
            {"type": "ocr", "config": {}},
            {"type": "grade", "config": {"model_entry_id": "valid"}},
            {"type": "grade", "config": {"model_entry_id": "missing"}},
        ],
    )
    run = SimpleNamespace(input=run_input(), status="running", error="", steps_log=[], finished_at=None)
    db = AssistantDb(assistant)

    asyncio.run(pipeline.execute_pipeline(db, configured, run))

    assert resolved == ["valid", "missing"]
    assert external_calls == []
    assert run.status == "failed"
    assert "Модель missing не найдена" in run.error
    assert run.finished_at is not None
    assert db.commits == 1


def grade_plan() -> pipeline.PipelinePlan:
    assistant = SimpleNamespace(id="assistant")
    provider = SimpleNamespace(name="DeepSeek")
    model = SimpleNamespace(model_id="deepseek-v4-pro")
    prompt = SimpleNamespace(system_prompt="Проверяющий", version=1)
    return pipeline.PipelinePlan(
        assistant=assistant,
        grades={
            0: pipeline.GradeStepPlan(
                provider=provider,
                model=model,
                prompt=prompt,
                model_use=current_model_use_policy().classify(model),
            )
        },
    )


def configured_pipeline():
    return SimpleNamespace(
        assistant_id="assistant",
        steps=[{"type": "grade", "config": {"model_entry_id": "model"}}],
    )


def configured_run():
    return SimpleNamespace(input=run_input(), status="running", error="", steps_log=[], finished_at=None)


def test_failed_grade_makes_the_whole_run_failed(monkeypatch) -> None:
    async def fake_grounding(*_args, **_kwargs):
        return ""

    async def failed_grade(*_args, **_kwargs):
        return grading.GradeOutcome(
            output=None,
            raw_text="",
            duration_ms=1,
            tokens_total=None,
            error="Ответ модели не прошёл контракт",
        )

    monkeypatch.setattr(pipeline, "build_grounding_block", fake_grounding)
    monkeypatch.setattr(pipeline.grading, "run_grading", failed_grade)
    db = AssistantDb(None)
    run = configured_run()

    asyncio.run(pipeline.execute_pipeline(db, configured_pipeline(), run, grade_plan()))

    assert run.status == "failed"
    assert "Ответ модели не прошёл контракт" in run.error
    assert run.steps_log[0]["status"] == "failed"
    assert run.finished_at is not None


def test_unexpected_exception_is_persisted_as_failed(monkeypatch) -> None:
    async def fake_grounding(*_args, **_kwargs):
        return ""

    async def broken_grade(*_args, **_kwargs):
        raise RuntimeError("provider parser exploded")

    monkeypatch.setattr(pipeline, "build_grounding_block", fake_grounding)
    monkeypatch.setattr(pipeline.grading, "run_grading", broken_grade)
    db = AssistantDb(None)
    run = configured_run()

    asyncio.run(pipeline.execute_pipeline(db, configured_pipeline(), run, grade_plan()))

    assert run.status == "failed"
    assert "RuntimeError" in run.error
    assert "provider parser exploded" in run.error
    assert run.finished_at is not None
    assert db.commits == 1


class SequenceDb:
    def __init__(self, *values):
        self.values = iter(values)

    async def execute(self, _statement):
        return ScalarResult(next(self.values))


def test_disabled_or_nonproduction_models_fail_preflight() -> None:
    disabled = ModelEntry(id="disabled", provider_id="provider", model_id="disabled-model", enabled=False)
    with pytest.raises(pipeline.PipelineError, match="отключена"):
        asyncio.run(pipeline._resolve_model(SequenceDb(disabled), disabled.id))

    model = ModelEntry(id="architect-model", provider_id="architect", model_id="architect", enabled=True)
    architect = Provider(
        id="architect", name="Architect", base_url="https://example.invalid", enabled=True, purpose="architect"
    )
    with pytest.raises(pipeline.PipelineError, match="не предназначен"):
        asyncio.run(pipeline._resolve_model(SequenceDb(model, architect), model.id))


def test_explicit_prompt_must_belong_to_assistant_and_grader_role() -> None:
    wrong_prompt = SimpleNamespace(id="prompt", assistant_id="other-assistant", role="tutor", status="active")

    with pytest.raises(pipeline.PipelineError, match="не принадлежит"):
        asyncio.run(pipeline._resolve_grader_prompt(SequenceDb(wrong_prompt), "expected-assistant", wrong_prompt.id))


def test_run_api_does_not_persist_a_run_when_preflight_fails(monkeypatch) -> None:
    configured = SimpleNamespace(
        id="pipeline",
        assistant_id="assistant",
        steps=[{"type": "grade", "config": {"model_entry_id": "missing"}}],
    )

    async def fake_get_pipeline(*_args):
        return configured

    async def rejected_preflight(*_args):
        raise pipeline.PipelineError("Модель missing не найдена")

    class Db:
        added = False

        def add(self, _value):
            self.added = True

    db = Db()
    monkeypatch.setattr(pipelines_api, "_get_pipeline", fake_get_pipeline)
    monkeypatch.setattr(pipelines_api, "preflight_pipeline", rejected_preflight)

    with pytest.raises(HTTPException, match="Модель missing не найдена"):
        asyncio.run(
            pipelines_api.run_pipeline(
                "assistant",
                "pipeline",
                PipelineRunRequest(
                    task_text="Условие",
                    rubric=RUBRIC,
                    max_score=5,
                    ocr_text="Работа студента",
                ),
                db,
                SimpleNamespace(id="teacher"),
            )
        )
    assert db.added is False
