import re
from pathlib import Path

import snowballstemmer
from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.llm import client as llm
from app.models import KnowledgeChunk, KnowledgeDocument, ModelEntry, Provider
from app.services.ocr import run_datalab_ocr

TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
OCR_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | OCR_EXTENSIONS

MAX_CHUNK_CHARS = 2500
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")

_stemmer = snowballstemmer.stemmer("russian")


async def ensure_fts(conn) -> None:
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


async def index_chunks(db: AsyncSession, chunks: list[KnowledgeChunk]) -> None:
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
    await db.execute(
        sql_text("DELETE FROM kb_fts WHERE chunk_id IN (SELECT id FROM knowledge_chunks WHERE document_id = :doc_id)"),
        {"doc_id": document_id},
    )


async def deindex_assistant(db: AsyncSession, assistant_id: str) -> None:
    await db.execute(sql_text("DELETE FROM kb_fts WHERE assistant_id = :aid"), {"aid": assistant_id})


async def search_chunks(db: AsyncSession, assistant_id: str, query: str, limit: int = 8) -> list[KnowledgeChunk]:
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


async def _extract_markdown(document: KnowledgeDocument) -> str:
    path = Path(document.file_path)
    content = path.read_bytes()
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return content.decode("utf-8", errors="replace")
    return await run_datalab_ocr(
        document.original_filename or path.name,
        content,
        document.mime_type or "application/octet-stream",
        max_poll_attempts=get_settings().datalab_kb_max_poll_attempts,
    )


async def ingest_document(document_id: str) -> None:
    async with SessionLocal() as db:
        document = await _load_document(db, document_id)
        if document is None:
            return
        document.status = "parsing"
        document.error = ""
        await db.commit()
        try:
            markdown = await _extract_markdown(document)
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
            await db.commit()
        except Exception as err:
            await db.rollback()
            document = await _load_document(db, document_id)
            if document is not None:
                document.status = "failed"
                document.error = str(err)[:2000]
                await db.commit()


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
