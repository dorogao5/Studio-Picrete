import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeDocument, ReferenceSheet
from app.services.kb import normalize_ru, search_chunks

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
    "не подставляйте значения из общих знаний. При расхождении источников приоритет: правила курса → "
    "материалы курса → справочные источники."
)
KB_HEADER = "## ВЫДЕРЖКИ ИЗ МАТЕРИАЛОВ КУРСА (контекст, терминология)"
AUTHORITY_LABELS = {
    "course_policy": "правила курса",
    "course_lecture": "материал курса",
    "reference": "справочный источник",
    "unverified": "непроверенный источник",
}

_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
_QUERY_STOPWORDS = {
    "а",
    "без",
    "бы",
    "в",
    "вам",
    "ваш",
    "где",
    "для",
    "до",
    "его",
    "ее",
    "если",
    "еще",
    "же",
    "за",
    "и",
    "из",
    "или",
    "как",
    "когда",
    "ли",
    "мне",
    "можно",
    "мой",
    "мы",
    "на",
    "над",
    "не",
    "но",
    "о",
    "об",
    "он",
    "она",
    "они",
    "от",
    "по",
    "под",
    "почему",
    "при",
    "про",
    "с",
    "так",
    "такое",
    "то",
    "у",
    "уже",
    "что",
    "это",
    "я",
}


def _token_pairs(text: str) -> list[tuple[str, str]]:
    tokens = _TOKEN_RE.findall(text.casefold().replace("ё", "е"))
    stems = normalize_ru(" ".join(tokens)).split()
    return list(zip(tokens, stems, strict=True))


def _query_terms(query: str) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for token, stem in _token_pairs(query):
        if token in _QUERY_STOPWORDS or (len(token) < 2 and not token.isdigit()):
            continue
        pair = (token, stem)
        if pair not in seen:
            seen.add(pair)
            terms.append(pair)
    return terms


def _field_score(text: str, terms: list[tuple[str, str]], weight: int) -> int:
    pairs = _token_pairs(text)
    tokens = {token for token, _ in pairs}
    stems = {stem for _, stem in pairs}
    matches = 0
    for token, stem in terms:
        # Exact token matching is important for short course abbreviations: «ИК»
        # is a token in «ИК-спектроскопия», but must not match inside «аналитики».
        if token in tokens or (len(stem) >= 3 and stem in stems):
            matches += 1
    return matches * weight


def _rank_sheets_for_query(sheets: list[ReferenceSheet], query: str) -> list[ReferenceSheet]:
    terms = _query_terms(query)
    if not terms:
        return []
    ranked: list[tuple[int, int, int, ReferenceSheet]] = []
    for position, sheet in enumerate(sheets):
        score = (
            _field_score(sheet.title or "", terms, 12)
            + _field_score(sheet.description or "", terms, 4)
            + _field_score(sheet.content_markdown or "", terms, 1)
        )
        if score > 0:
            ranked.append((-score, int(sheet.ord or 0), position, sheet))
    ranked.sort(key=lambda item: item[:3])
    return [sheet for _, _, _, sheet in ranked]


def _select_sheets(
    sheets: list[ReferenceSheet], *, query: str, query_aware: bool
) -> list[ReferenceSheet]:
    if not query_aware or not query.strip():
        return sheets
    return _rank_sheets_for_query(sheets, query)


async def build_grounding_block(
    db: AsyncSession,
    assistant_id: str,
    *,
    sheet_ids: list[str] | None = None,
    query: str = "",
    kb_limit: int = 6,
    include_kb: bool = True,
    max_chars: int = 24000,
    allowed_visibilities: tuple[str, ...] = ("student", "teacher_only", "assessment_private"),
) -> str:
    stmt = select(ReferenceSheet).where(ReferenceSheet.assistant_id == assistant_id)
    stmt = stmt.where(ReferenceSheet.visibility.in_(allowed_visibilities))
    if sheet_ids is None:
        stmt = stmt.where(ReferenceSheet.is_canonical.is_(True))
    else:
        stmt = stmt.where(ReferenceSheet.id.in_(sheet_ids))
    sheets = (
        await db.execute(stmt.order_by(ReferenceSheet.ord, ReferenceSheet.created_at))
    ).scalars().all()
    sheets = _select_sheets(list(sheets), query=query, query_aware=sheet_ids is None)

    source_meta: dict[str, tuple[str, str]] = {}
    source_ids = {sheet.source_document_id for sheet in sheets if sheet.source_document_id}
    if source_ids:
        rows = (
            await db.execute(
                select(KnowledgeDocument.id, KnowledgeDocument.title, KnowledgeDocument.authority).where(
                    KnowledgeDocument.id.in_(source_ids)
                )
            )
        ).all()
        source_meta = {document_id: (title, authority) for document_id, title, authority in rows}

    used = 0
    sheet_parts: list[str] = []
    omitted: list[str] = []
    for sheet in sheets:
        content = (sheet.content_markdown or "").strip()
        if not content:
            continue
        label = SHEET_KIND_LABELS.get(sheet.kind, SHEET_KIND_LABELS["other"])
        source = source_meta.get(sheet.source_document_id or "")
        authority = f", {AUTHORITY_LABELS.get(source[1], source[1])}" if source else ""
        section = f"### {sheet.title} ({label}{authority})\n{content}"
        cost = len(section) + (0 if sheet_parts else len(SHEETS_HEADER))
        if used + cost > max_chars:
            remaining = max_chars - used - len(SHEETS_HEADER if not sheet_parts else "") - 200
            # Слишком большой лист режем по остатку бюджета, а не выкидываем молча; мелкие пробуем дальше.
            if remaining > 2000:
                section = (
                    f"### {sheet.title} ({label})\n{content[:remaining]}\n"
                    f"[…обрезано: справочник больше бюджета контекста, полная версия — во вкладке «Материалы»]"
                )
                sheet_parts.append(section)
                used += len(section)
            else:
                omitted.append(sheet.title)
            continue
        sheet_parts.append(section)
        used += cost
    if omitted:
        note = "(Не поместились в контекст: " + ", ".join(omitted) + ")"
        sheet_parts.append(note)
        used += len(note)

    kb_parts: list[str] = []
    if include_kb and query.strip() and used < max_chars:
        chunks = await search_chunks(
            db,
            assistant_id,
            query,
            limit=kb_limit,
            allowed_visibilities=allowed_visibilities,
        )
        doc_titles: dict[str, str] = {}
        doc_ids = {chunk.document_id for chunk in chunks}
        if doc_ids:
            rows = (
                await db.execute(
                    select(KnowledgeDocument.id, KnowledgeDocument.title, KnowledgeDocument.authority).where(
                        KnowledgeDocument.id.in_(doc_ids)
                    )
                )
            ).all()
            doc_titles = {
                document_id: f"{title} [{AUTHORITY_LABELS.get(authority, authority)}]"
                for document_id, title, authority in rows
            }
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
