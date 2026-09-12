import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm import client, essential_tools as et
from app.models import ModelEntry, Provider

URI = "gpt://test-folder/test-model"


@pytest.mark.parametrize("alias", ["LLM_SAMPLING_BY_MODEL", "STUDIO_LLM_SAMPLING_BY_MODEL"])
def test_sampling_env_alias(monkeypatch, alias):
    monkeypatch.delenv("LLM_SAMPLING_BY_MODEL", raising=False)
    monkeypatch.delenv("STUDIO_LLM_SAMPLING_BY_MODEL", raising=False)
    monkeypatch.setenv(alias, json.dumps({URI: {"temperature": 0, "top_p": 1, "presence_penalty": -2}}))
    assert Settings(_env_file=None).llm_sampling_by_model == {URI: {"temperature": 0, "top_p": 1, "presence_penalty": -2}}


@pytest.mark.parametrize("raw", [
    '{"PRIVATE":{},"PRIVATE":{}}', '{"PRIVATE":{"temperature":1,"temperature":2}}',
    '{"PRIVATE":{"unknown":1}}', '{"PRIVATE":{"temperature":NaN}}',
    '{"PRIVATE":{"top_p":Infinity}}', '{"PRIVATE":{"temperature":true}}',
    '{"PRIVATE":{"temperature":-1}}', '{"PRIVATE":{"temperature":2.1}}',
    '{"PRIVATE":{"top_p":1.1}}', '{"PRIVATE":{"top_p":-0.1}}',
    '{"PRIVATE":{"presence_penalty":2.1}}', '{"PRIVATE":{"presence_penalty":-2.1}}',
    '{"PRIVATE":{"temperature":"0.5"}}', '{"PRIVATE":{"temperature":null}}',
    '{"PRIVATE":[]}', '["PRIVATE"]', '{"PRIVATE":',
])
def test_sampling_rejects_invalid_without_echo(monkeypatch, raw):
    monkeypatch.setenv("LLM_SAMPLING_BY_MODEL", raw)
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("tools_enabled", [False, True])
@pytest.mark.parametrize("family", ["qwen", "deepseek", "generic"])
@pytest.mark.parametrize("options", [None, {}, {"top_p": 0.8},
    {"temperature": 0.6, "top_p": 0.95, "presence_penalty": 0}])
def test_sampling_operator_overrides_and_continuation(monkeypatch, tools_enabled, family, options):
    mapping = {URI: options} if options is not None else {"test-model": {"temperature": 2}}
    settings = Settings(_env_file=None, llm_sampling_by_model=mapping,
        essential_tools_gateway_url="https://gateway.invalid", essential_tools_gateway_token="x" * 32)
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    monkeypatch.setattr(et, "get_settings", lambda: settings)
    monkeypatch.setattr(client, "decrypt_secret", lambda _: "secret")
    monkeypatch.setattr(et, "decrypt_secret", lambda _: "secret")
    calls = []
    def handler(request):
        if request.url.path == "/invoke":
            return httpx.Response(200, json={"tool_name": "calculator", "tool_version": "test",
                "arguments": {"expression": "1+1"}, "normalized_result": {"value": "2"},
                "status": "success", "trace_id": "test"})
        calls.append(json.loads(request.content))
        tool_call = tools_enabled and len(calls) == 1
        delta = {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
            "function": {"name": "calculator", "arguments": '{"expression":"1+1"}'}}]} if tool_call else {"content": "2"}
        event = {"choices": [{"delta": delta, "finish_reason": "tool_calls" if tool_call else "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
    real = httpx.AsyncClient
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    asyncio.run(client.chat(Provider(base_url="https://model.invalid", extra_headers={}),
        ModelEntry(model_id=URI, family=family), "system", "user", essential_tools=tools_enabled,
        temperature=0.1, thinking="enabled"))
    expected = {} if family == "deepseek" else {"temperature": 0.1}
    expected.update(options or {})
    assert len(calls) == (2 if tools_enabled else 1)
    for payload in calls:
        assert {key: payload[key] for key in ("temperature", "top_p", "presence_penalty") if key in payload} == expected
        if family == "deepseek":
            assert payload["thinking"] == {"type": "enabled"}
