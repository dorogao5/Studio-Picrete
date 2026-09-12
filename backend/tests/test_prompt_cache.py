import json
import os

import pytest

from app.llm.client import aggregate_usage
from app.services.taskgen import build_generation_user_message
from app.services.contracts import PHYSICAL_GENERATION_JSON_EXAMPLE, JSON_LATEX_ESCAPING_NOTE
from app.llm import essential_tools as et
from test_essential_tools import mock_dialogue, run


def test_generation_contract_precedes_changing_inputs_without_losing_data():
    first = build_generation_user_message(topic="Термодинамика", difficulty="easy", count=1,
        grounding="REFERENCE-A", instructions="TEACHER-A", rubric=[{"criterion_name": "RULE-A"}],
        existing_statements=["EXISTING-A"], chemistry_check="off", reuse_blueprint=True)
    second = build_generation_user_message(topic="Кинетика", difficulty="hard", count=2,
        grounding="REFERENCE-B", chemistry_check="off", reuse_blueprint=True)
    prefix = os.path.commonprefix([first, second])
    assert PHYSICAL_GENERATION_JSON_EXAMPLE in prefix
    assert JSON_LATEX_ESCAPING_NOTE in prefix
    for value in ("Термодинамика", "easy", "REFERENCE-A", "TEACHER-A", "RULE-A", "EXISTING-A"):
        assert value in first[len(prefix):]
    assert first.count(PHYSICAL_GENERATION_JSON_EXAMPLE) == 1
    assert "Кинетика" in second and "hard" in second


@pytest.mark.parametrize("value", [None, -1, True, "12", 1.5])
def test_unknown_or_invalid_cache_counter_is_not_zero(value):
    calls = [{"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": value}}]
    assert aggregate_usage(calls)["prompt_tokens_details"]["cached_tokens"] is None


def test_usage_sums_each_completion_including_reasoning_and_preserves_unknown():
    calls = [{"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
              "prompt_tokens_details": {"cached_tokens": n},
              "completion_tokens_details": {"reasoning_tokens": 5}} for n in (0, 80)]
    result = aggregate_usage(calls)
    assert result["prompt_tokens"] == 200
    assert result["prompt_tokens_details"]["cached_tokens"] == 80
    assert result["completion_tokens_details"]["reasoning_tokens"] == 10
    assert aggregate_usage(calls[:1])["prompt_tokens_details"]["cached_tokens"] == 0
    calls.append({"prompt_tokens": 30})
    result = aggregate_usage(calls)
    assert result["prompt_tokens"] == 230
    assert result["prompt_tokens_details"]["cached_tokens"] is None
    assert aggregate_usage([])["prompt_tokens"] is None
    assert "reasoning_content" not in json.dumps(result)


def test_tool_flow_reports_cache_and_keeps_existing_history(monkeypatch):
    _, requests = mock_dialogue(monkeypatch)
    original = et.completion

    async def completion(*args):
        message, usage = await original(*args)
        usage["prompt_tokens_details"] = {"cached_tokens": 0 if len(requests) == 1 else 8}
        return message, usage

    monkeypatch.setattr(et, "completion", completion)
    result = run()
    assert result.raw["usage"]["prompt_tokens_details"]["cached_tokens"] == 8
    assert result.tokens_prompt == 20
    assert requests[1]["messages"][:2] == requests[0]["messages"]
    assert requests[1]["messages"][2]["reasoning_content"] == "private-reasoning"
    assert requests[1]["tools"] == requests[0]["tools"]
    assert requests[1]["response_format"] == requests[0]["response_format"]
