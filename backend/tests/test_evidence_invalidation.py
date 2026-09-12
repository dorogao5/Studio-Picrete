import asyncio
from types import SimpleNamespace

from app.services.evidence_invalidation import invalidate_task_evidence


class _Result:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def scalars(self) -> list[object]:
        return self.values


class _Db:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    async def execute(self, _statement):
        return _Result(self.values)


def test_invalidation_preserves_task_content_and_records_stale_reason() -> None:
    task = SimpleNamespace(
        statement="Неизменённое условие",
        answer="42",
        status="approved",
        approved=True,
        validation={
            "verdict": "validated",
            "content_fingerprint": "old-fingerprint",
            "reasons": [],
            "approval": {"basis": "teacher_override"},
        },
    )

    count = asyncio.run(
        invalidate_task_evidence(_Db([task]), "assistant", reason="Изменился профиль дисциплины")
    )

    assert count == 1
    assert task.statement == "Неизменённое условие"
    assert task.answer == "42"
    assert task.status == "needs_review"
    assert task.approved is False
    assert task.validation["verdict"] == "needs_review"
    assert task.validation["stale_evidence"]["previous_content_fingerprint"] == "old-fingerprint"
    assert "approval" not in task.validation


def test_unrelated_knowledge_keeps_self_contained_variant_but_not_dependencies():
    from copy import deepcopy
    task = SimpleNamespace(status="validated", approved=False,
        validation={"policy_version":"single-verifier-v1", "verdict":"validated", "reasons":[]},
        grounding={"blueprint":{"source_number":"8.100"}, "kb_chunks":0, "sheets":[], "data_used":[]})
    dependent=deepcopy(task);dependent.grounding["sheets"]=[{"id":"used-sheet"}]
    legacy=deepcopy(task);legacy.validation["policy_version"]="legacy"
    assert asyncio.run(invalidate_task_evidence(_Db([task,dependent,legacy]),"a",reason="KB changed",knowledge_change=True))==2
    assert task.status=="validated"
    assert dependent.status==legacy.status=="needs_review"
    assert asyncio.run(invalidate_task_evidence(_Db([task]),"a",reason="Template changed",template_id="t"))==1
    assert task.status=="needs_review"
