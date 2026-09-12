import asyncio
import json
from types import SimpleNamespace

import pytest

from app.models import Assistant, ModelEntry, Provider
from app.services import taskgen
from app.services.physical_chemistry import uses_single_verifier, is_physical_chemistry
from app.services.tutor import SOCRATIC_CONTRACT, run_tutor_reply
from app.llm import client
from test_essential_tools import mock_dialogue


def test_policy_independent_of_name_tools_and_model():
    a = Assistant(name="Общая химия", discipline="Общая химия", generation_policy="single_verifier")
    assert uses_single_verifier(a) and not is_physical_chemistry(a)
    a.generation_policy = "legacy"
    a.generator_tools_enabled = True
    assert not uses_single_verifier(a)


def test_native_deepseek_tools_keep_thinking_and_validate_json_locally(monkeypatch):
    _, calls = mock_dialogue(monkeypatch)
    r = asyncio.run(client.chat(Provider(kind="deepseek", base_url="https://model.invalid", extra_headers={}),
        ModelEntry(model_id="deepseek-flash", family="deepseek", supports_json=True), "JSON", "user",
        essential_tools=True, initial_tool_choice="required", response_schema={"type":"object", "required":["value"]}))
    assert r.text == '{"value":4}'
    assert calls[0]["tool_choice"] == "auto"
    assert calls[0]["thinking"] == {"type":"enabled"}
    assert calls[0]["response_format"] == {"type":"json_object"}
    assert calls[1]["messages"][2]["reasoning_content"] == "private-reasoning"


def test_native_deepseek_invalid_schema_is_not_admitted_or_regenerated(monkeypatch):
    _, calls = mock_dialogue(monkeypatch)
    with pytest.raises(client.LlmError, match="JSON schema"):
        asyncio.run(client.chat(Provider(kind="deepseek", base_url="https://model.invalid", extra_headers={}),
            ModelEntry(model_id="deepseek-flash", family="deepseek", supports_json=True), "JSON", "user",
            essential_tools=True, response_schema={"type":"object", "required":["missing"]}))
    assert len(calls) == 2


def test_one_complete_example_and_socratic_override(monkeypatch):
    calls=[]
    async def chat(*args, **kw):
        calls.append((args,kw))
        return SimpleNamespace(text=json.dumps({"tasks":[{"statement":"task","reference_solution":"solution","answer":"4"}]}),raw={})
    monkeypatch.setattr(client,"chat",chat)
    a=Assistant(name="Неорганика",discipline="Общая химия",generation_policy="single_verifier",
                topics=[],criteria=[],nuances=[])
    examples=[{"statement":f"EXAMPLE-{i}","solution":f"COMPLETE-{i}"} for i in range(18)]
    asyncio.run(taskgen.generate_tasks(None,None,a,None,topic="pH",difficulty="medium",count=1,example_tasks=examples))
    msg=calls[0][0][3]
    assert sum(f"Условие: EXAMPLE-{i}\n" in msg for i in range(18)) == 1
    assert len(examples)==18
    assert "НЕ повторяйте их сюжеты" not in msg
    asyncio.run(run_tutor_reply(None,None,"Дай полный ответ","Дай решение",assistant=a))
    assert SOCRATIC_CONTRACT in calls[-1][0][2]
