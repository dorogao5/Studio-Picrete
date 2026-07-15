#!/usr/bin/env python3
"""Idempotently install curated chemistry content into Picrete Studio.

The command is read-only by default::

    PICRETE_STUDIO_TOKEN=... python3 ops/apply_content_release.py \
        --base-url https://dev.picrete.com

Pass ``--apply`` explicitly to mutate Studio.  A curl-style file containing
``Authorization: Bearer ...`` can be supplied with ``--auth-header-file`` so
the credential never has to appear in process arguments.

This tool deliberately does not upload source material or create reference
sheets. Run ``sync_curated_sources.py`` first. A release is allowed only after
every sheet named by its manifest matches its certified digest and is bound to
the exact parsed, trusted, student-visible Studio document version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


DEFAULT_ASSISTANT_IDS = {
    "general_inorganic_lab": "9fbc228df47e4d679c7a49b57d65af59",
    "colloid_chemistry": "bb3be1b8a8aa46769fa63c270d4ee6a3",
    "analytical_chemistry": "9243941e323d457aa57ec00cd0192a92",
}
DEFAULT_DEEPSEEK_MODEL_ENTRY_ID = "4afae41c3ef7438fae8799fe0ab37763"
TOKEN_ENV = "PICRETE_STUDIO_TOKEN"
PIPELINE_NAME = "Основной сценарий"
PIPELINE_DESCRIPTION = "Распознавание → две независимые проверки → автоматическая сверка"
TRUSTED_AUTHORITIES = {"course_policy", "course_lecture", "reference"}
PROMPT_ROLES = ("generator", "grader", "tutor")
BINDING_NORMALIZATION = "picrete-markdown-nfc-lf-v1"
ALLOWED_CERTIFICATION_STATUSES = {
    "certified",
    "certified_theory_and_preflight_calculation_only",
}
LIMITED_CERTIFICATION_STATUS = "certified_theory_and_preflight_calculation_only"
OPERATIONAL_BLOCKED_STATUS = "blocked_until_approved_protocol_binding"
DECISION_TASK_STATUSES = {"validated", "approved", "needs_review"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHEET_KINDS = {"data_table", "glossary", "conventions", "formulas", "other"}


class ReleaseError(RuntimeError):
    """A fail-closed release validation or API error."""


class Api(Protocol):
    def get(self, path: str) -> Any: ...

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any: ...

    def patch(self, path: str, payload: dict[str, Any]) -> Any: ...


class SameOriginRedirectHandler(HTTPRedirectHandler):
    """Never forward an authorization credential to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        current = urlparse(req.full_url)
        target = urlparse(urljoin(req.full_url, newurl))
        if (current.scheme, current.hostname, current.port) != (target.scheme, target.hostname, target.port):
            raise ReleaseError("Studio API attempted a cross-origin redirect; release stopped")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class StudioApi:
    def __init__(
        self, base_url: str, authorization: str, timeout: float = 30.0, ca_file: Path | None = None
    ) -> None:
        self.base_url = normalize_api_url(base_url)
        self.authorization = authorization
        self.timeout = timeout
        self.opener = build_opener(SameOriginRedirectHandler(), HTTPSHandler(context=build_ssl_context(ca_file)))

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=data,
            method=method,
            headers={
                "Authorization": self.authorization,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "picrete-content-release/1",
            },
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as error:
            detail = _safe_http_detail(error)
            raise ReleaseError(f"Studio API {method} {path} returned HTTP {error.code}: {detail}") from None
        except URLError as error:
            raise ReleaseError(f"Studio API {method} {path} is unavailable: {error.reason}") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ReleaseError(f"Studio API {method} {path} returned non-JSON data") from None

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, payload)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("PATCH", path, payload)


def _safe_http_detail(error: HTTPError) -> str:
    try:
        raw = error.read(4096)
        parsed = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "request rejected"
    detail = parsed.get("detail") if isinstance(parsed, dict) else None
    if isinstance(detail, str):
        return detail[:500]
    if isinstance(detail, list):
        messages = [item.get("msg", "invalid request") for item in detail if isinstance(item, dict)]
        return "; ".join(messages)[:500] or "request rejected"
    return "request rejected"


def normalize_api_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ReleaseError("--base-url must be an absolute URL without credentials, query or fragment")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ReleaseError("--base-url must use HTTPS (HTTP is allowed only for a loopback test server)")
    if parsed.path.rstrip("/") in {"", "/api"}:
        root = value.removesuffix("/api")
        return f"{root}/api"
    raise ReleaseError("--base-url must point to the Studio origin or its /api root")


def build_ssl_context(ca_file: Path | None = None) -> ssl.SSLContext:
    """Use an explicit or platform CA bundle without ever disabling verification."""
    if ca_file is not None:
        if not ca_file.is_file():
            raise ReleaseError("--ca-file does not point to a readable CA bundle")
        try:
            return ssl.create_default_context(cafile=str(ca_file))
        except ssl.SSLError as error:
            raise ReleaseError(f"--ca-file is not a valid CA bundle: {error.reason}") from None

    paths = ssl.get_default_verify_paths()
    candidates = [
        os.environ.get(paths.openssl_cafile_env, "") if paths.openssl_cafile_env else "",
        paths.cafile or "",
        "/etc/ssl/cert.pem",
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/opt/homebrew/etc/openssl@3/cert.pem",
        "/opt/homebrew/etc/ca-certificates/cert.pem",
    ]
    for candidate in dict.fromkeys(candidates):
        if candidate and Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def load_authorization(header_file: Path | None, environ: dict[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    if header_file is not None:
        try:
            lines = [line.strip() for line in header_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError as error:
            raise ReleaseError(f"Cannot read authorization header file: {error.strerror}") from None
        if len(lines) != 1:
            raise ReleaseError("Authorization header file must contain exactly one non-empty line")
        value = lines[0]
        if ":" in value:
            name, value = value.split(":", 1)
            if name.strip().casefold() != "authorization":
                raise ReleaseError("Authorization header file may contain only the Authorization header")
            value = value.strip()
    else:
        token = environ.get(TOKEN_ENV, "").strip()
        if not token:
            raise ReleaseError(f"Set {TOKEN_ENV} or pass --auth-header-file")
        value = token if token.casefold().startswith("bearer ") else f"Bearer {token}"
    if not value.casefold().startswith("bearer ") or not value[7:].strip():
        raise ReleaseError("Authorization value must use the Bearer scheme")
    if any(character.isspace() for character in value[7:].strip()):
        raise ReleaseError("Bearer token must not contain whitespace")
    return f"Bearer {value[7:].strip()}"


def merge_additions(current: object, additions: object, *, field: str) -> list[str]:
    if not isinstance(current, list) or not all(isinstance(item, str) for item in current):
        raise ReleaseError(f"Assistant field {field} is not a string list")
    if not isinstance(additions, list) or not all(isinstance(item, str) and item.strip() for item in additions):
        raise ReleaseError(f"Manifest field assistant_profile_patch.{field}_to_add is invalid")
    result = list(current)
    seen = set(current)
    for item in additions:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _index_unique(items: list[dict[str, Any]], field: str, *, resource: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        value = item.get(field)
        if isinstance(value, str):
            grouped.setdefault(value, []).append(item)
    duplicates = sorted(key for key, values in grouped.items() if len(values) > 1)
    if duplicates:
        raise ReleaseError(f"Ambiguous {resource} {field}: {', '.join(duplicates)}")
    return {key: values[0] for key, values in grouped.items()}


def normalize_markdown(content: object) -> str:
    """Canonicalize Markdown for a stable, reviewable release digest."""
    if not isinstance(content, str):
        raise ReleaseError("Reference sheet content_markdown must be a string")
    normalized = unicodedata.normalize("NFC", content)
    normalized = normalized.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def normalized_markdown_sha256(content: object) -> str:
    normalized = normalize_markdown(content)
    if not normalized:
        raise ReleaseError("Reference sheet content_markdown must not be empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _read_bound_sheet_content(package_dir: Path, slug: str, binding: dict[str, Any]) -> str:
    content_file = binding.get("content_file")
    expected_relative = f"sheets/{slug}.md"
    if content_file != expected_relative:
        raise ReleaseError(f"Manifest sheet {slug} must use content_file {expected_relative}")
    package_root = package_dir.resolve()
    candidate = (package_dir / content_file).resolve()
    try:
        candidate.relative_to(package_root)
    except ValueError:
        raise ReleaseError(f"Manifest sheet {slug} content_file escapes its package") from None
    try:
        content = candidate.read_text(encoding="utf-8")
    except OSError as error:
        raise ReleaseError(f"Cannot read bound sheet content {candidate}: {error.strerror}") from None
    except UnicodeDecodeError:
        raise ReleaseError(f"Bound sheet content is not valid UTF-8: {candidate}") from None
    return content


def validate_sheet_release_binding(
    *,
    manifest: dict[str, Any],
    package_dir: Path,
    slug: str,
    proposal: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    binding = proposal.get("release_binding")
    if not isinstance(binding, dict):
        raise ReleaseError(f"Manifest sheet {slug} has no release_binding")
    if binding.get("normalization") != BINDING_NORMALIZATION:
        raise ReleaseError(f"Manifest sheet {slug} uses an unsupported Markdown normalization")
    declared_digest = binding.get("content_sha256")
    if not isinstance(declared_digest, str) or SHA256_PATTERN.fullmatch(declared_digest) is None:
        raise ReleaseError(f"Manifest sheet {slug} has an invalid content_sha256")

    local_content = _read_bound_sheet_content(package_dir, slug, binding)
    local_digest = normalized_markdown_sha256(local_content)
    if local_digest != declared_digest:
        raise ReleaseError(f"Manifest sheet {slug} content_sha256 does not match its local content_file")

    document_upload = manifest.get("document_upload")
    if not isinstance(document_upload, dict):
        raise ReleaseError("Manifest document_upload is missing")
    expected_document = {
        "source_document_title": document_upload.get("title"),
        "source_effective_version": document_upload.get("effective_version"),
        "source_course_scope": document_upload.get("course_scope"),
    }
    for field, expected in expected_document.items():
        value = binding.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ReleaseError(f"Manifest sheet {slug} release_binding.{field} must be non-empty")
        if value != expected:
            raise ReleaseError(f"Manifest sheet {slug} release_binding.{field} disagrees with document_upload")
    if binding["source_effective_version"] != manifest.get("content_version"):
        raise ReleaseError(f"Manifest sheet {slug} is not bound to this exact content_version")
    return binding, local_digest


def validate_document_release_binding(manifest: dict[str, Any], package_dir: Path) -> str:
    document_upload = manifest.get("document_upload")
    if not isinstance(document_upload, dict):
        raise ReleaseError("Manifest document_upload is missing")
    if document_upload.get("file") != "grounding.md":
        raise ReleaseError(f"{package_dir.name}: curated document file must be grounding.md")
    declared_digest = document_upload.get("content_sha256")
    if not isinstance(declared_digest, str) or SHA256_PATTERN.fullmatch(declared_digest) is None:
        raise ReleaseError(f"{package_dir.name}: document_upload.content_sha256 is invalid")
    grounding_path = (package_dir / "grounding.md").resolve()
    try:
        grounding_path.relative_to(package_dir.resolve())
        content = grounding_path.read_text(encoding="utf-8")
    except ValueError:
        raise ReleaseError(f"{package_dir.name}: grounding.md escapes its package") from None
    except OSError as error:
        raise ReleaseError(f"Cannot read curated grounding {grounding_path}: {error.strerror}") from None
    except UnicodeDecodeError:
        raise ReleaseError(f"Curated grounding is not valid UTF-8: {grounding_path}") from None
    local_digest = normalized_markdown_sha256(content)
    if local_digest != declared_digest:
        raise ReleaseError(f"{package_dir.name}: grounding.md does not match document_upload.content_sha256")
    return local_digest


def resolve_reference_sheets(
    manifest: dict[str, Any],
    package_dir: Path,
    sheets: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> dict[str, str]:
    proposals = manifest.get("reference_sheet_proposals")
    templates = manifest.get("task_templates")
    if not isinstance(proposals, list) or not isinstance(templates, list):
        raise ReleaseError("Manifest must contain reference_sheet_proposals and task_templates lists")
    by_slug = _index_unique(proposals, "slug", resource="manifest sheet")
    by_title = _index_unique(sheets, "title", resource="Studio sheet")
    documents_by_id = _index_unique(documents, "id", resource="Studio document")
    referenced_slugs: set[str] = set()
    for template in templates:
        slugs = template.get("reference_sheet_slugs")
        if not isinstance(slugs, list) or not all(isinstance(slug, str) for slug in slugs):
            raise ReleaseError(f"Template {template.get('slug', '<unknown>')} has invalid reference_sheet_slugs")
        referenced_slugs.update(slugs)
    if referenced_slugs != set(by_slug):
        missing = sorted(referenced_slugs - set(by_slug))
        unused = sorted(set(by_slug) - referenced_slugs)
        details = []
        if missing:
            details.append(f"unknown: {', '.join(missing)}")
        if unused:
            details.append(f"unused: {', '.join(unused)}")
        raise ReleaseError(f"Manifest sheet/template closure is incomplete ({'; '.join(details)})")

    resolved: dict[str, str] = {}
    for slug in sorted(referenced_slugs):
        proposal = by_slug.get(slug)
        if proposal is None:
            raise ReleaseError(f"Template refers to unknown manifest sheet slug: {slug}")
        title = proposal.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ReleaseError(f"Manifest sheet {slug} has no exact title")
        kind = proposal.get("kind")
        ord_value = proposal.get("ord")
        if kind not in SHEET_KINDS:
            raise ReleaseError(f"Manifest sheet {slug} has an invalid kind")
        if not isinstance(ord_value, int) or isinstance(ord_value, bool):
            raise ReleaseError(f"Manifest sheet {slug} has an invalid ord")
        if proposal.get("visibility") != "student" or proposal.get("is_canonical") is not True:
            raise ReleaseError(f"Manifest sheet {slug} must be canonical and student-visible")
        binding, local_digest = validate_sheet_release_binding(
            manifest=manifest,
            package_dir=package_dir,
            slug=slug,
            proposal=proposal,
        )
        sheet = by_title.get(title)
        if sheet is None:
            raise ReleaseError(f"Required Studio sheet is missing: {title}")
        if sheet.get("kind") != kind or sheet.get("ord") != ord_value:
            raise ReleaseError(f"Required Studio sheet metadata does not match the certified release: {title}")
        if sheet.get("visibility") != "student" or sheet.get("is_canonical") is not True:
            raise ReleaseError(f"Required Studio sheet is not canonical and student-visible: {title}")
        if normalized_markdown_sha256(sheet.get("content_markdown")) != local_digest:
            raise ReleaseError(f"Required Studio sheet content does not match the certified digest: {title}")
        document_id = sheet.get("source_document_id")
        if not isinstance(document_id, str) or not document_id:
            raise ReleaseError(f"Required Studio sheet is not bound to a source document: {title}")
        document = documents_by_id.get(document_id)
        if document is None:
            raise ReleaseError(f"Required Studio sheet has a dangling source binding: {title}")
        if document.get("authority") not in TRUSTED_AUTHORITIES:
            raise ReleaseError(f"Required Studio sheet is bound to an untrusted source: {title}")
        if document.get("status") != "parsed":
            raise ReleaseError(f"Required Studio sheet source is not parsed: {title}")
        if sheet.get("visibility") != "student" or document.get("visibility") != "student":
            raise ReleaseError(f"Required Studio sheet or its source is not student-visible: {title}")
        exact_document_fields = {
            "title": binding["source_document_title"],
            "effective_version": binding["source_effective_version"],
            "course_scope": binding["source_course_scope"],
        }
        if any(document.get(field) != expected for field, expected in exact_document_fields.items()):
            raise ReleaseError(f"Required Studio sheet source version binding does not match: {title}")
        resolved[slug] = str(sheet["id"])
    return resolved


def build_template_payloads(manifest: dict[str, Any], sheet_ids: dict[str, str]) -> list[dict[str, Any]]:
    templates = manifest.get("task_templates")
    if not isinstance(templates, list):
        raise ReleaseError("Manifest task_templates must be a list")
    names: set[str] = set()
    payloads: list[dict[str, Any]] = []
    for template in templates:
        payload = template.get("payload")
        rubric = template.get("recommended_rubric")
        slugs = template.get("reference_sheet_slugs")
        if not isinstance(payload, dict) or not isinstance(rubric, list) or not isinstance(slugs, list):
            raise ReleaseError(f"Template {template.get('slug', '<unknown>')} is incomplete")
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ReleaseError(f"Template {template.get('slug', '<unknown>')} has no name")
        if name in names:
            raise ReleaseError(f"Manifest contains duplicate task template name: {name}")
        names.add(name)
        try:
            references = [sheet_ids[slug] for slug in slugs]
        except KeyError as error:
            raise ReleaseError(f"Template {name} refers to unresolved sheet slug: {error.args[0]}") from None
        desired = dict(payload)
        desired["reference_sheet_ids"] = references
        desired["rubric"] = rubric
        payloads.append(desired)
    return payloads


def _desired_pipeline(model_entry_id: str) -> dict[str, Any]:
    return {
        "name": PIPELINE_NAME,
        "description": PIPELINE_DESCRIPTION,
        "steps": [
            {"type": "ocr", "config": {}},
            {"type": "grade", "config": {"model_entry_id": model_entry_id, "role": "primary"}},
            {"type": "grade", "config": {"model_entry_id": model_entry_id, "role": "auditor"}},
            {"type": "consensus", "config": {"disagreement_threshold_pct": 20}},
        ],
    }


def _changed(current: dict[str, Any], desired: dict[str, Any]) -> bool:
    return any(current.get(key) != value for key, value in desired.items())


def _validate_manifest(manifest: dict[str, Any], package_name: str) -> None:
    if manifest.get("schema_version") != "picrete-content-manifest-v1":
        raise ReleaseError(f"{package_name}: unsupported manifest schema")
    if not isinstance(manifest.get("content_version"), str) or not manifest["content_version"].strip():
        raise ReleaseError(f"{package_name}: content_version is missing")
    certification = manifest.get("source_and_blueprint_certification")
    if not isinstance(certification, dict) or certification.get("per_task_teacher_approval") is not False:
        raise ReleaseError(f"{package_name}: manifest is not certified for automatic task admission")
    certification_status = certification.get("certification_status")
    if certification_status not in ALLOWED_CERTIFICATION_STATUSES:
        raise ReleaseError(f"{package_name}: certification_status does not permit an automatic release")
    if certification_status == "certified" and str(manifest.get("binding_status") or "").startswith("pending"):
        raise ReleaseError(f"{package_name}: a fully certified release cannot have a pending course binding")
    if (
        certification_status == LIMITED_CERTIFICATION_STATUS
        and certification.get("operational_procedure_status") != OPERATIONAL_BLOCKED_STATUS
    ):
        raise ReleaseError(f"{package_name}: limited certification must keep operational procedures blocked")
    document_upload = manifest.get("document_upload")
    if not isinstance(document_upload, dict):
        raise ReleaseError(f"{package_name}: document_upload is missing")
    for field in ("title", "effective_version", "course_scope"):
        if not isinstance(document_upload.get(field), str) or not document_upload[field].strip():
            raise ReleaseError(f"{package_name}: document_upload.{field} must be non-empty")
    if document_upload["effective_version"] != manifest["content_version"]:
        raise ReleaseError(f"{package_name}: document_upload.effective_version must equal content_version")
    digest = document_upload.get("content_sha256")
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        raise ReleaseError(f"{package_name}: document_upload.content_sha256 is invalid")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReleaseError(f"Cannot read {path}: {error.strerror}") from None
    except json.JSONDecodeError as error:
        raise ReleaseError(f"Invalid JSON in {path}: line {error.lineno}, column {error.colno}") from None
    if not isinstance(value, dict):
        raise ReleaseError(f"{path} must contain a JSON object")
    return value


def _read_prompts(package_dir: Path) -> dict[str, str]:
    prompts: dict[str, str] = {}
    for role in PROMPT_ROLES:
        path = package_dir / "prompts" / f"{role}.txt"
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ReleaseError(f"Cannot read prompt {path}: {error.strerror}") from None
        if not content.strip():
            raise ReleaseError(f"Prompt file is empty: {path}")
        prompts[role] = content
    return prompts


def _verify_model(providers: object, model_entry_id: str) -> None:
    if not isinstance(providers, list):
        raise ReleaseError("Studio /providers response is invalid")
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for provider in providers:
        if not isinstance(provider, dict) or not isinstance(provider.get("models"), list):
            continue
        for model in provider["models"]:
            if isinstance(model, dict) and model.get("id") == model_entry_id:
                matches.append((provider, model))
    if len(matches) != 1:
        raise ReleaseError(f"DeepSeek model entry {model_entry_id} must resolve exactly once in production providers")
    provider, model = matches[0]
    if provider.get("purpose") != "production" or provider.get("enabled") is not True:
        raise ReleaseError(f"DeepSeek model entry {model_entry_id} is not on an enabled production provider")
    if model.get("enabled") is not True or "deepseek" not in str(model.get("family", "")).casefold():
        raise ReleaseError(f"Model entry {model_entry_id} is not an enabled DeepSeek-family model")


def _select_prompt(prompts: list[dict[str, Any]], role: str, content: str) -> dict[str, Any] | None:
    exact = [
        prompt
        for prompt in prompts
        if prompt.get("role") == role
        and prompt.get("system_prompt") == content
        and str(prompt.get("target_family", "")).casefold() == "deepseek"
    ]
    if not exact:
        same_content = [
            prompt for prompt in prompts if prompt.get("role") == role and prompt.get("system_prompt") == content
        ]
        if same_content:
            raise ReleaseError(f"Exact {role} prompt already exists but is not marked for the DeepSeek family")
        return None
    active = [prompt for prompt in exact if prompt.get("status") == "active"]
    candidates = active or exact
    return max(candidates, key=lambda prompt: (int(prompt.get("version", 0)), str(prompt.get("id", ""))))


@dataclass
class PackagePlan:
    package_name: str
    assistant_id: str
    content_version: str
    assistant_patch: dict[str, Any]
    templates_create: list[dict[str, Any]]
    templates_update: list[tuple[str, dict[str, Any]]]
    prompts: list[tuple[str, str, str | None, bool]]
    pipeline_create: dict[str, Any] | None
    pipeline_update: tuple[str, dict[str, Any]] | None
    defaults_patch: dict[str, str]
    resolved_sheet_count: int
    tasks_with_evidence_invalidated: int
    approved_tasks_invalidated: int

    def summary(self) -> dict[str, Any]:
        prompt_install = sum(existing_id is None for _, _, existing_id, _ in self.prompts)
        prompt_activate = sum(needs_activation for _, _, _, needs_activation in self.prompts)
        return {
            "assistant_id": self.assistant_id,
            "content_version": self.content_version,
            "profile_update": bool(self.assistant_patch),
            "resolved_sheets": self.resolved_sheet_count,
            "templates_create": len(self.templates_create),
            "templates_update": len(self.templates_update),
            "prompts_install": prompt_install,
            "prompts_activate": prompt_activate,
            "pipeline_create": self.pipeline_create is not None,
            "pipeline_update": self.pipeline_update is not None,
            "defaults_update": bool(self.defaults_patch),
            "tasks_with_evidence_invalidated": self.tasks_with_evidence_invalidated,
            "approved_tasks_invalidated": self.approved_tasks_invalidated,
        }


def preflight_package(
    api: Api, package_dir: Path, assistant_id: str, model_entry_id: str
) -> PackagePlan:
    package_name = package_dir.name
    manifest = _read_json(package_dir / "import-manifest.json")
    _validate_manifest(manifest, package_name)
    document_digest = validate_document_release_binding(manifest, package_dir)
    prompt_files = _read_prompts(package_dir)

    assistant = api.get(f"assistants/{assistant_id}")
    sheets = api.get(f"assistants/{assistant_id}/sheets")
    documents = api.get(f"assistants/{assistant_id}/kb/documents")
    existing_templates = api.get(f"assistants/{assistant_id}/templates")
    existing_prompts = api.get(f"assistants/{assistant_id}/prompts")
    existing_pipelines = api.get(f"assistants/{assistant_id}/pipelines")
    existing_tasks = api.get(f"assistants/{assistant_id}/tasks")
    if not isinstance(assistant, dict):
        raise ReleaseError(f"{package_name}: assistant response is invalid")
    list_responses = (sheets, documents, existing_templates, existing_prompts, existing_pipelines, existing_tasks)
    if not all(isinstance(response, list) for response in list_responses):
        raise ReleaseError(f"{package_name}: a Studio collection response is invalid")

    document_upload = manifest["document_upload"]
    exact_documents = [
        document
        for document in documents
        if isinstance(document, dict)
        and all(
            document.get(field) == document_upload[field]
            for field in ("title", "effective_version", "course_scope")
        )
    ]
    if len(exact_documents) != 1:
        raise ReleaseError(f"{package_name}: exact curated document binding must resolve exactly once")
    exact_document = exact_documents[0]
    exact_document_id = exact_document.get("id")
    if not isinstance(exact_document_id, str) or not exact_document_id:
        raise ReleaseError(f"{package_name}: exact curated document has no ID")
    document_detail = api.get(f"assistants/{assistant_id}/kb/documents/{exact_document_id}")
    if not isinstance(document_detail, dict):
        raise ReleaseError(f"{package_name}: exact curated document detail is invalid")
    if document_detail.get("status") != "parsed":
        raise ReleaseError(f"{package_name}: exact curated document is not parsed")
    if (
        document_detail.get("authority") not in TRUSTED_AUTHORITIES
        or document_detail.get("visibility") != "student"
    ):
        raise ReleaseError(f"{package_name}: exact curated document is not trusted/student-visible")
    if normalized_markdown_sha256(document_detail.get("markdown")) != document_digest:
        raise ReleaseError(f"{package_name}: exact curated document content digest does not match grounding.md")

    sheet_ids = resolve_reference_sheets(manifest, package_dir, sheets, documents)
    desired_templates = build_template_payloads(manifest, sheet_ids)
    templates_by_name = _index_unique(existing_templates, "name", resource="Studio task template")
    pipelines_by_name = _index_unique(existing_pipelines, "name", resource="Studio pipeline")

    profile = manifest.get("assistant_profile_patch")
    if not isinstance(profile, dict):
        raise ReleaseError(f"{package_name}: assistant_profile_patch is missing")
    desired_topics = merge_additions(assistant.get("topics"), profile.get("topics_to_add"), field="topics")
    desired_nuances = merge_additions(assistant.get("nuances"), profile.get("nuances_to_add"), field="nuances")
    assistant_patch: dict[str, Any] = {}
    if assistant.get("topics") != desired_topics:
        assistant_patch["topics"] = desired_topics
    if assistant.get("nuances") != desired_nuances:
        assistant_patch["nuances"] = desired_nuances

    templates_create: list[dict[str, Any]] = []
    templates_update: list[tuple[str, dict[str, Any]]] = []
    for desired in desired_templates:
        current = templates_by_name.get(desired["name"])
        if current is None:
            templates_create.append(desired)
        elif _changed(current, desired):
            templates_update.append((str(current["id"]), desired))

    prompt_plans: list[tuple[str, str, str | None, bool]] = []
    for role in PROMPT_ROLES:
        selected = _select_prompt(existing_prompts, role, prompt_files[role])
        existing_id = None if selected is None else str(selected["id"])
        needs_activation = selected is None or selected.get("status") != "active"
        prompt_plans.append((role, prompt_files[role], existing_id, needs_activation))

    desired_pipeline = _desired_pipeline(model_entry_id)
    current_pipeline = pipelines_by_name.get(PIPELINE_NAME)
    pipeline_create = desired_pipeline if current_pipeline is None else None
    pipeline_update = None
    if current_pipeline is not None and _changed(current_pipeline, desired_pipeline):
        pipeline_update = (str(current_pipeline["id"]), desired_pipeline)

    defaults_patch: dict[str, str] = {}
    if assistant.get("default_generator_model_id") != model_entry_id:
        defaults_patch["default_generator_model_id"] = model_entry_id
    if assistant.get("default_grader_model_id") != model_entry_id:
        defaults_patch["default_grader_model_id"] = model_entry_id

    updated_template_ids = {template_id for template_id, _ in templates_update}
    affected_tasks = [
        task
        for task in existing_tasks
        if isinstance(task, dict)
        and task.get("status") in DECISION_TASK_STATUSES
        and (bool(assistant_patch) or task.get("template_id") in updated_template_ids)
    ]

    return PackagePlan(
        package_name=package_name,
        assistant_id=assistant_id,
        content_version=str(manifest["content_version"]),
        assistant_patch=assistant_patch,
        templates_create=templates_create,
        templates_update=templates_update,
        prompts=prompt_plans,
        pipeline_create=pipeline_create,
        pipeline_update=pipeline_update,
        defaults_patch=defaults_patch,
        resolved_sheet_count=len(sheet_ids),
        tasks_with_evidence_invalidated=len(affected_tasks),
        approved_tasks_invalidated=sum(
            task.get("approved") is True or task.get("status") == "approved" for task in affected_tasks
        ),
    )


def apply_package(api: Api, plan: PackagePlan) -> None:
    prefix = f"assistants/{plan.assistant_id}"
    if plan.assistant_patch:
        api.patch(prefix, plan.assistant_patch)
    for payload in plan.templates_create:
        api.post(f"{prefix}/templates", payload)
    for template_id, payload in plan.templates_update:
        api.patch(f"{prefix}/templates/{template_id}", payload)
    if plan.defaults_patch:
        api.patch(prefix, plan.defaults_patch)

    prompt_ids: dict[str, str] = {}
    for role, content, existing_id, _ in plan.prompts:
        if existing_id is not None:
            prompt_ids[role] = existing_id
            continue
        created = api.post(
            f"{prefix}/prompts",
            {
                "role": role,
                "system_prompt": content,
                "notes": f"Curated content release {plan.content_version}",
                "target_family": "deepseek",
            },
        )
        if not isinstance(created, dict) or not isinstance(created.get("id"), str):
            raise ReleaseError(f"Studio did not return an ID for the new {role} prompt")
        prompt_ids[role] = created["id"]

    if plan.pipeline_create is not None:
        api.post(f"{prefix}/pipelines", plan.pipeline_create)
    if plan.pipeline_update is not None:
        pipeline_id, payload = plan.pipeline_update
        api.patch(f"{prefix}/pipelines/{pipeline_id}", payload)

    for role, _, _, needs_activation in plan.prompts:
        if needs_activation:
            api.post(f"{prefix}/prompts/{prompt_ids[role]}/activate")


def _parse_overrides(values: list[str], defaults: dict[str, str]) -> dict[str, str]:
    result = dict(defaults)
    for value in values:
        package, separator, assistant_id = value.partition("=")
        if not separator or package not in defaults or not assistant_id.strip():
            choices = ", ".join(sorted(defaults))
            raise ReleaseError(f"--assistant-map must be PACKAGE=ID, where PACKAGE is one of: {choices}")
        result[package] = assistant_id.strip()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely install certified Picrete chemistry content")
    parser.add_argument("--base-url", required=True, help="Studio origin, for example https://dev.picrete.com")
    parser.add_argument("--auth-header-file", type=Path, help="File containing Authorization: Bearer …")
    parser.add_argument(
        "--package",
        dest="packages",
        action="append",
        choices=sorted(DEFAULT_ASSISTANT_IDS),
        help="Package to release; repeat as needed (default: all)",
    )
    parser.add_argument(
        "--assistant-map",
        action="append",
        default=[],
        metavar="PACKAGE=ID",
        help="Override the built-in package-to-assistant mapping",
    )
    parser.add_argument(
        "--deepseek-model-entry-id",
        default=DEFAULT_DEEPSEEK_MODEL_ENTRY_ID,
        help="Decision-grade DeepSeek model entry used for generation and grading",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds")
    parser.add_argument("--ca-file", type=Path, help="Optional PEM CA bundle; TLS verification is always enabled")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply the preflighted changes")
    mode.add_argument("--dry-run", action="store_true", help="Explicitly select the default read-only mode")
    return parser


def run(argv: list[str] | None = None, *, api: Api | None = None, content_root: Path | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if not 0 < args.timeout <= 300:
        raise ReleaseError("--timeout must be greater than 0 and at most 300 seconds")
    authorization = load_authorization(args.auth_header_file)
    api = api or StudioApi(args.base_url, authorization, args.timeout, args.ca_file)
    mappings = _parse_overrides(args.assistant_map, DEFAULT_ASSISTANT_IDS)
    selected = list(dict.fromkeys(args.packages or DEFAULT_ASSISTANT_IDS))
    root = content_root or Path(__file__).resolve().parent / "content"

    _verify_model(api.get("providers"), args.deepseek_model_entry_id)
    plans = [
        preflight_package(api, root / package_name, mappings[package_name], args.deepseek_model_entry_id)
        for package_name in selected
    ]
    if args.apply:
        for plan in plans:
            apply_package(api, plan)
        verification = [
            preflight_package(api, root / package_name, mappings[package_name], args.deepseek_model_entry_id)
            for package_name in selected
        ]
        remaining = {
            plan.package_name: plan.summary()
            for plan in verification
            if any(
                (
                    plan.assistant_patch,
                    plan.templates_create,
                    plan.templates_update,
                    any(existing_id is None or needs_activation for _, _, existing_id, needs_activation in plan.prompts),
                    plan.pipeline_create,
                    plan.pipeline_update,
                    plan.defaults_patch,
                )
            )
        }
        if remaining:
            raise ReleaseError(f"Content release did not converge: {json.dumps(remaining, ensure_ascii=False)}")

    package_summaries = {plan.package_name: plan.summary() for plan in plans}
    mutation_count = sum(
        int(value)
        for summary in package_summaries.values()
        for key, value in summary.items()
        if key
        in {
            "profile_update",
            "templates_create",
            "templates_update",
            "prompts_install",
            "prompts_activate",
            "pipeline_create",
            "pipeline_update",
            "defaults_update",
        }
    )
    return {
        "ok": True,
        "mode": "apply" if args.apply else "dry-run",
        "packages": package_summaries,
        "planned_mutations": mutation_count,
    }


def main() -> int:
    try:
        result = run()
    except ReleaseError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
