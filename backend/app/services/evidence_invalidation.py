from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GeneratedTask


async def invalidate_task_evidence(
    db: AsyncSession,
    assistant_id: str,
    *,
    reason: str,
    template_id: str | None = None,
    knowledge_change: bool = False,
) -> int:
    """Mark stored decisions stale after an input to their evidence changes.

    Task content is preserved. Only release eligibility is revoked, so the
    background revalidation queue can rebuild evidence without a teacher
    reviewing every task.
    """

    statement = select(GeneratedTask).where(
        GeneratedTask.assistant_id == assistant_id,
        GeneratedTask.status.in_(("validated", "approved", "needs_review")),
    )
    if template_id is not None:
        statement = statement.where(GeneratedTask.template_id == template_id)
    tasks = list((await db.execute(statement)).scalars())
    invalidated_at = datetime.now(UTC).isoformat()
    count = 0
    for task in tasks:
        grounding = getattr(task, "grounding", None)
        grounding = grounding if isinstance(grounding, dict) else {}
        previous_validation = task.validation if isinstance(task.validation, dict) else {}
        if (knowledge_change and previous_validation.get("policy_version") == "single-verifier-v1"
                and grounding.get("blueprint") and grounding.get("kb_chunks") == 0
                and grounding.get("sheets") == [] and grounding.get("data_used") == []):
            # A new/unrelated KB item cannot change a self-contained candidate
            # whose verifier used no KB chunks or reference sheets.
            continue
        count += 1
        previous = task.validation if isinstance(task.validation, dict) else {}
        validation = dict(previous)
        validation.pop("approval", None)
        prior_reasons = [str(value) for value in validation.get("reasons") or [] if str(value).strip()]
        validation.update(
            verdict="needs_review",
            reasons=[reason, *[value for value in prior_reasons if value != reason]],
            stale_evidence={
                "reason": reason,
                "invalidated_at": invalidated_at,
                "previous_content_fingerprint": str(previous.get("content_fingerprint") or ""),
            },
        )
        task.validation = validation
        task.status = "needs_review"
        task.approved = False
    return count
