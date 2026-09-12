from types import SimpleNamespace

from app.services.export import build_bank_export, build_variants_export


def _task(validation: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id="task-1",
        template_id="template-1",
        answer="c = 100 мг/л",
        images=["images/calibration.png"],
        topic="Количественный анализ",
        statement="Рассчитайте концентрацию.",
        reference_solution="По исходным данным c = 100 мг/л.",
        max_score=10,
        rubric=[],
        validation=validation,
    )


def test_variants_export_uses_tolerance_frozen_in_validation_evidence() -> None:
    result = build_variants_export(
        [_task({"validation_config": {"tolerance_pct": 1.5}})],
        {"template-1": 20.0},
    )

    assert result["tasks"][0]["answer_tolerance"] == 1.5


def test_legacy_variants_export_falls_back_to_current_template_tolerance() -> None:
    result = build_variants_export([_task({})], {"template-1": 2.0})

    assert result["tasks"][0]["answer_tolerance"] == 2.0


def test_bank_export_preserves_relative_image_paths() -> None:
    result = build_bank_export([_task({})], source_code="studio", source_title="Химия", version="1")

    assert result["paragraphs"][0]["tasks"][0]["solution"] == "По исходным данным c = 100 мг/л."
    assert result["paragraphs"][0]["tasks"][0]["images"] == ["images/calibration.png"]
