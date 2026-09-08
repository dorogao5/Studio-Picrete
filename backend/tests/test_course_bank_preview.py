import asyncio
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.api import integration


def test_bank_bridge_uses_bound_course_and_forces_server_side_bank(monkeypatch):
    captured = {}
    async def course(*args): return None, SimpleNamespace(external_course_id="real-course")
    async def request(method, course, path, **kw):
        captured.update(method=method, course=course.external_course_id, path=path, **kw)
        return {"items": [], "total": 0}
    monkeypatch.setattr(integration, "_course_or_404", course)
    monkeypatch.setattr(integration, "_picrete_request", request)
    asyncio.run(integration.course_task_bank("a", "c", "7.61", -10, None, None))
    assert captured == {"method": "GET", "course": "real-course", "path": "task-bank", "params": {"q": "7.61", "skip": 0}}


def test_unbound_course_cannot_access_bank(monkeypatch):
    monkeypatch.setattr(integration, "_ensure_configured", lambda: None)
    with pytest.raises(HTTPException, match="привяжите"):
        asyncio.run(integration._picrete_request("GET", SimpleNamespace(external_course_id=""), "task-bank"))


def test_preview_rejects_missing_active_grader(monkeypatch):
    async def course(*args): return None, None
    async def snapshot(*args): return {"assistant": {"grading_enabled": False}}
    monkeypatch.setattr(integration, "_course_or_404", course)
    monkeypatch.setattr(integration, "_build_snapshot", snapshot)
    with pytest.raises(HTTPException, match="активируйте"):
        asyncio.run(integration.course_grading_preview("a", "c", integration.BankPreviewRequest(task_id="1", student_text="answer"), None, None))
