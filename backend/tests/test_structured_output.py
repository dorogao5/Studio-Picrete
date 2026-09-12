import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.llm import client
from app.models import Assistant, GeneratedTask, ModelEntry, Provider
from app.services import physical_chemistry as pc
from app.services.contracts import GRADING_RESPONSE_SCHEMA, PHYSICAL_GENERATION_RESPONSE_SCHEMA


MODEL_URI = "gpt://folder/qwen3.6-35b-a3b"


@pytest.mark.parametrize("model_uri,expected", [
    (MODEL_URI, True),
    ("gpt://other-folder/qwen3.6-35b-a3b", False),
    (MODEL_URI + "/latest", False),
    ("gpt://folder/qwen-future", False),
])
def test_schema_allowlist_uses_exact_uri_and_environment(monkeypatch, model_uri, expected):
    monkeypatch.setenv("STUDIO_JSON_SCHEMA_MODEL_IDS", f" , {MODEL_URI}, gpt://folder/another , ")
    monkeypatch.setattr(pc, "get_settings", lambda: Settings(_env_file=None))
    assert pc.physical_json_schema_enabled(
        Assistant(name="Физическая химия", discipline="Физическая химия"), Provider(kind="yandex"),
        ModelEntry(model_id=model_uri, family="qwen", supports_json=True),
    ) is expected


def test_schema_capability_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("STUDIO_JSON_SCHEMA_MODEL_IDS", raising=False)
    assert Settings(_env_file=None).json_schema_model_ids == ""


@pytest.mark.parametrize("enabled,discipline,kind,family,expected", [
    (False, "Физическая химия", "yandex", "qwen", False),
    (True, "Физическая химия", "yandex", "qwen", True),
    (True, "Аналитическая химия", "yandex", "qwen", False),
    (True, "Физическая химия", "custom", "qwen", False),
    (True, "Физическая химия", "yandex", "deepseek", False),
])
def test_schema_capability_is_scoped(monkeypatch, enabled, discipline, kind, family, expected):
    monkeypatch.setattr(pc, "get_settings", lambda: Settings(_env_file=None, json_schema_model_ids=MODEL_URI if enabled else ""))
    assert pc.physical_json_schema_enabled(
        Assistant(name=discipline, discipline=discipline), Provider(kind=kind),
        ModelEntry(model_id=MODEL_URI, family=family, supports_json=True),
    ) is expected


@pytest.mark.parametrize("schema", [None, PHYSICAL_GENERATION_RESPONSE_SCHEMA, GRADING_RESPONSE_SCHEMA])
@pytest.mark.parametrize("effort", [None, "high"])
def test_client_sends_requested_schema_once(monkeypatch, schema, effort):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    real_client = httpx.AsyncClient
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(client, "decrypt_secret", lambda _: "mock-key")
    result = asyncio.run(client._chat(
        Provider(name="mock", base_url="https://mock.invalid/v1", api_key_encrypted="", extra_headers={}),
        ModelEntry(model_id=MODEL_URI, family="qwen", supports_json=True),
        "system", "user", json_mode=True, response_schema=schema, reasoning_effort=effort,
    ))
    assert len(requests) == 1
    assert result.raw["finish_reason"] == "stop"
    expected = {"type": "json_object"} if schema is None else {
        "type": "json_schema", "json_schema": {"name": "picrete_response", "strict": True, "schema": schema},
    }
    assert requests[0]["response_format"] == expected
    assert requests[0].get("reasoning_effort") == effort
    if effort is None:
        assert "reasoning_effort" not in requests[0]
    assert "max_tokens" not in requests[0]


@pytest.mark.parametrize("finish_reason,error", [("length", None), ("content_filter", None),
                                               ("error", None), (None, "provider stream failure")])
def test_incomplete_stream_or_error_is_not_retried_as_empty_json(monkeypatch, finish_reason, error):
    calls = []
    def handler(request):
        calls.append(request)
        event = {"error": {"message": error}} if error else {
            "choices": [{"delta": {"content": "{}"}, "finish_reason": finish_reason}],
        }
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(client, "decrypt_secret", lambda _: "mock-key")
    with pytest.raises(client.LlmError, match=error or f"finish_reason={finish_reason}"):
        asyncio.run(client._chat(
            Provider(name="mock", base_url="https://mock.invalid/v1", api_key_encrypted="", extra_headers={}),
            ModelEntry(model_id=MODEL_URI, family="qwen", supports_json=True), "system", "user", json_mode=True,
        ))
    assert len(calls) == 1


def test_schemas_require_root_keys_and_full_task_fields():
    assert PHYSICAL_GENERATION_RESPONSE_SCHEMA["required"] == ["tasks"]
    tasks = PHYSICAL_GENERATION_RESPONSE_SCHEMA["properties"]["tasks"]
    assert tasks["minItems"] == 1
    assert set(tasks["items"]["required"]) == {
        "statement", "reference_solution", "answer", "images", "rubric", "max_score",
        "difficulty", "topic", "data_used", "chemistry_facts",
    }
    assert tasks["items"]["properties"]["chemistry_facts"]["additionalProperties"] is False
    assert {"total_score", "max_score", "criteria_scores", "feedback"} <= set(GRADING_RESPONSE_SCHEMA["required"])


@pytest.mark.parametrize("kind,family,effort", [("yandex", "deepseek", "high"),
                                             ("custom", "deepseek", None), ("yandex", "qwen", None)])
def test_physical_verifier_high_effort_is_scoped(monkeypatch, kind, family, effort):
    calls = []
    async def chat(*args, **kwargs):
        calls.append(kwargs)
        return client.LlmResult(text='{"verdict":"pass","corrected_task":null}', duration_ms=1)
    monkeypatch.setattr(pc.llm, "chat", chat)
    task = GeneratedTask(statement="test", reference_solution="test", answer="1", rubric=[],
                         max_score=10, difficulty="easy", topic="test", grounding={}, images=[])
    asyncio.run(pc.run_physical_validation(task=task, provider=Provider(kind=kind),
        model=ModelEntry(model_id="deepseek-v4-pro", family=family), grounding="", discipline_context=""))
    assert len(calls) == 1 and calls[0].get("reasoning_effort") == effort
    assert "max_tokens" not in calls[0]


@pytest.mark.parametrize("enabled", [True, False])
def test_physical_generator_and_grader_use_full_schema(monkeypatch, enabled):
    from app.services import taskgen, grading
    from app.services.contracts import PHYSICAL_GENERATION_JSON_EXAMPLE
    assistant = Assistant(name="Физическая химия", discipline="Физическая химия", topics=[], nuances=[], criteria=[])
    provider = Provider(kind="yandex")
    model = ModelEntry(model_id=MODEL_URI, family="qwen", supports_json=True)
    calls = []
    async def chat(*args, **kwargs):
        calls.append(kwargs)
        text = PHYSICAL_GENERATION_JSON_EXAMPLE if len(calls) == 1 else json.dumps({
            "unreadable": False, "total_score": 8, "max_score": 10,
            "criteria_scores": [{"criterion_name": "Расчёт", "score": 8, "max_score": 10, "comment": "ошибка"}],
            "feedback": "Исправьте вычисление", "needs_teacher_review": False,
        })
        return client.LlmResult(text=text, duration_ms=1)
    monkeypatch.setattr(pc.llm, "chat", chat)
    monkeypatch.setattr(pc, "get_settings", lambda: Settings(_env_file=None, json_schema_model_ids=MODEL_URI if enabled else ""))
    asyncio.run(taskgen.generate_tasks(provider, model, assistant, "configured", topic="test", difficulty="easy", count=1))
    asyncio.run(grading.run_grading(provider, model, "configured", "task", "solution",
        [{"criterion_name": "Расчёт", "max_score": 10}], 10, "student work", assistant=assistant))
    assert len(calls) == 2
    if enabled:
        assert calls[0]["response_schema"] == PHYSICAL_GENERATION_RESPONSE_SCHEMA
        assert calls[1]["response_schema"] == GRADING_RESPONSE_SCHEMA
    else:
        assert all("response_schema" not in call for call in calls)
    assert all("reasoning_effort" not in call for call in calls)


@pytest.mark.parametrize("structured", [True, False])
def test_missing_finish_reason_rejected_for_schema_only(monkeypatch, structured):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"{}"}}]}\n\ndata: [DONE]\n\n')
    real_client = httpx.AsyncClient
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(client, "decrypt_secret", lambda _: "mock-key")
    async def run():
        return await client._chat(
            Provider(name="mock", base_url="https://mock.invalid/v1", api_key_encrypted="", extra_headers={}),
            ModelEntry(model_id=MODEL_URI, family="qwen", supports_json=True), "system", "user",
            response_schema=PHYSICAL_GENERATION_RESPONSE_SCHEMA if structured else None,
        )
    if structured:
        with pytest.raises(client.LlmError, match="finish_reason"):
            asyncio.run(run())
    else:
        assert asyncio.run(run()).text == "{}"
    assert len(calls) == 1
