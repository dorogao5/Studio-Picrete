import json
from collections.abc import Mapping

from app.services.chemistry_validation import (
    CheckState,
    ChemistryTask,
    validate_chemistry_task,
)


CHEMISTRY_FACT_BLOCKS = frozenset(
    {
        "stoichiometry",
        "dilution",
        "titration",
        "gravimetry",
        "conductometry",
        "faraday",
        "calibration",
        "bet",
        "smoluchowski",
        "dlvo",
    }
)

FACT_BLOCK_BY_CHECK = {
    "chemistry.stoichiometry": "stoichiometry",
    "chemistry.dilution": "dilution",
    "analytical.titration": "titration",
    "analytical.gravimetry": "gravimetry",
    "analytical.conductometry": "conductometry",
    "analytical.faraday": "faraday",
    "analytical.calibration": "calibration",
    "colloid.bet": "bet",
    "colloid.smoluchowski": "smoluchowski",
    "colloid.dlvo": "dlvo",
}

CHECK_BY_FACT_BLOCK = {block: check_id for check_id, block in FACT_BLOCK_BY_CHECK.items()}
GENERIC_CHECK_IDS = frozenset(
    {"chemistry.units", "chemistry.reaction_balance", "chemistry.scale_compatibility"}
)


def normalize_chemistry_facts(value: object) -> dict | None:
    """Accept only bounded JSON fact blocks understood by deterministic checks."""

    if not isinstance(value, Mapping):
        return None
    normalized: dict = {}
    for key, block in value.items():
        name = str(key).strip()
        if name not in CHEMISTRY_FACT_BLOCKS or not isinstance(block, Mapping):
            return None
        try:
            clean = json.loads(json.dumps(dict(block), ensure_ascii=False))
        except (TypeError, ValueError):
            return None
        if len(json.dumps(clean, ensure_ascii=False)) > 20_000:
            return None
        normalized[name] = clean
    return normalized


def required_check_ids(chemistry_check: str, facts: Mapping[str, object]) -> set[str]:
    required = {
        CHECK_BY_FACT_BLOCK[block]
        for block in facts
        if block in CHECK_BY_FACT_BLOCK
    }
    if chemistry_check != "auto" and chemistry_check in FACT_BLOCK_BY_CHECK:
        required.add(chemistry_check)
    return required


def chemistry_admission_evidence(
    *,
    discipline: str,
    statement: str,
    reference_solution: str,
    answer: str,
    topic: str,
    facts: Mapping[str, object],
    facts_source: str,
    chemistry_check: str,
) -> dict:
    required = required_check_ids(chemistry_check, facts)
    explicit_core_check = chemistry_check != "auto" and chemistry_check in FACT_BLOCK_BY_CHECK
    selected = None if chemistry_check == "auto" else set(GENERIC_CHECK_IDS) | required
    report = validate_chemistry_task(
        ChemistryTask(
            discipline=discipline,
            statement=statement,
            reference_solution=reference_solution,
            answer=answer,
            topic=topic,
            facts=facts,
        ),
        selected_checks=selected,
    ).to_dict()
    results = report["results"]
    states = {result["check_id"]: result["state"] for result in results}
    unsafe_states = {
        CheckState.FAIL.value,
        CheckState.WARNING.value,
        CheckState.INDETERMINATE.value,
        CheckState.ERROR.value,
    }
    unsafe = [result for result in results if result["state"] in unsafe_states]
    required_not_passed = sorted(check_id for check_id in required if states.get(check_id) != CheckState.PASS.value)
    passed = [result for result in results if result["state"] == CheckState.PASS.value]
    if unsafe or required_not_passed:
        admission_effect = "block"
    elif explicit_core_check and required and passed:
        admission_effect = "pass"
    else:
        # Generic unit/balance checks alone are useful evidence, but they do not
        # prove the core calculation. Fact blocks supplied by a model under an
        # ``auto`` contract are deliberately not promoted to core evidence: an
        # irrelevant but internally consistent block must never make a numeric
        # task releasable. A frozen template must name its deterministic check.
        admission_effect = "limited"
    return {
        **report,
        "required_check_ids": sorted(required),
        "required_not_passed": required_not_passed,
        "facts_source": facts_source,
        "admission_effect": admission_effect,
    }
