import re
from pathlib import Path

import pymupdf
import snowballstemmer
from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.llm import client as llm
from app.models import KnowledgeChunk, KnowledgeDocument, ModelEntry, Provider
from app.services import storage
from app.services.ocr import run_datalab_ocr

TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
OCR_EXTENSIONS = PDF_EXTENSIONS | IMAGE_EXTENSIONS
ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | OCR_EXTENSIONS

# Ниже этого среднего числа символов на страницу считаем, что текстового слоя нет
# (скан) и нужен дорогой OCR. Обычный PDF с текстом даёт 800–3000 символов/стр.
MIN_TEXT_CHARS_PER_PAGE = 90

MAX_CHUNK_CHARS = 2500
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")

# Строки-заголовки в выгрузке из PDF конспектов/учебников (текстового слоя без разметки).
_PDF_HEADING_RE = re.compile(
    r"^(ЛЕКЦИЯ|РАЗДЕЛ|ГЛАВА|ТЕМА|ЧАСТЬ|ПРИЛОЖЕНИЕ|ВВЕДЕНИЕ|ЗАКЛЮЧЕНИЕ|§\s*\d+)\b.*",
    re.IGNORECASE,
)
_PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")
_HYPHEN_BREAK_RE = re.compile(r"([а-яёa-z])[­‐‑-]\n\s*([а-яёa-z])", re.IGNORECASE)

_stemmer = snowballstemmer.stemmer("russian")


def _clean_pdf_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("­", "")  # мягкий перенос
    # Склейка переносов слов на границе строк: «свойст-\nвами» → «свойствами».
    text = _HYPHEN_BREAK_RE.sub(r"\1\2", text)
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if _PAGE_NUMBER_RE.fullmatch(stripped):
            continue  # висячий номер страницы
        lines.append(re.sub(r"[ \t]+", " ", line).rstrip())
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pdf_to_markdown(content: bytes) -> tuple[str, int, float]:
    """(markdown, число_страниц, средн_символов_на_страницу) из текстового слоя PDF.

    Движок — PyMuPDF: восстанавливает пробелы по позициям глифов, поэтому вытягивает
    даже PDF с дефектным текстовым слоем (слипшиеся слова), где pypdf/pdfplumber ломаются.
    """
    doc = pymupdf.open(stream=content, filetype="pdf")
    try:
        page_count = doc.page_count
        total_chars = 0
        parts: list[str] = []
        for page in doc:
            cleaned = _clean_pdf_text(page.get_text())
            total_chars += len(cleaned)
            if not cleaned:
                continue
            out_lines: list[str] = []
            for line in cleaned.split("\n"):
                stripped = line.strip()
                if stripped and _PDF_HEADING_RE.match(stripped) and len(stripped) <= 120:
                    out_lines.append(f"## {stripped}")
                else:
                    out_lines.append(line)
            parts.append("\n".join(out_lines))
    finally:
        doc.close()
    avg = total_chars / max(page_count, 1)
    return "\n\n".join(parts).strip(), page_count, avg


async def ensure_fts(conn) -> None:
    """Поисковый индекс: SQLite → виртуальная таблица FTS5; Postgres → tsvector('russian') + GIN."""
    if conn.dialect.name == "postgresql":
        await conn.exec_driver_sql(
            "ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS search_vector tsvector"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_kb_search ON knowledge_chunks USING gin (search_vector)"
        )
        await conn.exec_driver_sql(
            "UPDATE knowledge_chunks SET search_vector = to_tsvector('russian', heading || ' ' || content) "
            "WHERE search_vector IS NULL"
        )
        return
    result = await conn.exec_driver_sql("PRAGMA table_info(kb_fts)")
    columns = {row[1] for row in result}
    needs_reindex = bool(columns) and "assistant_id" not in columns
    if needs_reindex:
        await conn.exec_driver_sql("DROP TABLE kb_fts")
        columns = set()
    if not columns:
        await conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(chunk_id UNINDEXED, assistant_id UNINDEXED, content_norm)"
        )
    if needs_reindex:
        rows = await conn.exec_driver_sql("SELECT id, assistant_id, heading, content FROM knowledge_chunks")
        for chunk_id, assistant_id, heading, content in rows:
            await conn.exec_driver_sql(
                "INSERT INTO kb_fts (chunk_id, assistant_id, content_norm) VALUES (?, ?, ?)",
                (chunk_id, assistant_id, normalize_ru(f"{heading} {content}")),
            )


def normalize_ru(text: str) -> str:
    tokens = re.findall(r"\w+", text.lower().replace("ё", "е"))
    return " ".join(_stemmer.stemWords(tokens))


def _append_chunk(chunks: list[dict], heading: str, content: str, kind: str) -> None:
    content = content.strip()
    if not content:
        return
    chunks.append({"heading": heading, "content": content, "kind": kind, "char_len": len(content)})


def _section_blocks(lines: list[str]) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    text_buf: list[str] = []
    table_buf: list[str] = []

    def close_text() -> None:
        nonlocal text_buf
        block = "\n".join(text_buf).strip()
        if block:
            blocks.append(("text", block))
        text_buf = []

    def close_table() -> None:
        nonlocal table_buf
        block = "\n".join(table_buf).strip()
        if block:
            blocks.append(("table", block))
        table_buf = []

    for line in lines:
        if line.lstrip().startswith("|"):
            close_text()
            table_buf.append(line)
        else:
            close_table()
            text_buf.append(line)
    close_text()
    close_table()
    return blocks


def _split_text(block: str) -> list[str]:
    if len(block) <= MAX_CHUNK_CHARS:
        return [block]
    pieces: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", block):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= MAX_CHUNK_CHARS:
            current = candidate
            continue
        if current:
            pieces.append(current)
        while len(paragraph) > MAX_CHUNK_CHARS:
            pieces.append(paragraph[:MAX_CHUNK_CHARS])
            paragraph = paragraph[MAX_CHUNK_CHARS:]
        current = paragraph
    if current:
        pieces.append(current)
    return pieces


def split_markdown(md: str) -> list[dict]:
    chunks: list[dict] = []
    trail: dict[int, str] = {}
    section_lines: list[str] = []

    def flush() -> None:
        nonlocal section_lines
        heading = " › ".join(trail[level] for level in sorted(trail))
        for kind, block in _section_blocks(section_lines):
            if kind == "table":
                _append_chunk(chunks, heading, block, "table")
            else:
                for piece in _split_text(block):
                    _append_chunk(chunks, heading, piece, "text")
        section_lines = []

    for line in md.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            trail[level] = match.group(2).strip()
            for deeper in [lv for lv in trail if lv > level]:
                del trail[deeper]
        else:
            section_lines.append(line)
    flush()
    return chunks


def _is_postgres(db: AsyncSession) -> bool:
    return db.get_bind().dialect.name == "postgresql"


async def index_chunks(db: AsyncSession, chunks: list[KnowledgeChunk]) -> None:
    if _is_postgres(db):
        ids = [chunk.id for chunk in chunks]
        if ids:
            await db.execute(
                sql_text(
                    "UPDATE knowledge_chunks SET search_vector = to_tsvector('russian', heading || ' ' || content) "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": ids},
            )
        return
    for chunk in chunks:
        await db.execute(
            sql_text(
                "INSERT INTO kb_fts (chunk_id, assistant_id, content_norm) "
                "VALUES (:chunk_id, :assistant_id, :content_norm)"
            ),
            {
                "chunk_id": chunk.id,
                "assistant_id": chunk.assistant_id,
                "content_norm": normalize_ru(f"{chunk.heading} {chunk.content}"),
            },
        )


async def deindex_document(db: AsyncSession, document_id: str) -> None:
    if _is_postgres(db):
        return  # tsvector живёт в строке чанка и умирает вместе с ней
    await db.execute(
        sql_text("DELETE FROM kb_fts WHERE chunk_id IN (SELECT id FROM knowledge_chunks WHERE document_id = :doc_id)"),
        {"doc_id": document_id},
    )


async def deindex_assistant(db: AsyncSession, assistant_id: str) -> None:
    if _is_postgres(db):
        return
    await db.execute(sql_text("DELETE FROM kb_fts WHERE assistant_id = :aid"), {"aid": assistant_id})


async def search_chunks(db: AsyncSession, assistant_id: str, query: str, limit: int = 8) -> list[KnowledgeChunk]:
    if _is_postgres(db):
        words = re.findall(r"\w+", query.lower().replace("ё", "е"))
        if not words:
            return []
        ts_query = " | ".join(words)
        try:
            chunk_ids = (
                await db.execute(
                    sql_text(
                        "SELECT id FROM knowledge_chunks "
                        "WHERE assistant_id = :aid AND search_vector @@ to_tsquery('russian', :q) "
                        "ORDER BY ts_rank(search_vector, to_tsquery('russian', :q)) DESC LIMIT :n"
                    ),
                    {"q": ts_query, "aid": assistant_id, "n": limit},
                )
            ).scalars().all()
        except Exception:
            return []
    else:
        normalized = normalize_ru(query)
        if not normalized:
            return []
        match_query = " OR ".join(normalized.split())
        try:
            chunk_ids = (
                await db.execute(
                    sql_text(
                        "SELECT chunk_id FROM kb_fts WHERE kb_fts MATCH :q AND assistant_id = :aid "
                        "ORDER BY bm25(kb_fts) LIMIT :n"
                    ),
                    {"q": match_query, "aid": assistant_id, "n": limit},
                )
            ).scalars().all()
        except Exception:
            return []
    if not chunk_ids:
        return []
    chunks = (
        await db.execute(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(chunk_ids)))
    ).scalars().all()
    by_id = {chunk.id: chunk for chunk in chunks}
    return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]


async def _load_document(db: AsyncSession, document_id: str) -> KnowledgeDocument | None:
    return (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
    ).scalar_one_or_none()


async def _load_document_bytes(document: KnowledgeDocument) -> bytes:
    """Оригинал: локальный файл (свежая загрузка) или S3 (после выгрузки)."""
    path = Path(document.file_path)
    if document.file_path and path.exists():
        return path.read_bytes()
    if document.s3_key and storage.s3_enabled():
        return await storage.download_bytes(document.s3_key)
    raise FileNotFoundError(f"Оригинал документа недоступен: {document.file_path or document.s3_key}")


async def _offload_original(document: KnowledgeDocument) -> None:
    """После успешного парсинга выгружаем оригинал в S3 и освобождаем диск."""
    if not storage.s3_enabled() or document.s3_key:
        return
    path = Path(document.file_path)
    if not (document.file_path and path.exists()):
        return
    key = f"kb/{document.assistant_id}/{document.id}{path.suffix.lower()}"
    document.s3_key = await storage.upload_bytes(
        key, path.read_bytes(), document.mime_type or "application/octet-stream"
    )
    path.unlink(missing_ok=True)


async def _extract_markdown(document: KnowledgeDocument) -> tuple[str, str, int]:
    """(markdown, метод['text'|'ocr'], число_страниц). Текст извлекаем дёшево, OCR — только fallback."""
    content = await _load_document_bytes(document)
    suffix = Path(document.original_filename or document.file_path or "").suffix.lower()

    if suffix in TEXT_EXTENSIONS:
        return content.decode("utf-8", errors="replace"), "text", 0

    if suffix in PDF_EXTENSIONS:
        # Сначала пробуем достать текстовый слой (быстро и бесплатно). OCR включаем,
        # только если слоя фактически нет — это скан. Так учебник на сотни страниц
        # не гоняется через дорогой DataLab без нужды.
        try:
            markdown, page_count, avg_chars = _pdf_to_markdown(content)
        except Exception:
            markdown, page_count, avg_chars = "", 0, 0.0
        if markdown and avg_chars >= MIN_TEXT_CHARS_PER_PAGE:
            return markdown, "text", page_count

    ocr_markdown = await run_datalab_ocr(
        document.original_filename or f"document{suffix}",
        content,
        document.mime_type or "application/octet-stream",
        max_poll_attempts=get_settings().datalab_kb_max_poll_attempts,
    )
    return ocr_markdown, "ocr", 0


async def _resolve_architect_pair(db: AsyncSession) -> tuple[Provider, ModelEntry] | None:
    provider = (
        await db.execute(
            select(Provider).where(Provider.purpose == "architect", Provider.enabled.is_(True)).order_by(Provider.created_at)
        )
    ).scalars().first()
    if provider is None:
        return None
    model = (
        await db.execute(select(ModelEntry).where(ModelEntry.provider_id == provider.id, ModelEntry.enabled.is_(True)))
    ).scalars().first()
    if model is None:
        return None
    return provider, model


async def auto_analyze_document(document_id: str) -> None:
    """Фоновый авто-разбор после парсинга: результат ждёт преподавателя, применять — одним кликом."""
    async with SessionLocal() as db:
        document = await _load_document(db, document_id)
        if document is None or document.status != "parsed":
            return
        pair = await _resolve_architect_pair(db)
        if pair is None:
            document.analysis_status = "failed"
            document.analysis_error = "Модель-архитектор не настроена на сервере"
            await db.commit()
            return
        document.analysis_status = "running"
        document.analysis_error = ""
        await db.commit()
        try:
            analysis = await analyze_document(db, document, *pair)
        except Exception as err:
            document = await _load_document(db, document_id)
            if document is not None:
                document.analysis_status = "failed"
                document.analysis_error = str(err)[:2000]
                await db.commit()
            return
        document = await _load_document(db, document_id)
        if document is not None:
            document.analysis = analysis
            document.analysis_status = "ready"
            await db.commit()


async def ingest_document(document_id: str) -> None:
    async with SessionLocal() as db:
        document = await _load_document(db, document_id)
        if document is None:
            return
        document.status = "parsing"
        document.error = ""
        document.analysis_status = "none"
        document.analysis = {}
        document.analysis_error = ""
        await db.commit()
        try:
            markdown, method, page_count = await _extract_markdown(document)
            document.extract_method = method
            if page_count:
                document.page_count = page_count
            await deindex_document(db, document_id)
            await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))
            document.markdown = markdown
            chunks: list[KnowledgeChunk] = []
            for ord_, part in enumerate(split_markdown(markdown)):
                chunk = KnowledgeChunk(
                    document_id=document_id,
                    assistant_id=document.assistant_id,
                    ord=ord_,
                    heading=part["heading"][:512],
                    content=part["content"],
                    kind=part["kind"],
                    char_len=part["char_len"],
                )
                db.add(chunk)
                chunks.append(chunk)
            await db.flush()
            await index_chunks(db, chunks)
            document.status = "parsed"
            try:
                await _offload_original(document)
            except Exception:
                pass  # S3 недоступен — оригинал остаётся на диске, выгрузится при следующем парсинге
            await db.commit()
        except Exception as err:
            await db.rollback()
            document = await _load_document(db, document_id)
            if document is not None:
                document.status = "failed"
                document.error = str(err)[:2000]
                await db.commit()
            return
    await auto_analyze_document(document_id)


SYLLABUS_SYSTEM_PROMPT = (
    "Вы — методист высшей школы. Из текста рабочей программы дисциплины (РПД) или конспекта вы выделяете "
    "перечень разделов и тем курса. Отвечаете строго JSON-объектом, без текста вне JSON."
)


async def extract_syllabus(
    db: AsyncSession, document: KnowledgeDocument, provider: Provider, model: ModelEntry
) -> list[str]:
    user_message = (
        "Извлеките из документа разделы и темы курса. Каждая тема — краткая формулировка на русском языке: "
        "название раздела/темы и, кратко, её содержание. Всего 5–30 тем.\n"
        'Верните JSON вида {"topics": ["Тема 1: ...", "Тема 2: ..."]}.\n\n'
        f"Документ «{document.title}»:\n\n{document.markdown[:60000]}"
    )
    result = await llm.chat(provider, model, SYLLABUS_SYSTEM_PROMPT, user_message, temperature=0.2, json_mode=True)
    parsed = llm.extract_json(result.text)
    raw_topics = parsed.get("topics")
    if not isinstance(raw_topics, list):
        raise llm.LlmError("Модель не вернула список тем")
    topics = [item.strip() for item in raw_topics if isinstance(item, str) and item.strip()]
    if not topics:
        raise llm.LlmError("Модель не вернула ни одной темы")
    return topics[:40]


# ---------------------------------------------------------------- авто-анализ документа

ANALYZE_SYSTEM_PROMPT = (
    "Вы — методист высшей школы. Вы анализируете материалы курса (РПД, конспект, учебник, задачник) "
    "и извлекаете справочные данные, обозначения, определения и формулы РОВНО в той нотации и с теми "
    "значениями, как они введены В ЭТОМ документе.\n"
    "СТРОГО ЗАПРЕЩЕНО добавлять данные, константы, определения или формулы из общих знаний или интернета — "
    "только то, что реально присутствует в тексте. Числовые значения, единицы измерения и обозначения "
    "копируйте дословно. Если чего-то в тексте нет — не включайте это. Отвечаете строго JSON, без текста вне JSON."
)

VALID_SHEET_KINDS = {"data_table", "glossary", "conventions", "formulas", "other"}

ANALYZE_INSTRUCTIONS = (
    "Извлеките из фрагмента:\n"
    "1. topics — разделы и темы курса, затронутые здесь (краткие формулировки; если явной программы нет — можно пусто).\n"
    "2. sheets — справочные материалы, которые РЕАЛЬНО присутствуют в тексте. Каждый sheet — объект "
    '{"title","kind","description","content_markdown"}, где kind одно из:\n'
    "   • data_table — числовые таблицы (константы, табличные значения): сохраните markdown-таблицей с теми же числами и единицами;\n"
    "   • conventions — введённые в курсе обозначения и символы (что каким символом обозначено);\n"
    "   • glossary — ключевые термины с определениями ровно так, как они даны в курсе;\n"
    "   • formulas — ключевые уравнения курса в LaTeX ($...$) с названием и условиями применимости (берите те формулы, что есть в тексте);\n"
    "   content_markdown — готовый к показу markdown; формулы оборачивайте в $...$.\n"
    "3. summary — 1–2 предложения о курсе (заполняйте только если это начало документа).\n"
    "4. notation_notes — краткая сводка специфичной для этого курса нотации/терминологии, которой ассистент обязан придерживаться.\n"
    'Верните JSON {"topics":[],"sheets":[],"summary":"","notation_notes":""}.\n'
    "ВАЖНО: внутри строк JSON LaTeX-команды пишите с двойным бэкслешем: \\\\frac, \\\\alpha, \\\\sigma."
)


def _analysis_windows(text: str, size: int = 90000, overlap: int = 1500) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    windows: list[str] = []
    start = 0
    while start < len(text):
        windows.append(text[start : start + size])
        start += size - overlap
    return windows


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _merge_sheets(raw_sheets: list[dict]) -> list[dict]:
    order: list[tuple[str, str]] = []
    by_key: dict[tuple[str, str], dict] = {}
    for sheet in raw_sheets:
        if not isinstance(sheet, dict):
            continue
        title = str(sheet.get("title") or "").strip()
        content = str(sheet.get("content_markdown") or "").strip()
        kind = str(sheet.get("kind") or "other").strip()
        if kind not in VALID_SHEET_KINDS:
            kind = "other"
        if not title or not content:
            continue
        key = (kind, _norm_key(title))
        if key not in by_key:
            by_key[key] = {
                "title": title,
                "kind": kind,
                "description": str(sheet.get("description") or "").strip(),
                "content_markdown": content,
            }
            order.append(key)
        else:
            existing = by_key[key]
            seen = {line.strip() for line in existing["content_markdown"].splitlines()}
            extra = [line for line in content.splitlines() if line.strip() and line.strip() not in seen]
            if extra:
                existing["content_markdown"] += "\n" + "\n".join(extra)
    return [by_key[key] for key in order][:16]


def _dedup_topics(raw_topics: list) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_topics:
        if not isinstance(item, str):
            continue
        text = item.strip()
        key = _norm_key(text)
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out[:40]


async def analyze_document(
    db: AsyncSession, document: KnowledgeDocument, provider: Provider, model: ModelEntry
) -> dict:
    """Архитектор извлекает из документа темы, справочники и нотацию для авто-заполнения полей."""
    windows = _analysis_windows(document.markdown or "")
    if not windows:
        raise llm.LlmError("В документе нет извлечённого текста для анализа")
    all_topics: list = []
    all_sheets: list[dict] = []
    summary = ""
    notation: list[str] = []
    for index, window in enumerate(windows):
        user_message = (
            f"Документ «{document.title}» — фрагмент {index + 1} из {len(windows)}.\n\n"
            f"{ANALYZE_INSTRUCTIONS}\n\nФрагмент:\n\n{window}"
        )
        result = await llm.chat(
            provider, model, ANALYZE_SYSTEM_PROMPT, user_message, temperature=0.1, json_mode=True
        )
        parsed = llm.extract_json(result.text)
        if isinstance(parsed.get("topics"), list):
            all_topics.extend(parsed["topics"])
        if isinstance(parsed.get("sheets"), list):
            all_sheets.extend(parsed["sheets"])
        if not summary and isinstance(parsed.get("summary"), str):
            summary = parsed["summary"].strip()
        note = parsed.get("notation_notes")
        if isinstance(note, str) and note.strip():
            notation.append(note.strip())
    return {
        "summary": summary,
        "topics": _dedup_topics(all_topics),
        "sheets": _merge_sheets(all_sheets),
        "notation_notes": notation[0] if notation else "",
    }
