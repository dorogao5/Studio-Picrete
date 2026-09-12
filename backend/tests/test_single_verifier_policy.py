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
    examples[7]["generation_anchor"] = True
    asyncio.run(taskgen.generate_tasks(Provider(kind="deepseek"),None,a,None,topic="pH",difficulty="medium",count=1,example_tasks=examples,existing_statements=["UNRELATED_PREVIOUS_TASK"]))
    assert calls[0][1]["reasoning_effort"] == "high"
    assert "КОНТРАКТ ТИПОВОГО ВАРИАНТА" in calls[0][0][2]
    msg=calls[0][0][3]
    assert sum(f"Условие: EXAMPLE-{i}\n" in msg for i in range(18)) == 1
    assert "Условие: EXAMPLE-7\n" in msg
    assert "COMPLETE-7" in msg
    assert len(examples)==18
    assert "UNRELATED_PREVIOUS_TASK" not in msg
    assert "НЕ повторяйте их сюжеты" not in msg
    asyncio.run(run_tutor_reply(None,None,"Дай полный ответ","Дай решение",assistant=a))
    assert SOCRATIC_CONTRACT in calls[-1][0][2]


def test_generator_schema_warning_preserves_draft_for_verifier(monkeypatch):
    from app.llm import essential_tools as et
    mock_dialogue(monkeypatch)
    async def completion(*args, **kwargs):
        return {"role":"assistant","content":json.dumps({"tasks":[{"statement":"A real draft"}]})}, {}
    monkeypatch.setattr(et,"completion",completion)
    result=asyncio.run(client.chat(Provider(kind="deepseek",base_url="https://model.invalid",extra_headers={}),
        ModelEntry(model_id="deepseek-flash",family="deepseek",supports_json=True),"JSON","user",
        essential_tools=True,response_schema={"type":"object","properties":{"tasks":{"type":"array",
            "items":{"type":"object","required":["statement","answer"]}}}}))
    assert result.raw["draft_schema_warning"]["disposition"]=="same_candidate_to_independent_verifier"
    assert json.loads(result.text)["tasks"][0]["statement"]=="A real draft"

@pytest.mark.parametrize("missing_core", [False, True])
def test_native_verifier_metadata_repair_never_invents_core(monkeypatch, missing_core):
    from app.llm import essential_tools as et
    from app.services import physical_chemistry as pc
    from test_physical_pipeline import task
    mock_dialogue(monkeypatch)
    original = task()
    verified = pc._task_payload(original)
    for key in ("images", "difficulty", "topic", "data_used", "chemistry_facts"):
        verified.pop(key, None)
    if missing_core:
        verified.pop("reference_solution")
    async def completion(*args, **kwargs):
        return {"role":"assistant", "content":json.dumps({"verdict":"pass","issues":[],
            "verified_task":verified,"verification":{"solution":"checked", "answer":"2"}})}, {}
    monkeypatch.setattr(et,"completion",completion)
    validation, fixed = asyncio.run(pc.run_physical_validation(task=original,
        provider=Provider(kind="deepseek",base_url="https://model.invalid",extra_headers={}),
        model=ModelEntry(model_id="deepseek-flash",family="deepseek",supports_json=True),
        grounding="",discipline_context="",essential_tools=True,generation_policy="single_verifier"))
    if missing_core:
        assert fixed is None and validation["verdict"]=="needs_review"
        assert "unvalidated_final" in validation["calculation_audit"]
    else:
        assert validation["verdict"]=="validated"
        assert fixed["reference_solution"]=="original"
        assert fixed["topic"]==original.topic and fixed["difficulty"]==original.difficulty
        assert validation["calculation_audit"]["verifier_schema_warning"]["disposition"]=="normalize_metadata_then_validate"
    assert validation["calculation_audit"]["model_calls"]==1
