from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.llm import client as llm
from app.llm.presets import PROVIDER_PRESETS
from app.models import ModelEntry, Provider, User
from app.schemas import (
    ModelEntryCreate,
    ModelEntryOut,
    ModelEntryUpdate,
    ProviderCreate,
    ProviderOut,
    ProviderTestResponse,
    ProviderUpdate,
)
from app.security import encrypt_secret, get_current_user

router = APIRouter(prefix="/providers", tags=["providers"], dependencies=[Depends(get_current_user)])


def _to_out(provider: Provider) -> ProviderOut:
    out = ProviderOut.model_validate(provider)
    out.has_api_key = bool(provider.api_key_encrypted)
    return out


async def _get_provider(db: AsyncSession, provider_id: str) -> Provider:
    provider = (
        await db.execute(
            select(Provider).options(selectinload(Provider.models)).where(Provider.id == provider_id)
        )
    ).scalar_one_or_none()
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Провайдер не найден")
    return provider


@router.get("/presets")
async def presets() -> list[dict]:
    # Архитектор промптов настраивается на сервере в фоне — преподавателям его не показываем.
    return [p for p in PROVIDER_PRESETS if p.get("purpose") != "architect"]


@router.get("", response_model=list[ProviderOut])
async def list_providers(db: AsyncSession = Depends(get_db)) -> list[ProviderOut]:
    providers = (
        (
            await db.execute(
                select(Provider)
                .where(Provider.purpose == "production")
                .options(selectinload(Provider.models))
                .order_by(Provider.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_to_out(p) for p in providers]


@router.post("", response_model=ProviderOut)
async def create_provider(body: ProviderCreate, db: AsyncSession = Depends(get_db)) -> ProviderOut:
    provider = Provider(
        name=body.name,
        kind=body.kind,
        purpose=body.purpose,
        base_url=body.base_url.rstrip("/"),
        api_key_encrypted=encrypt_secret(body.api_key),
        extra_headers=body.extra_headers,
    )
    db.add(provider)
    await db.commit()
    provider = await _get_provider(db, provider.id)
    return _to_out(provider)


@router.patch("/{provider_id}", response_model=ProviderOut)
async def update_provider(provider_id: str, body: ProviderUpdate, db: AsyncSession = Depends(get_db)) -> ProviderOut:
    provider = await _get_provider(db, provider_id)
    if body.name is not None:
        provider.name = body.name
    if body.base_url is not None:
        provider.base_url = body.base_url.rstrip("/")
    if body.api_key is not None and body.api_key != "":
        provider.api_key_encrypted = encrypt_secret(body.api_key)
    if body.extra_headers is not None:
        provider.extra_headers = body.extra_headers
    if body.enabled is not None:
        provider.enabled = body.enabled
    await db.commit()
    return _to_out(await _get_provider(db, provider_id))


@router.delete("/{provider_id}")
async def delete_provider(provider_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    provider = await _get_provider(db, provider_id)
    await db.delete(provider)
    await db.commit()
    return {"ok": True}


@router.post("/{provider_id}/test", response_model=ProviderTestResponse)
async def test_provider(provider_id: str, db: AsyncSession = Depends(get_db)) -> ProviderTestResponse:
    provider = await _get_provider(db, provider_id)
    model = next((m for m in provider.models if m.enabled), None)
    if model is None:
        return ProviderTestResponse(ok=False, message="Добавьте хотя бы одну модель для проверки соединения")
    try:
        result = await llm.chat(
            provider, model, "Вы — эхо-сервис.", "Ответьте одним словом: работает", temperature=0.0, timeout=60.0
        )
    except llm.LlmError as err:
        return ProviderTestResponse(ok=False, message=str(err))
    return ProviderTestResponse(
        ok=True,
        message=f"Модель {model.model_id} ответила: {result.text[:100]}",
        duration_ms=result.duration_ms,
    )


@router.post("/{provider_id}/models", response_model=ModelEntryOut)
async def add_model(provider_id: str, body: ModelEntryCreate, db: AsyncSession = Depends(get_db)) -> ModelEntry:
    await _get_provider(db, provider_id)
    model = ModelEntry(provider_id=provider_id, **body.model_dump())
    if not model.display_name:
        model.display_name = model.model_id
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return model


@router.patch("/{provider_id}/models/{model_id}", response_model=ModelEntryOut)
async def update_model(
    provider_id: str, model_id: str, body: ModelEntryUpdate, db: AsyncSession = Depends(get_db)
) -> ModelEntry:
    model = (
        await db.execute(
            select(ModelEntry).where(ModelEntry.id == model_id, ModelEntry.provider_id == provider_id)
        )
    ).scalar_one_or_none()
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Модель не найдена")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(model, field, value)
    await db.commit()
    await db.refresh(model)
    return model


@router.delete("/{provider_id}/models/{model_id}")
async def delete_model(provider_id: str, model_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    model = (
        await db.execute(
            select(ModelEntry).where(ModelEntry.id == model_id, ModelEntry.provider_id == provider_id)
        )
    ).scalar_one_or_none()
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Модель не найдена")
    await db.delete(model)
    await db.commit()
    return {"ok": True}
