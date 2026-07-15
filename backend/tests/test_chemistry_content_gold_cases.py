from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.chemistry_validation import (
    CHEMISTRY_VALIDATION_VERSION,
    ChemistryTask,
    validate_chemistry_task,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTENT_ROOT = REPOSITORY_ROOT / "ops" / "content"
CONTENT_PACKAGES = tuple(
    package
    for package in sorted(CONTENT_ROOT.iterdir())
    if (package / "gold-cases-r2.json").is_file() and (package / "blueprints-r2.json").is_file()
)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    assert isinstance(value, dict), f"{path} must contain a JSON object"
    return value


def _case_parameters() -> list[Any]:
    parameters: list[Any] = []
    for package in CONTENT_PACKAGES:
        suite = _read_json(package / "gold-cases-r2.json")
        for case in suite["cases"]:
            parameters.append(pytest.param(package, suite, case, id=f"{package.name}:{case['id']}"))
    return parameters


@pytest.mark.parametrize(("package", "suite", "case"), _case_parameters())
def test_r2_gold_and_mutation_case_matches_deterministic_validator(
    package: Path,
    suite: dict[str, Any],
    case: dict[str, Any],
) -> None:
    assert suite["schema_version"] == "picrete-chemistry-gold-cases-v1"
    assert suite["validator_version"] == CHEMISTRY_VALIDATION_VERSION

    selected_check = case["selected_check"]
    selected_checks = None if selected_check == "auto" else {selected_check}
    report = validate_chemistry_task(ChemistryTask(**case["task"]), selected_checks=selected_checks)
    serialized_report = report.to_dict()

    assert serialized_report["validation_version"] == suite["validator_version"]
    expected = case["expected"]
    matching_results = [result for result in report.results if result.check_id == expected["check_id"]]
    assert len(matching_results) == 1, (
        f"{package.name}/{case['id']} expected exactly one {expected['check_id']} result; "
        f"received {[result.check_id for result in report.results]}"
    )
    assert matching_results[0].state.value == expected["state"]

    admission_effect = expected["automatic_admission_effect"]
    if expected["state"] == "pass":
        assert admission_effect in {"pass", "limited_or_pass_with_other_evidence"}
    else:
        assert admission_effect == "block"

    if selected_check != "auto":
        assert expected["check_id"] == selected_check


def test_r2_blueprints_manifests_and_case_suites_have_one_frozen_contract() -> None:
    assert CONTENT_PACKAGES, "no R2 chemistry content packages were discovered"

    for package in CONTENT_PACKAGES:
        manifest = _read_json(package / "import-manifest.json")
        blueprints = _read_json(package / "blueprints-r2.json")
        suite = _read_json(package / "gold-cases-r2.json")

        assert manifest["schema_version"] == "picrete-content-manifest-v1"
        assert blueprints["schema_version"] == "picrete-typed-blueprints-v1"
        assert suite["schema_version"] == "picrete-chemistry-gold-cases-v1"
        assert manifest["content_version"] == blueprints["content_version"] == suite["content_version"]
        assert blueprints["validator_version"] == suite["validator_version"] == CHEMISTRY_VALIDATION_VERSION
        assert manifest["release_artifacts"]["typed_blueprints"] == "blueprints-r2.json"
        assert manifest["release_artifacts"]["gold_and_mutation_cases"] == "gold-cases-r2.json"

        templates = {item["slug"]: item for item in manifest["task_templates"]}
        contracts = {item["template_slug"]: item for item in blueprints["contracts"]}
        cases = {item["id"]: item for item in suite["cases"]}

        assert len(templates) == len(manifest["task_templates"]), f"duplicate template slug in {package.name}"
        assert len(contracts) == len(blueprints["contracts"]), f"duplicate blueprint contract in {package.name}"
        assert len(cases) == len(suite["cases"]), f"duplicate gold/mutation case id in {package.name}"

        typed_templates = {
            slug: item for slug, item in templates.items() if item["payload"]["chemistry_check"] != "auto"
        }
        assert typed_templates.keys() <= contracts.keys(), f"typed template has no blueprint contract in {package.name}"

        for slug, contract in contracts.items():
            assert slug in templates, f"blueprint {slug} is absent from {package.name} manifest"
            assert contract["chemistry_check"] == templates[slug]["payload"]["chemistry_check"]
            assert contract["gold_case_ids"], f"blueprint {slug} has no certification cases"
            for case_id in contract["gold_case_ids"]:
                assert case_id in cases, f"blueprint {slug} references missing case {case_id}"
                assert cases[case_id]["selected_check"] == contract["chemistry_check"]

        for case_id, case in cases.items():
            if case["kind"] == "mutation":
                root_id = case["mutates"]
                assert root_id in cases, f"mutation {case_id} references missing gold case {root_id}"
                assert cases[root_id]["kind"] == "gold"
                assert case["selected_check"] == cases[root_id]["selected_check"]


def test_faraday_r2_cases_and_templates_expose_the_certified_constant() -> None:
    package = CONTENT_ROOT / "analytical_chemistry"
    manifest = _read_json(package / "import-manifest.json")
    blueprints = _read_json(package / "blueprints-r2.json")
    suite = _read_json(package / "gold-cases-r2.json")

    expected_constant = "96485.33212 C/mol"
    faraday_cases = [case for case in suite["cases"] if case["selected_check"] == "analytical.faraday"]
    assert faraday_cases
    for case in faraday_cases:
        assert f"F={expected_constant}" in case["task"]["statement"]
        assert case["task"]["facts"]["faraday"]["faraday_constant"] == expected_constant

    faraday_contracts = [
        contract for contract in blueprints["contracts"] if contract["chemistry_check"] == "analytical.faraday"
    ]
    assert faraday_contracts
    for contract in faraday_contracts:
        schema = contract["chemistry_facts_schema"]
        assert "faraday_constant" in schema["required"]
        assert "C/mol" in schema["types"]["faraday_constant"]

    faraday_templates = [
        template
        for template in manifest["task_templates"]
        if template["payload"]["chemistry_check"] == "analytical.faraday"
    ]
    assert faraday_templates
    for template in faraday_templates:
        instructions = template["payload"]["instructions"]
        assert f"F={expected_constant}" in instructions
        assert "chemistry_facts.faraday" in instructions
