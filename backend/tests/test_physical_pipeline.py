import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Base, Assistant, GeneratedTask, GenerationBatch, PromptVersion, TaskTemplate, ModelEntry, Provider
from app.services import physical_chemistry as pc, taskgen
from app.services.task_evidence import evidence_matches_task
from app.api import integration


def task():
    return GeneratedTask(id="task", assistant_id="assistant", batch_id="batch",
        statement="A -> B, k=2", reference_solution="original", answer="2",
        rubric=[{"criterion_name": "old", "max_score": 10}], max_score=10,
        difficulty="easy", topic="kinetics", grounding={"data_used": [], "chemistry_facts": {}},
        images=[], status="draft", validation={}, approved=False, model_used="qwen")


def correction():
    return {"statement": "A -> B, k=2", "reference_solution": "repaired", "answer": "4",
            "rubric": [{"criterion_name": "corrected", "max_score": 8}], "max_score": 8}


@pytest.mark.parametrize("verdict,issues,repair,expected", [
    ("pass", [], None, "validated"),
    ("pass", ["arithmetic"], correction(), "validated"),
    ("fail", ["arithmetic"], correction(), "validated"),
    ("pass", ["Harmless rounding within tolerance"], None, "validated"),
    ("fail", ["unresolved"], None, "needs_review"),
    ("pass", [], {"statement": "incomplete"}, "needs_review"),
    ("pass", ["Harmless note"], {"statement": "incomplete"}, "needs_review"),
    ("pass", [], {}, "needs_review"),
    ("pass", [], [], "needs_review"),
    ("pass", [], "malformed", "needs_review"),
    ("pass", [], {**correction(), "answer": {"value": 4}}, "needs_review"),
    ("unknown", [], correction(), "needs_review"),
])
def test_verifier_contract(monkeypatch, verdict, issues, repair, expected):
    calls = []
    async def chat(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(text=json.dumps(dict(verdict=verdict, issues=issues,
            corrected_task=repair, verification={})))
    monkeypatch.setattr(pc.llm, "chat", chat)
    async def run():
        original = task()
        result, fixed = await pc.run_physical_validation(task=original, provider=None,
            model="deepseek-v4-pro", grounding="", discipline_context="", system_prompt="CUSTOM")
        assert result["verdict"] == expected
        assert result["correction"]["applied"] is False
        assert evidence_matches_task(result, original)
        assert bool(fixed) == (expected == "validated" and repair is not None)
        assert len(calls) == 1 and calls[0][2] == "CUSTOM"
        if expected == "validated":
            assert result["verifier"]["issues"] == issues
            assert result["reasons"] == []
    asyncio.run(run())


@pytest.mark.parametrize("configured", [None, "", "  \n", "Editable domain rules and JSON contract"])
def test_verifier_prompt_is_authoritative_or_fallback(monkeypatch, configured):
    calls = []
    async def chat(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(text='{"verdict":"pass","issues":[],"corrected_task":null}')
    monkeypatch.setattr(pc.llm, "chat", chat)
    asyncio.run(pc.run_physical_validation(task=task(), provider=None, model="deepseek-v4-pro",
        grounding="", discipline_context="", system_prompt=configured))
    expected = configured if configured and configured.strip() else pc.PHYSICAL_CHEMISTRY_VERIFIER_PROMPT
    assert calls[0][2] == expected


@pytest.mark.parametrize("configured", [None, "Editable verifier rules"])
def test_verifier_preview_matches_runtime_prompt(monkeypatch, configured):
    from app.api import preview
    from app.schemas import PromptPreviewRequest
    async def run():
        engine, sessions = await setup_db()
        async with sessions() as db:
            assistant = Assistant(id="assistant", name="Физическая химия", discipline="Физическая химия")
            db.add(assistant)
            if configured is not None:
                db.add(PromptVersion(assistant_id=assistant.id, role="verifier", status="active",
                    version=1, target_family="deepseek", system_prompt=configured))
            await db.commit()
            async def grounding(*args, **kwargs):
                return "course context"
            monkeypatch.setattr(preview, "build_grounding_block", grounding)
            result = await preview.prompt_preview(assistant.id, PromptPreviewRequest(role="verifier"), db, None)
            assert result.system_prompt == pc.physical_verifier_prompt(configured)
            assert json.loads(result.user_message)["canonical_grounding"] == "course context"
        await engine.dispose()
    asyncio.run(run())


async def setup_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.parametrize("edit", [None, "statement", "rubric", "topic", "difficulty", "grounding"])
def test_batch_same_row_and_concurrent_edit(monkeypatch, edit):
    async def run():
        engine, sessions = await setup_db()
        async with sessions() as db:
            a = Assistant(id="assistant", name="Физическая химия", discipline="Физическая химия")
            b = GenerationBatch(id="batch", assistant_id=a.id, validated_count=0)
            t = task()
            db.add_all([a, b, t, PromptVersion(assistant_id=a.id, role="verifier", status="active",
                version=1, target_family="deepseek", system_prompt="CONFIGURED VERIFIER")])
            db.add(PromptVersion(assistant_id=a.id, role="grader", status="active", version=1,
                target_family="qwen", system_prompt="STUDENT GRADING ONLY"))
            await db.commit()
            calls = []
            async def chat(*args, **kwargs):
                calls.append(args)
                if edit:
                    async with sessions() as other:
                        edited = await other.get(GeneratedTask, t.id)
                        setattr(edited, edit, {"teacher": True} if edit == "grounding" else
                            [{"criterion_name": "teacher", "max_score": 10}] if edit == "rubric" else "teacher")
                        await other.commit()
                return SimpleNamespace(text=json.dumps(dict(verdict="pass", issues=["fix"],
                    corrected_task=correction(), verification={})))
            monkeypatch.setattr(pc.llm, "chat", chat)
            monkeypatch.setattr(taskgen, "task_is_export_ready", lambda _: True)
            await taskgen._validate_batch(db, b, [t], dict(validation_solver=False,
                answer_format="numeric", tolerance_pct=2), None,
                SimpleNamespace(model_id="deepseek-v4-pro", family="deepseek"), "", "")
            await db.refresh(t)
            assert len(calls) == 1 and "CONFIGURED VERIFIER" in calls[0][2]
            assert "STUDENT GRADING ONLY" not in calls[0][2]
            assert t.id == "task" and t.batch_id == "batch"
            if edit:
                assert t.reference_solution == "original" and t.status == "draft"
                assert t.validation == {} and b.validated_count == 0
                assert "teacher" in str(getattr(t, edit))
            else:
                assert t.reference_solution == "repaired" and t.answer == "4"
                assert t.rubric[0]["criterion_name"] == "corrected" and t.max_score == 8
                assert t.validation["correction"]["applied"] is True
                assert evidence_matches_task(t.validation, t) and b.validated_count == 1
        await engine.dispose()
    asyncio.run(run())


def test_runtime_separates_student_qwen_from_task_deepseek():
    async def run():
        engine, sessions = await setup_db()
        async with sessions() as db:
            provider = Provider(id="provider", name="test", kind="yandex", enabled=True, base_url="unused")
            qwen = ModelEntry(id="qwen", provider_id=provider.id, enabled=True, family="qwen",
                model_id="gpt://b1g0ibcval4b15nf4jcj/qwen3.6-35b-a3b")
            verifier = ModelEntry(id="deepseek", provider_id=provider.id, enabled=True, family="deepseek",
                model_id="deepseek-v4-pro")
            assistant = Assistant(id="assistant", name="Физическая химия", discipline="Физическая химия",
                default_generator_model_id=qwen.id, default_grader_model_id=qwen.id, verifier_model_id=verifier.id)
            db.add_all([provider, qwen, verifier, assistant])
            await db.commit()
            policy = await integration._build_runtime_policy(db, assistant)
            assert policy["tutor_model_id"] == policy["decision_model_id"] == qwen.model_id
            assert policy["tier"] == "decision" and "task_validation" not in policy["allowed_uses"]
            assert pc.task_verifier_model_id(assistant) == verifier.id
            assert not pc.current_model_use_policy().classify(qwen).decision_capable
            assistant.discipline = assistant.name = "Аналитическая химия"
            assert not pc.student_grading_model_use(assistant, qwen).decision_capable
            assistant.verifier_model_id = None
            assert pc.task_verifier_model_id(assistant) == qwen.id
        await engine.dispose()
    asyncio.run(run())


def test_physical_does_not_fall_back_to_student_grader():
    assistant = Assistant(name="Физическая химия", discipline="Физическая химия", default_grader_model_id="qwen")
    assert pc.task_verifier_model_id(assistant) is None


def test_verifier_schema_and_prompt_contract():
    from app.schemas import AssistantUpdate, PromptVersionCreate, PromptGenerateRequest, PromptPreviewRequest
    from app.services.meta_prompt import build_meta_prompt
    assert AssistantUpdate(verifier_model_id="deepseek").verifier_model_id == "deepseek"
    assert PromptVersionCreate(role="verifier", system_prompt="check").role == "verifier"
    assert PromptGenerateRequest(role="verifier", target_model_entry_id="deepseek").role == "verifier"
    assert PromptPreviewRequest(role="verifier").role == "verifier"
    assistant = Assistant(name="Физическая химия", discipline="Физическая химия", criteria=[], nuances=[], topics=[])
    assert "corrected_task" in build_meta_prompt(assistant, "verifier", "deepseek")


@pytest.mark.parametrize("generation_error", [False, True])
def test_physical_batch_never_buys_replacement(monkeypatch, generation_error):
    async def run():
        engine, sessions = await setup_db()
        async with sessions() as db:
            assistant = Assistant(id="assistant", name="Физическая химия", discipline="Физическая химия",
                verifier_model_id="deepseek", criteria=[], topics=[], nuances=[])
            batch = GenerationBatch(id="batch", assistant_id=assistant.id,
                requested_count=1, generated_count=0, validated_count=0,
                params={"model_entry_id": "qwen", "count": 1})
            db.add_all([assistant, batch])
            await db.commit()
            calls = []
            async def resolve(_, model_id):
                return SimpleNamespace(name="test"), SimpleNamespace(model_id="deepseek-v4-pro" if model_id == "deepseek" else "qwen",
                    family="deepseek" if model_id == "deepseek" else "qwen", enabled=True)
            async def grounding(*a, **k): return ""
            async def generate(*a, **k):
                calls.append("generator")
                if generation_error:
                    raise pc.llm.LlmError("mock provider failure")
                return [{**correction(), "data_used": [], "chemistry_facts": {}}]
            async def chat(*a, **k):
                calls.append("verifier")
                return SimpleNamespace(text=json.dumps({"verdict": "fail", "issues": ["cannot repair"], "corrected_task": None}))
            async def forbidden(*a, **k): raise AssertionError("generic critic or generator repair invoked")
            monkeypatch.setattr(taskgen, "_resolve_batch_model", resolve)
            monkeypatch.setattr(taskgen, "build_generation_grounding", grounding)
            monkeypatch.setattr(taskgen, "generate_tasks", generate)
            monkeypatch.setattr(taskgen, "run_validation", forbidden)
            monkeypatch.setattr(taskgen, "repair_generated_task", forbidden)
            monkeypatch.setattr(pc.llm, "chat", chat)
            if generation_error:
                with pytest.raises(pc.llm.LlmError):
                    await taskgen._execute_batch(db, batch)
                assert calls == ["generator"]
            else:
                await taskgen._execute_batch(db, batch)
                assert calls == ["generator", "verifier"]
                tasks = (await db.execute(select(GeneratedTask))).scalars().all()
                assert len(tasks) == 1 and tasks[0].status == "needs_review"
                assert batch.params["quality_summary"]["replacement_generations"] == 0
                # Completion describes the finished generation run, not the
                # admission of every candidate; review tasks remain stored.
                assert batch.status == "completed" and batch.validated_count == 0
        await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("difficulty", ["easy", "hard", "medium"])
def test_student_template_difficulty(monkeypatch, difficulty):
    async def run():
        engine, sessions = await setup_db()
        async with sessions() as db:
            a = Assistant(id="assistant", name="Физическая химия", discipline="Физическая химия",
                default_generator_model_id="gen", default_grader_model_id="grade", verifier_model_id="verify")
            db.add(a)
            for level in ["easy", "hard"]:
                db.add(TaskTemplate(id=level, name=level, assistant_id=a.id, topic="kinetics", difficulty=level))
            await db.commit()
            monkeypatch.setattr(integration, "_authenticate_picrete_generation", lambda _: None)
            async def resolve(_, model_id):
                return SimpleNamespace(name="test"), SimpleNamespace(id=model_id, model_id=model_id)
            async def prompt(*args):
                return None
            class StopBeforeLLM(Exception):
                pass
            async def stop(_):
                raise StopBeforeLLM()
            monkeypatch.setattr(integration, "resolve_model", resolve)
            monkeypatch.setattr(integration, "require_decision_model", lambda *a, **k: None)
            monkeypatch.setattr(integration, "resolve_generator_prompt_version", prompt)
            monkeypatch.setattr(integration, "run_batch", stop)
            body = integration.StudentTrainerGenerationRequest(assistant_id=a.id, topic="kinetics",
                difficulty=difficulty, count=1)
            if difficulty == "medium":
                with pytest.raises(integration.HTTPException) as exc:
                    await integration.generate_student_trainer_tasks(body, "", db)
                assert exc.value.status_code == 422
            else:
                with pytest.raises(StopBeforeLLM):
                    await integration.generate_student_trainer_tasks(body, "", db)
                batch = (await db.execute(select(GenerationBatch))).scalar_one()
                assert batch.template_id == difficulty
                assert batch.params["solver_model_entry_id"] == "verify"
        await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("ready_count,batch_status", [
    (4, "completed"), (4, "failed"), (5, "completed"), (0, "completed"), (0, "failed"),
])
def test_student_delivers_ready_subset_and_retains_pending(monkeypatch, ready_count, batch_status):
    async def run():
        engine, sessions = await setup_db()
        async with sessions() as db:
            assistant = Assistant(id="assistant", name="Физическая химия", discipline="Физическая химия",
                default_generator_model_id="qwen", verifier_model_id="deepseek")
            db.add_all([assistant, TaskTemplate(id="template", name="kinetics", assistant_id=assistant.id,
                topic="kinetics", difficulty="easy")])
            await db.commit()
            calls = []
            async def resolve(_, model_id):
                return SimpleNamespace(name="test"), SimpleNamespace(id=model_id, model_id=model_id)
            async def prompt(*args):
                return None
            async def batch_run(batch_id):
                calls.append(batch_id)
                batch = await db.get(GenerationBatch, batch_id)
                batch.status = batch_status
                batch.generated_count = 5
                batch.validated_count = ready_count
                for index in range(5):
                    candidate = task()
                    candidate.id = f"candidate-{index}"
                    candidate.batch_id = batch_id
                    candidate.status = "validated" if index < ready_count else "needs_review"
                    db.add(candidate)
                await db.commit()
            monkeypatch.setattr(integration, "_authenticate_picrete_generation", lambda _: None)
            monkeypatch.setattr(integration, "resolve_model", resolve)
            monkeypatch.setattr(integration, "require_decision_model", lambda *a, **k: None)
            monkeypatch.setattr(integration, "resolve_generator_prompt_version", prompt)
            monkeypatch.setattr(integration, "run_batch", batch_run)
            monkeypatch.setattr(integration, "task_is_export_ready", lambda t: t.status == "validated")
            body = integration.StudentTrainerGenerationRequest(assistant_id=assistant.id, topic="kinetics", count=5)
            if ready_count:
                result = await integration.generate_student_trainer_tasks(body, "", db)
                assert result["requested_count"] == 5
                assert result["ready_count"] == ready_count
                assert result["pending_count"] == 5 - ready_count
                assert sum(len(p["tasks"]) for p in result["paragraphs"]) == ready_count
                assert result["student_batch_id"] == calls[0]
            else:
                with pytest.raises(integration.HTTPException) as exc:
                    await integration.generate_student_trainer_tasks(body, "", db)
                assert exc.value.status_code == 502
            assert len(calls) == 1
            stored = (await db.execute(select(GeneratedTask))).scalars().all()
            assert len(stored) == 5
            assert sum(t.status == "needs_review" for t in stored) == 5 - ready_count
        await engine.dispose()
    asyncio.run(run())
