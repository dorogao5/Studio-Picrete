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


def test_preview_still_rejects_model_outside_explicit_allowlists() -> None:
    with pytest.raises(ModelUsePolicyError, match="отсутствует в allowlist"):
        require_decision_model(_model("qwen3.7-max"), allow_advisory=True, policy=_policy())


def _validation_kwargs() -> dict:
    return {
        "statement": "Определите искомую массу продукта по приведённым в условии данным.",
        "reference_solution": "По приведённым данным получаем итоговый результат m = 5 г.",
        "reference_answer": "m = 5 г",
        "rubric": [{"criterion_name": "Расчёт", "max_score": 10}],
        "max_score": 10,
        # These tests isolate model-role policy. Numeric chemistry admission is
        # covered separately by test_chemistry_admission.py.
        "answer_format": "text",
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


def test_decision_solver_requires_two_matching_passes_and_critic(monkeypatch) -> None:
    policy = _policy()
    calls: list[str] = []
    critic_calls: list[str] = []

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

    async def fake_critic(_provider, model, **_kwargs):
        critic_calls.append(model.model_id)
        return {
            "status": "pass",
            "checks": {
                "statement_self_contained": True,
                "reference_consistent": True,
                "solver_matches_reference": True,
                "verifier_matches_reference": True,
                "solver_agreement": True,
                "structured_facts_grounded": True,
                "units_and_chemistry_consistent": True,
            },
            "issues": [],
        }

    monkeypatch.setattr(validation, "current_model_use_policy", lambda: policy)
    monkeypatch.setattr(validation, "solver_check", fake_solver)
    monkeypatch.setattr(validation, "critic_check", fake_critic)
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
    assert result["critic"]["status"] == "pass"
    assert calls == ["deepseek-v4-pro", "deepseek-v4-pro"]
    assert critic_calls == ["deepseek-v4-pro"]


def test_numeric_critic_cannot_override_proven_tolerance_match() -> None:
    critic = {
        "status": "fail",
        "checks": {
            "reference_consistent": True,
            "solver_agreement": False,
            "solver_matches_reference": False,
            "statement_self_contained": True,
            "structured_facts_grounded": True,
            "units_and_chemistry_consistent": True,
            "verifier_matches_reference": True,
        },
        "issues": ["Solver outputs 146.3 м²/г instead of 146 м²/г, breaching significant figure rules."],
    }

    reconciled = validation._apply_deterministic_numeric_critic_evidence(
        critic,
        answer_format="numeric",
        solver={"status": "match", "answer": "146.3 м²/г"},
        verifier={"status": "match", "answer": "146 м²/г"},
        cross_comparison={"verdict": "match"},
    )

    assert reconciled["status"] == "pass"
    assert reconciled["issues"] == []
    assert reconciled["overridden_issues"] == critic["issues"]
    assert reconciled["deterministic_overrides"] == ["solver_agreement", "solver_matches_reference"]
    assert reconciled["basis"] == "deterministic_numeric_tolerance"


def test_numeric_critic_still_blocks_independent_chemistry_failure() -> None:
    critic = {
        "status": "fail",
        "checks": {key: True for key in validation.CRITIC_REQUIRED_CHECKS},
        "issues": ["Нарушен атомный баланс"],
    }
    critic["checks"]["solver_agreement"] = False
    critic["checks"]["units_and_chemistry_consistent"] = False

    reconciled = validation._apply_deterministic_numeric_critic_evidence(
        critic,
        answer_format="numeric",
        solver={"status": "match"},
        verifier={"status": "match"},
        cross_comparison={"verdict": "match"},
    )

    assert reconciled == critic


def test_decision_solver_is_not_green_when_critic_finds_a_problem(monkeypatch) -> None:
    policy = _policy()

    async def fake_solver(*_args, **_kwargs):
        return {
            "status": "ok",
            "solution": "Одинаковое, но ошибочное решение",
            "answer": "m = 5 г",
            "error": "",
            "duration_ms": 1,
            "tokens_total": 10,
        }

    async def fake_critic(*_args, **_kwargs):
        return {
            "status": "fail",
            "checks": {
                "statement_self_contained": True,
                "reference_consistent": False,
                "solver_matches_reference": True,
                "verifier_matches_reference": True,
                "solver_agreement": True,
                "structured_facts_grounded": True,
                "units_and_chemistry_consistent": False,
            },
            "issues": ["В эталоне нарушен материальный баланс"],
        }

    monkeypatch.setattr(validation, "current_model_use_policy", lambda: policy)
    monkeypatch.setattr(validation, "solver_check", fake_solver)
    monkeypatch.setattr(validation, "critic_check", fake_critic)
    provider = Provider(id="provider-1", name="DeepSeek", base_url="https://example.test")

    result = asyncio.run(
        validation.run_validation(
            **_validation_kwargs(),
            solver_provider=provider,
            solver_model=_model("deepseek-v4-pro"),
        )
    )

    assert result["verdict"] == "needs_review"
    assert any("материальный баланс" in reason for reason in result["reasons"])


def test_text_paraphrases_can_be_admitted_only_by_full_semantic_critic(monkeypatch) -> None:
    policy = _policy()
    answers = iter(
        [
            (
                "Рост концентрации электролита усиливает экранирование зарядов и сжимает диффузную часть ДЭС.",
                "При росте ионной силы экранирование усиливается, поэтому двойной электрический слой становится тоньше.",
            ),
            (
                "Из определения длины Дебая следует её обратная зависимость от квадратного корня ионной силы.",
                "Большая ионная сила означает меньшую дебаевскую длину и более сильное экранирование.",
            ),
        ]
    )

    async def fake_solver(*_args, **_kwargs):
        solution, answer = next(answers)
        return {
            "status": "ok",
            "solution": solution,
            "answer": answer,
            "error": "",
            "duration_ms": 1,
            "tokens_total": 10,
        }

    async def fake_critic(*_args, **_kwargs):
        return {
            "status": "pass",
            "checks": {
                "statement_self_contained": True,
                "reference_consistent": True,
                "solver_matches_reference": True,
                "verifier_matches_reference": True,
                "solver_agreement": True,
                "structured_facts_grounded": True,
                "units_and_chemistry_consistent": True,
            },
            "issues": [],
        }

    monkeypatch.setattr(validation, "current_model_use_policy", lambda: policy)
    monkeypatch.setattr(validation, "solver_check", fake_solver)
    monkeypatch.setattr(validation, "critic_check", fake_critic)
    provider = Provider(id="provider-1", name="DeepSeek", base_url="https://example.test")
    kwargs = {
        **_validation_kwargs(),
        "statement": (
            "Объясните, как увеличение ионной силы раствора влияет на длину Дебая "
            "и экранирование заряда в рамках модели двойного электрического слоя."
        ),
        "reference_solution": (
            "Дебаевская длина обратно пропорциональна квадратному корню из ионной силы. "
            "Поэтому добавление электролита усиливает экранирование и сжимает диффузный слой."
        ),
        "reference_answer": (
            "Увеличение ионной силы уменьшает длину Дебая, усиливает экранирование "
            "и сжимает двойной электрический слой."
        ),
    }

    result = asyncio.run(
        validation.run_validation(
            **kwargs,
            solver_provider=provider,
            solver_model=_model("deepseek-v4-pro"),
        )
    )

    assert result["verdict"] == "validated"
    assert result["reasons"] == []
    for field in ("solver", "verifier"):
        assert result[field]["status"] == "match"
        assert result[field]["comparison"]["previous_verdict"] == "uncertain"
        assert result[field]["comparison"]["basis"] == "subject_critic_semantic_entailment"
    assert result["cross_comparison"]["verdict"] == "match"
    assert result["cross_comparison"]["previous_verdict"] == "uncertain"
    assert result["reference_solution_check"]["basis"] == "subject_critic_semantic_entailment"


def test_text_paraphrases_stay_blocked_when_critic_omits_reference_entailment(monkeypatch) -> None:
    policy = _policy()

    async def fake_solver(*_args, **_kwargs):
        return {
            "status": "ok",
            "solution": "Объяснение через усиление экранирования и уменьшение толщины диффузного слоя.",
            "answer": "Электролит усиливает экранирование, поэтому диффузный слой сжимается.",
            "error": "",
            "duration_ms": 1,
            "tokens_total": 10,
        }

    async def incomplete_critic(*_args, **_kwargs):
        return {
            "status": "pass",
            "checks": {
                "statement_self_contained": True,
                "reference_consistent": True,
                "solver_matches_reference": True,
                # verifier_matches_reference is deliberately absent.
                "solver_agreement": True,
                "structured_facts_grounded": True,
                "units_and_chemistry_consistent": True,
            },
            "issues": [],
        }

    monkeypatch.setattr(validation, "current_model_use_policy", lambda: policy)
    monkeypatch.setattr(validation, "solver_check", fake_solver)
    monkeypatch.setattr(validation, "critic_check", incomplete_critic)
    provider = Provider(id="provider-1", name="DeepSeek", base_url="https://example.test")
    kwargs = {
        **_validation_kwargs(),
        "statement": (
            "Объясните, как увеличение ионной силы раствора влияет на длину Дебая "
            "и экранирование заряда в двойном электрическом слое."
        ),
        "reference_solution": "Рост ионной силы уменьшает длину Дебая и усиливает экранирование заряда.",
        "reference_answer": "Длина Дебая уменьшается, а экранирование усиливается.",
    }

    result = asyncio.run(
        validation.run_validation(
            **kwargs,
            solver_provider=provider,
            solver_model=_model("deepseek-v4-pro"),
        )
    )

    assert result["verdict"] == "needs_review"
    assert result["solver"]["status"] == "uncertain"
    assert result["cross_comparison"]["verdict"] == "match"


def test_formula_representation_only_incomplete_can_reach_critic_after_chemistry_pass() -> None:
    solver = {
        "status": "incomplete",
        "answer": "Cr2O7^2- + 14H+ + 6e- -> 2Cr^3+ + 7H2O; n=0.0120 mol",
        "solution": "Полное решение",
        "comparison": {
            "verdict": "incomplete",
            "matched_count": 1,
            "required_count": 1,
            "missing_reference_groups": [],
            "unexpected_solver_numbers": [14, 6, 7],
            "missing_text_claims": [],
        },
    }
    verifier = {
        **solver,
        "comparison": {
            "verdict": "match",
            "matched_count": 1,
            "required_count": 1,
            "missing_reference_groups": [],
            "unexpected_solver_numbers": [],
            "missing_text_claims": [],
        },
    }
    cross = {
        "verdict": "incomplete",
        "matched_count": 5,
        "required_count": 7,
        "missing_reference_groups": [[7], [7]],
        "unexpected_solver_numbers": [7, 7],
        "missing_text_claims": [],
    }

    assert validation._semantic_entailment_candidate(
        "text", solver, verifier, cross, chemistry_verified=True
    )
    assert not validation._semantic_entailment_candidate(
        "text", solver, verifier, cross, chemistry_verified=False
    )


def test_two_reference_matching_answers_can_send_lexical_disagreement_to_critic() -> None:
    matched = {
        "status": "match",
        "answer": "n1=n2=0.0120 моль; количество вещества сохраняется",
        "solution": "Полное решение",
        "comparison": {"verdict": "match"},
    }
    cross = {
        "verdict": "incomplete",
        "matched_count": 2,
        "required_count": 2,
        "missing_reference_groups": [],
        "unexpected_solver_numbers": [],
        "missing_text_claims": ["количество вещества сохраняется"],
    }

    assert validation._semantic_entailment_candidate(
        "text", matched, matched, cross, chemistry_verified=True
    )
    assert not validation._semantic_entailment_candidate(
        "text", matched, matched, cross, chemistry_verified=False
    )


def test_semantic_critic_never_receives_a_genuinely_missing_formula_result() -> None:
    report = {
        "status": "incomplete",
        "answer": "Только часть ответа",
        "solution": "Неполное решение",
        "comparison": {
            "verdict": "incomplete",
            "matched_count": 1,
            "required_count": 2,
            "missing_reference_groups": [[0.012]],
            "unexpected_solver_numbers": [],
            "missing_text_claims": [],
        },
    }

    assert not validation._semantic_entailment_candidate(
        "text",
        report,
        report,
        report["comparison"],
        chemistry_verified=True,
    )


def test_full_solution_evidence_can_reach_critic_when_compact_answer_omits_a_subpart() -> None:
    answer_comparison = {
        "verdict": "incomplete",
        "matched_count": 4,
        "required_count": 6,
        "missing_reference_groups": [[8], [5]],
        "unexpected_solver_numbers": [],
        "missing_text_claims": ["Полуреакции"],
    }
    report = {
        "status": "incomplete",
        "answer": "Ионное уравнение и итоговое количество продукта",
        "solution": "Полное решение с обеими полуреакциями, балансом и итогом",
        "comparison": answer_comparison,
        "solution_comparison": {
            "verdict": "match",
            "matched_count": 6,
            "required_count": 6,
            "missing_reference_groups": [],
            "unexpected_solver_numbers": [0.025, 0.00525],
            "missing_text_claims": [],
            "extra_numbers_allowed": True,
        },
        "answer_solution_comparison": {
            "verdict": "match",
            "missing_reference_groups": [],
            "missing_text_claims": [],
            "extra_numbers_allowed": True,
        },
    }
    complete_report = {
        **report,
        "status": "match",
        "comparison": {**answer_comparison, "verdict": "match"},
    }
    cross = {
        "verdict": "incomplete",
        "matched_count": 4,
        "required_count": 6,
        "missing_reference_groups": [[8], [5]],
        "unexpected_solver_numbers": [],
        "missing_text_claims": ["Полуреакции"],
    }

    assert validation._solution_backed_entailment_candidate(
        "text", report, complete_report, cross, chemistry_verified=True
    )
    assert not validation._solution_backed_entailment_candidate(
        "text", report, complete_report, cross, chemistry_verified=False
    )
    assert not validation._solution_backed_entailment_candidate(
        "text", report, report, cross, chemistry_verified=True
    )
    contradictory_answer = {
        **report,
        "answer_solution_comparison": {
            **report["answer_solution_comparison"],
            "verdict": "mismatch",
        },
    }
    assert not validation._solution_backed_entailment_candidate(
        "text", contradictory_answer, complete_report, cross, chemistry_verified=True
    )


def test_incomplete_answer_stays_blocked_when_full_solution_also_lacks_evidence() -> None:
    comparison = {
        "verdict": "incomplete",
        "matched_count": 1,
        "required_count": 2,
        "missing_reference_groups": [[0.012]],
        "unexpected_solver_numbers": [],
        "missing_text_claims": [],
    }
    report = {
        "status": "incomplete",
        "answer": "Только часть ответа",
        "solution": "Решение тоже не содержит итог",
        "comparison": comparison,
        "solution_comparison": {**comparison},
        "answer_solution_comparison": {**comparison},
    }

    assert not validation._solution_backed_entailment_candidate(
        "text", report, report, comparison, chemistry_verified=True
    )


def test_full_solution_fallback_still_requires_passing_subject_critic(monkeypatch) -> None:
    policy = _policy()
    critic_calls: list[bool] = []
    solver_calls = 0

    async def compact_solver(*_args, **_kwargs):
        nonlocal solver_calls
        solver_calls += 1
        solution = (
            "Полуреакция содержит 8 электронов; коэффициент второго реагента 5; "
            "итоговое количество n=0.0021 моль."
        )
        return {
            "status": "ok",
            "solution": solution,
            "answer": "n=0.0021 моль" if solver_calls % 2 else solution,
            "error": "",
            "duration_ms": 1,
            "tokens_total": 10,
        }

    async def passing_critic(*_args, **_kwargs):
        critic_calls.append(True)
        return {
            "status": "pass",
            "checks": {key: True for key in validation.CRITIC_REQUIRED_CHECKS},
            "issues": [],
        }

    def deterministic_pass(**_kwargs):
        return {
            "validation_version": "test-v1",
            "discipline": "general_inorganic",
            "deterministic_pass": True,
            "applicable_count": 1,
            "blocking_codes": [],
            "indeterminate_codes": [],
            "warning_codes": [],
            "results": [],
            "required_check_ids": ["chemistry.stoichiometry"],
            "required_not_passed": [],
            "facts_source": "test",
            "admission_effect": "pass",
        }

    monkeypatch.setattr(validation, "current_model_use_policy", lambda: policy)
    monkeypatch.setattr(validation, "solver_check", compact_solver)
    monkeypatch.setattr(validation, "critic_check", passing_critic)
    monkeypatch.setattr(validation, "chemistry_admission_evidence", deterministic_pass)
    provider = Provider(id="provider-1", name="DeepSeek", base_url="https://example.test")
    kwargs = {
        **_validation_kwargs(),
        "statement": "Для данной ОВР приведите полуреакцию, коэффициент и итоговое количество продукта.",
        "reference_solution": (
            "Полуреакция содержит 8 электронов; коэффициент второго реагента 5; "
            "итоговое количество n=0.0021 моль."
        ),
        "reference_answer": "Полуреакция: 8 электронов; коэффициент: 5; n=0.0021 моль.",
        "validation_config": {"task_kind": "calculation", "chemistry_check": "chemistry.stoichiometry"},
        "discipline_context": "Общая и неорганическая химия",
        "chemistry_facts": {},
    }

    result = asyncio.run(
        validation.run_validation(
            **kwargs,
            solver_provider=provider,
            solver_model=_model("deepseek-v4-pro"),
        )
    )

    assert critic_calls == [True]
    assert result["verdict"] == "validated"
    assert result["solver"]["comparison"]["previous_verdict"] == "incomplete"
    assert result["solver"]["solution_comparison"]["verdict"] == "match"
    assert result["critic"]["status"] == "pass"
    assert result["critic"]["basis"] == validation.SOLUTION_BACKED_ENTAILMENT_BASIS

    async def failing_critic(*_args, **_kwargs):
        return {
            "status": "fail",
            "checks": {
                **{key: True for key in validation.CRITIC_REQUIRED_CHECKS},
                "solver_agreement": False,
            },
            "issues": ["В solution присутствует противоречащий итог"],
        }

    monkeypatch.setattr(validation, "critic_check", failing_critic)
    blocked = asyncio.run(
        validation.run_validation(
            **kwargs,
            solver_provider=provider,
            solver_model=_model("deepseek-v4-pro"),
        )
    )

    assert blocked["verdict"] == "needs_review"
    assert blocked["solver"]["status"] == "incomplete"
    assert blocked["critic"]["status"] == "fail"

    late_evidence_calls = 0

    async def late_evidence_solver(*_args, **_kwargs):
        nonlocal late_evidence_calls
        late_evidence_calls += 1
        full = (
            "Полуреакция содержит 8 электронов; коэффициент второго реагента 5; "
            "итоговое количество n=0.0021 моль."
        )
        solution = ("Промежуточный текст без результата. " * 180 + full) if late_evidence_calls == 1 else full
        return {
            "status": "ok",
            "solution": solution,
            "answer": "n=0.0021 моль" if late_evidence_calls == 1 else full,
            "error": "",
            "duration_ms": 1,
            "tokens_total": 10,
        }

    monkeypatch.setattr(validation, "solver_check", late_evidence_solver)
    monkeypatch.setattr(validation, "critic_check", passing_critic)
    truncated = asyncio.run(
        validation.run_validation(
            **kwargs,
            solver_provider=provider,
            solver_model=_model("deepseek-v4-pro"),
        )
    )

    assert truncated["verdict"] == "needs_review"
    assert truncated["solver"]["solution_comparison"]["verdict"] != "match"
    assert len(truncated["solver"]["solution"]) == validation.SOLVER_EVIDENCE_CHAR_LIMIT
    assert critic_calls == [True]
