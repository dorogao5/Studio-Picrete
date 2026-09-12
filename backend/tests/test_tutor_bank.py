import asyncio
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.api import tutor
from app.schemas import TutorBankTask
from app.services.tutor import build_tutor_context


def test_bank_tutor_uses_bound_course_and_exact_task_with_reference(monkeypatch):
    calls = []
    async def course(db, assistant, course_id):
        calls.append((assistant, course_id))
        return None, SimpleNamespace(external_course_id="bound")
    async def request(method, bound, path, **kwargs):
        assert bound.external_course_id == "bound"
        assert kwargs["params"] == {"q": "7.2", "skip": 0}
        return {"items": [{"id": "task", "number": "7.2", "text": "condition", "solution": "reference"}]}
    monkeypatch.setattr(tutor, "_course_or_404", course)
    monkeypatch.setattr(tutor, "_picrete_request", request)
    result = asyncio.run(tutor.resolve_bank_task(None, "assistant", TutorBankTask(course_id="course", task_id="task", task_number="7.2")))
    assert calls == [("assistant", "course")]
    assert result["task"]["solution"] == "reference"
    context = build_tutor_context(SimpleNamespace(statement=result["task"]["text"], reference_solution=result["task"]["solution"], answer=""), "student", "")
    assert "не раскрывайте также пересказом или таблицей" in context
    assert "reference" in context and "student" in context


def test_bank_tutor_rejects_task_from_other_result(monkeypatch):
    async def course(*args): return None, None
    async def request(*args, **kwargs):
        return {"items": [{"id": "different", "number": "7.2", "text": "other"}]}
    monkeypatch.setattr(tutor, "_course_or_404", course)
    monkeypatch.setattr(tutor, "_picrete_request", request)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tutor.resolve_bank_task(None, "a", TutorBankTask(course_id="c", task_id="task", task_number="7.2")))
    assert exc.value.status_code == 404
