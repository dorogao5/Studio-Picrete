from datetime import datetime

from app.services.model_policy import current_model_use_policy
from app.services.chemistry_validation import CHEMISTRY_VALIDATION_VERSION
from app.services.task_evidence import (
    APPROVAL_SCHEMA_VERSION,
    evidence_matches_task,
    task_content_fingerprint,
)
from app.services.validation import CRITIC_REQUIRED_CHECKS, VALIDATION_POLICY_VERSION


def validation_is_current_decision(value: object, task: object | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    model_policy = value.get("model_policy")
    if not isinstance(model_policy, dict):
        return False
    model_id = str(model_policy.get("model_id") or "").strip()
    current_use = current_model_use_policy().classify(model_id)
    solver = value.get("solver")
    verifier = value.get("verifier")
    cross_comparison = value.get("cross_comparison")
    critic = value.get("critic")
    chemistry = value.get("chemistry")
    reference_solution_check = value.get("reference_solution_check")
    data = value.get("data")
    source_lineage = value.get("source_lineage")
    sanity = value.get("sanity")
    dedup = value.get("dedup")
    reasons = value.get("reasons")
    validation_config = value.get("validation_config")
    calculation_requires_chemistry = isinstance(validation_config, dict) and (
        validation_config.get("task_kind") == "calculation" or value.get("answer_format") == "numeric"
    )
    return (
        value.get("verdict") == "validated"
        and value.get("answer_format") in {"numeric", "choice", "text", "formula"}
        and bool(str(value.get("content_fingerprint") or "").strip())
        and value.get("policy_version") == VALIDATION_POLICY_VERSION
        and model_policy.get("decision_capable") is True
        and current_use.decision_capable
        and model_policy.get("policy_version") == current_use.policy_version
        and isinstance(solver, dict)
        and solver.get("status") == "match"
        and isinstance(solver.get("comparison"), dict)
        and solver["comparison"].get("verdict") == "match"
        and isinstance(verifier, dict)
        and verifier.get("status") == "match"
        and isinstance(verifier.get("comparison"), dict)
        and verifier["comparison"].get("verdict") == "match"
        and isinstance(cross_comparison, dict)
        and cross_comparison.get("verdict") == "match"
        and isinstance(critic, dict)
        and critic.get("status") == "pass"
        and isinstance(critic.get("checks"), dict)
        and all(critic["checks"].get(key) is True for key in CRITIC_REQUIRED_CHECKS)
        and critic.get("issues") == []
        and isinstance(chemistry, dict)
        and chemistry.get("validation_version") == CHEMISTRY_VALIDATION_VERSION
        and chemistry.get("admission_effect") in ({"pass"} if calculation_requires_chemistry else {"pass", "limited"})
        and chemistry.get("blocking_codes") == []
        and chemistry.get("indeterminate_codes") == []
        and chemistry.get("warning_codes") == []
        and isinstance(chemistry.get("results"), list)
        and all(
            next(
                (
                    result.get("state")
                    for result in chemistry["results"]
                    if isinstance(result, dict) and result.get("check_id") == check_id
                ),
                None,
            )
            == "pass"
            for check_id in chemistry.get("required_check_ids") or []
        )
        and isinstance(reference_solution_check, dict)
        and reference_solution_check.get("verdict") == "match"
        and isinstance(data, dict)
        and data.get("status") == "ok"
        and not data.get("unknown_numbers")
        and not data.get("unknown_sources")
        and isinstance(source_lineage, dict)
        and source_lineage.get("status") == "ok"
        and source_lineage.get("unbound_sources") == []
        and isinstance(sanity, dict)
        and sanity.get("issues") == []
        and isinstance(dedup, dict)
        and dedup.get("duplicate") is False
        and reasons == []
        and (task is None or evidence_matches_task(value, task))
    )


def has_complete_approval(value: object, task: object | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    approval = value.get("approval")
    if not isinstance(approval, dict):
        return False
    basis = approval.get("basis")
    if basis != "teacher_override":
        return False
    if approval.get("schema_version") != APPROVAL_SCHEMA_VERSION:
        return False
    if not str(approval.get("reviewed_by") or "").strip():
        return False
    reviewed_at = str(approval.get("reviewed_at") or "").strip()
    try:
        datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    fingerprint = str(approval.get("content_fingerprint") or "").strip()
    if not fingerprint:
        return False
    if task is not None:
        config = approval.get("validation_config")
        if fingerprint != task_content_fingerprint(task, config):
            return False
    return len(str(approval.get("reason") or "").strip()) >= 10


def task_is_export_ready(task: object) -> bool:
    status = getattr(task, "status", None)
    validation = getattr(task, "validation", None)
    approved = getattr(task, "approved", False)
    if status == "validated" and not approved:
        return validation_is_current_decision(validation, task)
    return status == "approved" and approved is True and has_complete_approval(validation, task)
