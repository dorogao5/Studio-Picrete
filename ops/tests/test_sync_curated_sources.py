from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest


OPS_DIR = Path(__file__).resolve().parents[1]
RELEASE_SPEC = importlib.util.spec_from_file_location("apply_content_release", OPS_DIR / "apply_content_release.py")
assert RELEASE_SPEC is not None and RELEASE_SPEC.loader is not None
release = importlib.util.module_from_spec(RELEASE_SPEC)
sys.modules[RELEASE_SPEC.name] = release
RELEASE_SPEC.loader.exec_module(release)

SYNC_SPEC = importlib.util.spec_from_file_location("sync_curated_sources", OPS_DIR / "sync_curated_sources.py")
assert SYNC_SPEC is not None and SYNC_SPEC.loader is not None
sync = importlib.util.module_from_spec(SYNC_SPEC)
sys.modules[SYNC_SPEC.name] = sync
SYNC_SPEC.loader.exec_module(sync)


ASSISTANT_ID = release.DEFAULT_ASSISTANT_IDS["general_inorganic_lab"]
CONTENT_VERSION = "TEST-CURATED-R3"
DOCUMENT_TITLE = "Курируемый документ · R3"
COURSE_SCOPE = "course-test"
SHEET_TITLE = "TEST-00 · Проверенная карточка"
SHEET_CONTENT = "## TEST-00. Проверенная карточка\n\nПроверенное содержание.\n"


def write_package(
    root: Path,
    package_name: str = "general_inorganic_lab",
    *,
    slug: str = "test-00",
    sheet_title: str = SHEET_TITLE,
    sheet_content: str = SHEET_CONTENT,
) -> None:
    package = root / package_name
    (package / "sheets").mkdir(parents=True)
    (package / "sheets" / f"{slug}.md").write_text(sheet_content, encoding="utf-8")
    grounding_content = "# Curated R3\n\n" + sheet_content
    (package / "grounding.md").write_text(grounding_content, encoding="utf-8")
    manifest = {
        "schema_version": "picrete-content-manifest-v1",
        "content_version": CONTENT_VERSION,
        "source_and_blueprint_certification": {
            "certification_status": "certified",
            "per_task_teacher_approval": False,
        },
        "document_upload": {
            "file": "grounding.md",
            "title": DOCUMENT_TITLE,
            "doc_type": "methodical",
            "authority": "course_lecture",
            "visibility": "student",
            "course_scope": COURSE_SCOPE,
            "effective_version": CONTENT_VERSION,
            "content_sha256": release.normalized_markdown_sha256(grounding_content),
            "analyze": False,
        },
        "reference_sheet_proposals": [
            {
                "slug": slug,
                "title": sheet_title,
                "kind": "conventions",
                "description": "Проверенная карточка",
                "content_anchor": slug.upper(),
                "visibility": "student",
                "is_canonical": True,
                "ord": 100,
                "release_binding": {
                    "normalization": release.BINDING_NORMALIZATION,
                    "content_file": f"sheets/{slug}.md",
                    "content_sha256": release.normalized_markdown_sha256(sheet_content),
                    "source_document_title": DOCUMENT_TITLE,
                    "source_effective_version": CONTENT_VERSION,
                    "source_course_scope": COURSE_SCOPE,
                },
            }
        ],
        "task_templates": [{"slug": "template", "reference_sheet_slugs": [slug]}],
    }
    (package / "import-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


class FakeSyncApi:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = [
            {
                "id": "old-document",
                "title": "Старая версия",
                "effective_version": "R2",
                "course_scope": COURSE_SCOPE,
                "authority": "course_lecture",
                "visibility": "student",
                "status": "parsed",
                "markdown": "old",
            }
        ]
        self.sheets: list[dict[str, Any]] = [
            {
                "id": "sheet-1",
                "title": SHEET_TITLE,
                "kind": "conventions",
                "description": "Старая карточка",
                "content_markdown": "old\n",
                "source_document_id": "old-document",
                "visibility": "student",
                "is_canonical": True,
                "ord": 100,
            }
        ]
        self.tasks = [
            {"id": "approved", "status": "approved", "approved": True},
            {"id": "draft", "status": "draft", "approved": False},
        ]
        self.writes: list[tuple[str, str, dict[str, Any] | None]] = []
        self.get_calls = 0

    def get(self, path: str) -> Any:
        self.get_calls += 1
        suffix = path.split(f"assistants/{ASSISTANT_ID}/", 1)[-1]
        if suffix == "kb/documents":
            return deepcopy(self.documents)
        if suffix == "sheets":
            return deepcopy(self.sheets)
        if suffix == "tasks":
            return deepcopy(self.tasks)
        if suffix.startswith("kb/documents/"):
            document_id = suffix.rsplit("/", 1)[1]
            return deepcopy(next(document for document in self.documents if document["id"] == document_id))
        raise AssertionError(path)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        self.writes.append(("PATCH", path, deepcopy(payload)))
        sheet_id = path.rsplit("/", 1)[1]
        sheet = next(item for item in self.sheets if item["id"] == sheet_id)
        sheet.update(deepcopy(payload))
        return deepcopy(sheet)

    def upload_document(
        self,
        assistant_id: str,
        *,
        file_path: Path,
        fields: dict[str, str],
    ) -> Any:
        assert assistant_id == ASSISTANT_ID
        self.writes.append(("UPLOAD", str(file_path), deepcopy(fields)))
        created = {
            "id": "document-r3",
            **fields,
            "status": "parsed",
            "markdown": file_path.read_text(encoding="utf-8"),
        }
        self.documents.append(created)
        return deepcopy(created)


def arguments() -> list[str]:
    return [
        "--base-url",
        "https://dev.picrete.com",
        "--package",
        "general_inorganic_lab",
    ]


def test_sync_is_dry_run_by_default_then_applies_and_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package(tmp_path)
    api = FakeSyncApi()
    monkeypatch.setenv(release.TOKEN_ENV, "not-printed")

    dry_run = sync.run(arguments(), api=api, content_root=tmp_path)
    summary = dry_run["packages"]["general_inorganic_lab"]
    assert dry_run["mode"] == "dry-run"
    assert dry_run["planned_mutations"] == 2
    assert summary["document_upload"] is True
    assert summary["sheets_update"] == 1
    assert summary["tasks_with_evidence_invalidated"] == 1
    assert summary["approved_tasks_invalidated"] == 1
    assert api.writes == []

    applied = sync.run([*arguments(), "--apply"], api=api, content_root=tmp_path)
    assert applied["mode"] == "apply"
    assert [write[0] for write in api.writes] == ["UPLOAD", "PATCH"]
    assert api.sheets[0]["source_document_id"] == "document-r3"
    assert api.sheets[0]["content_markdown"] == release.normalize_markdown(SHEET_CONTENT)

    writes_after_apply = len(api.writes)
    second_dry_run = sync.run(arguments(), api=api, content_root=tmp_path)
    assert second_dry_run["planned_mutations"] == 0
    assert len(api.writes) == writes_after_apply


def test_local_digest_drift_stops_before_remote_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package(tmp_path)
    (tmp_path / "general_inorganic_lab" / "sheets" / "test-00.md").write_text(
        "tampered\n", encoding="utf-8"
    )
    api = FakeSyncApi()
    monkeypatch.setenv(release.TOKEN_ENV, "not-printed")
    with pytest.raises(release.ReleaseError, match="does not match its local content_file"):
        sync.run(arguments(), api=api, content_root=tmp_path)
    assert api.get_calls == 0
    assert api.writes == []


def test_sheet_must_remain_an_exact_grounding_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package(tmp_path)
    grounding = "# Curated R3\n\nДругой материал.\n"
    package = tmp_path / "general_inorganic_lab"
    (package / "grounding.md").write_text(grounding, encoding="utf-8")
    manifest_path = package / "import-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["document_upload"]["content_sha256"] = release.normalized_markdown_sha256(grounding)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    api = FakeSyncApi()
    monkeypatch.setenv(release.TOKEN_ENV, "not-printed")
    with pytest.raises(release.ReleaseError, match="not an exact section"):
        sync.run(arguments(), api=api, content_root=tmp_path)
    assert api.get_calls == 0
    assert api.writes == []


def test_existing_exact_document_must_match_grounding_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package(tmp_path)
    api = FakeSyncApi()
    api.documents.append(
        {
            "id": "wrong-r3",
            "title": DOCUMENT_TITLE,
            "effective_version": CONTENT_VERSION,
            "course_scope": COURSE_SCOPE,
            "authority": "course_lecture",
            "visibility": "student",
            "status": "parsed",
            "markdown": "wrong content\n",
        }
    )
    monkeypatch.setenv(release.TOKEN_ENV, "not-printed")
    with pytest.raises(release.ReleaseError, match="content digest does not match"):
        sync.run(arguments(), api=api, content_root=tmp_path)
    assert api.writes == []


def test_duplicate_exact_document_binding_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package(tmp_path)
    api = FakeSyncApi()
    grounding = (tmp_path / "general_inorganic_lab" / "grounding.md").read_text(encoding="utf-8")
    for index in range(2):
        api.documents.append(
            {
                "id": f"r3-{index}",
                "title": DOCUMENT_TITLE,
                "effective_version": CONTENT_VERSION,
                "course_scope": COURSE_SCOPE,
                "authority": "course_lecture",
                "visibility": "student",
                "status": "parsed",
                "markdown": grounding,
            }
        )
    monkeypatch.setenv(release.TOKEN_ENV, "not-printed")
    with pytest.raises(release.ReleaseError, match="ambiguous"):
        sync.run(arguments(), api=api, content_root=tmp_path)
    assert api.writes == []


def test_all_packages_are_preflighted_before_first_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package(tmp_path)
    write_package(
        tmp_path,
        "colloid_chemistry",
        slug="other-00",
        sheet_title="OTHER-00 · Missing",
        sheet_content="## OTHER-00. Missing\n",
    )
    api = FakeSyncApi()
    monkeypatch.setenv(release.TOKEN_ENV, "not-printed")
    args = [
        "--base-url",
        "https://dev.picrete.com",
        "--package",
        "general_inorganic_lab",
        "--package",
        "colloid_chemistry",
        "--assistant-map",
        f"colloid_chemistry={ASSISTANT_ID}",
        "--apply",
    ]
    with pytest.raises(release.ReleaseError, match="required existing sheet is missing"):
        sync.run(args, api=api, content_root=tmp_path)
    assert api.writes == []


def test_repository_packages_have_reproducible_r3_sheet_bindings() -> None:
    expected = {
        "analytical_chemistry": ("ANALYTICAL-CURATED-2026-07-R3", 6),
        "colloid_chemistry": ("COLLOID-CURATED-2026-07-R3", 4),
        "general_inorganic_lab": ("LAB-CURATED-2026-07-R3", 9),
    }
    for package_name, (version, sheet_count) in expected.items():
        spec = sync.load_package_spec(
            OPS_DIR / "content" / package_name,
            release.DEFAULT_ASSISTANT_IDS[package_name],
        )
        assert spec.content_version == version
        assert len(spec.sheets) == sheet_count
        assert all(len(sheet.content_sha256) == 64 for sheet in spec.sheets)
