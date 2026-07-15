#!/usr/bin/env python3
"""Synchronize cryptographically bound curated documents and reference sheets.

The command is read-only by default.  ``--apply`` must be supplied explicitly.
It never deletes old documents or sheets: a new exact-version Markdown document
is uploaded when needed, then the existing exact-title sheets are patched to the
locally certified content and source binding.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request

import apply_content_release as release


class SyncApi(Protocol):
    def get(self, path: str) -> Any: ...

    def patch(self, path: str, payload: dict[str, Any]) -> Any: ...

    def upload_document(
        self,
        assistant_id: str,
        *,
        file_path: Path,
        fields: dict[str, str],
    ) -> Any: ...


class HttpSyncApi:
    def __init__(self, api: release.StudioApi) -> None:
        self.api = api

    def get(self, path: str) -> Any:
        return self.api.get(path)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return self.api.patch(path, payload)

    def upload_document(
        self,
        assistant_id: str,
        *,
        file_path: Path,
        fields: dict[str, str],
    ) -> Any:
        boundary = f"picrete-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )
        filename = file_path.name.replace('"', "")
        mime = mimetypes.guess_type(filename)[0] or "text/markdown"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
                f"Content-Type: {mime}\r\n\r\n".encode(),
                file_path.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        request = Request(
            f"{self.api.base_url}/assistants/{assistant_id}/kb/documents",
            data=b"".join(chunks),
            method="POST",
            headers={
                "Authorization": self.api.authorization,
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "picrete-curated-source-sync/1",
            },
        )
        try:
            with self.api.opener.open(request, timeout=self.api.timeout) as response:
                raw = response.read()
        except HTTPError as error:
            detail = release._safe_http_detail(error)
            raise release.ReleaseError(
                f"Studio API POST curated document returned HTTP {error.code}: {detail}"
            ) from None
        except URLError as error:
            raise release.ReleaseError(f"Studio curated document upload is unavailable: {error.reason}") from None
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise release.ReleaseError("Studio curated document upload returned non-JSON data") from None
        return result


@dataclass(frozen=True)
class SheetSpec:
    slug: str
    title: str
    payload: dict[str, Any]
    content_sha256: str


@dataclass(frozen=True)
class PackageSpec:
    package_name: str
    package_dir: Path
    assistant_id: str
    content_version: str
    document_fields: dict[str, str]
    grounding_path: Path
    grounding_sha256: str
    sheets: tuple[SheetSpec, ...]


@dataclass
class SyncPlan:
    spec: PackageSpec
    document_id: str | None
    upload_document: bool
    wait_for_document: bool
    sheet_updates: list[tuple[str, dict[str, Any]]]
    tasks_with_evidence_invalidated: int
    approved_tasks_invalidated: int

    def summary(self) -> dict[str, Any]:
        return {
            "assistant_id": self.spec.assistant_id,
            "content_version": self.spec.content_version,
            "document_upload": self.upload_document,
            "document_wait": self.wait_for_document,
            "sheets_update": len(self.sheet_updates),
            "tasks_with_evidence_invalidated": self.tasks_with_evidence_invalidated,
            "approved_tasks_invalidated": self.approved_tasks_invalidated,
        }


def load_package_spec(package_dir: Path, assistant_id: str) -> PackageSpec:
    manifest = release._read_json(package_dir / "import-manifest.json")
    release._validate_manifest(manifest, package_dir.name)
    document_upload = manifest["document_upload"]
    if document_upload.get("authority") not in release.TRUSTED_AUTHORITIES:
        raise release.ReleaseError(f"{package_dir.name}: curated document authority is not trusted")
    if document_upload.get("visibility") != "student":
        raise release.ReleaseError(f"{package_dir.name}: curated document must be student-visible")

    grounding_sha256 = release.validate_document_release_binding(manifest, package_dir)
    grounding_path = (package_dir / "grounding.md").resolve()
    try:
        grounding_path.relative_to(package_dir.resolve())
        grounding_content = grounding_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise release.ReleaseError(f"Cannot read curated grounding {grounding_path}: {error}") from None
    normalized_grounding = release.normalize_markdown(grounding_content)

    proposals = manifest.get("reference_sheet_proposals")
    templates = manifest.get("task_templates")
    if not isinstance(proposals, list) or not isinstance(templates, list):
        raise release.ReleaseError(f"{package_dir.name}: sheet/template lists are missing")
    proposals_by_slug = release._index_unique(proposals, "slug", resource="manifest sheet")
    referenced_slugs: set[str] = set()
    for template in templates:
        slugs = template.get("reference_sheet_slugs") if isinstance(template, dict) else None
        if not isinstance(slugs, list) or not all(isinstance(slug, str) for slug in slugs):
            raise release.ReleaseError(f"{package_dir.name}: a template has invalid reference_sheet_slugs")
        referenced_slugs.update(slugs)
    if referenced_slugs != set(proposals_by_slug):
        raise release.ReleaseError(f"{package_dir.name}: sheet/template closure is incomplete")

    sheet_specs: list[SheetSpec] = []
    for slug in sorted(proposals_by_slug):
        proposal = proposals_by_slug[slug]
        binding, digest = release.validate_sheet_release_binding(
            manifest=manifest,
            package_dir=package_dir,
            slug=slug,
            proposal=proposal,
        )
        if proposal.get("kind") not in release.SHEET_KINDS:
            raise release.ReleaseError(f"Manifest sheet {slug} has an invalid kind")
        if not isinstance(proposal.get("ord"), int) or isinstance(proposal.get("ord"), bool):
            raise release.ReleaseError(f"Manifest sheet {slug} has an invalid ord")
        if proposal.get("visibility") != "student" or proposal.get("is_canonical") is not True:
            raise release.ReleaseError(f"Manifest sheet {slug} must be canonical and student-visible")
        title = proposal.get("title")
        if not isinstance(title, str) or not title.strip():
            raise release.ReleaseError(f"Manifest sheet {slug} has no exact title")
        content = release.normalize_markdown(
            release._read_bound_sheet_content(package_dir, slug, binding)
        )
        anchor = proposal.get("content_anchor")
        if not isinstance(anchor, str) or not anchor.strip() or not content.startswith(f"## {anchor}."):
            raise release.ReleaseError(f"Manifest sheet {slug} does not start at its declared content_anchor")
        if content not in normalized_grounding:
            raise release.ReleaseError(f"Manifest sheet {slug} is not an exact section of grounding.md")
        sheet_specs.append(
            SheetSpec(
                slug=slug,
                title=title,
                content_sha256=digest,
                payload={
                    "kind": proposal["kind"],
                    "description": str(proposal.get("description") or ""),
                    "content_markdown": content,
                    "visibility": "student",
                    "is_canonical": True,
                    "ord": proposal["ord"],
                },
            )
        )

    return PackageSpec(
        package_name=package_dir.name,
        package_dir=package_dir,
        assistant_id=assistant_id,
        content_version=str(manifest["content_version"]),
        document_fields={
            "title": str(document_upload["title"]),
            "doc_type": str(document_upload.get("doc_type") or "methodical"),
            "authority": str(document_upload["authority"]),
            "visibility": "student",
            "course_scope": str(document_upload["course_scope"]),
            "effective_version": str(document_upload["effective_version"]),
            "analyze": "false" if document_upload.get("analyze") is False else "true",
        },
        grounding_path=grounding_path,
        grounding_sha256=grounding_sha256,
        sheets=tuple(sheet_specs),
    )


def _exact_document_matches(document: dict[str, Any], fields: dict[str, str]) -> bool:
    return all(document.get(field) == fields[field] for field in ("title", "effective_version", "course_scope"))


def _verify_document_detail(spec: PackageSpec, detail: object) -> None:
    if not isinstance(detail, dict):
        raise release.ReleaseError(f"{spec.package_name}: curated document detail is invalid")
    if detail.get("status") != "parsed":
        raise release.ReleaseError(f"{spec.package_name}: curated document did not reach parsed status")
    if detail.get("authority") != spec.document_fields["authority"] or detail.get("visibility") != "student":
        raise release.ReleaseError(f"{spec.package_name}: curated document trust metadata changed")
    if not _exact_document_matches(detail, spec.document_fields):
        raise release.ReleaseError(f"{spec.package_name}: curated document version binding changed")
    if release.normalized_markdown_sha256(detail.get("markdown")) != spec.grounding_sha256:
        raise release.ReleaseError(f"{spec.package_name}: curated document content digest does not match grounding.md")


def preflight_package(api: SyncApi, spec: PackageSpec) -> SyncPlan:
    prefix = f"assistants/{spec.assistant_id}"
    documents = api.get(f"{prefix}/kb/documents")
    sheets = api.get(f"{prefix}/sheets")
    tasks = api.get(f"{prefix}/tasks")
    if not isinstance(documents, list) or not isinstance(sheets, list) or not isinstance(tasks, list):
        raise release.ReleaseError(f"{spec.package_name}: a Studio collection response is invalid")

    exact_documents = [
        document
        for document in documents
        if isinstance(document, dict) and _exact_document_matches(document, spec.document_fields)
    ]
    if len(exact_documents) > 1:
        raise release.ReleaseError(f"{spec.package_name}: exact curated document binding is ambiguous")
    document_id: str | None = None
    upload_document = not exact_documents
    wait_for_document = False
    if exact_documents:
        document = exact_documents[0]
        document_id_value = document.get("id")
        if not isinstance(document_id_value, str) or not document_id_value:
            raise release.ReleaseError(f"{spec.package_name}: exact curated document has no ID")
        document_id = document_id_value
        if document.get("authority") != spec.document_fields["authority"] or document.get("visibility") != "student":
            raise release.ReleaseError(f"{spec.package_name}: exact curated document is not trusted/student-visible")
        status = document.get("status")
        if status == "parsed":
            _verify_document_detail(spec, api.get(f"{prefix}/kb/documents/{document_id}"))
        elif status in {"uploaded", "parsing"}:
            wait_for_document = True
        else:
            raise release.ReleaseError(f"{spec.package_name}: exact curated document is in failed state {status!r}")

    sheets_by_title = release._index_unique(sheets, "title", resource="Studio sheet")
    updates: list[tuple[str, dict[str, Any]]] = []
    for sheet_spec in spec.sheets:
        current = sheets_by_title.get(sheet_spec.title)
        if current is None:
            raise release.ReleaseError(f"{spec.package_name}: required existing sheet is missing: {sheet_spec.title}")
        sheet_id = current.get("id")
        if not isinstance(sheet_id, str) or not sheet_id:
            raise release.ReleaseError(f"{spec.package_name}: required sheet has no ID: {sheet_spec.title}")
        desired = dict(sheet_spec.payload)
        desired["source_document_id"] = document_id
        changed = upload_document or any(current.get(key) != value for key, value in desired.items())
        if changed:
            updates.append((sheet_id, desired))

    decisions = [
        task
        for task in tasks
        if isinstance(task, dict) and task.get("status") in release.DECISION_TASK_STATUSES
    ]
    return SyncPlan(
        spec=spec,
        document_id=document_id,
        upload_document=upload_document,
        wait_for_document=wait_for_document,
        sheet_updates=updates,
        tasks_with_evidence_invalidated=len(decisions) if updates else 0,
        approved_tasks_invalidated=(
            sum(task.get("approved") is True or task.get("status") == "approved" for task in decisions)
            if updates
            else 0
        ),
    )


def wait_for_parsed_document(
    api: SyncApi,
    spec: PackageSpec,
    document_id: str,
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    path = f"assistants/{spec.assistant_id}/kb/documents/{document_id}"
    while True:
        detail = api.get(path)
        if not isinstance(detail, dict):
            raise release.ReleaseError(f"{spec.package_name}: curated document detail is invalid")
        status = detail.get("status")
        if status == "parsed":
            _verify_document_detail(spec, detail)
            return
        if status == "failed":
            raise release.ReleaseError(
                f"{spec.package_name}: curated document parsing failed: {str(detail.get('error') or '')[:300]}"
            )
        if time.monotonic() >= deadline:
            raise release.ReleaseError(f"{spec.package_name}: timed out waiting for curated document parsing")
        time.sleep(0.5)


def apply_package(api: SyncApi, plan: SyncPlan, *, timeout: float) -> None:
    spec = plan.spec
    prefix = f"assistants/{spec.assistant_id}"
    document_id = plan.document_id
    if plan.upload_document:
        created = api.upload_document(
            spec.assistant_id,
            file_path=spec.grounding_path,
            fields=spec.document_fields,
        )
        if not isinstance(created, dict) or not isinstance(created.get("id"), str):
            raise release.ReleaseError(f"{spec.package_name}: Studio did not return a curated document ID")
        document_id = created["id"]
    if document_id is None:
        raise release.ReleaseError(f"{spec.package_name}: no curated document ID is available")
    if plan.upload_document or plan.wait_for_document:
        wait_for_parsed_document(api, spec, document_id, timeout=timeout)

    for sheet_id, payload in plan.sheet_updates:
        desired = dict(payload)
        desired["source_document_id"] = document_id
        updated = api.patch(f"{prefix}/sheets/{sheet_id}", desired)
        if not isinstance(updated, dict) or updated.get("source_document_id") != document_id:
            raise release.ReleaseError(f"{spec.package_name}: Studio did not persist a sheet source binding")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronize certified Picrete curated sources")
    parser.add_argument("--base-url", required=True, help="Studio origin, for example https://dev.picrete.com")
    parser.add_argument("--auth-header-file", type=Path, help="File containing Authorization: Bearer …")
    parser.add_argument(
        "--package",
        dest="packages",
        action="append",
        choices=sorted(release.DEFAULT_ASSISTANT_IDS),
        help="Package to synchronize; repeat as needed (default: all)",
    )
    parser.add_argument("--assistant-map", action="append", default=[], metavar="PACKAGE=ID")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-request and parse wait timeout")
    parser.add_argument("--ca-file", type=Path, help="Optional PEM CA bundle; TLS verification remains enabled")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Upload/rebind the preflighted curated sources")
    mode.add_argument("--dry-run", action="store_true", help="Explicitly select the default read-only mode")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    api: SyncApi | None = None,
    content_root: Path | None = None,
) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if not 0 < args.timeout <= 300:
        raise release.ReleaseError("--timeout must be greater than 0 and at most 300 seconds")
    authorization = release.load_authorization(args.auth_header_file)
    if api is None:
        api = HttpSyncApi(
            release.StudioApi(args.base_url, authorization, timeout=args.timeout, ca_file=args.ca_file)
        )
    mappings = release._parse_overrides(args.assistant_map, release.DEFAULT_ASSISTANT_IDS)
    selected = list(dict.fromkeys(args.packages or release.DEFAULT_ASSISTANT_IDS))
    root = content_root or Path(__file__).resolve().parent / "content"

    # Validate every local file and declared digest before any remote call or write.
    specs = [load_package_spec(root / package_name, mappings[package_name]) for package_name in selected]
    # Preflight every remote package before the first mutation.
    plans = [preflight_package(api, spec) for spec in specs]
    if args.apply:
        for plan in plans:
            apply_package(api, plan, timeout=args.timeout)
        verification = [preflight_package(api, spec) for spec in specs]
        remaining = {
            plan.spec.package_name: plan.summary()
            for plan in verification
            if plan.upload_document or plan.wait_for_document or plan.sheet_updates
        }
        if remaining:
            raise release.ReleaseError(f"Curated source sync did not converge: {json.dumps(remaining, ensure_ascii=False)}")

    summaries = {plan.spec.package_name: plan.summary() for plan in plans}
    return {
        "ok": True,
        "mode": "apply" if args.apply else "dry-run",
        "packages": summaries,
        "planned_mutations": sum(
            int(summary["document_upload"]) + int(summary["sheets_update"])
            for summary in summaries.values()
        ),
    }


def main() -> int:
    try:
        result = run()
    except release.ReleaseError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
