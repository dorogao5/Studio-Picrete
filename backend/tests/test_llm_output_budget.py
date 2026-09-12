import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm import client, essential_tools as et
from app.models import ModelEntry, Provider


URI = "gpt://test-folder/example-model"


def test_budget_env_alias_and_exact_keys(monkeypatch):
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS_BY_MODEL", json.dumps({URI: 12345}))
    settings = Settings(_env_file=None)
    assert settings.llm_max_output_tokens_by_model == {URI: 12345}
    assert settings.llm_max_output_tokens_by_model.get("example-model") is None


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "100", 2**64])
def test_budget_rejects_non_positive_or_non_integer_values(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_max_output_tokens_by_model={URI: value})


@pytest.mark.parametrize("raw", ['{"PRIVATE":1,"PRIVATE":2}', '{"PRIVATE":',
    '["PRIVATE"]', '"PRIVATE"', '{"PRIVATE":true}', '{"PRIVATE":1.5}',
    '{"PRIVATE":18446744073709551616}', '{"PRIVATE":0}', '{"PRIVATE":-1}',
    '{"PRIVATE key":1}', '{"":1}'])
def test_invalid_env_map_is_rejected_without_echo(monkeypatch, raw):
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS_BY_MODEL", raw)
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    assert "PRIVATE" not in str(caught.value)


def test_empty_default_and_u64_upper_bound(monkeypatch):
    monkeypatch.delenv("LLM_MAX_OUTPUT_TOKENS_BY_MODEL", raising=False)
    monkeypatch.delenv("STUDIO_LLM_MAX_OUTPUT_TOKENS_BY_MODEL", raising=False)
    assert Settings(_env_file=None).llm_max_output_tokens_by_model == {}
    assert Settings(_env_file=None, llm_max_output_tokens_by_model={URI: 2**64-1}).llm_max_output_tokens_by_model[URI] == 2**64-1


@pytest.mark.parametrize("tools_enabled", [False, True])
@pytest.mark.parametrize("model_id,explicit,expected", [
    (URI, None, 12345), (URI, 23456, 23456), ("example-model", None, None),
])
def test_budget_transport_exact_match_and_explicit_priority(monkeypatch, tools_enabled, model_id, explicit, expected):
    settings = Settings(_env_file=None, llm_max_output_tokens_by_model={URI: 12345},
        essential_tools_gateway_url="https://gateway.invalid", essential_tools_gateway_token="x" * 32)
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    monkeypatch.setattr(et, "get_settings", lambda: settings)
    monkeypatch.setattr(client, "decrypt_secret", lambda _: "secret")
    monkeypatch.setattr(et, "decrypt_secret", lambda _: "secret")
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        event = {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
    real = httpx.AsyncClient
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler)))
    asyncio.run(client.chat(Provider(base_url="https://model.invalid", extra_headers={}),
        ModelEntry(model_id=model_id, family="generic"), "system", "user",
        essential_tools=tools_enabled, max_tokens=explicit))
    assert len(requests) == 1
    assert requests[0].get("max_tokens") == expected
    assert ("max_tokens" in requests[0]) is (expected is not None)


@pytest.mark.parametrize("tools_enabled", [False, True])
def test_length_audit_keeps_usage_without_text_secrets_or_retry(monkeypatch, tools_enabled):
    settings = Settings(_env_file=None, essential_tools_gateway_url="https://gateway.invalid",
        essential_tools_gateway_token="x" * 32)
    monkeypatch.setattr(client, "get_settings", lambda: settings)
    monkeypatch.setattr(et, "get_settings", lambda: settings)
    monkeypatch.setattr(client, "decrypt_secret", lambda _: "SECRET")
    monkeypatch.setattr(et, "decrypt_secret", lambda _: "SECRET")
    requests = []
    def handler(request):
        requests.append(request)
        events = [
            {"choices": [{"delta": {"content": "PRIVATE", "reasoning_content": "PRIVATE"}, "finish_reason": "length"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
                "completion_tokens_details": {"reasoning_tokens": 19, "secret": "SECRET"}, "secret": "SECRET"}},
        ]
        return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n")
    real = httpx.AsyncClient
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler)))
    with pytest.raises(client.LlmError, match="length") as caught:
        asyncio.run(client.chat(Provider(base_url="https://model.invalid", name="provider", extra_headers={}),
            ModelEntry(model_id=URI, family="generic"), "system", "user", essential_tools=tools_enabled))
    audit = caught.value.raw
    failed = audit["failed_completion"] if tools_enabled else audit
    assert failed["finish_reason"] == "length" and failed["usage"]["total_tokens"] == 30
    assert failed["usage"]["completion_tokens_details"] == {"reasoning_tokens": 19}
    assert "SECRET" not in json.dumps(audit) and "PRIVATE" not in json.dumps(audit)
    if tools_enabled:
        assert audit["model_calls"] == 1 and audit["usage_by_call"] == [failed["usage"]]
        assert audit["usage"]["total_tokens"] == 30
    assert len(requests) == 1
