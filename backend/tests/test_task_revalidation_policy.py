from types import SimpleNamespace

from app.services.task_revalidation import _generated_candidate_should_be_discarded


def _task(*, model_used: str = "", batch_id: str | None = None):
    return SimpleNamespace(model_used=model_used, batch_id=batch_id)


def test_failed_generated_candidate_is_discarded_without_teacher_queue() -> None:
    assert _generated_candidate_should_be_discarded(
        _task(model_used="DeepSeek/deepseek-v4-pro"),
        {"verdict": "needs_review"},
    )
    assert _generated_candidate_should_be_discarded(
        _task(batch_id="batch-1"),
        {"verdict": "needs_review"},
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
