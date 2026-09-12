#!/usr/bin/env python3
"""Release reviewed physical chemistry content; no uploads or model calls.

Default: --dry-run. Requires the Studio ORM, PostgreSQL, and a Picrete bearer
token in PICRETE_ACCESS_TOKEN (including for dry-run). --apply can alternatively
log in using PICRETE_USERNAME/PICRETE_PASSWORD from the process environment.
Integration URL/token come exclusively from Studio Settings. Never pass secrets
on the command line. --reviewed-by is an actual active Studio teacher/admin ID;
--reviewed-at is the actual review timestamp, not an invented run timestamp.
The approval explicitly records Codex review on the account holder's behalf,
not a claim that the account holder personally reviewed it or a model validated it.

Run from a Git-built image containing /app/ops and /app/app, for example:
  /app/.venv/bin/python /app/ops/release_physical_chemistry.py \
    --reviewed-by USER_ID --reviewed-at ACTUAL_ISO8601_TIME
Then review the JSON plan and rerun with --apply --expect-digest DIGEST.
No writes occur on a dry-run, including no ORM flush or authentication POST.

Packaging prerequisite (added locally by the deployment maintainer): Compose
build context must be repository root, dockerfile backend/Dockerfile, backend
COPY sources prefixed with backend/, and this script/content copied to /app/ops.
Include a root .dockerignore excluding secrets, .git, data, .venv and
frontend/node_modules. Commit all of this before building. Do not
docker-cp this script or mount a workstation source tree into production.

Apply is resumable, not a distributed transaction: commit Studio first, then
upsert the Picrete bank/snapshot, then save/publish the trainer with revision
checks. Run in a maintenance window; concurrent publication is not supported.
No deletions. Existing trainer draft changes cause an abort (except a saved
copy of precisely this release, to permit recovery after an interrupted run).
Extra templates for unrelated topics are preserved. Duplicate canonical
topic/level templates cause an abort instead of silently changing selection.
Extra active legacy prompt roles are retired, not deleted, to leave exactly
generator/grader/tutor=qwen and verifier=deepseek active.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit
import uuid

SCRIPT_VERSION = "physical-chemistry-release-v1"
SOURCE = "studio_fizicheskaya_himiya"
ASSISTANT_ID = "dc0d7a2fa01749b98f7af520739c3364"
STUDIO_COURSE_ID = "8b3f566b0fc746b79ac61a9e5348d904"
PICRETE_COURSE_ID = "565f6c25-937d-419d-be07-bf7b4f60bd5c"
TRAINER_ID = "690cad8b-ea8c-45a0-8dd8-7d94ee4810ea"
REVIEWER_ID = "43e7e903e1fb41b78c8700f963d845b8"
SUPERSEDED_SHEET_IDS = {"79210f522adb4fad9a0330b62963ab7f", "f69392c631b64492b5b927fe5d4f4e8e"}
QWEN_ID = "179d8425e71e47ffb6ae0da47f7a48c6"
DEEPSEEK_ID = "88c6b31119d04d14a3e62fbfb12b0588"
LEVELS = ("easy", "medium", "hard")
ROLES = {"generator": "qwen", "grader": "qwen", "tutor": "qwen", "verifier": "deepseek"}


class ReleaseError(Exception):
    """A credential-free, operator-readable error."""


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def stable_id(kind, key):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"picrete:{SOURCE}:{kind}:{key}").hex


def bank_id(number):
    # Must match Rust sanitize_id_fragment: ASCII alphanumeric, one '_' per
    # other character (NOT regex runs). A number may never be renumbered.
    fragment = "".join(c if c.isascii() and c.isalnum() else "_" for c in number)
    return f"tb_{SOURCE}_{fragment}"


def text_field(row, key):
    value = row.get(key)
    require(isinstance(value, str) and bool(value.strip()), f"Missing nonempty {key}")
    return value


def review_reason(notes):
    require(isinstance(notes, (str, list, dict)) and bool(notes), "Missing actual review_notes")
    value = notes if isinstance(notes, str) else json.dumps(notes, ensure_ascii=False, sort_keys=True)
    require(len(value.strip()) >= 10, "review_notes must contain a substantive review")
    require(value.strip().casefold() not in {"not reviewed", "pending review", "needs review"},
            "Unreviewed task cannot be approved")
    return value


def valid_rubric(rubric, max_score):
    if not isinstance(rubric, list) or not rubric:
        return False
    try:
        return (math.isfinite(float(max_score)) and float(max_score) > 0
                and all(isinstance(r, dict) and bool(str(r.get('criterion_name', '')).strip())
                        and math.isfinite(float(r['max_score'])) and float(r['max_score']) > 0 for r in rubric)
                and math.isclose(sum(float(r['max_score']) for r in rubric), float(max_score)))
    except (TypeError, ValueError, KeyError):
        return False


def load_content(root):
    """Read a closed set of derived artifacts, never any source/upload manifest."""
    files = {}

    def read(relative):
        p = root / relative
        require(p.is_file() and p.resolve().is_relative_to(root.resolve()),
                f"Required curated artifact missing or outside content root: {relative}")
        value = p.read_text(encoding="utf-8")
        require(bool(value.strip()), f"Empty curated artifact: {relative}")
        files[relative] = value
        return value

    blueprints = json.loads(read("blueprints.json"))
    version = text_field(blueprints, "version")
    topics = blueprints.get("topics", [])
    require(len(topics) == 5, "Exactly five canonical topic blueprints required")
    require(len({t.get("topic") for t in topics}) == 5, "Duplicate canonical topics")
    require(len({t.get("slug") for t in topics}) == 5, "Duplicate canonical slugs")
    sheets = {"phys-00-conventions": read("sheets/phys-00-conventions.md")}
    for topic in topics:
        for key in ("slug", "topic", "sheet", "instructions"):
            text_field(topic, key)
        require(re.fullmatch(r"phys-[0-9]{2}-[a-z-]+", topic["sheet"]), "Invalid sheet slug")
        require(topic["sheet"] not in sheets, "Each topic must bind a distinct derived sheet")
        sheets[topic["sheet"]] = read(f"sheets/{topic['sheet']}.md")
        require(len(topic.get("examples", [])) == 2, "Exactly two few-shot examples required per topic")
        for example in topic["examples"]:
            for key in ("statement", "solution", "answer"):
                text_field(example, key)
    require(len(list((root / "sheets").glob("*.md"))) == 6, "Expected exactly six derived sheets")
    prompts = {role: read(f"prompts/{role}.txt") for role in ROLES}
    profile = json.loads(read("profile.json"))
    require(set(profile) == {'description', 'nuances', 'criteria'},
            'profile.json must contain exactly description, nuances, criteria')
    text_field(profile, 'description')
    require(isinstance(profile['nuances'], list) and all(isinstance(n, str) and n.strip() for n in profile['nuances']),
            'Profile nuances must be a list of nonempty strings')
    require(isinstance(profile['criteria'], list) and bool(profile['criteria']), 'Profile grading criteria required')
    profile_rubric = [{'criterion_name': r.get('name'), 'description': r.get('description', ''),
                      'max_score': r.get('max_score')} for r in profile['criteria']]
    require(valid_rubric(profile_rubric, 10), 'Profile criteria must define a valid 10-point rubric')
    reviewed = json.loads(read("reviewed-bank.json")).get("tasks", [])
    eryomin = json.loads(read("eryomin-bank.json")).get("tasks", [])
    require(len(reviewed) >= 37, "reviewed-bank must contain all 37 originals plus any additions")
    require(len(eryomin) == 23, "eryomin-bank must contain exactly 23 reviewed tasks")
    tasks = []
    canonical = {t["topic"] for t in topics}
    for filename, rows in (("reviewed-bank.json", reviewed), ("eryomin-bank.json", eryomin)):
        for original in rows:
            row = copy.deepcopy(original)
            for key in ("id", "number", "topic", "difficulty", "text", "solution", "answer"):
                text_field(row, key)
            require(row["number"] == row["number"].strip(), "Task numbers cannot contain edge whitespace")
            require(row["topic"] in canonical, "Bank contains a noncanonical topic")
            require(row["difficulty"] in LEVELS, "Bank contains an unaudited difficulty")
            review_reason(row.get("review_notes"))
            require(not row.get("images"), "This release cannot import image-dependent tasks")
            row["bank_file"] = filename
            tasks.append(row)
    for key in ("id", "number"):
        require(len({t[key] for t in tasks}) == len(tasks), f"Duplicate bank {key}")
    require(len({bank_id(t['number']) for t in tasks}) == len(tasks), "Sanitized Picrete ID collision")
    return {"version": version, "topics": topics, "sheets": sheets, "prompts": prompts,
            "tasks": tasks, "profile": profile,
            "file_hashes": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in files.items()}}


def merge_trainer(published, tasks):
    result = copy.deepcopy(published)
    sections = result.get("sections", [])
    require(len({s["id"] for s in sections}) == len(sections), "Duplicate trainer section IDs")
    by_title = {s["title"]: s for s in sections}
    require(len(by_title) == len(sections), "Ambiguous trainer section titles")
    for row in tasks:
        require(row["topic"] in by_title, "Canonical topic absent from published trainer; refusing to invent section ID")
        item_id = bank_id(row["number"])
        section = by_title[row["topic"]]
        for other in sections:
            require(other is section or all(i["task_id"] != item_id for i in other["items"]),
                    "Existing task belongs to a different section; explicit mapping review required")
        matches = [i for i in section["items"] if i["task_id"] == item_id]
        require(len(matches) <= 1, "Duplicate trainer item")
        if matches:
            matches[0]["difficulty"] = row["difficulty"]
        else:
            section["items"].append({"task_id": item_id, "difficulty": row["difficulty"]})
        require(len(section["items"]) <= 300, "Trainer section exceeds API item limit")
    return result


def build_export(content, release_digest):
    return {"source": {"code": SOURCE, "title": "Физическая химия", "version": release_digest},
            "paragraphs": [
                {"paragraph": str(index), "topic": topic["topic"], "theory_text": "",
                 "tasks": [{k: row[k] for k in ("number", "text", "solution", "answer", "difficulty")}
                           | {"volume": "medium", "task_type": "calculation", "images": []}
                           for row in content["tasks"] if row["topic"] == topic["topic"]]}
                for index, topic in enumerate(content["topics"], 1)]}


class Picrete:
    """Explicit endpoint allowlist: impossible to accidentally call a model route."""

    def __init__(self, settings, apply):
        import httpx
        base = settings.picrete_api_url.rstrip("/")
        parts = urlsplit(base)
        require(parts.scheme in ("http", "https") and parts.hostname and not parts.username
                and not parts.password and not parts.query and not parts.fragment,
                "Invalid configured Picrete API origin")
        require(parts.scheme == "https" or parts.hostname in ("localhost", "127.0.0.1", "::1"),
                "Plain HTTP credentials permitted only on loopback")
        require(parts.path in ("", "/"), "STUDIO_PICRETE_API_URL must be an origin without /api/v1")
        self.client = httpx.AsyncClient(base_url=base, timeout=120, follow_redirects=False, trust_env=False)
        self.integration = settings.picrete_integration_token
        require(bool(self.integration), "Studio integration token missing")
        self.token = os.environ.get("PICRETE_ACCESS_TOKEN", "")
        self.apply = apply
        self.course = f"/api/v1/courses/{PICRETE_COURSE_ID}"
        self.trainer = f"{self.course}/practice/catalog/{TRAINER_ID}"
        self.bridge = f"/api/v1/internal/studio/courses/{PICRETE_COURSE_ID}/task-bank/import"
        self.snapshot = f"/api/v1/internal/studio/course-assistants/{PICRETE_COURSE_ID}"
        self.reads = {f"{self.course}/task-bank/items", f"{self.course}/task-bank/sources",
                      f"{self.course}/assistant", self.trainer}
        self.writes = {("POST", self.bridge), ("PUT", self.snapshot),
                       ("PUT", self.trainer), ("POST", self.trainer + "/publish")}

    async def request(self, method, path, *, internal=False, **kwargs):
        require((method == "GET" and path in self.reads) or
                (self.apply and (method, path) in self.writes), "Endpoint/method outside release allowlist")
        token = self.integration if internal else self.token
        require(bool(token), "PICRETE_ACCESS_TOKEN required (dry-run never logs in)")
        response = await self.client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        require(response.is_success, f"Picrete {method} {path}: HTTP {response.status_code}; body suppressed")
        return response.json()

    async def authenticate(self):
        if self.token:
            return
        require(self.apply, "Dry-run requires PICRETE_ACCESS_TOKEN; no login POST is made")
        username, password = os.environ.get("PICRETE_USERNAME"), os.environ.get("PICRETE_PASSWORD")
        require(username and password, "Provide PICRETE_ACCESS_TOKEN or external PICRETE_USERNAME/PICRETE_PASSWORD")
        response = await self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        require(response.is_success, f"Picrete login HTTP {response.status_code}; body suppressed")
        self.token = response.json().get("access_token", "")
        require(bool(self.token), "Picrete login did not return a bearer token")

    async def bank(self):
        rows, skip = [], 0
        while True:
            page = await self.request("GET", f"{self.course}/task-bank/items",
                                      params={"source": SOURCE, "skip": skip, "limit": 1000})
            batch = page["items"]
            rows.extend(batch)
            if len(rows) >= page["total_count"]:
                break
            require(bool(batch) and len(rows) < 20000, "Invalid bank pagination")
            skip += len(batch)
        require(len({r['id'] for r in rows}) == len(rows), "Duplicate remote bank IDs")
        return rows


class Changes:
    def __init__(self):
        self.items = []
        self.new = []

    def set(self, obj, values):
        changed = [key for key, value in values.items() if getattr(obj, key, None) != value]
        if changed:
            self.items.append({"kind": type(obj).__name__, "id": obj.id, "fields": changed})
            for key in changed:
                setattr(obj, key, copy.deepcopy(values[key]))

    def add(self, obj):
        self.new.append(obj)
        self.items.append({"kind": type(obj).__name__, "id": obj.id, "create": True})
        return obj


async def plan_studio(db, content, args, release_digest, live_bank):
    from sqlalchemy import select
    from app.models import Assistant, Course, GeneratedTask, ModelEntry, PromptVersion, Provider, ReferenceSheet, TaskTemplate, User
    from app.services.task_evidence import APPROVAL_SCHEMA_VERSION, normalize_validation_config, task_content_fingerprint
    from app.services.task_approval import task_is_export_ready
    from app.services.physical_chemistry import is_physical_chemistry
    from app.api.integration import _build_runtime_policy, _seal_snapshot

    changes = Changes()
    assistant = await db.get(Assistant, ASSISTANT_ID)
    course = await db.get(Course, STUDIO_COURSE_ID)
    require(assistant and is_physical_chemistry(assistant), "Expected physical chemistry assistant missing")
    require(course and course.assistant_id == ASSISTANT_ID and course.external_course_id == PICRETE_COURSE_ID,
            "Studio/Picrete course binding mismatch")
    reviewer = await db.get(User, args.reviewed_by)
    require(reviewer and reviewer.is_active and reviewer.role in ("admin", "teacher"),
            "reviewed-by must identify the actual active Studio teacher/admin accepting this review")
    require(reviewer.id == REVIEWER_ID and reviewer.username == 'doroga', 'Expected authorized doroga review account')
    models = {}
    for family, model_id in (("qwen", QWEN_ID), ("deepseek", DEEPSEEK_ID)):
        model = await db.get(ModelEntry, model_id)
        require(model and model.enabled and model.family == family, f"Required {family} model unavailable")
        provider = await db.get(Provider, model.provider_id)
        require(provider and provider.enabled, f"Required {family} provider disabled")
        if family == "qwen":
            require("qwen3.6" in model.model_id.casefold(), "Expected actual Qwen 3.6 model, refusing a silent replacement")
        models[family] = model
    require(hasattr(assistant, "verifier_model_id"), "Deploy Kant verifier_model_id ORM/schema change first")
    changes.set(assistant, {"default_generator_model_id": QWEN_ID, "default_grader_model_id": QWEN_ID,
                            "verifier_model_id": DEEPSEEK_ID, "grading_enabled": True,
                            "topics": list(dict.fromkeys((assistant.topics or []) + [t['topic'] for t in content['topics']]))})
    changes.set(assistant, content['profile'])
    require(bool(assistant.criteria), "Existing assistant grading criteria are required; not fabricated by release")

    async def scoped(cls):
        return list((await db.scalars(select(cls).where(cls.assistant_id == ASSISTANT_ID))).all())

    sheets = await scoped(ReferenceSheet)
    sheet_ids = {}
    for index, (slug, markdown) in enumerate(content["sheets"].items()):
        title = next((line.lstrip('#').strip() for line in markdown.splitlines() if line.startswith('# ')), slug)
        marker = slug[:7].upper()  # PHYS-00, PHYS-01, ...
        matches = [s for s in sheets if s.id == stable_id("sheet", slug) or s.title.startswith(marker)
                   or s.title == title]
        require(len(matches) <= 1, f"Ambiguous existing derived sheet: {slug}")
        sheet = matches[0] if matches else changes.add(ReferenceSheet(id=stable_id("sheet", slug), assistant_id=ASSISTANT_ID))
        if not matches:
            sheets.append(sheet)
        changes.set(sheet, {"title": title, "kind": "conventions" if index == 0 else "formulas",
                             "content_markdown": markdown, "source_document_id": None,
                             "is_canonical": True, "visibility": "student", "ord": 500 + index,
                             "description": f"Derived curated sheet: {slug}", "created_by": sheet.created_by or args.reviewed_by})
        sheet_ids[slug] = sheet.id
    for sheet in sheets:
        if sheet.id in SUPERSEDED_SHEET_IDS:
            changes.set(sheet, {'is_canonical': False})

    templates = await scoped(TaskTemplate)
    template_map = {}
    for topic in content["topics"]:
        for level in LEVELS:
            matches = [t for t in templates if t.topic == topic["topic"] and t.difficulty == level]
            require(len(matches) <= 1, f"Duplicate canonical template: {topic['slug']}/{level}")
            template = matches[0] if matches else changes.add(TaskTemplate(
                id=stable_id("template", topic['slug'] + ':' + level), assistant_id=ASSISTANT_ID))
            changes.set(template, {"name": f"{topic['topic']} / {level}", "topic": topic["topic"],
                "difficulty": level, "instructions": f"Выбранная сложность: {level}. Используйте только вариант этого уровня.\n\n{topic['instructions']}",
                "example_tasks": topic["examples"], "example": "", "reference_sheet_ids": [sheet_ids['phys-00-conventions'], sheet_ids[topic['sheet']]],
                "task_kind": "calculation", "answer_format": "numeric", "numeric_tolerance_pct": 2.0,
                "validation_solver": False, "validation_data_check": False, "chemistry_check": "off", "kb_query": "",
                "rubric": []})
            template_map[(topic["topic"], level)] = template

    prompts = await scoped(PromptVersion)
    active = {}
    for role, family in ROLES.items():
        matching = [p for p in prompts if p.role == role and p.system_prompt == content['prompts'][role]
                    and p.target_family == family]
        current = [p for p in matching if p.status == "active"]
        chosen = sorted(current or matching, key=lambda p: (p.version, p.id), reverse=True)
        prompt = chosen[0] if chosen else changes.add(PromptVersion(
            id=stable_id("prompt", role + ':' + digest([content['prompts'][role], family])),
            assistant_id=ASSISTANT_ID, role=role,
            version=max((p.version for p in prompts if p.role == role), default=0) + 1,
            system_prompt=content['prompts'][role], target_family=family, source="manual",
            notes=f"{SCRIPT_VERSION}; curated prompt; no model call", architect_model=""))
        changes.set(prompt, {"status": "active"})
        active[role] = prompt
    for prompt in prompts:
        if prompt.status == "active" and prompt is not active.get(prompt.role):
            changes.set(prompt, {"status": "draft"})

    existing_tasks = {t.id: t for t in await scoped(GeneratedTask)}
    live_by_id = {row['id']: row for row in live_bank}
    prepared = []
    existing_reviewed = 0
    for row in content['tasks']:
        # Reviewed-bank IDs are Picrete bank IDs, not Studio UUIDs. Recover the
        # original Studio row from its EXACT old published content, then persist
        # the mapping. Never infer it from ordering or the task's new text.
        mapped = [t for t in existing_tasks.values()
                  if (t.grounding or {}).get('physical_chemistry_release', {}).get('input_id') == row['id']]
        if not mapped and row['id'] in live_by_id:
            old = live_by_id[row['id']]
            require(old.get('answer') is not None, 'Picrete bearer must have teacher/admin answer access')
            mapped = [t for t in existing_tasks.values()
                      if t.statement == old['text'] and t.reference_solution == old['solution']
                      and t.answer == old['answer']]
            require(len(mapped) == 1, 'Cannot uniquely map an existing Picrete task to Studio by original content')
        require(len(mapped) <= 1, 'Ambiguous stored Studio/Picrete task mapping')
        task_id = (mapped[0].id if mapped else row['id'] if re.fullmatch(r"[0-9a-f]{32}", row['id'])
                   else stable_id('task', row['id']))
        task = existing_tasks.get(task_id)
        if task and row['bank_file'] == 'reviewed-bank.json':
            existing_reviewed += 1
        if task is None:
            # Prevent a primary-key collision from hijacking another assistant.
            require(await db.get(GeneratedTask, task_id) is None, "Task ID belongs to another assistant")
            task = changes.add(GeneratedTask(id=task_id, assistant_id=ASSISTANT_ID, images=[], rubric=[], max_score=10.0))
        template = template_map[(row['topic'], row['difficulty'])]
        prior = (task.grounding or {}).get('physical_chemistry_release', {})
        require(not prior or prior.get('number') == row['number'], "Refusing to renumber an already released Studio task")
        require(not task.images, "Existing task has images; cannot silently strip them")
        rubric = task.rubric
        max_score = task.max_score
        if not valid_rubric(rubric, max_score):
            rubric = [{'criterion_name': r['name'], 'description': r.get('description', ''),
                       'max_score': r['max_score']} for r in content['profile']['criteria']]
            max_score = 10.0
        changes.set(task, {'statement': row['text'], 'reference_solution': row['solution'], 'answer': row['answer'],
                           'topic': row['topic'], 'difficulty': row['difficulty'], 'template_id': template.id,
                           'status': 'approved', 'approved': True,
                           'rubric': rubric, 'max_score': max_score,
                           'grounding': dict(task.grounding or {}) | {'physical_chemistry_release': {
                               'script_version': SCRIPT_VERSION, 'content_digest': release_digest,
                               'input_id': row['id'], 'number': row['number'], 'bank_file': row['bank_file'],
                               'review_notes': row['review_notes'], 'source_page': row.get('source_page'),
                               'topic': row['topic'], 'difficulty': row['difficulty']}}})
        config = normalize_validation_config({'answer_format': template.answer_format,
            'tolerance_pct': template.numeric_tolerance_pct, 'sheet_ids': template.reference_sheet_ids,
            'task_kind': 'calculation', 'chemistry_check': 'off'})
        approval = {'basis': 'teacher_override', 'schema_version': APPROVAL_SCHEMA_VERSION,
                    'reviewed_by': args.reviewed_by, 'reviewed_at': args.reviewed_at,
                    'reason': 'проверено Codex по поручению doroga; ' + review_reason(row['review_notes']),
                    'validation_config': config,
                    'content_fingerprint': task_content_fingerprint(task, config)}
        # Old automatic evidence is retained as history, never marked passed.
        changes.set(task, {'validation': dict(task.validation or {}) | {'approval': approval}})
        require(task_is_export_ready(task), "Approval fingerprint rejected by Studio export contract")
        prepared.append(task)
    require(existing_reviewed >= 37, "The 37 existing Studio tasks must be addressed by their real IDs, not duplicated")
    require(len({t.id for t in prepared}) == len(prepared), 'Two input tasks map to one Studio row')
    runtime = await _build_runtime_policy(db, assistant)
    snapshot = _seal_snapshot({'schema_version': 1,
        'assistant': {key: getattr(assistant, key) for key in
                      ('id', 'name', 'discipline', 'description', 'audience', 'language', 'topics', 'criteria', 'nuances')}
                     | {'runtime_policy': runtime, 'grading_enabled': assistant.grading_enabled},
        'prompts': {role: {'id': p.id, 'version': p.version, 'system_prompt': p.system_prompt,
                           'target_family': p.target_family} for role, p in active.items()},
        'reference_sheets': [{k: getattr(s, k) for k in ('id','title','kind','description','content_markdown')}
                             for s in sorted(sheets, key=lambda s: (s.ord, s.id))
                             if s.is_canonical and s.visibility == 'student']})
    # No fake PlaygroundResult or grading-preflight success: this explicit
    # curated release uses the internal snapshot bridge, not paid UI preflight.
    return changes, snapshot, course, prepared


async def run(args, content, release_digest):
    # Imports are delayed so --help and --self-test need no configured database.
    from sqlalchemy import text
    from app.db import SessionLocal, engine
    from app.config import get_settings
    require(engine.dialect.name == 'postgresql', 'Run against Studio PostgreSQL; SQLite is not a release target')
    api = Picrete(get_settings(), args.apply)
    try:
        await api.authenticate()
        async with SessionLocal(autoflush=False) as db:
            if not args.apply:
                await db.execute(text('SET TRANSACTION READ ONLY'))
            else:
                # Serializes cooperating runs without a persistent lock table.
                await db.execute(text('SELECT pg_advisory_xact_lock(734821903)'))
            bank = await api.bank()
            changes, snapshot, course, prepared = await plan_studio(db, content, args, release_digest, bank)
            by_id = {row['id']: row for row in bank}
            desired_ids = {bank_id(row['number']) for row in content['tasks']}
            # Every existing static-bank number must remain in this release.
            require(set(by_id) <= desired_ids, 'Curated inputs omit existing Picrete bank tasks; refusing a partial release')
            require(len(bank) >= 37, 'Expected at least 37 existing Picrete tasks')
            for row in bank:
                require(row['id'] == bank_id(row['number']), 'Live number-to-ID mapping differs from bridge contract')
            export = build_export(content, release_digest)
            sources = await api.request('GET', f'{api.course}/task-bank/sources')
            source = next((s for s in sources if s['code'] == SOURCE), None)
            bank_version_changed = source is None or source.get('version') != release_digest
            bank_changes = []
            for paragraph in export['paragraphs']:
                for row in paragraph['tasks']:
                    old = by_id.get(bank_id(row['number']))
                    fields = ('number','text','solution','answer','difficulty','volume','task_type','images')
                    if old is None or old['topic'] != paragraph['topic'] or any(old.get(k) != row[k] for k in fields):
                        bank_changes.append(bank_id(row['number']))
            published = await api.request('GET', api.trainer)
            draft = await api.request('GET', api.trainer, params={'manage': 'true'})
            require(published['published'], 'Existing published trainer required')
            desired = merge_trainer(published['definition'], content['tasks'])
            require(draft['definition'] in (published['definition'], desired),
                    'Unpublished trainer edits detected; refusing to overwrite them')
            status = await api.request('GET', f'{api.course}/assistant')
            snapshot_changed = status.get('snapshot_version') != snapshot['version']
            trainer_changed = published['definition'] != desired
            plan = {'script_version': SCRIPT_VERSION, 'content_version': content['version'],
                'digest': release_digest, 'mode': 'apply' if args.apply else 'dry-run',
                'tasks': len(content['tasks']), 'reviewed_bank': len(content['tasks']) - 23, 'eryomin_bank': 23,
                'sheets': 6, 'canonical_templates': 15, 'studio_changes': changes.items,
                'bank_changed_ids': bank_changes, 'bank_version_changed': bank_version_changed,
                'publish_snapshot': snapshot_changed,
                'publish_trainer': trainer_changed, 'trainer_revision': draft['revision'],
                'snapshot_version': snapshot['version'], 'file_hashes': content['file_hashes'],
                'model_calls': 0, 'uploads': 0, 'live_test_success_claimed': False}
            print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
            if not args.apply:
                await db.rollback()
                return
            require(args.expect_digest == release_digest, '--apply requires --expect-digest from reviewed dry-run')
            # Commit only after all remote read/preflight and shape checks pass.
            db.add_all(changes.new)
            await db.commit()
            if bank_changes or bank_version_changed:
                result = await api.request('POST', api.bridge, internal=True, json=export)
                require(set(result.get('item_ids', [])) == desired_ids, 'Bridge returned unexpected task IDs')
            if snapshot_changed:
                await api.request('PUT', api.snapshot, internal=True, json=snapshot)
            # Track the acknowledged remote version only after bridge success.
            if course.published_version != snapshot['version']:
                course.published_version = snapshot['version']
                course.published_at = datetime.now(timezone.utc)
                await db.commit()
            if trainer_changed:
                current = await api.request('GET', api.trainer, params={'manage': 'true'})
                require(current['revision'] == draft['revision'], 'Trainer concurrently changed; rerun dry-run')
                revision = current['revision']
                if current['definition'] != desired:
                    saved = await api.request('PUT', api.trainer, json={'definition': desired, 'revision': revision})
                    revision = saved['revision']
                await api.request('POST', api.trainer + '/publish', json={'definition': desired, 'revision': revision})
            # Read-back verification, no task execution. An interrupted run can
            # safely be repeated with the SAME review timestamp/digest.
            final_bank = {r['id']: r for r in await api.bank()}
            for paragraph in export['paragraphs']:
                for row in paragraph['tasks']:
                    actual = final_bank.get(bank_id(row['number']))
                    require(actual and all(actual.get(k) == row[k] for k in
                            ('number','text','solution','answer','difficulty','volume','task_type','images'))
                            and actual['topic'] == paragraph['topic'], 'Bank read-back mismatch')
            final_trainer = await api.request('GET', api.trainer)
            require(final_trainer['definition'] == desired, 'Published trainer read-back mismatch')
            final_status = await api.request('GET', f'{api.course}/assistant')
            require(final_status.get('snapshot_version') == snapshot['version'], 'Snapshot read-back mismatch')
            print(json.dumps({'status': 'applied-and-read-back', 'digest': release_digest,
                              'model_calls': 0, 'uploads': 0}))
    finally:
        await api.client.aclose()
        await engine.dispose()


def self_test():
    """Pure in-memory regressions; never connects to a database or HTTP."""
    assert bank_id('1.2') == 'tb_studio_fizicheskaya_himiya_1_2'
    assert bank_id('1..2') != bank_id('1.2')
    assert bank_id('1-2') == bank_id('1.2')  # input collision check must catch this
    base = {'title': 'T', 'description': 'D', 'sections': [
        {'id': 'unchanged', 'title': 'topic', 'target': 3,
         'items': [{'task_id': bank_id('1.1'), 'difficulty': 'hard'},
                   {'task_id': 'unrelated', 'difficulty': 'medium'}]}]}
    rows = [{'number': '1.1', 'topic': 'topic', 'difficulty': 'easy'},
            {'number': '1.2', 'topic': 'topic', 'difficulty': 'hard'}]
    result = merge_trainer(base, rows)
    assert base['sections'][0]['items'][0]['difficulty'] == 'hard'
    assert result['sections'][0]['id'] == 'unchanged'
    assert result['sections'][0]['items'][1] == base['sections'][0]['items'][1]
    assert merge_trainer(result, rows) == result
    try:
        merge_trainer(base, [{'number': '9', 'topic': 'missing', 'difficulty': 'easy'}])
    except ReleaseError:
        pass
    else:
        raise AssertionError('missing topic accepted')
    assert stable_id('task', 'x') == stable_id('task', 'x')
    print('self-test: pure ID collision / trainer preservation / merge idempotence checks passed; no live checks')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true', help='default; no database flush or HTTP writes')
    modes.add_argument('--apply', action='store_true')
    parser.add_argument('--content-dir', type=Path, default=Path(__file__).resolve().parent / 'content/physical_chemistry')
    parser.add_argument('--reviewed-by', default=REVIEWER_ID, help='authorized Studio doroga account; review attributed to Codex')
    parser.add_argument('--reviewed-at', help='actual ISO-8601 review time with timezone, reused on retries')
    parser.add_argument('--expect-digest', help='exact digest from inspected dry-run; required on apply')
    parser.add_argument('--self-test', action='store_true', help='pure local regressions only')
    args = parser.parse_args()
    if args.self_test:
        require(not args.apply, 'Cannot combine self-test and apply')
        self_test()
        return
    require(args.reviewed_by and args.reviewed_at, '--reviewed-by and --reviewed-at are required; review is never fabricated')
    reviewed_at = datetime.fromisoformat(args.reviewed_at.replace('Z', '+00:00'))
    require(reviewed_at.tzinfo is not None and reviewed_at <= datetime.now(timezone.utc),
            'Review timestamp must have a timezone and cannot be in the future')
    args.reviewed_at = reviewed_at.isoformat()
    content = load_content(args.content_dir)
    release_digest = digest({'script_version': SCRIPT_VERSION, 'files': content['file_hashes'],
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'reviewed_by': args.reviewed_by, 'reviewed_at': args.reviewed_at})
    if args.apply:
        require(args.expect_digest == release_digest, 'Content/review changed or --expect-digest missing; rerun dry-run')
    # Repo execution and /app/ops execution, with no caller-specific PYTHONPATH.
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / 'backend' if (root / 'backend/app').is_dir() else root))
    logging.disable(logging.CRITICAL)  # ORM/HTTP exception logs may contain credentials
    asyncio.run(run(args, content, release_digest))


if __name__ == '__main__':
    try:
        main()
    except ReleaseError as exc:
        print(f'Release stopped: {exc}', file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Do not print SQL parameters, URL credentials, response bodies or tokens.
        print(f'Release stopped: {type(exc).__name__}; sensitive details suppressed. '
              'If applying, some stages may already be committed; rerun dry-run.', file=sys.stderr)
        sys.exit(1)
