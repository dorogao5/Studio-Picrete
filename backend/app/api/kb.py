import mimetypes
import uuid
from collections.abc import Iterable
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.assistants import get_assistant_or_404, resolve_architect
from app.config import get_settings
from app.db import get_db
from app.llm import client as llm
from app.models import KnowledgeChunk, KnowledgeDocument, ReferenceSheet, User
from app.schemas import (
    DocumentAnalysisResponse,
    KnowledgeChunkOut,
    KnowledgeDocumentDetailOut,
    KnowledgeDocumentOut,
    ReferenceSheetCreate,
    ReferenceSheetOut,
    ReferenceSheetUpdate,
    SheetFromChunksRequest,
    SyllabusExtractRequest,
    SyllabusExtractResponse,
)
from app.security import get_current_user
from app.services import kb, storage

router = APIRouter(tags=["kb"], dependencies=[Depends(get_current_user)])

DOC_TYPES = {"rpd", "notes", "textbook", "problem_book", "reference", "methodical", "other"}


async def _get_document_or_404(db: AsyncSession, assistant_id: str, document_id: str) -> KnowledgeDocument:
    document = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id, KnowledgeDocument.assistant_id == assistant_id
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    return document


async def _get_sheet_or_404(db: AsyncSession, assistant_id: str, sheet_id: str) -> ReferenceSheet:
    sheet = (
        await db.execute(
            select(ReferenceSheet).where(ReferenceSheet.id == sheet_id, ReferenceSheet.assistant_id == assistant_id)
        )
    ).scalar_one_or_none()
    if sheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Справочный материал не найден")
    return sheet


async def _attach_chunk_counts(db: AsyncSession, documents: Iterable[KnowledgeDocument]) -> list[KnowledgeDocument]:
    documents = list(documents)
    ids = [d.id for d in documents]
    counts: dict[str, int] = {}
    if ids:
        rows = (
            await db.execute(
                select(KnowledgeChunk.document_id, func.count())
                .where(KnowledgeChunk.document_id.in_(ids))
                .group_by(KnowledgeChunk.document_id)
            )
        ).all()
        counts = dict(rows)
    for d in documents:
        d.chunk_count = counts.get(d.id, 0)
    return documents


@router.post("/assistants/{assistant_id}/kb/documents", response_model=KnowledgeDocumentOut)
async def upload_document(
    assistant_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    doc_type: str = Form("other"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> KnowledgeDocument:
    await get_assistant_or_404(assistant_id, db)
    if doc_type not in DOC_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Неизвестный тип документа: {doc_type}")
    filename = file.filename or "document"
    suffix = Path(filename).suffix.lower()
    if suffix not in kb.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Поддерживаются PDF, изображения (JPG/PNG/WebP) и текст (.md/.txt)",
        )
    content = await file.read()
    settings = get_settings()
    if len(content) > settings.kb_max_file_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Файл больше {settings.kb_max_file_mb} МБ")
    path = settings.kb_dir / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(content)
    document = KnowledgeDocument(
        assistant_id=assistant_id,
        title=title.strip() or Path(filename).stem,
        doc_type=doc_type,
        original_filename=filename,
        file_path=str(path),
        mime_type=file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        size_bytes=len(content),
        status="uploaded",
        created_by=user.id,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    background_tasks.add_task(kb.ingest_document, document.id)
    document.chunk_count = 0
    return document


@router.get("/assistants/{assistant_id}/kb/documents", response_model=list[KnowledgeDocumentOut])
async def list_documents(assistant_id: str, db: AsyncSession = Depends(get_db)) -> list[KnowledgeDocument]:
    await get_assistant_or_404(assistant_id, db)
    documents = (
        await db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.assistant_id == assistant_id)
            .order_by(KnowledgeDocument.created_at.desc())
        )
    ).scalars()
    return await _attach_chunk_counts(db, documents)


@router.get("/assistants/{assistant_id}/kb/documents/{document_id}", response_model=KnowledgeDocumentDetailOut)
async def get_document(
    assistant_id: str, document_id: str, db: AsyncSession = Depends(get_db)
) -> KnowledgeDocument:
    document = await _get_document_or_404(db, assistant_id, document_id)
    return (await _attach_chunk_counts(db, [document]))[0]


@router.get("/assistants/{assistant_id}/kb/documents/{document_id}/chunks", response_model=list[KnowledgeChunkOut])
async def list_chunks(
    assistant_id: str, document_id: str, db: AsyncSession = Depends(get_db)
) -> list[KnowledgeChunk]:
    await _get_document_or_404(db, assistant_id, document_id)
    return list(
        (
            await db.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id).order_by(KnowledgeChunk.ord)
            )
        ).scalars()
    )


@router.post("/assistants/{assistant_id}/kb/documents/{document_id}/reparse", response_model=KnowledgeDocumentOut)
async def reparse_document(
    assistant_id: str, document_id: str, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> KnowledgeDocument:
    document = await _get_document_or_404(db, assistant_id, document_id)
    await kb.deindex_document(db, document_id)
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))
    document.status = "uploaded"
    document.error = ""
    await db.commit()
    await db.refresh(document)
    background_tasks.add_task(kb.ingest_document, document_id)
    document.chunk_count = 0
    return document


@router.delete("/assistants/{assistant_id}/kb/documents/{document_id}")
async def delete_document(assistant_id: str, document_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    document = await _get_document_or_404(db, assistant_id, document_id)
    await kb.deindex_document(db, document_id)
    if document.file_path:
        Path(document.file_path).unlink(missing_ok=True)
    if document.s3_key and storage.s3_enabled():
        try:
            await storage.delete_object(document.s3_key)
        except Exception:
            pass  # осиротевший объект приберёт ночной бэкап-скрипт
    await db.delete(document)
    await db.commit()
    return {"ok": True}


@router.get("/assistants/{assistant_id}/kb/search", response_model=list[KnowledgeChunkOut])
async def search_kb(
    assistant_id: str, q: str = "", limit: int = 8, db: AsyncSession = Depends(get_db)
) -> list[KnowledgeChunk]:
    await get_assistant_or_404(assistant_id, db)
    return await kb.search_chunks(db, assistant_id, q, limit=max(1, min(limit, 50)))


@router.post("/assistants/{assistant_id}/kb/extract-syllabus", response_model=SyllabusExtractResponse)
async def extract_syllabus(
    assistant_id: str, body: SyllabusExtractRequest, db: AsyncSession = Depends(get_db)
) -> SyllabusExtractResponse:
    await get_assistant_or_404(assistant_id, db)
    document = await _get_document_or_404(db, assistant_id, body.document_id)
    if document.status != "parsed" or not document.markdown.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Документ ещё не разобран — дождитесь статуса «готов»")
    provider, model = await resolve_architect(db)
    try:
        topics = await kb.extract_syllabus(db, document, provider, model)
    except llm.LlmError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err))
    return SyllabusExtractResponse(topics=topics)


@router.post("/assistants/{assistant_id}/kb/documents/{document_id}/analyze", response_model=DocumentAnalysisResponse)
async def analyze_document(
    assistant_id: str, document_id: str, refresh: bool = False, db: AsyncSession = Depends(get_db)
) -> DocumentAnalysisResponse:
    await get_assistant_or_404(assistant_id, db)
    document = await _get_document_or_404(db, assistant_id, document_id)
    if document.status != "parsed" or not document.markdown.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Текст ещё не извлечён — дождитесь статуса «готов»")
    # Авто-разбор уже отработал в фоне — отдаём сохранённый результат мгновенно.
    if not refresh and document.analysis_status in {"ready", "applied"} and document.analysis:
        return DocumentAnalysisResponse(**document.analysis)
    provider, model = await resolve_architect(db)
    try:
        analysis = await kb.analyze_document(db, document, provider, model)
    except llm.LlmError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err))
    document.analysis = analysis
    document.analysis_status = "ready"
    document.analysis_error = ""
    await db.commit()
    return DocumentAnalysisResponse(**analysis)


@router.post(
    "/assistants/{assistant_id}/kb/documents/{document_id}/analysis-applied",
    response_model=KnowledgeDocumentOut,
)
async def mark_document_analysis_applied(
    assistant_id: str, document_id: str, db: AsyncSession = Depends(get_db)
) -> KnowledgeDocument:
    document = await _get_document_or_404(db, assistant_id, document_id)
    if document.analysis_status not in {"ready", "applied"} or not document.analysis:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Сначала дождитесь готового разбора документа")
    document.analysis_status = "applied"
    await db.commit()
    await db.refresh(document)
    return (await _attach_chunk_counts(db, [document]))[0]


# ---------------------------------------------------------------- sheets


@router.get("/assistants/{assistant_id}/sheets", response_model=list[ReferenceSheetOut])
async def list_sheets(assistant_id: str, db: AsyncSession = Depends(get_db)) -> list[ReferenceSheet]:
    await get_assistant_or_404(assistant_id, db)
    return list(
        (
            await db.execute(
                select(ReferenceSheet)
                .where(ReferenceSheet.assistant_id == assistant_id)
                .order_by(ReferenceSheet.ord, ReferenceSheet.created_at)
            )
        ).scalars()
    )


@router.post("/assistants/{assistant_id}/sheets", response_model=ReferenceSheetOut)
async def create_sheet(
    assistant_id: str,
    body: ReferenceSheetCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReferenceSheet:
    await get_assistant_or_404(assistant_id, db)
    if body.source_document_id:
        await _get_document_or_404(db, assistant_id, body.source_document_id)

    title = body.title.strip()
    content = body.content_markdown.strip()
    existing = (
        await db.execute(
            select(ReferenceSheet).where(
                ReferenceSheet.assistant_id == assistant_id,
                ReferenceSheet.kind == body.kind,
                ReferenceSheet.title == title,
                ReferenceSheet.content_markdown == content,
            )
        )
    ).scalars().first()
    if existing is not None:
        existing.description = body.description
        existing.is_canonical = body.is_canonical
        existing.ord = body.ord
        if body.source_document_id:
            existing.source_document_id = body.source_document_id
        await db.commit()
        await db.refresh(existing)
        return existing

    sheet = ReferenceSheet(
        assistant_id=assistant_id,
        created_by=user.id,
        **body.model_dump(exclude={"title", "content_markdown"}),
        title=title,
        content_markdown=content,
    )
    db.add(sheet)
    await db.commit()
    await db.refresh(sheet)
    return sheet


@router.post("/assistants/{assistant_id}/sheets/from-chunks", response_model=ReferenceSheetOut)
async def create_sheet_from_chunks(
    assistant_id: str,
    body: SheetFromChunksRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReferenceSheet:
    await get_assistant_or_404(assistant_id, db)
    document = await _get_document_or_404(db, assistant_id, body.document_id)
    chunks = list(
        (
            await db.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document.id, KnowledgeChunk.id.in_(body.chunk_ids))
                .order_by(KnowledgeChunk.ord)
            )
        ).scalars()
    )
    if not chunks:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Фрагменты документа не найдены")
    sheet = ReferenceSheet(
        assistant_id=assistant_id,
        title=body.title,
        kind=body.kind,
        content_markdown="\n\n".join(chunk.content for chunk in chunks),
        source_document_id=document.id,
        created_by=user.id,
    )
    db.add(sheet)
    await db.commit()
    await db.refresh(sheet)
    return sheet


@router.patch("/assistants/{assistant_id}/sheets/{sheet_id}", response_model=ReferenceSheetOut)
async def update_sheet(
    assistant_id: str, sheet_id: str, body: ReferenceSheetUpdate, db: AsyncSession = Depends(get_db)
) -> ReferenceSheet:
    sheet = await _get_sheet_or_404(db, assistant_id, sheet_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(sheet, field, value)
    await db.commit()
    await db.refresh(sheet)
    return sheet


@router.delete("/assistants/{assistant_id}/sheets/{sheet_id}")
async def delete_sheet(assistant_id: str, sheet_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    sheet = await _get_sheet_or_404(db, assistant_id, sheet_id)
    await db.delete(sheet)
    await db.commit()
    return {"ok": True}
