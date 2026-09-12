"""Versioned, scoped release of existing blueprint instructions and four prompts.

Dry-run has no writes or model calls. Apply requires the digest just reviewed.
Preserves the bank, examples, other courses and historical prompt versions.
Snapshot publication is a separate normal preflight/review step after live QA.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Assistant, ModelEntry, Provider, PromptVersion, TaskTemplate
from app.services.contracts import GRADING_JSON_CONTRACT

ASSISTANT = "9fbc228df47e4d679c7a49b57d65af59"
ROOT = Path(__file__).parent / "content/general_inorganic"


def content():
    return {"blueprints": json.loads((ROOT/"blueprints.json").read_text()),
            "prompts": {r:(ROOT/f"prompts/{r}.md").read_text() +
                        ("\n"+GRADING_JSON_CONTRACT if r=="grader" else "")
                        for r in ("generator","verifier","grader","tutor")}}


async def main(args):
    data=content()
    async with SessionLocal() as db:
        a=await db.get(Assistant,ASSISTANT)
        assert a and "неорган" in a.discipline.lower()
        models=(await db.scalars(select(ModelEntry).join(Provider).where(
            ModelEntry.model_id=="deepseek-flash", Provider.kind=="deepseek",
            Provider.enabled.is_(True), ModelEntry.enabled.is_(True)))).all()
        assert len(models)==1, "Select exactly one native DeepSeek Flash model"
        model=models[0]
        provider=await db.get(Provider,model.provider_id)
        assert provider.base_url.rstrip('/') in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}
        templates=(await db.scalars(select(TaskTemplate).where(TaskTemplate.assistant_id==ASSISTANT))).all()
        assert {t.id for t in templates}=={t['id'] for t in data['blueprints']}, "Blueprint inventory changed"
        prompts=(await db.scalars(select(PromptVersion).where(PromptVersion.assistant_id==ASSISTANT))).all()
        before={"assistant":{k:getattr(a,k) for k in ("generation_policy","default_generator_model_id",
            "default_grader_model_id","verifier_model_id","generator_tools_enabled","verifier_tools_enabled",
            "decision_tools_enabled","tutor_tools_enabled")},
            "templates":{t.id:{"instructions":t.instructions,"examples":t.example_tasks} for t in templates},
            "prompts":{p.id:{"text":p.system_prompt,"status":p.status} for p in prompts}}
        digest=hashlib.sha256(json.dumps({"before":before,"after":data,"model":model.id},
                                      sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        print(json.dumps({"digest":digest,"assistant":a.name,"model":model.model_id,"provider":provider.kind,
                          "blueprints":len(templates),"roles":list(data['prompts']),"apply":args.apply}),flush=True)
        if not args.apply:
            return
        assert args.expect_digest==digest, "Release state changed; rerun dry-run"
        a.generation_policy="single_verifier"
        a.default_generator_model_id=a.default_grader_model_id=a.verifier_model_id=model.id
        for role in ("generator","verifier","decision","tutor"):
            setattr(a,f"{role}_tools_enabled",True)
        byid={t.id:t for t in templates}
        for target in data['blueprints']:
            row=byid[target['id']]
            assert row.name==target['name']
            row.instructions=target['instructions']
            anchor=target['generation_source_number']
            assert sum(e.get('source_number')==anchor for e in row.example_tasks)==1
            row.example_tasks=[{**e,'generation_anchor':e.get('source_number')==anchor}
                               for e in row.example_tasks]
        for role,text in data['prompts'].items():
            active=[p for p in prompts if p.role==role and p.status=="active"]
            if len(active)==1 and active[0].system_prompt==text and active[0].target_family=="deepseek":
                continue
            for p in active:
                p.status="retired"
            db.add(PromptVersion(assistant_id=ASSISTANT,role=role,system_prompt=text,
                version=max([p.version for p in prompts if p.role==role] or [0])+1,
                status="active",target_family="deepseek",source="manual",
                notes="Codex: course pipeline alignment, authorised by course owner; 2026-09-12"))
        await db.commit()
        print(json.dumps({"applied":True,"snapshot_published":False}),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--expect-digest',default='')
    asyncio.run(main(parser.parse_args()))
