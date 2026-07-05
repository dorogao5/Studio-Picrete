from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeDocument, ReferenceSheet
from app.services.kb import search_chunks

SHEET_KIND_LABELS = {
    "data_table": "Таблица данных",
    "glossary": "Глоссарий",
    "conventions": "Обозначения",
    "formulas": "Формулы",
    "other": "Справка",
}

SHEETS_HEADER = (
    "## СПРАВОЧНЫЕ МАТЕРИАЛЫ КУРСА\n"
    "Используйте ТОЛЬКО эти данные и обозначения. Если необходимых данных здесь нет — явно скажите об этом,\n"
    "не подставляйте значения из общих знаний."
)
KB_HEADER = "## ВЫДЕРЖКИ ИЗ МАТЕРИАЛОВ КУРСА (контекст, терминология)"


async def build_grounding_block(
    db: AsyncSession,
    assistant_id: str,
    *,
    sheet_ids: list[str] | None = None,
    query: str = "",
    kb_limit: int = 6,
    include_kb: bool = True,
    max_chars: int = 24000,
) -> str:
    stmt = select(ReferenceSheet).where(ReferenceSheet.assistant_id == assistant_id)
    if sheet_ids is None:
        stmt = stmt.where(ReferenceSheet.is_canonical.is_(True))
    else:
        stmt = stmt.where(ReferenceSheet.id.in_(sheet_ids))
    sheets = (
        await db.execute(stmt.order_by(ReferenceSheet.ord, ReferenceSheet.created_at))
    ).scalars().all()

    used = 0
    sheet_parts: list[str] = []
    for sheet in sheets:
        content = (sheet.content_markdown or "").strip()
        if not content:
            continue
        label = SHEET_KIND_LABELS.get(sheet.kind, SHEET_KIND_LABELS["other"])
        section = f"### {sheet.title} ({label})\n{content}"
        cost = len(section) + (0 if sheet_parts else len(SHEETS_HEADER))
        if used + cost > max_chars:
            break
        sheet_parts.append(section)
        used += cost

    kb_parts: list[str] = []
    if include_kb and query.strip() and used < max_chars:
        chunks = await search_chunks(db, assistant_id, query, limit=kb_limit)
        doc_titles: dict[str, str] = {}
        doc_ids = {chunk.document_id for chunk in chunks}
        if doc_ids:
            rows = (
                await db.execute(
                    select(KnowledgeDocument.id, KnowledgeDocument.title).where(KnowledgeDocument.id.in_(doc_ids))
                )
            ).all()
            doc_titles = dict(rows)
        for chunk in chunks:
            title = doc_titles.get(chunk.document_id, "Документ")
            header = f"{title} — {chunk.heading}" if chunk.heading else title
            section = f"### {header}\n{chunk.content}"
            cost = len(section) + (0 if kb_parts else len(KB_HEADER))
            if used + cost > max_chars:
                break
            kb_parts.append(section)
            used += cost

    blocks: list[str] = []
    if sheet_parts:
        blocks.append(SHEETS_HEADER)
        blocks.extend(sheet_parts)
    if kb_parts:
        blocks.append(KB_HEADER)
        blocks.extend(kb_parts)
    return "\n\n".join(blocks)
