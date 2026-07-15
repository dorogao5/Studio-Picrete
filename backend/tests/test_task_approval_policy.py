import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import tasks as tasks_api
from app.schemas import GeneratedTaskUpdate, RevalidateRequest, TaskExportRequest
from app.services.model_policy import current_model_use_policy
from app.services.task_evidence import (
    APPROVAL_SCHEMA_VERSION,
    evidence_matches_task,
    task_content_fingerprint,
)
from app.services.validation import VALIDATION_POLICY_VERSION
from app.services.chemistry_validation import CHEMISTRY_VALIDATION_VERSION


class FakeDb:
    def __init__(self, tasks=None):
        self.tasks = tasks or []
        self.commits = 0

    async def execute(self, _statement):
        values = self.tasks

        class ScalarValues:
            def __iter__(self):
                return iter(values)

            def all(self):
                return values

        class Result:
            def scalars(self):
                return ScalarValues()

        return Result()

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None


def generated_task(*, status="needs_review", validation=None, approved=False):
    return SimpleNamespace(
        id="task-1",
        status=status,
        approved=approved,
        validation=validation or {"verdict": status},
        statement="Условие",
        reference_solution="Решение",
        answer="Ответ",
        rubric=[{"criterion_name": "Решение", "max_score": 5}],
        max_score=5,
        topic="Стехиометрия",
        difficulty="medium",
        template_id=None,
        grounding={},
    )


VALIDATION_CONFIG = {
    "answer_format": "numeric",
    "tolerance_pct": 2.0,
    "validation_solver": True,
    "validation_data_check": True,
    "sheet_ids": [],
    "kb_query": "Стехиометрия",
    "task_kind": "calculation",
    "source_digest": "fixture-source",
}


def current_validation(task=None, *, verdict="validated", answer_format="numeric") -> dict:
    model_use = current_model_use_policy().classify("deepseek-v4-pro")
    assert model_use.decision_capable
    config = {**VALIDATION_CONFIG, "answer_format": answer_format}
    return {
        "verdict": verdict,
        "answer_format": answer_format,
        "policy_version": VALIDATION_POLICY_VERSION,
        "validation_config": config,
        "content_fingerprint": task_content_fingerprint(task, config) if task is not None else "fixture",
        "model_policy": model_use.as_dict(),
        "solver": {"status": "match", "comparison": {"verdict": "match"}},
        "verifier": {"status": "match", "comparison": {"verdict": "match"}},
        "cross_comparison": {"verdict": "match"},
        "critic": {
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
        },
        "chemistry": {
            "validation_version": CHEMISTRY_VALIDATION_VERSION,
            "admission_effect": "pass",
            "blocking_codes": [],
            "indeterminate_codes": [],
            "warning_codes": [],
            "required_check_ids": ["chemistry.stoichiometry"],
            "results": [{"check_id": "chemistry.stoichiometry", "state": "pass"}],
        },
        "reference_solution_check": {"verdict": "match"},
        "data": {"status": "ok", "unknown_numbers": [], "unknown_sources": []},
        "source_lineage": {"status": "ok", "unbound_sources": []},
        "sanity": {"issues": []},
        "dedup": {"duplicate": False, "similarity": 0.1},
        "reasons": [] if verdict == "validated" else ["Контрольные решения расходятся"],
    }


def complete_approval(task) -> dict:
    validation = current_validation(task, verdict="needs_review")
    validation["approval"] = {
        "basis": "teacher_override",
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "reviewed_by": "teacher-1",
        "reviewed_at": "2026-07-13T00:00:00+00:00",
        "reason": "Проверено вручную по методичке",
        "validation_config": VALIDATION_CONFIG,
        "content_fingerprint": task_content_fingerprint(task, VALIDATION_CONFIG),
    }
    return validation


def call_update(monkeypatch, task, body):
    async def fake_get(*_args):
        return task

    monkeypatch.setattr(tasks_api, "_get_task_or_404", fake_get)
    db = FakeDb()
    result = asyncio.run(
        tasks_api.update_task(
            "assistant",
            task.id,
            GeneratedTaskUpdate(**body),
            db,
            SimpleNamespace(id="teacher-1"),
        )
    )
    return result, db


def test_unvalidated_task_cannot_bypass_automatic_check(monkeypatch) -> None:
    task = generated_task()
    with pytest.raises(HTTPException, match="актуальной автоматической проверки"):
        call_update(monkeypatch, task, {"status": "approved"})


def test_current_needs_review_task_requires_teacher_reason(monkeypatch) -> None:
    task = generated_task()
    task.validation = current_validation(task, verdict="needs_review")
    with pytest.raises(HTTPException, match="исключение из автоматической политики"):
        call_update(monkeypatch, task, {"status": "approved"})


def test_teacher_override_is_recorded(monkeypatch) -> None:
    task = generated_task()
    task.validation = current_validation(task, verdict="needs_review")
    result, db = call_update(
        monkeypatch,
        task,
        {"status": "approved", "approval_reason": "Проверено вручную по методичке"},
    )

    assert result.status == "approved"
    assert result.approved is True
    assert result.validation["approval"]["basis"] == "teacher_override"
    assert result.validation["approval"]["schema_version"] == APPROVAL_SCHEMA_VERSION
    assert result.validation["approval"]["reviewed_by"] == "teacher-1"
    assert db.commits == 1


def test_current_policy_validation_does_not_need_teacher_approval(monkeypatch) -> None:
    task = generated_task(status="validated")
    task.validation = current_validation(task)
    with pytest.raises(HTTPException, match="актуальной автоматической проверки"):
        call_update(monkeypatch, task, {"status": "approved"})


def test_editing_content_invalidates_previous_approval(monkeypatch) -> None:
    task = generated_task(status="approved", validation={"approval": {"basis": "teacher_override"}}, approved=True)
    result, _db = call_update(monkeypatch, task, {"answer": "Исправленный ответ"})

    assert result.answer == "Исправленный ответ"
    assert result.status == "draft"
    assert result.approved is False
    assert result.validation == {}


def test_editing_structured_chemistry_facts_invalidates_automatic_evidence() -> None:
    task = generated_task(status="validated")
    task.grounding = {
        "data_used": [],
        "chemistry_facts": {
            "dilution": {"c1": "0.1 mol/L", "v1": "10 mL", "c2": "0.01 mol/L", "v2": "100 mL"}
        },
    }
    config = {**VALIDATION_CONFIG, "task_evidence_digest": "bound"}
    evidence = {
        "validation_config": config,
        "content_fingerprint": task_content_fingerprint(task, config),
    }

    assert evidence_matches_task(evidence, task)
    task.grounding["chemistry_facts"]["dilution"]["v2"] = "50 mL"
    assert not evidence_matches_task(evidence, task)


def test_revalidation_atomically_clears_previous_approval(monkeypatch) -> None:
    task = generated_task(status="approved", approved=True)
    previous_approval = complete_approval(task)["approval"]
    task.validation = {"verdict": "validated", "approval": previous_approval}

    async def fake_assistant(*_args):
        return SimpleNamespace(
            default_grader_model_id="solver-1",
            default_generator_model_id=None,
            name="Химия",
            discipline="Общая химия",
            audience="студенты",
            language="ru",
            description="",
            topics=[],
            criteria=[],
            nuances=[],
        )

    async def fake_task(*_args):
        return task

    async def fake_model(*_args):
        return SimpleNamespace(name="DeepSeek"), SimpleNamespace(model_id="deepseek-v4-pro")

    async def fake_sheets(*_args, **_kwargs):
        return []

    async def fake_grounding(*_args, **_kwargs):
        return ""

    async def fake_validation(**_kwargs):
        result = current_validation(task, verdict="needs_review")
        result["reasons"] = ["Новый результат проверки"]
        result["approval"] = previous_approval
        return result

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    monkeypatch.setattr(tasks_api, "_get_task_or_404", fake_task)
    monkeypatch.setattr(tasks_api, "resolve_model", fake_model)
    monkeypatch.setattr(tasks_api, "load_reference_sheets", fake_sheets)
    monkeypatch.setattr(tasks_api, "build_generation_grounding", fake_grounding)
    monkeypatch.setattr(tasks_api, "run_validation", fake_validation)
    db = FakeDb()

    result = asyncio.run(tasks_api.revalidate_task("assistant", task.id, RevalidateRequest(), db))

    assert result.status == "needs_review"
    assert result.approved is False
    assert result.validation["verdict"] == "needs_review"
    assert result.validation["reasons"] == ["Новый результат проверки"]
    assert "approval" not in result.validation
    assert db.commits == 1


def test_explicit_export_rejects_unapproved_tasks(monkeypatch) -> None:
    task = generated_task()

    async def fake_assistant(*_args):
        return SimpleNamespace(discipline="Химия")

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    with pytest.raises(HTTPException, match="требуют автоматической перепроверки"):
        asyncio.run(
            tasks_api.export_tasks(
                "assistant",
                TaskExportRequest(task_ids=[task.id], mode="bank"),
                FakeDb([task]),
            )
        )


def test_export_rejects_legacy_approval_without_audit_record(monkeypatch) -> None:
    task = generated_task(status="approved", validation={"verdict": "validated"}, approved=True)

    async def fake_assistant(*_args):
        return SimpleNamespace(discipline="Химия")

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    with pytest.raises(HTTPException, match="требуют автоматической перепроверки"):
        asyncio.run(
            tasks_api.export_tasks(
                "assistant",
                TaskExportRequest(task_ids=[task.id], mode="bank"),
                FakeDb([task]),
            )
        )


@pytest.mark.parametrize(
    "validation",
    [
        {"approval": {"basis": "teacher_override"}},
        {"approval": "teacher_override"},
        ["malformed"],
    ],
)
def test_export_rejects_incomplete_or_malformed_audit(monkeypatch, validation) -> None:
    task = generated_task(status="approved", validation=validation, approved=True)

    async def fake_assistant(*_args):
        return SimpleNamespace(discipline="Химия")

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    with pytest.raises(HTTPException, match="требуют автоматической перепроверки"):
        asyncio.run(
            tasks_api.export_tasks(
                "assistant",
                TaskExportRequest(task_ids=[], mode="bank"),
                FakeDb([task]),
            )
        )


def test_default_export_rejects_inconsistent_approval_state(monkeypatch) -> None:
    task = generated_task(status="draft", approved=True)
    task.validation = complete_approval(task)

    async def fake_assistant(*_args):
        return SimpleNamespace(discipline="Химия")

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    with pytest.raises(HTTPException, match="требуют автоматической перепроверки"):
        asyncio.run(
            tasks_api.export_tasks(
                "assistant",
                TaskExportRequest(task_ids=[], mode="bank"),
                FakeDb([task]),
            )
        )


def test_export_accepts_complete_current_approval(monkeypatch) -> None:
    task = generated_task(status="approved", approved=True)
    task.validation = complete_approval(task)

    async def fake_assistant(*_args):
        return SimpleNamespace(discipline="Химия")

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    result = asyncio.run(
        tasks_api.export_tasks(
            "assistant",
            TaskExportRequest(task_ids=[], mode="bank"),
            FakeDb([task]),
        )
    )

    assert result["paragraphs"][0]["tasks"][0]["text"] == "Условие"


def test_export_accepts_complete_automatic_evidence_without_human_approval(monkeypatch) -> None:
    task = generated_task(status="validated", approved=False)
    task.validation = current_validation(task)

    async def fake_assistant(*_args):
        return SimpleNamespace(discipline="Химия")

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    result = asyncio.run(
        tasks_api.export_tasks(
            "assistant",
            TaskExportRequest(task_ids=[task.id], mode="bank"),
            FakeDb([task]),
        )
    )

    assert result["paragraphs"][0]["tasks"][0]["text"] == "Условие"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("solver", {"status": "match"}),
        ("verifier", {"status": "uncertain", "comparison": {"verdict": "uncertain"}}),
        ("cross_comparison", {"verdict": "uncertain"}),
        ("critic", {"status": "fail", "checks": {}, "issues": ["Нарушен баланс"]}),
        (
            "chemistry",
            {
                "validation_version": CHEMISTRY_VALIDATION_VERSION,
                "admission_effect": "block",
                "blocking_codes": ["chemistry.stoichiometry"],
                "indeterminate_codes": [],
                "warning_codes": [],
                "required_check_ids": ["chemistry.stoichiometry"],
                "results": [
                    {"check_id": "chemistry.stoichiometry", "state": "fail"},
                ],
            },
        ),
        ("reference_solution_check", {"verdict": "incomplete"}),
        ("data", {"status": "skipped", "unknown_numbers": [], "unknown_sources": []}),
        ("source_lineage", {"status": "warn", "unbound_sources": ["Таблица без источника"]}),
        ("sanity", {"issues": ["Пустая рубрика"]}),
        ("dedup", {"duplicate": True}),
        ("reasons", ["Есть блокирующая причина"]),
    ],
)
def test_incomplete_or_failed_evidence_never_auto_exports(monkeypatch, field, value) -> None:
    task = generated_task(status="validated", approved=False)
    validation = current_validation(task)
    validation[field] = value
    task.validation = validation

    async def fake_assistant(*_args):
        return SimpleNamespace(discipline="Химия")

    monkeypatch.setattr(tasks_api, "get_assistant_or_404", fake_assistant)
    with pytest.raises(HTTPException, match="требуют автоматической перепроверки"):
        asyncio.run(
            tasks_api.export_tasks(
                "assistant",
                TaskExportRequest(task_ids=[task.id], mode="bank"),
                FakeDb([task]),
            )
        )


def test_content_edit_invalidates_automatic_evidence_without_status_change() -> None:
    task = generated_task(status="validated", approved=False)
    task.validation = current_validation(task)
    assert tasks_api.task_is_export_ready(task) is True

    task.answer = "Другой ответ"

    assert tasks_api.task_is_export_ready(task) is False


def test_formula_can_auto_export_when_every_check_is_decisive() -> None:
    task = generated_task(status="validated", approved=False)
    task.validation = current_validation(task, answer_format="formula")

    assert tasks_api.task_is_export_ready(task) is True


def test_semantic_admission_without_both_reference_entailments_never_exports() -> None:
    task = generated_task(status="validated", approved=False)
    task.validation = current_validation(task, answer_format="text")
    del task.validation["critic"]["checks"]["verifier_matches_reference"]

    assert tasks_api.task_is_export_ready(task) is False


def test_teacher_override_survives_automatic_policy_metadata_change() -> None:
    task = generated_task(status="approved", approved=True)
    task.validation = complete_approval(task)
    task.validation["policy_version"] = "future-auto-policy"

    assert tasks_api.task_is_export_ready(task) is True
