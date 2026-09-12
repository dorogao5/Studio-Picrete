#!/usr/bin/env python3
"""Correct three existing live tasks; dry-run first, then --apply --expect-digest.

Run inside the Git-built Studio image with PICRETE_ACCESS_TOKEN supplied externally.
No generation, uploads, deletions, task creation, or trainer replacement.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from release_physical_chemistry import (
    ASSISTANT_ID, REVIEWER_ID, Picrete, ReleaseError, digest, require,
    review_reason, valid_rubric,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend' if (ROOT / 'backend').is_dir() else ROOT))
SOURCE = 'studio_fizicheskaya_himiya_dynamic'
FIELDS = ('statement', 'reference_solution', 'answer', 'difficulty', 'topic', 'rubric', 'max_score', 'images')
MARKER = 'live_generation_correction'


def prepare(task, row, seal, reviewed_at):
    from app.services.task_evidence import APPROVAL_SCHEMA_VERSION, task_content_fingerprint
    from app.services.task_approval import task_is_export_ready
    require(task is not None, 'Missing existing Studio task')
    require(all(getattr(task, k) == row[k] for k in ('id', 'batch_id', 'assistant_id'))
            and task.assistant_id == ASSISTANT_ID, 'Studio identity mismatch')
    require(set(row['corrected']) == set(FIELDS), 'Unexpected correction fields')
    cfg = row['original_validation_config']
    before = {k: getattr(task, k) for k in FIELDS}
    marker = (task.grounding or {}).get(MARKER)
    if marker:
        require(marker.get('digest') == seal and marker.get('before_fingerprint') == row['original_content_fingerprint']
                and marker.get('before_fields_sha256') == row['original_fields_sha256']
                and before == row['corrected'] and task_is_export_ready(task), 'Corrected task drift')
        require(marker.get('after_fingerprint') == task_content_fingerprint(task, cfg), 'Corrected fingerprint drift')
        return False
    require(digest(before) == row['original_fields_sha256'], 'Original fields SHA mismatch')
    require(task_content_fingerprint(task, cfg) == row['original_content_fingerprint'], 'Native fingerprint mismatch')
    require(valid_rubric(row['corrected']['rubric'], row['corrected']['max_score'])
            and not row['corrected']['images'], 'Invalid corrected rubric/images')
    for key in FIELDS:
        setattr(task, key, copy.deepcopy(row['corrected'][key]))
    fingerprint = task_content_fingerprint(task, cfg)
    task.validation = dict(task.validation or {}) | {'approval': {
        'basis': 'teacher_override', 'schema_version': APPROVAL_SCHEMA_VERSION,
        'reviewed_by': REVIEWER_ID, 'reviewed_at': reviewed_at,
        'reason': 'проверено Codex по поручению doroga; ' + review_reason(row['review_notes']),
        'validation_config': cfg, 'content_fingerprint': fingerprint}}
    task.grounding = dict(task.grounding or {}) | {MARKER: {
        'digest': seal, 'before_fingerprint': row['original_content_fingerprint'],
        'before_fields_sha256': row['original_fields_sha256'], 'after_fingerprint': fingerprint,
        'review_notes': row['review_notes']}}
    task.status, task.approved = 'approved', True
    require(task_is_export_ready(task), 'Manual approval rejected')
    return True


async def existing(api, number):
    item_id = 'tb_' + SOURCE + '_' + re.sub(r'[^a-zA-Z0-9]', '_', number)
    page = await api.request('GET', api.course + '/task-bank/items',
                             params={'source': SOURCE, 'q': number, 'limit': 1000})
    require(page['total_count'] <= len(page['items']), 'Ambiguous truncated bank query')
    rows = [r for r in page['items'] if r['id'] == item_id]
    require(len(rows) == 1 and rows[0]['number'] == number and rows[0]['source'] == SOURCE,
            'Missing or mismatched existing bank number/ID/source')
    return rows[0]


def bank_fields(fields):
    return {'text': fields['statement'], 'solution': fields['reference_solution'],
            'answer': fields['answer'], 'difficulty': fields['difficulty']}


async def run(args, data, seal):
    from sqlalchemy import text
    from app.config import get_settings
    from app.db import SessionLocal, engine
    from app.models import GeneratedTask, User
    require(engine.dialect.name == 'postgresql', 'Studio PostgreSQL required')
    api = Picrete(get_settings(), args.apply)
    try:
        await api.authenticate()
        async with SessionLocal(autoflush=False) as db:
            await db.execute(text('SELECT pg_advisory_xact_lock(734821903)' if args.apply else 'SET TRANSACTION READ ONLY'))
            reviewer = await db.get(User, REVIEWER_ID)
            require(reviewer and reviewer.username == 'doroga' and reviewer.is_active, 'Reviewer identity mismatch')
            paragraphs, expected, changed = [], {}, 0
            for row in data['tasks']:
                task = await db.get(GeneratedTask, row['id'], with_for_update=args.apply)
                changed += prepare(task, row, seal, args.reviewed_at)
                number = f"student-{row['batch_id'][:12]}-1-1"
                live = await existing(api, number)
                actual = {k: live.get(k) for k in bank_fields(row['corrected'])}
                desired = bank_fields(row['corrected'])
                require(actual in (bank_fields(row['original']), desired), 'Remote task content drift')
                require(not live.get('images'), 'Remote images cannot be stripped')
                expected[number] = desired
                if actual != desired:
                    paragraphs.append({'paragraph': '1', 'topic': row['corrected']['topic'], 'theory_text': '',
                        'tasks': [desired | {'number': number, 'images': [],
                            'volume': live['volume'], 'task_type': live['task_type']}]})
            print(json.dumps({'digest': seal, 'studio_changes': changed, 'bank_changes': len(paragraphs),
                              'apply': args.apply, 'model_calls': 0, 'uploads': 0}))
            if not args.apply:
                await db.rollback()
                return
            await db.commit()  # A failed bridge is resumable via the sealed correction marker.
            if paragraphs:
                await api.request('POST', api.bridge, internal=True, json={'source': {
                    'code': SOURCE, 'title': 'Физическая химия · задачи, сгенерированные для тренажёра',
                    'version': seal}, 'paragraphs': paragraphs})
            for number, desired in expected.items():
                live = await existing(api, number)
                require(all(live.get(k) == v for k, v in desired.items()), 'Bank readback mismatch; retry same digest')
            print('Verified three existing corrected tasks; unrelated bank entries untouched.')
    finally:
        await api.client.aclose()
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    parser.add_argument('--expect-digest')
    parser.add_argument('--reviewed-at', required=True, help='Actual authorized review ISO timestamp with timezone; reuse on retry')
    args = parser.parse_args()
    require(datetime.fromisoformat(args.reviewed_at).utcoffset() is not None, 'Timezone required')
    data = json.loads((ROOT / 'ops/content/physical_chemistry/live-generation-corrections.json').read_text())
    require(len(data['tasks']) == 3 and len({r['id'] for r in data['tasks']}) == 3
            and len({r['batch_id'][:12] for r in data['tasks']}) == 3, 'Exactly three distinct tasks/batches required')
    seal = digest({'artifact': data, 'script': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'reviewed_at': args.reviewed_at, 'reviewed_by': REVIEWER_ID})
    require(not args.apply or args.expect_digest == seal, 'Apply requires matching dry-run --expect-digest')
    asyncio.run(run(args, data, seal))


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, ReleaseError) else f'Correction failed: {type(exc).__name__}; details suppressed', file=sys.stderr)
        sys.exit(1)
