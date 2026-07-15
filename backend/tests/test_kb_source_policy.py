from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.kb import _ensure_student_sheet_source_allowed
from app.schemas import KnowledgeDocumentUpdate, ReferenceSheetUpdate


def _source(*, authority: str = "course_lecture", visibility: str = "student", status: str = "parsed"):
    return SimpleNamespace(authority=authority, visibility=visibility, status=status)


def test_student_sheet_accepts_only_parsed_trusted_student_source() -> None:
    _ensure_student_sheet_source_allowed(_source(), "student")

    for source in (
        _source(authority="unverified"),
        _source(visibility="quarantine"),
        _source(status="parsing"),
    ):
        with pytest.raises(HTTPException) as error:
            _ensure_student_sheet_source_allowed(source, "student")
        assert error.value.status_code == 422


def test_non_student_sheet_can_remain_bound_to_quarantined_source() -> None:
    _ensure_student_sheet_source_allowed(
        _source(authority="unverified", visibility="quarantine", status="parsed"),
        "quarantine",
    )


def test_document_metadata_and_sheet_source_binding_are_typed() -> None:
    update = KnowledgeDocumentUpdate(
        title="  Версия курса  ",
        authority="course_policy",
        visibility="student",
        course_scope="course-42",
        effective_version="2026-r3",
    )
    assert update.course_scope == "course-42"
    assert ReferenceSheetUpdate(source_document_id="document-1").source_document_id == "document-1"

    with pytest.raises(ValidationError):
        KnowledgeDocumentUpdate(authority="trusted_because_model_said_so")
