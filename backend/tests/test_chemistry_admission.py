import asyncio

from app.models import ModelEntry, Provider
from app.services import validation
from app.services.chemistry_facts import chemistry_admission_evidence
from app.services.model_policy import ModelUsePolicy


def _policy() -> ModelUsePolicy:
    return ModelUsePolicy(
        version="test-chemistry-policy",
        decision_model_ids=frozenset({"deepseek-v4-pro"}),
        advisory_model_ids=frozenset(),
    )


def _model() -> ModelEntry:
    return ModelEntry(
        id="model-1",
        provider_id="provider-1",
        model_id="deepseek-v4-pro",
        family="deepseek",
        supports_json=True,
    )


def _provider() -> Provider:
    return Provider(id="provider-1", name="DeepSeek", base_url="https://example.test")


def _dilution_facts(final_volume: str = "100 mL") -> dict:
    return {
        "dilution": {
            "c1": "0.100 mol/L",
            "v1": "10.0 mL",
            "c2": "0.0100 mol/L",
            "v2": final_volume,
        }
    }


def _validation_kwargs() -> dict:
    return {
        "statement": (
            "10.0 mL раствора с c1 = 0.100 mol/L разбавили до V2 = 100 mL. "
            "Определите конечную концентрацию."
        ),
        "reference_solution": (
            "Используем c1V1=c2V2: c2 = 0.100 mol/L · 10.0 mL / 100 mL = 0.0100 mol/L."
        ),
        "reference_answer": "c2 = 0.0100 mol/L",
        "rubric": [{"criterion_name": "Материальный баланс", "max_score": 10}],
        "max_score": 10,
        "answer_format": "numeric",
        "tolerance_pct": 2,
        "grounding": "",
        "sheets_text": "",
        "existing_statements": [],
        "data_used": [],
        "discipline_context": "Дисциплина: Аналитическая химия",
        "topic": "Приготовление стандартных растворов",
        "validation_config": {
            "answer_format": "numeric",
            "tolerance_pct": 2,
            "validation_solver": True,
            "validation_data_check": True,
            "task_kind": "calculation",
            "chemistry_check": "chemistry.dilution",
        },
    }


def test_required_deterministic_check_passes_only_for_consistent_facts() -> None:
    passed = chemistry_admission_evidence(
        discipline="Аналитическая химия",
        statement="Разбавление стандартного раствора",
        reference_solution="",
        answer="",
        topic="Разбавление",
        facts=_dilution_facts(),
        facts_source="test",
        chemistry_check="chemistry.dilution",
    )
    failed = chemistry_admission_evidence(
        discipline="Аналитическая химия",
        statement="Разбавление стандартного раствора",
        reference_solution="",
        answer="",
        topic="Разбавление",
        facts=_dilution_facts("50 mL"),
        facts_source="test",
        chemistry_check="chemistry.dilution",
    )

    assert passed["admission_effect"] == "pass"
    assert passed["required_not_passed"] == []
    assert failed["admission_effect"] == "block"
    assert failed["blocking_codes"] == ["chemistry.dilution"]


def test_auto_contract_cannot_promote_an_irrelevant_fact_block_to_core_evidence() -> None:
    evidence = chemistry_admission_evidence(
        discipline="Аналитическая химия",
        statement="Рассчитайте массовую долю осадка по приведённым данным.",
        reference_solution="Расчёт массовой доли выполнен по условию.",
        answer="12.0 %",
        topic="Гравиметрия",
        facts=_dilution_facts(),
        facts_source="generator",
        chemistry_check="auto",
    )

    assert evidence["required_check_ids"] == ["chemistry.dilution"]
    assert evidence["required_not_passed"] == []
    assert evidence["admission_effect"] == "limited"


def test_deterministic_failure_stops_expensive_semantic_agents(monkeypatch) -> None:
    solver_calls = 0

    async def fake_solver(*_args, **_kwargs):
        nonlocal solver_calls
        solver_calls += 1
        raise AssertionError("solver must not run after deterministic failure")

    monkeypatch.setattr(validation, "current_model_use_policy", _policy)
    monkeypatch.setattr(validation, "solver_check", fake_solver)
    result = asyncio.run(
        validation.run_validation(
            **_validation_kwargs(),
            chemistry_facts=_dilution_facts("50 mL"),
            chemistry_facts_source="generator",
            solver_provider=_provider(),
            solver_model=_model(),
        )
    )

    assert result["verdict"] == "needs_review"
    assert result["chemistry"]["admission_effect"] == "block"
    assert solver_calls == 0
    assert any("материальный баланс" in reason.casefold() for reason in result["reasons"])


def test_legacy_task_rebuilds_facts_before_admission(monkeypatch) -> None:
    extraction_calls = 0

    async def fake_extractor(*_args, **_kwargs):
        nonlocal extraction_calls
        extraction_calls += 1
        return {"status": "ok", "facts": _dilution_facts(), "model": "DeepSeek/deepseek-v4-pro"}

    async def fake_solver(*_args, **_kwargs):
        return {
            "status": "ok",
            "solution": "c2 = c1V1/V2 = 0.0100 mol/L",
            "answer": "c2 = 0.0100 mol/L",
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

    monkeypatch.setattr(validation, "current_model_use_policy", _policy)
    monkeypatch.setattr(validation, "extract_chemistry_facts", fake_extractor)
    monkeypatch.setattr(validation, "solver_check", fake_solver)
    monkeypatch.setattr(validation, "critic_check", fake_critic)
    result = asyncio.run(
        validation.run_validation(
            **_validation_kwargs(),
            solver_provider=_provider(),
            solver_model=_model(),
            extract_chemistry_facts_if_missing=True,
        )
    )

    assert result["verdict"] == "validated"
    assert result["chemistry"]["facts_source"] == "deepseek_extractor"
    assert result["chemistry"]["admission_effect"] == "pass"
    assert extraction_calls == 1
