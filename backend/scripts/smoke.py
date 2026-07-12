"""Смоук без LLM: KB-ингест (md), поиск, справочники, блюпринты, превью промптов, экспорт.

Запуск: из backend/ — .venv/bin/python scripts/smoke.py (поднимает uvicorn на 8199 с временной БД).
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pymupdf

PORT = 8199
BASE = f"http://127.0.0.1:{PORT}"

SAMPLE_MD = """# Физическая химия

## Термохимия

Закон Гесса: тепловой эффект реакции не зависит от пути. Энтальпия образования вещества.

## Химическое равновесие и энергия Гиббса

Стандартная энергия Гиббса реакции связана с константой равновесия уравнением ΔG° = −RT ln K.

| Вещество | ΔG°f, кДж/моль |
|----------|----------------|
| CO2(г)   | −394,4         |
| H2O(ж)   | −237,2         |
| NH3(г)   | −16,5          |
"""


def highlighted_answer_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Question 1", fontsize=12)
    page.insert_text((72, 96), "A. Wrong answer", fontsize=12)
    page.insert_text((72, 120), "B. Correct answer", fontsize=12)
    page.insert_textbox(
        pymupdf.Rect(72, 150, 520, 260),
        "Reference explanation for the answer key. " * 5,
        fontsize=10,
    )
    rects = page.search_for("B. Correct answer")
    assert rects
    page.add_highlight_annot(rects)
    content = doc.tobytes()
    doc.close()
    return content


def wait_health(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE}/healthz", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.5)
    raise SystemExit("сервер не поднялся")


def main() -> None:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(("OK  " if ok else "FAIL") + f" {name}" + (f" — {detail}" if detail and not ok else ""))

    client = httpx.Client(base_url=BASE, timeout=60)

    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin"})
    check("login", r.status_code == 200, r.text[:200])
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    r = client.post("/api/assistants", json={"name": "Смоук", "discipline": "Физическая химия"})
    check("create assistant", r.status_code == 200, r.text[:200])
    aid = r.json()["id"]

    r = client.post(
        f"/api/assistants/{aid}/kb/documents",
        files={"file": ("konspekt.md", SAMPLE_MD.encode(), "text/markdown")},
        data={"title": "Конспект", "doc_type": "notes"},
    )
    check("upload md doc", r.status_code == 200, r.text[:300])
    doc_id = r.json()["id"]

    doc = {}
    for _ in range(30):
        time.sleep(0.5)
        doc = client.get(f"/api/assistants/{aid}/kb/documents/{doc_id}").json()
        if doc.get("status") in ("parsed", "failed"):
            break
    check("doc parsed", doc.get("status") == "parsed", str(doc.get("error", ""))[:300])
    check("chunks counted", doc.get("chunk_count", 0) >= 2, f"chunk_count={doc.get('chunk_count')}")

    r = client.get(f"/api/assistants/{aid}/kb/documents/{doc_id}/chunks")
    table_chunks = [c for c in r.json() if c["kind"] == "table"] if r.status_code == 200 else []
    check("table chunk detected", bool(table_chunks), r.text[:200])

    r = client.post(
        f"/api/assistants/{aid}/kb/documents",
        files={"file": ("answers.pdf", highlighted_answer_pdf(), "application/pdf")},
        data={"title": "Ответы к тесту", "doc_type": "problem_book"},
    )
    check("upload highlighted answer PDF", r.status_code == 200, r.text[:300])
    answer_doc_id = r.json()["id"]
    answer_doc = {}
    for _ in range(30):
        time.sleep(0.5)
        answer_doc = client.get(f"/api/assistants/{aid}/kb/documents/{answer_doc_id}").json()
        if answer_doc.get("status") in ("parsed", "failed"):
            break
    answer_markdown = answer_doc.get("markdown", "")
    check(
        "PDF highlight semantics preserved",
        answer_doc.get("status") == "parsed"
        and "Выделено в оригинале" in answer_markdown
        and "B. Correct answer" in answer_markdown,
        answer_markdown[:300],
    )

    r = client.get(f"/api/assistants/{aid}/kb/search", params={"q": "энергия Гиббса"})
    check("fts search (morphology)", r.status_code == 200 and len(r.json()) > 0, r.text[:200])

    r = client.post(
        f"/api/assistants/{aid}/sheets",
        json={"title": "ΔG°f (Приложение 1)", "kind": "data_table", "content_markdown": "| CO2 | −394,4 |"},
    )
    check("create sheet", r.status_code == 200, r.text[:200])
    sheet_id = r.json()["id"]

    r = client.post(
        f"/api/assistants/{aid}/sheets",
        json={"title": "ΔG°f (Приложение 1)", "kind": "data_table", "content_markdown": "| CO2 | −394,4 |"},
    )
    check("sheet create is idempotent", r.status_code == 200 and r.json()["id"] == sheet_id, r.text[:200])

    if table_chunks:
        r = client.post(
            f"/api/assistants/{aid}/sheets/from-chunks",
            json={"document_id": doc_id, "chunk_ids": [table_chunks[0]["id"]], "title": "Из конспекта", "kind": "data_table"},
        )
        check("sheet from chunks", r.status_code == 200, r.text[:200])

    r = client.post(
        f"/api/assistants/{aid}/templates",
        json={
            "name": "Расчёт ΔG",
            "topic": "Химическое равновесие",
            "task_kind": "calculation",
            "answer_format": "numeric",
            "reference_sheet_ids": [sheet_id],
            "example_tasks": [{"statement": "Вычислите ΔG°", "solution": "ΔG° = −RT ln K", "answer": "−5,7 кДж/моль"}],
        },
    )
    check("create blueprint", r.status_code == 200, r.text[:300])
    tpl_id = r.json()["id"]

    r = client.patch(f"/api/assistants/{aid}/templates/{tpl_id}", json={"numeric_tolerance_pct": 5.0})
    check("patch blueprint", r.status_code == 200 and r.json()["numeric_tolerance_pct"] == 5.0, r.text[:200])

    for role in ("grader", "generator", "tutor"):
        r = client.post(
            f"/api/assistants/{aid}/prompt-preview",
            json={"role": role, "template_id": tpl_id if role == "generator" else None},
        )
        body = r.json() if r.status_code == 200 else {}
        check(
            f"prompt preview {role}",
            r.status_code == 200 and bool(body.get("system_prompt")) and bool(body.get("user_message")),
            r.text[:300],
        )

    r = client.post(f"/api/assistants/{aid}/tasks/export", json={"mode": "bank", "task_ids": []})
    check("export empty bank -> 422 с подсказкой", r.status_code == 422 and "одобрите" in r.text, r.text[:200])

    r = client.get(f"/api/assistants/{aid}/tasks/batches")
    check("batches list", r.status_code == 200, r.text[:200])

    r = client.get(f"/api/assistants/{aid}/tutor/runs")
    check("tutor runs list", r.status_code == 200, r.text[:200])

    r = client.delete(f"/api/assistants/{aid}/kb/documents/{doc_id}")
    check("delete doc", r.status_code == 200, r.text[:200])
    r = client.delete(f"/api/assistants/{aid}/kb/documents/{answer_doc_id}")
    check("delete answer PDF", r.status_code == 200, r.text[:200])

    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env.update(
            {
                "STUDIO_DATABASE_URL": f"sqlite+aiosqlite:///{tmp}/smoke.db",
                "STUDIO_DATA_DIR": tmp,
                "STUDIO_FIRST_ADMIN_USERNAME": "admin",
                "STUDIO_FIRST_ADMIN_PASSWORD": "admin",
                "STUDIO_ARCHITECT_BASE_URL": "",
                "STUDIO_ARCHITECT_API_KEY": "",
            }
        )
        proc = subprocess.Popen(
            [".venv/bin/uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
            env=env,
            cwd=Path(__file__).resolve().parent.parent,
        )
        try:
            wait_health()
            main()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
