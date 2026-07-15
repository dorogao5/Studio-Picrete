import hashlib
import json
from typing import Any


CONTENT_FINGERPRINT_VERSION = "task-content-v1"
APPROVAL_SCHEMA_VERSION = "teacher-override-v1"


def _json_digest(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_validation_config(value: object) -> dict[str, Any]:
    config = value if isinstance(value, dict) else {}
    try:
        tolerance_pct = float(config.get("tolerance_pct", 0.0))
    except (TypeError, ValueError):
        tolerance_pct = 0.0
    sheet_ids = config.get("sheet_ids")
    if not isinstance(sheet_ids, list):
        sheet_ids = []
    return {
        "answer_format": str(config.get("answer_format") or "").strip(),
        "tolerance_pct": tolerance_pct,
        "validation_solver": config.get("validation_solver") is True,
        "validation_data_check": config.get("validation_data_check") is True,
        "sheet_ids": sorted({str(value).strip() for value in sheet_ids if str(value).strip()}),
        "kb_query": str(config.get("kb_query") or "").strip(),
        "task_kind": str(config.get("task_kind") or "").strip(),
        "chemistry_check": str(config.get("chemistry_check") or "auto").strip(),
        "source_digest": str(config.get("source_digest") or "").strip(),
        "profile_digest": str(config.get("profile_digest") or "").strip(),
        "task_evidence_digest": str(config.get("task_evidence_digest") or "").strip(),
        "chemistry_facts_digest": str(config.get("chemistry_facts_digest") or "").strip(),
    }


def build_task_content_fingerprint(
    *,
    statement: object,
    reference_solution: object,
    answer: object,
    rubric: object,
    max_score: object,
    validation_config: object,
) -> str:
    try:
        normalized_max_score: float | str = float(max_score)
    except (TypeError, ValueError):
        normalized_max_score = str(max_score or "")
    payload = {
        "schema": CONTENT_FINGERPRINT_VERSION,
        "statement": str(statement or ""),
        "reference_solution": str(reference_solution or ""),
        "answer": str(answer or ""),
        "rubric": rubric if isinstance(rubric, list) else [],
        "max_score": normalized_max_score,
        "validation_config": normalize_validation_config(validation_config),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def task_content_fingerprint(task: object, validation_config: object) -> str:
    config = normalize_validation_config(validation_config)
    # Validation binds model-supplied deterministic facts and provenance to the
    # exact task snapshot. Recalculate the task-owned part on every approval or
    # export check so editing grounding cannot leave stale evidence looking valid.
    if config.get("task_evidence_digest"):
        grounding = getattr(task, "grounding", None)
        grounding = grounding if isinstance(grounding, dict) else {}
        config["task_evidence_digest"] = _json_digest(
            {
                "data_used": grounding.get("data_used"),
                "chemistry_facts": grounding.get("chemistry_facts"),
            }
        )
    return build_task_content_fingerprint(
        statement=getattr(task, "statement", ""),
        reference_solution=getattr(task, "reference_solution", ""),
        answer=getattr(task, "answer", ""),
        rubric=getattr(task, "rubric", []),
        max_score=getattr(task, "max_score", 0),
        validation_config=config,
    )


def evidence_matches_task(value: object, task: object) -> bool:
    if not isinstance(value, dict):
        return False
    config = value.get("validation_config")
    fingerprint = str(value.get("content_fingerprint") or "").strip()
    return bool(fingerprint and fingerprint == task_content_fingerprint(task, config))
