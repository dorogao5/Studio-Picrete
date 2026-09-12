"""Bounded live acceptance: one generated candidate per selected existing blueprint.

Explicit --live required; no automatic replacement attempts. Results stay in Studio
for review. JSONL contains identifiers and transport counters, never credentials/reasoning.
"""
import argparse
import asyncio
import json
import time
from sqlalchemy import select
from app.db import SessionLocal, engine
from app.models import Assistant, GeneratedTask, GenerationBatch, TaskTemplate
from app.services.taskgen import run_batch
from app.services.task_approval import task_is_export_ready
from release_general_inorganic import ASSISTANT


def audit_summary(audit):
    audit=audit or {}
    return {"model_calls":audit.get("model_calls"),"usage":audit.get("usage"),
            "tools":[{"name":t.get("tool_name"),"status":t.get("status")} for t in audit.get("tool_traces",[])]}


async def main(args):
    assert args.live, 'Pass --live to authorize this bounded paid acceptance run'
    async with SessionLocal() as db:
        a=await db.get(Assistant,ASSISTANT)
        assert a.generation_policy=='single_verifier'
        templates=(await db.scalars(select(TaskTemplate).where(TaskTemplate.assistant_id==ASSISTANT)
                                   .order_by(TaskTemplate.name))).all()
        if args.template:
            templates=[t for t in templates if t.id in args.template]
        if args.limit:
            templates=templates[:args.limit]
        specs=[(t.id,t.name,t.topic,t.difficulty) for t in templates]
        mid=a.default_generator_model_id
        vid=a.verifier_model_id
    sem=asyncio.Semaphore(3)
    async def check(spec):
        async with sem:
            tid,name,topic,difficulty=spec
            started=time.monotonic()
            async with SessionLocal() as db:
                batch=GenerationBatch(assistant_id=ASSISTANT,template_id=tid,status='running',
                    params={'model_entry_id':mid,'solver_model_entry_id':vid,'count':args.count,
                            'topic':topic,'difficulty':difficulty,'validate_tasks':True},
                    requested_count=args.count,generated_count=0,validated_count=0,
                    created_by='codex-inorganic-acceptance')
                db.add(batch)
                await db.commit()
                bid=batch.id
            await run_batch(bid)
            async with SessionLocal() as db:
                b=await db.get(GenerationBatch,bid)
                tasks=(await db.scalars(select(GeneratedTask).where(GeneratedTask.batch_id==bid))).all()
                print(json.dumps({'template_id':tid,'name':name,'batch_id':bid,
                    'seconds':round(time.monotonic()-started,2),'ready':sum(task_is_export_ready(t) for t in tasks),
                    'requested':args.count,'status':b.status,'error':b.error,
                    'summary':(b.params or {}).get('quality_summary'),
                    'generation_errors':(b.params or {}).get('generation_error_audits'),
                    'tasks':[{'id':t.id,'status':t.status,'reasons':(t.validation or {}).get('reasons'),
                              'generator':audit_summary((t.grounding or {}).get('calculation_audit')),
                              'verifier':audit_summary((t.validation or {}).get('calculation_audit'))} for t in tasks]},
                    ensure_ascii=False),flush=True)
    await asyncio.gather(*(check(s) for s in specs))
    await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--limit',type=int,default=0)
    parser.add_argument('--count',type=int,default=1,choices=range(1,6))
    parser.add_argument('--template',action='append')
    asyncio.run(main(parser.parse_args()))
