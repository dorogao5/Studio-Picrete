import asyncio
from types import SimpleNamespace

from app.services import taskgen
from app.services.model_policy import current_model_use_policy
from app.services.task_evidence import build_task_content_fingerprint
from app.services.chemistry_validation import CHEMISTRY_VALIDATION_VERSION
from app.services.validation import VALIDATION_POLICY_VERSION


class FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    async def execute(self, _statement):
        class Scalars:
            def all(self):
                return []

        class Result:
            def scalars(self):
                return Scalars()

            def all(self):
                return []

        return Result()

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _value) -> None:
        return None


class FakeKbDb(FakeDb):
    async def execute(self, _statement):
        class Result:
            def all(self):
                return [("doc-1", "Коллоидная химия · курс лекций", "course_lecture", "2026-r3")]

        return Result()


def _task(task_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        statement=f"Полное условие задачи {task_id} с достаточным количеством исходных данных.",
        reference_solution="Подробное эталонное решение с полным финальным ответом.",
        answer="m = 5 г",
        rubric=[{"criterion_name": "Расчёт", "max_score": 10}],
        max_score=10,
        topic="Стехиометрия",
        grounding={"data_used": [], "chemistry_facts": {}},
        status="draft",
        validation={},
        approved=False,
    )


def test_grounding_metadata_contains_only_sheets_rendered_for_the_generator() -> None:
    visible = SimpleNamespace(id="visible", title="COL-01 · Модель БЭТ", source_document_id="doc-colloid")
    omitted = SimpleNamespace(id="omitted", title="ANA-03 · Закон Фарадея", source_document_id="doc-analytical")

    metadata = asyncio.run(
        taskgen.build_grounding_meta(
            FakeDb(),
            [visible, omitted],
            "## СПРАВОЧНЫЕ МАТЕРИАЛЫ КУРСА\n### COL-01 · Модель БЭТ (Формулы)\nконтекст",
            "БЭТ",
        )
    )

    assert metadata["sheets"] == [
        {
            "id": "visible",
            "title": "COL-01 · Модель БЭТ",
            "source_document_id": "doc-colloid",
            "source_document_exists": False,
            "source_authority": "",
            "source_version": "",
        }
    ]


def test_grounding_metadata_freezes_exact_kb_header_and_document_lineage() -> None:
    header = "Коллоидная химия · курс лекций [материал курса] — ЛЕКЦИЯ 6"
    metadata = asyncio.run(
        taskgen.build_grounding_meta(
            FakeKbDb(),
            [],
            f"{taskgen.KB_HEADER}\n\n### {header}\nданные",
            "БЭТ",
            assistant_id="assistant-1",
        )
    )

    assert metadata["kb_chunks"] == 1
    assert metadata["kb_sources"] == [
        {
            "id": "",
            "title": header,
            "source_document_id": "doc-1",
            "source_document_exists": True,
            "source_authority": "course_lecture",
            "source_version": "2026-r3",
            "source_kind": "kb_chunk",
        }
    ]


def test_failed_candidates_are_discarded_while_green_tasks_are_ready(monkeypatch) -> None:
    calls = 0

    async def fake_validation(**kwargs):
        nonlocal calls
        calls += 1
        config = kwargs["validation_config"]
        model_use = current_model_use_policy().classify("deepseek-v4-pro")
        evidence = {
            "verdict": "validated",
            "answer_format": kwargs["answer_format"],
            "policy_version": VALIDATION_POLICY_VERSION,
            "validation_config": config,
            "content_fingerprint": build_task_content_fingerprint(
                statement=kwargs["statement"],
                reference_solution=kwargs["reference_solution"],
                answer=kwargs["reference_answer"],
                rubric=kwargs["rubric"],
                max_score=kwargs["max_score"],
                validation_config=config,
            ),
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
            "dedup": {"duplicate": False},
            "reasons": [],
        }
        if calls == 1:
            return evidence
        return {**evidence, "verdict": "needs_review", "reasons": ["Ответы разошлись"]}

    async def quiet_progress(*_args, **_kwargs):
        return None

    monkeypatch.setattr(taskgen, "run_validation", fake_validation)
    monkeypatch.setattr(taskgen, "_set_progress", quiet_progress)
    ready = _task("ready")
    discarded = _task("discarded")
    batch = SimpleNamespace(id="batch", assistant_id="assistant", validated_count=0)
    merged = {
        "answer_format": "numeric",
        "tolerance_pct": 2,
        "validation_solver": True,
        "validation_data_check": True,
        "task_kind": "calculation",
        "sheet_ids": [],
        "kb_query": "",
        "chemistry_check": "auto",
    }

    asyncio.run(
        taskgen._validate_batch(
            FakeDb(),
            batch,
            [ready, discarded],
            merged,
            SimpleNamespace(name="DeepSeek"),
            SimpleNamespace(model_id="deepseek-v4-pro"),
            "",
            "",
        )
    )

    assert batch.validated_count == 1
    assert ready.status == "validated"
    assert ready.approved is False
    assert discarded.status == "rejected"
    assert discarded.approved is False
    assert discarded.validation["candidate_disposition"] == "discarded"
