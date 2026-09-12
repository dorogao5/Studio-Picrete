import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.llm import client, essential_tools as et
from app.models import Assistant, ModelEntry, Provider
from app.schemas import AssistantCreate


def mock_dialogue(monkeypatch, *, arguments='{"expression":"sqrt(9)+exp(0)"}', name="calculator",
                  gateway_error=False, http_error=False, missing_finish=False, repair=False):
    requests = []
    model_calls = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append((str(request.url), payload, dict(request.headers)))
        if request.url.path == "/invoke":
            if http_error:
                return httpx.Response(503)
            return httpx.Response(200, json={"tool_name": name, "arguments": payload["arguments"],
                "tool_version": "v1", "trace_id": "trace", "normalized_result": {"value": "4"},
                "status": "error" if gateway_error and len(model_calls) == 1 else "success"})
        model_calls.append(payload)
        call_round = len(model_calls) == 1 or (repair and len(model_calls) == 2)
        delta = {"content": '{"value":4}'}
        if call_round:
            delta = {"reasoning_content": "private-reasoning", "tool_calls": [{"index": 0, "id": f"call{len(model_calls)}",
                "type": "function", "function": {"name": name, "arguments": arguments if len(model_calls) == 1
                                                  else '{"expression":"sqrt(9)+exp(0)"}'}}]}
        event = {"choices": [{"delta": delta, "finish_reason": None if missing_finish else
                              ("tool_calls" if call_round else "stop")}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")
    real = httpx.AsyncClient
    monkeypatch.setattr(et.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(et, "decrypt_secret", lambda _: "MODEL-SECRET")
    monkeypatch.setattr(et, "get_settings", lambda: Settings(_env_file=None,
        essential_tools_gateway_url="https://gateway.invalid", essential_tools_gateway_token="GATEWAY-SECRET" * 3))
    return requests, model_calls


def run(**kwargs):
    return asyncio.run(client.chat(Provider(base_url="https://model.invalid", extra_headers={}),
        ModelEntry(model_id="deepseek", family="deepseek", supports_json=True), "system", "user",
        essential_tools=True, json_mode=True, response_schema={"type": "object"}, **kwargs))


def test_tool_result_final_usage_auth_reasoning(monkeypatch):
    requests, calls = mock_dialogue(monkeypatch)
    result = run(max_tokens=12000, reasoning_effort="high")
    assert len(calls) == 2 and len(requests) == 3
    assert result.text == '{"value":4}' and result.tokens_total == 30
    assert calls[1]["messages"][2]["reasoning_content"] == "private-reasoning"
    assert calls[1]["messages"][3]["role"] == "tool"
    assert calls[0]["max_tokens"] == 12000 and calls[0]["reasoning_effort"] == "high"
    assert calls[1]["response_format"]["type"] == "json_schema"
    assert requests[0][2]["authorization"] == "Bearer MODEL-SECRET"
    assert requests[1][2]["authorization"] == "Bearer " + "GATEWAY-SECRET" * 3
    assert "private-reasoning" not in json.dumps(result.raw)
    assert "SECRET" not in json.dumps(result.raw)
    assert result.raw["tool_traces"][0]["trace_id"] == "trace"


@pytest.mark.parametrize("options,expected", [
    ({"name": "shell"}, 1), ({"http_error": True}, 2), ({"missing_finish": True}, 1),
])
def test_failures_never_retry_or_call_unknown_gateway(monkeypatch, options, expected):
    requests, calls = mock_dialogue(monkeypatch, **options)
    with pytest.raises(client.LlmError):
        run()
    assert len(requests) == expected and len(calls) == 1


@pytest.mark.parametrize("options", [{"arguments": "bad-json", "repair": True},
                                    {"gateway_error": True, "repair": True}])
def test_tool_errors_continue_same_dialogue(monkeypatch, options):
    requests, calls = mock_dialogue(monkeypatch, **options)
    result = run()
    assert len(calls) == 3 and result.text == '{"value":4}'
    assert result.raw["tool_traces"][0]["status"] in {"error", "invalid_request"}
    assert result.raw["tool_traces"][1]["status"] == "success"
    assert calls[1]["messages"][0] == calls[0]["messages"][0]
    assert len([r for r in requests if r[0].endswith("/invoke")]) == (1 if "arguments" in options else 2)


def test_budgets_defaults_metadata_and_flags():
    settings = Settings(_env_file=None)
    assert settings.essential_tools_max_rounds == 8 and settings.essential_tools_max_calls == 24
    assistant = AssistantCreate(name="test", discipline="test")
    assert all(getattr(assistant, f"{role}_tools_enabled") is False for role in ("generator", "verifier", "tutor", "decision"))
    assert "universal_gas_constant_si" in et.DEFINITIONS["reference_db"]["parameters"]["properties"]["reference_id"]["description"]


def test_flags_database_defaults():
    from test_physical_pipeline import setup_db
    async def check():
        engine, sessions = await setup_db()
        async with sessions() as db:
            assistant = Assistant(name="test", discipline="test")
            db.add(assistant)
            await db.commit()
            await db.refresh(assistant)
            assert assistant.generator_tools_enabled is False and assistant.verifier_tools_enabled is False
            assert assistant.tutor_tools_enabled is False and assistant.decision_tools_enabled is False
        await engine.dispose()
    asyncio.run(check())


def test_total_deadline_includes_gateway(monkeypatch):
    mock_dialogue(monkeypatch)
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(et, "completion", slow)
    with pytest.raises(client.LlmError, match="Запрос остановлен"):
        run(timeout=0.01)


@pytest.mark.parametrize("prefix", ["", "STUDIO_"])
def test_gateway_environment_aliases(monkeypatch, prefix):
    values = {"GATEWAY_URL": "http://gateway:8080", "GATEWAY_TOKEN": "x" * 32,
              "MAX_ROUNDS": "7", "MAX_CALLS": "23", "TIMEOUT": "9"}
    for suffix, value in values.items():
        monkeypatch.delenv("ESSENTIAL_TOOLS_" + suffix, raising=False)
        monkeypatch.delenv("STUDIO_ESSENTIAL_TOOLS_" + suffix, raising=False)
        monkeypatch.setenv(prefix + "ESSENTIAL_TOOLS_" + suffix, value)
    settings = Settings(_env_file=None)
    assert settings.essential_tools_gateway_url == "http://gateway:8080"
    assert settings.essential_tools_gateway_token == "x" * 32
    assert settings.essential_tools_max_rounds == 7 and settings.essential_tools_max_calls == 23
    assert settings.essential_tools_timeout == 9


def test_default_gateway_timeout():
    assert Settings(_env_file=None).essential_tools_timeout == 10


def test_definitions_match_canonical_vendored_metadata():
    canonical = Path(__file__).resolve().parents[2] / "essential_skills/tool-definitions.json"
    assert et.DEFINITIONS == json.loads(canonical.read_text())
    assert "enum" not in et.DEFINITIONS["reference_db"]["parameters"]["properties"]["reference_id"]


def test_nonphysical_generator_opt_in_does_not_get_physical_schema(monkeypatch):
    from app.services import taskgen
    calls = []
    async def chat(*args, **kwargs):
        calls.append(kwargs)
        return client.LlmResult(text='{"tasks":[{"statement":"2+2","answer":"4"}]}', duration_ms=1)
    monkeypatch.setattr(taskgen.llm, "chat", chat)
    assistant = Assistant(name="Math", discipline="Math", topics=[], nuances=[], criteria=[], generator_tools_enabled=True)
    asyncio.run(taskgen.generate_tasks(Provider(kind="custom"), ModelEntry(), assistant, "configured",
                                      topic="test", difficulty="easy", count=1))
    assert calls[0]["essential_tools"] is True and "response_schema" not in calls[0]


def test_generic_solver_tools_opt_in(monkeypatch):
    from app.services.validation import solver_check
    calls = []
    async def chat(*args, **kwargs):
        calls.append(kwargs)
        return client.LlmResult(text='{"solution":"calculated","answer":"4"}', duration_ms=1,
                               raw={"tool_traces": [{"trace_id": "proof"}]})
    monkeypatch.setattr(client, "chat", chat)
    solved = asyncio.run(solver_check(Provider(kind="custom"), ModelEntry(), "2+2", "", "numeric", essential_tools=True))
    assert calls[0]["essential_tools"] is True
    assert solved["calculation_audit"]["tool_traces"][0]["trace_id"] == "proof"


def test_generator_and_verifier_audit_same_candidate(monkeypatch):
    from app.services import taskgen, physical_chemistry as pc
    from app.services.contracts import PHYSICAL_GENERATION_JSON_EXAMPLE
    from test_physical_pipeline import task
    calls = []
    async def chat(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return client.LlmResult(text=PHYSICAL_GENERATION_JSON_EXAMPLE, duration_ms=1,
                                   raw={"transport": "essential_tools", "tool_traces": [{"trace_id": "proof"}]})
        raise client.LlmError("gateway failed", raw={"transport": "essential_tools", "tool_traces": []})
    monkeypatch.setattr(taskgen.llm, "chat", chat)
    assistant = Assistant(name="Физическая химия", discipline="Физическая химия", topics=[], nuances=[],
                          criteria=[], generator_tools_enabled=True)
    items = asyncio.run(taskgen.generate_tasks(Provider(kind="yandex"), ModelEntry(), assistant,
                        "configured", topic="test", difficulty="easy", count=1))
    stored = taskgen.task_from_item(items[0], assistant_id="a", template_id=None, batch_id=None,
        topic="test", difficulty="easy", model_used="qwen", grounding_meta={},
        validation_contract={"chemistry_check": "off"})
    assert stored.grounding["calculation_audit"]["tool_traces"][0]["trace_id"] == "proof"
    candidate = task()
    validation, correction = asyncio.run(pc.run_physical_validation(task=candidate,
        provider=Provider(kind="yandex"), model=ModelEntry(model_id="deepseek-v4-pro", family="deepseek"),
        grounding="", discipline_context="", essential_tools=True))
    assert validation["verdict"] == "needs_review" and correction is None and candidate.id == "task"
    assert validation["calculation_audit"]["transport"] == "essential_tools"
    assert all(call["essential_tools"] for call in calls)


def test_fragmented_tool_call_stream():
    events = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call", "function": {
            "name": "calculator", "arguments": '{"expression":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
            "arguments": '"1+1"}'}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"total_tokens": 9}},
    ]
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
            text="".join("data: " + json.dumps(e) + "\n\n" for e in events)))) as c:
            message, usage = await et.completion(c, "http://model.invalid", {}, {})
            assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"expression": "1+1"}
            assert usage["total_tokens"] == 9
    asyncio.run(check())


@pytest.mark.parametrize("location,field", [("choice", "refusal"), ("choice", "error"),
                                          ("delta", "refusal"), ("delta", "error")])
def test_refusal_or_error_rejects_even_with_final_content(location, field):
    choice = {"delta": {"content": '{"value":4}'}, "finish_reason": "stop"}
    (choice if location == "choice" else choice["delta"])[field] = "provider rejection"
    event = {"choices": [choice]}
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
                200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"))) as c:
            with pytest.raises(client.LlmError, match="refused or returned an error"):
                await et.completion(c, "http://model.invalid", {}, {})
    asyncio.run(check())


def test_budget_forces_final_without_fabricated_proof(monkeypatch):
    requests, calls = mock_dialogue(monkeypatch, repair=True)
    monkeypatch.setattr(et, "get_settings", lambda: Settings(_env_file=None,
        essential_tools_gateway_url="https://gateway.invalid", essential_tools_gateway_token="x" * 32,
        essential_tools_max_rounds=1))
    with pytest.raises(client.LlmError, match="budget exhausted"):
        run()
    assert len(calls) == 2 and calls[1]["tool_choice"] == "none"
    assert "Do not claim unperformed" in calls[1]["messages"][0]["content"]
    assert all(message["role"] != "system" for message in calls[1]["messages"][1:])
    assert len(requests) == 3  # no second gateway execution, no replacement model call


def test_http_error_message_sanitizes_credentials():
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
                400, json={"error": {"message": "bad MODEL-SECRET parameter", "debug": "DO_NOT_PRINT"}}))) as c:
            with pytest.raises(client.LlmError) as caught:
                await et.completion(c, "http://model.invalid", {"Authorization": "Bearer MODEL-SECRET"}, {})
            assert "bad [REDACTED] parameter" in str(caught.value)
            assert "MODEL-SECRET" not in str(caught.value) and "DO_NOT_PRINT" not in str(caught.value)
    asyncio.run(check())
