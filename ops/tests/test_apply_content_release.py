from __future__ import annotations

import json
import importlib.util
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "apply_content_release.py"
SPEC = importlib.util.spec_from_file_location("apply_content_release", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


ASSISTANT_ID = release.DEFAULT_ASSISTANT_IDS["general_inorganic_lab"]
MODEL_ID = release.DEFAULT_DEEPSEEK_MODEL_ENTRY_ID
SHEET_CONTENT = "## LAB-01. Проверенный материал\n\nДанные и единицы проверены.\n"
GROUNDING_CONTENT = "# Curated TEST-R3\n\n" + SHEET_CONTENT
DOCUMENT_TITLE = "Лабораторный модуль · TEST-R3"
CONTENT_VERSION = "TEST-R3"
COURSE_SCOPE = "test-course"


def write_package(root: Path) -> None:
    package = root / "general_inorganic_lab"
    (package / "prompts").mkdir(parents=True)
    (package / "sheets").mkdir()
    (package / "sheets" / "sheet-1.md").write_text(SHEET_CONTENT, encoding="utf-8")
    (package / "grounding.md").write_text(GROUNDING_CONTENT, encoding="utf-8")
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
            "effective_version": CONTENT_VERSION,
            "course_scope": COURSE_SCOPE,
            "content_sha256": release.normalized_markdown_sha256(GROUNDING_CONTENT),
        },
        "reference_sheet_proposals": [
            {
                "slug": "sheet-1",
                "title": "LAB-01 · Проверенный материал",
                "kind": "formulas",
                "visibility": "student",
                "is_canonical": True,
                "ord": 101,
                "release_binding": {
                    "normalization": release.BINDING_NORMALIZATION,
                    "content_file": "sheets/sheet-1.md",
                    "content_sha256": release.normalized_markdown_sha256(SHEET_CONTENT),
                    "source_document_title": DOCUMENT_TITLE,
                    "source_effective_version": CONTENT_VERSION,
                    "source_course_scope": COURSE_SCOPE,
                },
            },
        ],
        "assistant_profile_patch": {
            "topics_to_add": ["Стехиометрия"],
            "nuances_to_add": ["Проверять единицы"],
        },
        "task_templates": [
            {
                "slug": "template-1",
                "reference_sheet_slugs": ["sheet-1"],
                "payload": {
                    "name": "Лаборатория · расчёт",
                    "topic": "Стехиометрия",
                    "difficulty": "medium",
                    "instructions": "Рассчитать величину.",
                    "example": "",
                    "task_kind": "calculation",
                    "answer_format": "numeric",
                    "numeric_tolerance_pct": 1,
                    "reference_sheet_ids": [],
                    "example_tasks": [],
                    "kb_query": "стехиометрия",
                    "validation_solver": True,
                    "validation_data_check": True,
                    "chemistry_check": "chemistry.stoichiometry",
                },
                "recommended_rubric": [
                    {"criterion_name": "Расчёт", "max_score": 10, "description": "Верный результат"}
                ],
            }
        ],
    }
    (package / "import-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for role in release.PROMPT_ROLES:
        (package / "prompts" / f"{role}.txt").write_text(f"{role} prompt\n", encoding="utf-8")


class FakeApi:
    def __init__(self) -> None:
        self.assistant = {
            "id": ASSISTANT_ID,
            "topics": ["Основы"],
            "nuances": [],
            "default_generator_model_id": None,
            "default_grader_model_id": None,
        }
        self.documents = [
            {
                "id": "document-1",
                "title": DOCUMENT_TITLE,
                "authority": "reference",
                "visibility": "student",
                "status": "parsed",
                "effective_version": CONTENT_VERSION,
                "course_scope": COURSE_SCOPE,
                "markdown": GROUNDING_CONTENT,
            }
        ]
        self.sheets = [
            {
                "id": "sheet-id-1",
                "title": "LAB-01 · Проверенный материал",
                "kind": "formulas",
                "content_markdown": SHEET_CONTENT,
                "source_document_id": "document-1",
                "visibility": "student",
                "is_canonical": True,
                "ord": 101,
            }
        ]
        self.templates: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        self.pipelines: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []
        self.providers = [
            {
                "id": "provider-1",
                "purpose": "production",
                "enabled": True,
                "models": [{"id": MODEL_ID, "enabled": True, "family": "deepseek"}],
            }
        ]
        self.writes: list[tuple[str, str, dict[str, Any] | None]] = []

    def get(self, path: str) -> Any:
        if path == "providers":
            return deepcopy(self.providers)
        prefix = f"assistants/{ASSISTANT_ID}"
        if path.startswith(f"{prefix}/kb/documents/"):
            document_id = path.rsplit("/", 1)[1]
            return deepcopy(next(document for document in self.documents if document["id"] == document_id))
        values = {
            prefix: self.assistant,
            f"{prefix}/sheets": self.sheets,
            f"{prefix}/kb/documents": self.documents,
            f"{prefix}/templates": self.templates,
            f"{prefix}/prompts": self.prompts,
            f"{prefix}/pipelines": self.pipelines,
            f"{prefix}/tasks": self.tasks,
        }
        return deepcopy(values[path])

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        self.writes.append(("PATCH", path, deepcopy(payload)))
        prefix = f"assistants/{ASSISTANT_ID}"
        if path == prefix:
            self.assistant.update(deepcopy(payload))
            return deepcopy(self.assistant)
        if path.startswith(f"{prefix}/templates/"):
            item = next(value for value in self.templates if value["id"] == path.rsplit("/", 1)[1])
            item.update(deepcopy(payload))
            return deepcopy(item)
        if path.startswith(f"{prefix}/pipelines/"):
            item = next(value for value in self.pipelines if value["id"] == path.rsplit("/", 1)[1])
            item.update(deepcopy(payload))
            return deepcopy(item)
        raise AssertionError(path)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        self.writes.append(("POST", path, deepcopy(payload)))
        prefix = f"assistants/{ASSISTANT_ID}"
        if path == f"{prefix}/templates":
            created = {"id": f"template-{len(self.templates) + 1}", **deepcopy(payload)}
            self.templates.append(created)
            return deepcopy(created)
        if path == f"{prefix}/prompts":
            role = payload["role"]
            versions = [value["version"] for value in self.prompts if value["role"] == role]
            created = {
                "id": f"prompt-{len(self.prompts) + 1}",
                "version": max(versions, default=0) + 1,
                "status": "draft",
                **deepcopy(payload),
            }
            self.prompts.append(created)
            return deepcopy(created)
        if path.endswith("/activate"):
            prompt_id = path.split("/")[-2]
            selected = next(value for value in self.prompts if value["id"] == prompt_id)
            for prompt in self.prompts:
                if prompt["role"] == selected["role"] and prompt["status"] == "active":
                    prompt["status"] = "archived"
            selected["status"] = "active"
            return deepcopy(selected)
        if path == f"{prefix}/pipelines":
            created = {"id": f"pipeline-{len(self.pipelines) + 1}", **deepcopy(payload)}
            self.pipelines.append(created)
            return deepcopy(created)
        raise AssertionError(path)


def test_url_and_authorization_parsing(tmp_path: Path) -> None:
    assert release.normalize_api_url("https://dev.picrete.com") == "https://dev.picrete.com/api"
    assert release.normalize_api_url("https://dev.picrete.com/api/") == "https://dev.picrete.com/api"
    assert release.normalize_api_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000/api"
    with pytest.raises(release.ReleaseError, match="HTTPS"):
        release.normalize_api_url("http://dev.picrete.com")
    with pytest.raises(release.ReleaseError, match="without credentials"):
        release.normalize_api_url("https://token@dev.picrete.com")

    header = tmp_path / "header"
    header.write_text("Authorization: Bearer secret-token\n", encoding="utf-8")
    assert release.load_authorization(header, {}) == "Bearer secret-token"
    assert release.load_authorization(None, {release.TOKEN_ENV: "secret-token"}) == "Bearer secret-token"
    with pytest.raises(release.ReleaseError, match=release.TOKEN_ENV):
        release.load_authorization(None, {})
    with pytest.raises(release.ReleaseError, match="CA bundle"):
        release.build_ssl_context(tmp_path / "missing.pem")


def test_markdown_digest_normalization_is_stable() -> None:
    decomposed = "\ufeff\nCafe\u0301  \r\n\r\n"
    assert release.normalize_markdown(decomposed) == "Café\n"
    assert release.normalized_markdown_sha256(decomposed) == release.normalized_markdown_sha256("Café\n")


def test_release_is_dry_run_by_default_and_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_package(tmp_path)
    api = FakeApi()
    monkeypatch.setenv(release.TOKEN_ENV, "not-printed")
    arguments = [
        "--base-url",
        "https://dev.picrete.com",
        "--package",
        "general_inorganic_lab",
    ]

    dry_run = release.run(arguments, api=api, content_root=tmp_path)
    assert dry_run["mode"] == "dry-run"
    assert dry_run["planned_mutations"] > 0
    assert api.writes == []

    applied = release.run([*arguments, "--apply"], api=api, content_root=tmp_path)
    assert applied["mode"] == "apply"
    assert len(api.templates) == 1
    assert api.templates[0]["reference_sheet_ids"] == ["sheet-id-1"]
    assert len(api.prompts) == 3
    assert all(prompt["status"] == "active" for prompt in api.prompts)
    assert api.assistant["default_generator_model_id"] == MODEL_ID
    assert api.assistant["default_grader_model_id"] == MODEL_ID
    assert api.pipelines[0]["steps"] == release._desired_pipeline(MODEL_ID)["steps"]

    writes_after_apply = len(api.writes)
    second_dry_run = release.run(arguments, api=api, content_root=tmp_path)
    assert second_dry_run["planned_mutations"] == 0
    assert len(api.writes) == writes_after_apply


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda api: api.sheets[0].update(source_document_id=None), "not bound"),
        (lambda api: api.documents[0].update(authority="unverified"), "not trusted"),
        (lambda api: api.documents[0].update(status="uploaded"), "not parsed"),
        (lambda api: api.sheets.append(deepcopy(api.sheets[0])), "Ambiguous Studio sheet"),
        (lambda api: api.sheets[0].update(is_canonical=False), "not canonical"),
        (lambda api: api.sheets[0].update(kind="conventions"), "metadata does not match"),
        (lambda api: api.sheets[0].update(ord=999), "metadata does not match"),
        (lambda api: api.sheets[0].update(content_markdown="changed"), "content does not match"),
        (lambda api: api.documents[0].update(title="old title"), "exact curated document binding"),
        (lambda api: api.documents[0].update(effective_version="TEST-R2"), "exact curated document binding"),
        (lambda api: api.documents[0].update(course_scope="other-course"), "exact curated document binding"),
    ],
)
def test_preflight_fails_closed_for_unsafe_source_sheet(
    tmp_path: Path, mutate, message: str
) -> None:
    write_package(tmp_path)
    api = FakeApi()
    mutate(api)
    with pytest.raises(release.ReleaseError, match=message):
        release.preflight_package(api, tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)
    assert api.writes == []


def test_preflight_rejects_local_sheet_digest_drift_before_writes(tmp_path: Path) -> None:
    write_package(tmp_path)
    api = FakeApi()
    sheet_file = tmp_path / "general_inorganic_lab" / "sheets" / "sheet-1.md"
    sheet_file.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="does not match its local content_file"):
        release.preflight_package(api, tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)
    assert api.writes == []


def test_preflight_rejects_local_grounding_digest_drift_before_writes(tmp_path: Path) -> None:
    write_package(tmp_path)
    api = FakeApi()
    (tmp_path / "general_inorganic_lab" / "grounding.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="grounding.md does not match"):
        release.preflight_package(api, tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)
    assert api.writes == []


def test_preflight_rejects_remote_grounding_digest_drift(tmp_path: Path) -> None:
    write_package(tmp_path)
    api = FakeApi()
    api.documents[0]["markdown"] = "wrong remote content\n"
    with pytest.raises(release.ReleaseError, match="content digest does not match"):
        release.preflight_package(api, tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)
    assert api.writes == []


@pytest.mark.parametrize(
    ("certification", "message"),
    [
        ({"certification_status": "candidate", "per_task_teacher_approval": False}, "does not permit"),
        (
            {
                "certification_status": "certified_theory_and_preflight_calculation_only",
                "per_task_teacher_approval": False,
            },
            "operational procedures blocked",
        ),
    ],
)
def test_manifest_certification_must_be_release_grade(
    tmp_path: Path, certification: dict[str, Any], message: str
) -> None:
    write_package(tmp_path)
    manifest_path = tmp_path / "general_inorganic_lab" / "import-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_and_blueprint_certification"] = certification
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match=message):
        release.preflight_package(FakeApi(), tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)


def test_limited_certification_requires_and_accepts_explicit_operational_block(tmp_path: Path) -> None:
    write_package(tmp_path)
    manifest_path = tmp_path / "general_inorganic_lab" / "import-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_and_blueprint_certification"] = {
        "certification_status": "certified_theory_and_preflight_calculation_only",
        "operational_procedure_status": release.OPERATIONAL_BLOCKED_STATUS,
        "per_task_teacher_approval": False,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = release.preflight_package(FakeApi(), tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)
    assert plan.resolved_sheet_count == 1


def test_fully_certified_manifest_cannot_keep_pending_course_binding(tmp_path: Path) -> None:
    write_package(tmp_path)
    manifest_path = tmp_path / "general_inorganic_lab" / "import-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["binding_status"] = "pending_course_binding"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="pending course binding"):
        release.preflight_package(FakeApi(), tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)


def test_manifest_content_file_is_fixed_to_the_sheet_slug(tmp_path: Path) -> None:
    write_package(tmp_path)
    manifest_path = tmp_path / "general_inorganic_lab" / "import-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reference_sheet_proposals"][0]["release_binding"]["content_file"] = "../grounding.md"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="must use content_file"):
        release.preflight_package(FakeApi(), tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)


def test_plan_reports_tasks_whose_evidence_will_be_invalidated(tmp_path: Path) -> None:
    write_package(tmp_path)
    api = FakeApi()
    api.assistant["topics"] = ["Основы", "Стехиометрия"]
    api.assistant["nuances"] = ["Проверять единицы"]
    desired = release.build_template_payloads(
        json.loads((tmp_path / "general_inorganic_lab" / "import-manifest.json").read_text(encoding="utf-8")),
        {"sheet-1": "sheet-id-1"},
    )[0]
    api.templates = [{"id": "template-existing", **desired, "instructions": "old"}]
    api.tasks = [
        {"id": "approved", "template_id": "template-existing", "status": "approved", "approved": True},
        {"id": "validated", "template_id": "template-existing", "status": "validated", "approved": False},
        {"id": "draft", "template_id": "template-existing", "status": "draft", "approved": False},
        {"id": "other", "template_id": "other-template", "status": "validated", "approved": False},
    ]
    plan = release.preflight_package(api, tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)
    assert plan.tasks_with_evidence_invalidated == 2
    assert plan.approved_tasks_invalidated == 1


def test_existing_exact_prompt_is_reused_and_activated(tmp_path: Path) -> None:
    write_package(tmp_path)
    api = FakeApi()
    api.prompts = [
        {
            "id": "existing-generator",
            "role": "generator",
            "version": 7,
            "system_prompt": "generator prompt\n",
            "target_family": "deepseek",
            "status": "archived",
        }
    ]
    plan = release.preflight_package(api, tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)
    generator = next(item for item in plan.prompts if item[0] == "generator")
    assert generator[2:] == ("existing-generator", True)
    release.apply_package(api, plan)
    assert not any(
        method == "POST" and path == f"assistants/{ASSISTANT_ID}/prompts" and body["role"] == "generator"
        for method, path, body in api.writes
    )
    assert next(prompt for prompt in api.prompts if prompt["id"] == "existing-generator")["status"] == "active"


def test_exact_prompt_with_wrong_family_fails_instead_of_duplicating(tmp_path: Path) -> None:
    write_package(tmp_path)
    api = FakeApi()
    api.prompts = [
        {
            "id": "generic-generator",
            "role": "generator",
            "version": 1,
            "system_prompt": "generator prompt\n",
            "target_family": "generic",
            "status": "active",
        }
    ]
    with pytest.raises(release.ReleaseError, match="not marked for the DeepSeek"):
        release.preflight_package(api, tmp_path / "general_inorganic_lab", ASSISTANT_ID, MODEL_ID)
    assert api.writes == []
