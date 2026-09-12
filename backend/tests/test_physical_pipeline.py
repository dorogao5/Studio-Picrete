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


@pytest.mark.parametrize("physical", [True, False])
def test_selected_topic_metadata_is_authoritative_only_when_requested(physical):
    item = {**correction(), "topic": "Model shorthand", "chemistry_facts": {}, "data_used": []}
    saved = taskgen.task_from_item(item, assistant_id="assistant", template_id=None, batch_id="batch",
        topic="Selected canonical topic", difficulty="easy", model_used="test", grounding_meta={},
        authoritative_topic=physical)
    assert saved.topic == ("Selected canonical topic" if physical else "Model shorthand")
    assert item["topic"] == "Model shorthand"
    fixed = pc._normalized_correction({**correction(), "topic": "Verifier rename"}, pc._task_payload(saved))
    assert fixed["topic"] == saved.topic


@pytest.mark.parametrize("physical", [True, False])
def test_generator_blueprint_and_json_contract_are_scoped(monkeypatch, physical):
    from app.services.contracts import GENERATION_JSON_CONTRACT, PHYSICAL_GENERATION_JSON_EXAMPLE
    discipline = "Физическая химия" if physical else "Аналитическая химия"
    assistant = Assistant(name=discipline, discipline=discipline, criteria=[], topics=[], nuances=[])
    calls = []
    async def chat(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(text=PHYSICAL_GENERATION_JSON_EXAMPLE)
    monkeypatch.setattr(pc.llm, "chat", chat)
    asyncio.run(taskgen.generate_tasks(None, None, assistant, None, topic="Кинетика Михаэлиса–Ментен",
        difficulty="medium", count=1, existing_statements=["existing numerical task"], chemistry_check="auto"))
    assert len(calls) == 1
    system, message = calls[0][0][2:4]
    assert "existing numerical task" in message
    if physical:
        assert "Повторное использование сюжета" in message
        assert "Не повторяйте целиком набор числовых исходных данных" in message
        assert "НЕ повторяйте их сюжеты и числа" not in message
        assert "непустой массив tasks" in message
        assert PHYSICAL_GENERATION_JSON_EXAMPLE in system and PHYSICAL_GENERATION_JSON_EXAMPLE in message
        assert "<тип проверяемого расчёта>" not in system + message
        assert "chemistry_facts всегда {}" in message
    else:
        assert "НЕ повторяйте их сюжеты и числа" in message
        assert GENERATION_JSON_CONTRACT in system and GENERATION_JSON_CONTRACT in message
        assert "chemistry_facts для детерминированной перепроверки" in message


def test_physical_json_example_is_complete_valid_json():
    from app.services.contracts import PHYSICAL_GENERATION_JSON_EXAMPLE
    example = json.loads(PHYSICAL_GENERATION_JSON_EXAMPLE)
    assert set(example) == {"tasks"} and len(example["tasks"]) == 1
    candidate = example["tasks"][0]
    assert set(candidate) == {"statement", "reference_solution", "answer", "images", "rubric",
                              "max_score", "difficulty", "topic", "data_used", "chemistry_facts"}
    assert candidate["chemistry_facts"] == {} and candidate["data_used"] == []
    assert sum(c["max_score"] for c in candidate["rubric"]) == candidate["max_score"]


def test_physical_generation_preview_uses_matching_contract():
    from app.api.preview import _build_generation_message
    from app.services.contracts import PHYSICAL_GENERATION_JSON_EXAMPLE
    message = _build_generation_message(None, "course context", ["old task"], reuse_blueprint=True)
    assert PHYSICAL_GENERATION_JSON_EXAMPLE in message
    assert "Повторное использование сюжета" in message
    assert "<тип проверяемого расчёта>" not in message


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
            corrected_task=repair, verification={"solution": "verified full solution", "answer": "4"})))
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


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("verdict", [" PASS ", "fail"])
def test_verified_reference_overlay_only_for_tools_pass(monkeypatch, enabled, verdict):
    async def chat(*args, **kwargs):
        return SimpleNamespace(text=json.dumps({"verdict": verdict, "issues": ["rounding note"],
            "corrected_task": None, "verification": {"solution": "Full derivation: k=0.1276434",
                "answer": "0.1276", "statement": "untrusted replacement", "rubric": []}}), raw={})
    monkeypatch.setattr(pc.llm, "chat", chat)
    original = task()
    before = pc._task_payload(original)
    validation, fixed = asyncio.run(pc.run_physical_validation(task=original, provider=None,
        model="deepseek-v4-pro", grounding="", discipline_context="", essential_tools=enabled))
    assert pc._task_payload(original) == before
    assert evidence_matches_task(validation, original)
    assert validation["verdict"] == ("validated" if verdict.strip().lower() == "pass" else "needs_review")
    if enabled and verdict.strip().lower() == "pass":
        assert fixed == {**before, "reference_solution": "Full derivation: k=0.1276434", "answer": "0.1276"}
        assert original.id == "task" and validation["correction"]["applied"] is False
    else:
        assert fixed is None


@pytest.mark.parametrize("verification", [None, {}, [], "bad", {"solution": "full"},
    {"solution": "", "answer": "4"}, {"solution": "full", "answer": "  "},
    {"solution": 123, "answer": "4"}, {"solution": "full", "answer": {"value": 4}}])
@pytest.mark.parametrize("enabled", [False, True])
def test_overlay_requires_nonempty_string_fields_without_breaking_legacy(monkeypatch, verification, enabled):
    async def chat(*args, **kwargs):
        return SimpleNamespace(text=json.dumps({"verdict": "pass", "issues": [],
            "corrected_task": None, "verification": verification}), raw={})
    monkeypatch.setattr(pc.llm, "chat", chat)
    original = task()
    validation, fixed = asyncio.run(pc.run_physical_validation(task=original, provider=None,
        model="deepseek-v4-pro", grounding="", discipline_context="", essential_tools=enabled))
    assert validation["verdict"] == ("needs_review" if enabled else "validated")
    assert fixed is None and original.reference_solution == "original"


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


@pytest.mark.parametrize("overlay", [False, True])
@pytest.mark.parametrize("edit", [None, "statement", "rubric", "topic", "difficulty", "grounding"])
def test_batch_same_row_and_concurrent_edit(monkeypatch, edit, overlay):
    async def run():
        engine, sessions = await setup_db()
        async with sessions() as db:
            a = Assistant(id="assistant", name="Физическая химия", discipline="Физическая химия",
                          verifier_tools_enabled=overlay)
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
                    corrected_task=None if overlay else {**correction(), "topic": "Verifier rename"},
                    verification={"solution": "repaired", "answer": "4"})), raw={})
            monkeypatch.setattr(pc.llm, "chat", chat)
            monkeypatch.setattr(taskgen, "task_is_export_ready", lambda _: True)
            await taskgen._validate_batch(db, b, [t], dict(validation_solver=False,
                answer_format="numeric", tolerance_pct=2), None,
                SimpleNamespace(model_id="deepseek-v4-pro", family="deepseek"), "", "")
            await db.refresh(t)
            assert len(calls) == 1 and "CONFIGURED VERIFIER" in calls[0][2]
            assert "STUDENT GRADING ONLY" not in calls[0][2]
            assert t.id == "task" and t.batch_id == "batch"
            if edit != "topic":
                assert t.topic == "kinetics"
            if edit:
                assert t.reference_solution == "original" and t.status == "draft"
                assert t.validation == {} and b.validated_count == 0
                assert "teacher" in str(getattr(t, edit))
            else:
                assert t.reference_solution == "repaired" and t.answer == "4"
                assert t.rubric[0]["criterion_name"] == ("old" if overlay else "corrected")
                assert t.max_score == (10 if overlay else 8)
                assert t.validation["correction"]["applied"] is True
                assert evidence_matches_task(t.validation, t) and b.validated_count == 1
        await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("schema_enabled", [True, False])
def test_runtime_separates_student_qwen_from_task_deepseek(monkeypatch, schema_enabled):
    monkeypatch.setattr(pc, "get_settings", lambda: SimpleNamespace(
        json_schema_model_ids="gpt://b1g0ibcval4b15nf4jcj/qwen3.6-35b-a3b" if schema_enabled else ""))
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
            assert policy["decision_supports_json_schema"] is schema_enabled
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
@pytest.mark.parametrize("tools_audit", [False, True])
def test_physical_batch_never_buys_replacement(monkeypatch, generation_error, tools_audit):
    async def run():
        engine, sessions = await setup_db()
        async with sessions() as db:
            assistant = Assistant(id="assistant", name="Физическая химия", discipline="Физическая химия",
                verifier_model_id="deepseek", criteria=[], topics=[], nuances=[])
            batch = GenerationBatch(id="batch", assistant_id=assistant.id,
                requested_count=1, generated_count=0, validated_count=0,
                params={"model_entry_id": "qwen", "count": 1, "topic": "Selected canonical topic"})
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
                    failed = {"finish_reason": "length", "usage": {
                        "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
                        "PRIVATE": "private model input"}, "content": "PRIVATE"}
                    raw = ({"failed_completion": failed, "model_calls": 2,
                        "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
                        "tool_traces": [{"arguments": "PRIVATE"}], "messages": "PRIVATE"}
                        if tools_audit else failed)
                    raise pc.llm.LlmError("mock provider failure", raw=raw)
                return [{**correction(), "topic": "Model shorthand", "data_used": [], "chemistry_facts": {}}]
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
                monkeypatch.setattr(taskgen, "SessionLocal", sessions)
                await taskgen.run_batch(batch.id)
                assert calls == ["generator"]
                # Read through a new session, after run_batch's rollback/error handling.
                async with sessions() as persisted_db:
                    persisted = await persisted_db.get(GenerationBatch, batch.id)
                    assert persisted.status == "failed" and "mock provider failure" in persisted.error
                    assert persisted.generated_count == 0
                    audits = persisted.params["generation_error_audits"]
                    assert len(audits) == 1 and audits[0]["attempt"] == 1
                    assert audits[0]["finish_reason"] == "length"
                    assert audits[0]["usage"]["total_tokens"] == 30
                    assert audits[0]["total_usage"]["total_tokens"] == (50 if tools_audit else 30)
                    assert "PRIVATE" not in json.dumps(audits)
                    assert "tool_traces" not in audits[0] and "messages" not in audits[0]
                    assert (await persisted_db.execute(select(GeneratedTask))).scalars().all() == []
            else:
                await taskgen._execute_batch(db, batch)
                assert calls == ["generator", "verifier"]
                tasks = (await db.execute(select(GeneratedTask))).scalars().all()
                assert len(tasks) == 1 and tasks[0].status == "needs_review"
                assert tasks[0].topic == "Selected canonical topic"
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
