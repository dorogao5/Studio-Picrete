from types import SimpleNamespace

from app.services.content_preflight import (
    build_task_preflight,
    create_review_token,
    inspect_task,
    task_release_digest,
    verify_review_token,
)


def task(**overrides):
    values = {
        "id": "task-1",
        "topic": "Термодинамика",
        "statement": "Рассчитайте $\\Delta G$ реакции.",
        "reference_solution": "Подставляем данные и получаем $\\Delta G=-131{,}75$ кДж.",
        "answer": "$-131{,}75$ кДж",
        "images": [],
        "rubric": [{"criterion_name": "Расчёт", "max_score": 10}],
        "max_score": 10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def codes(item) -> set[str]:
    return {issue["code"] for issue in inspect_task(item)}


def test_short_answers_and_spaced_fraction_are_valid() -> None:
    for answer in ("0", "2", "H", "O2"):
        assert "answer_incomplete" not in codes(task(answer=answer))
    assert "formula_fraction" not in codes(task(statement=r"Рассчитайте $\frac {1}{2}$"))


def test_blocks_missing_mandatory_image_and_unsafe_media_paths() -> None:
    missing = task(statement="По рисунку 4 определите знак $\\Delta H$.")
    unsafe = task(images=["../private/diagram.png"])

    assert "image_missing" in codes(missing)
    assert "image_path" in codes(unsafe)


def test_markdown_images_must_be_safe_and_bound_to_export_images() -> None:
    external = task(statement="Схема: ![ячейка](https://cdn.invalid/cell.png)")
    external_scheme = task(statement="Схема: ![ячейка](ftp://cdn.invalid/cell.png)")
    unsafe = task(statement="Схема: ![ячейка](data:image/png;base64,AAAA)")
    absolute = task(statement=r"Схема: ![ячейка](C:\banks\cell.png)")
    unix_absolute = task(statement="Схема: ![ячейка](/srv/banks/cell.png)")
    traversal = task(statement="Схема: ![ячейка](../banks/cell.png)")
    unbound = task(statement="Схема: ![ячейка](images/cell.png)", images=["images/other.png"])
    bound = task(statement="Схема: ![ячейка](images/cell.png)", images=["images/cell.png"])
    bound_with_title = task(
        statement='Схема: ![ячейка](images/cell.png "Электрохимическая ячейка")',
        images=["images/cell.png"],
    )

    assert "image_not_portable" in codes(external)
    assert "image_not_portable" in codes(external_scheme)
    assert "image_unsafe" in codes(unsafe)
    assert "image_path" in codes(absolute)
    assert "image_path" in codes(unix_absolute)
    assert "image_path" in codes(traversal)
    assert "markdown_image_unbound" in codes(unbound)
    assert "markdown_image_unbound" not in codes(bound)
    assert "markdown_image_unbound" not in codes(bound_with_title)


def test_blocks_broken_formula_external_reference_and_sign_loss() -> None:
    broken = task(
        statement="См. пояснение к 7.110 и вычислите $\\frac{a}.",
        reference_solution="Вычисление даёт $-270{,}4$ кДж по приведённым данным.",
        answer="$+270{,}4$ кДж",
    )

    assert {"formula_delimiter", "formula_fraction", "external_reference", "answer_sign_mismatch"} <= codes(broken)


def test_blocks_reference_to_an_unbundled_appendix() -> None:
    external = task(statement="Вычислите энергию, используя данные Приложения 1.")

    assert "external_reference" in codes(external)


def test_warns_when_compound_task_answer_has_fewer_subparts() -> None:
    compound = task(
        statement="1) Найдите массу; 2) определите выход продукта.",
        reference_solution="Решены оба пункта: масса равна 10 г, выход равен 80 %.",
        answer="1) 10 г",
    )

    assert "compound_split" in codes(compound)


def test_review_token_is_bound_to_exact_release_and_expires() -> None:
    tasks = [task()]
    digest = task_release_digest(tasks, "bank")
    token = create_review_token(scope="task-export:bank", digest=digest, secret_key="secret", now=100)

    assert verify_review_token(token, scope="task-export:bank", digest=digest, secret_key="secret", now=150)
    assert not verify_review_token(token, scope="task-export:variants", digest=digest, secret_key="secret", now=150)
    assert not verify_review_token(token, scope="task-export:bank", digest="changed", secret_key="secret", now=150)
    assert not verify_review_token(token, scope="task-export:bank", digest=digest, secret_key="secret", now=4000)


def test_clean_task_preflight_mints_review_token() -> None:
    result = build_task_preflight([task(images=["images/diagram.png"])], mode="bank", secret_key="secret")

    assert result["ok"] is True
    assert result["summary"] == {"tasks": 1, "images": 1, "blockers": 0, "warnings": 0}
    assert result["review_token"]


def test_legacy_review_survives_only_while_task_has_no_images() -> None:
    from app.services.task_evidence import evidence_matches_task, task_content_fingerprint
    item = task()
    evidence = {"validation_config": {}, "content_fingerprint": task_content_fingerprint(item, {}, legacy_without_images=True)}
    assert evidence_matches_task(evidence, item)
    item.images = ["images/new.png"]
    assert not evidence_matches_task(evidence, item)
