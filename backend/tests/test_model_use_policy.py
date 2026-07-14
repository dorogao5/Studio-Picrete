import asyncio

import pytest

from app.config import Settings
from app.models import ModelEntry, Provider
from app.services import validation
from app.services.model_policy import ModelUsePolicy, ModelUsePolicyError, require_decision_model


def _policy() -> ModelUsePolicy:
    return ModelUsePolicy(
        version="test-policy:fixed",
        decision_model_ids=frozenset({"deepseek-v4-pro"}),
        advisory_model_ids=frozenset({"deepseek-v4-flash"}),
    )


def _model(model_id: str) -> ModelEntry:
    return ModelEntry(
        id=model_id,
        provider_id="provider-1",
        model_id=model_id,
        family="deepseek",
        supports_json=True,
    )


def test_only_explicit_decision_allowlist_can_make_final_decisions() -> None:
    policy = _policy()

    assert policy.classify("DEEPSEEK-V4-PRO").decision_capable is True
    assert policy.classify("deepseek-v4-flash").decision_capable is False
    assert policy.classify("custom-super-pro").decision_capable is False
    assert policy.classify("custom-super-pro").explicitly_configured is False


def test_policy_reads_exact_model_allowlists_from_config() -> None:
    settings = Settings(
        _env_file=None,
        model_use_policy_version="course-policy-v7",
        decision_model_ids="custom-decision, DeepSeek-V4-Pro",
        advisory_model_ids="custom-preview, deepseek-v4-flash",
    )

    policy = ModelUsePolicy.from_settings(settings)

    assert policy.classify("custom-decision").decision_capable is True
    assert policy.classify("custom-preview").tier == "advisory"
    assert policy.classify("custom-decision-fast").decision_capable is False
    assert policy.version.startswith("course-policy-v7:")


def test_advisory_tutor_model_requires_explicit_preview() -> None:
    policy = _policy()

    with pytest.raises(ModelUsePolicyError, match="не разрешена"):
        require_decision_model(_model("deepseek-v4-flash"), policy=policy)

    use = require_decision_model(_model("deepseek-v4-flash"), allow_advisory=True, policy=policy)
    assert use.tier == "advisory"
    assert use.decision_capable is False


def _validation_kwargs() -> dict:
    return {
        "statement": "Определите искомую массу продукта по приведённым в условии данным.",
        "reference_solution": "По приведённым данным получаем итоговый результат m = 5 г.",
        "reference_answer": "m = 5 г",
        "rubric": [{"criterion_name": "Расчёт", "max_score": 10}],
        "max_score": 10,
        "answer_format": "numeric",
        "tolerance_pct": 2,
        "grounding": "",
        "sheets_text": "",
        "existing_statements": [],
        "data_used": [],
    }


def test_validation_without_solver_never_turns_green(monkeypatch) -> None:
    monkeypatch.setattr(validation, "current_model_use_policy", _policy)

    result = asyncio.run(validation.run_validation(**_validation_kwargs(), run_solver=False))

    assert result["verdict"] == "needs_review"
    assert result["model_policy"]["decision_capable"] is False
    assert any("отключена" in reason for reason in result["reasons"])


def test_validation_without_data_check_never_turns_green(monkeypatch) -> None:
    monkeypatch.setattr(validation, "current_model_use_policy", _policy)

    result = asyncio.run(validation.run_validation(**_validation_kwargs(), run_data=False, run_solver=False))

    assert result["verdict"] == "needs_review"
    assert result["data"]["status"] == "skipped"
    assert any("происхождения данных отключена" in reason for reason in result["reasons"])


@pytest.mark.parametrize("model_id", ["deepseek-v4-flash", "unknown-local-model"])
def test_advisory_or_unknown_solver_never_turns_green(monkeypatch, model_id: str) -> None:
    policy = _policy()
    calls: list[str] = []

    async def fake_solver(_provider, model, *_args, **_kwargs):
        calls.append(model.model_id)
        return {
            "status": "ok",
            "solution": "Корректное решение",
            "answer": "m = 5 г",
            "error": "",
            "duration_ms": 1,
            "tokens_total": 10,
        }

    monkeypatch.setattr(validation, "current_model_use_policy", lambda: policy)
    monkeypatch.setattr(validation, "solver_check", fake_solver)
    provider = Provider(id="provider-1", name="DeepSeek", base_url="https://example.test")

    result = asyncio.run(
        validation.run_validation(
            **_validation_kwargs(),
            solver_provider=provider,
            solver_model=_model(model_id),
        )
    )

    assert result["verdict"] == "needs_review"
    assert result["model_policy"]["tier"] == "advisory"
    assert result["verifier"]["status"] == "skipped"
    assert calls == [model_id]


def test_decision_solver_requires_two_matching_passes(monkeypatch) -> None:
    policy = _policy()
    calls: list[str] = []

    async def fake_solver(_provider, model, *_args, **_kwargs):
        calls.append(model.model_id)
        return {
            "status": "ok",
            "solution": "Корректное решение",
            "answer": "m = 5 г",
            "error": "",
            "duration_ms": 1,
            "tokens_total": 10,
        }

    monkeypatch.setattr(validation, "current_model_use_policy", lambda: policy)
    monkeypatch.setattr(validation, "solver_check", fake_solver)
    provider = Provider(id="provider-1", name="DeepSeek", base_url="https://example.test")

    result = asyncio.run(
        validation.run_validation(
            **_validation_kwargs(),
            solver_provider=provider,
            solver_model=_model("deepseek-v4-pro"),
        )
    )

    assert result["verdict"] == "validated"
    assert result["model_policy"]["tier"] == "decision"
    assert result["solver"]["status"] == result["verifier"]["status"] == "match"
    assert calls == ["deepseek-v4-pro", "deepseek-v4-pro"]
