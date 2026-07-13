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
    return (
        value.get("verdict") == "validated"
        and value.get("policy_version") == VALIDATION_POLICY_VERSION
        and model_policy.get("decision_capable") is True
        and current_use.decision_capable
        and model_policy.get("policy_version") == current_use.policy_version
    )


def has_complete_approval(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    approval = value.get("approval")
    if not isinstance(approval, dict):
        return False
    basis = approval.get("basis")
    if basis not in {"policy_validated", "teacher_override"}:
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
    if basis == "teacher_override":
        return len(str(approval.get("reason") or "").strip()) >= 10
    return validation_is_current_decision(value)
