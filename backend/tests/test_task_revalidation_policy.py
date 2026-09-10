from types import SimpleNamespace

from app.services.task_revalidation import _generated_candidate_should_be_discarded


def _task(*, model_used: str = "", batch_id: str | None = None):
    return SimpleNamespace(model_used=model_used, batch_id=batch_id)


def test_failed_generated_candidate_is_discarded_without_teacher_queue() -> None:
    assert _generated_candidate_should_be_discarded(
        _task(model_used="DeepSeek/deepseek-v4-pro"),
        {"verdict": "needs_review", "critic": {"status": "fail"}},
    )
    assert _generated_candidate_should_be_discarded(
        _task(batch_id="batch-1"),
        {"verdict": "needs_review", "critic": {"status": "fail"}},
    )


def test_manual_material_and_validated_candidate_are_not_discarded() -> None:
    assert not _generated_candidate_should_be_discarded(
        _task(),
        {"verdict": "needs_review"},
    )
    assert not _generated_candidate_should_be_discarded(
        _task(model_used="DeepSeek/deepseek-v4-pro"),
        {"verdict": "validated"},
    )


def test_skipped_or_unavailable_checks_do_not_reject_a_candidate():
    assert not _generated_candidate_should_be_discarded(
        _task(batch_id="batch-1"),
        {"verdict": "needs_review", "critic": {"status": "skipped"},
         "chemistry": {"indeterminate_codes": ["chemistry.reaction_balance"]}},
    )


def test_repaired_batch_count_is_updated_without_approving_tasks(monkeypatch):
    import asyncio
    from app.services import task_revalidation as service
    source = SimpleNamespace(id='batch', assistant_id='course', status='failed',
                             requested_count=3, validated_count=0, error='old failure', params={})
    tasks = [SimpleNamespace(ready=True, status="validated") for _ in range(3)]
    class Db:
        async def get(self, *_): return source
        async def execute(self, *_): return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: tasks))
        async def commit(self): pass
    monkeypatch.setattr(service, 'task_is_export_ready', lambda task: task.ready)
    asyncio.run(service.sync_generation_batch(Db(), SimpleNamespace(batch_id='batch')))
    assert source.validated_count == 3
    assert source.status == 'completed'
    assert source.error == ''
    assert source.params['original_run_error'] == 'old failure'
    source.status = 'running'
    source.validated_count = 0
    asyncio.run(service.sync_generation_batch(Db(), SimpleNamespace(batch_id='batch')))
    assert source.validated_count == 0
