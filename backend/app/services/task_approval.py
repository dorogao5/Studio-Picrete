from datetime import datetime

from app.services.model_policy import current_model_use_policy
from app.services.validation import VALIDATION_POLICY_VERSION


def validation_is_current_decision(value: object) -> bool:
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
    reference_solution_check = value.get("reference_solution_check")
    data = value.get("data")
    sanity = value.get("sanity")
    dedup = value.get("dedup")
    reasons = value.get("reasons")
    return (
        value.get("verdict") == "validated"
        and value.get("answer_format") in {"numeric", "choice", "text"}
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
        and isinstance(reference_solution_check, dict)
        and reference_solution_check.get("verdict") == "match"
        and isinstance(data, dict)
        and data.get("status") == "ok"
        and not data.get("unknown_numbers")
        and not data.get("unknown_sources")
        and isinstance(sanity, dict)
        and sanity.get("issues") == []
        and isinstance(dedup, dict)
        and dedup.get("duplicate") is False
        and reasons == []
    )


def has_complete_approval(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    approval = value.get("approval")
    if not isinstance(approval, dict):
        return False
    basis = approval.get("basis")
    if basis != "teacher_override":
        return False
    if not str(approval.get("reviewed_by") or "").strip():
        return False
    reviewed_at = str(approval.get("reviewed_at") or "").strip()
    try:
        datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if approval.get("policy_version") != VALIDATION_POLICY_VERSION:
        return False
    return len(str(approval.get("reason") or "").strip()) >= 10


def task_is_export_ready(task: object) -> bool:
    status = getattr(task, "status", None)
    validation = getattr(task, "validation", None)
    approved = getattr(task, "approved", False)
    if status == "validated" and not approved:
        return validation_is_current_decision(validation)
    return status == "approved" and approved is True and has_complete_approval(validation)
