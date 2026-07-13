import asyncio
from types import SimpleNamespace

from app.llm.client import LlmResult
from app.models import Assistant, ModelEntry, Provider
from app.services import grading, pipeline, taskgen, tutor
from app.services.assistant_profile import PROFILE_HEADING, build_assistant_profile


def _assistant() -> Assistant:
    return Assistant(
        id="assistant-1",
        name="Практикум",
        discipline="Неорганическая химия",
        description="Разбор задач первого курса",
        audience="студенты 1 курса",
        language="ru",
        topics=["Растворы", "Лабораторная работа"],
        criteria=[{"name": "Расчёт", "max_score": 4, "description": "Проверить единицы"}],
        nuances=["Не придумывать наблюдения"],
    )


def _provider_and_model() -> tuple[Provider, ModelEntry]:
    provider = Provider(id="provider-1", name="DeepSeek", base_url="https://example.test", enabled=True)
    model = ModelEntry(
        id="model-1",
        provider_id=provider.id,
        model_id="deepseek-v4-pro",
        family="deepseek",
        supports_json=True,
    )
    return provider, model


def test_profile_block_renders_all_teacher_settings_deterministically() -> None:
    profile = build_assistant_profile(_assistant())

    assert profile == """ПРОФИЛЬ КУРСА И ПРЕПОДАВАТЕЛЯ
Это актуальные настройки преподавателя; соблюдайте их во всех режимах ассистента.
Ассистент: Практикум
Дисциплина: Неорганическая химия
Аудитория: студенты 1 курса
Язык: ru
Назначение: Разбор задач первого курса
Темы курса: Растворы; Лабораторная работа
Критерии оценивания:
- Расчёт — максимум 4 балла — Проверить единицы
Требования преподавателя: Не придумывать наблюдения"""


def test_generation_and_manual_runtimes_send_profile_as_system_instruction(monkeypatch) -> None:
    assistant = _assistant()
    provider, model = _provider_and_model()
    captured: list[str] = []

    async def fake_chat(_provider, _model, system_prompt, _user_message, **_kwargs):
        captured.append(system_prompt)
        if "Генератор" in system_prompt:
            return LlmResult(text='{"tasks":[{"statement":"Условие"}]}', duration_ms=1)
        return LlmResult(text='{"total_score":4}', duration_ms=1)

    monkeypatch.setattr(taskgen.llm, "chat", fake_chat)
    monkeypatch.setattr(grading.llm, "chat", fake_chat)
    monkeypatch.setattr(tutor.llm, "chat", fake_chat)

    asyncio.run(
        taskgen.generate_tasks(
            provider,
            model,
            assistant,
            "Генератор",
            topic="Растворы",
            difficulty="medium",
            count=1,
        )
    )
    asyncio.run(
        grading.run_grading(
            provider,
            model,
            "Проверяющий",
            "Условие",
            "Решение",
            [{"criterion_name": "Расчёт", "max_score": 4}],
            4,
            "Работа студента",
            assistant=assistant,
        )
    )
    asyncio.run(tutor.run_tutor_reply(provider, model, "Тьютор", "Вопрос", assistant=assistant))

    assert len(captured) == 3
    assert all(PROFILE_HEADING in prompt for prompt in captured)
    assert all("Не придумывать наблюдения" in prompt for prompt in captured)


def test_pipeline_passes_current_assistant_profile_to_grader(monkeypatch) -> None:
    assistant = _assistant()
    provider, model = _provider_and_model()
    captured: dict = {}

    class Result:
        def scalar_one_or_none(self):
            return assistant

    class Db:
        async def execute(self, _statement):
            return Result()

        async def commit(self):
            return None

    async def fake_resolve_model(_db, _model_id):
        return provider, model

    async def fake_resolve_prompt(_db, _assistant_id, _prompt_id):
        return SimpleNamespace(system_prompt="Проверяющий", version=2)

    async def fake_grounding(*_args, **_kwargs):
        return "Справочник"

    async def fake_grading(*_args, **kwargs):
        captured["assistant"] = kwargs.get("assistant")
        return grading.GradeOutcome(
            output={"total_score": 4}, raw_text='{"total_score":4}', duration_ms=1, tokens_total=10
        )

    monkeypatch.setattr(pipeline, "_resolve_model", fake_resolve_model)
    monkeypatch.setattr(pipeline, "_resolve_grader_prompt", fake_resolve_prompt)
    monkeypatch.setattr(pipeline, "build_grounding_block", fake_grounding)
    monkeypatch.setattr(pipeline.grading, "run_grading", fake_grading)

    configured = SimpleNamespace(
        assistant_id=assistant.id,
        steps=[{"type": "grade", "title": "Проверка", "config": {"model_entry_id": model.id}}],
    )
    run = SimpleNamespace(
        input={
            "task_text": "Условие",
            "ocr_text": "Работа студента",
            "rubric": [{"criterion_name": "Расчёт", "max_score": 4}],
            "max_score": 4,
        },
        status="running",
        error="",
        steps_log=[],
        finished_at=None,
    )

    asyncio.run(pipeline.execute_pipeline(Db(), configured, run))

    assert run.status == "completed"
    assert captured["assistant"] is assistant
