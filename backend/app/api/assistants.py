from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.llm import client as llm
from app.models import Assistant, Course, ModelEntry, PromptVersion, Provider, User
from app.schemas import (
    AssistantCreate,
    AssistantOut,
    AssistantUpdate,
    CourseCreate,
    CourseOut,
    CourseUpdate,
    NuanceAdd,
    PromptGenerateRequest,
    PromptVersionCreate,
    PromptVersionOut,
)
from app.security import get_current_user
from app.services.meta_prompt import generate_system_prompt

router = APIRouter(prefix="/assistants", tags=["assistants"])


async def get_assistant_or_404(assistant_id: str, db: AsyncSession) -> Assistant:
    assistant = (await db.execute(select(Assistant).where(Assistant.id == assistant_id))).scalar_one_or_none()
    if assistant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Дисциплина не найдена")
    return assistant


async def _attach_names(db: AsyncSession, assistants: Iterable[Assistant]) -> list[Assistant]:
    assistants = list(assistants)
    ids = {a.created_by for a in assistants} | {a.updated_by for a in assistants}
    ids.discard("")
    name_map: dict[str, str] = {}
    if ids:
        rows = (await db.execute(select(User.id, User.username).where(User.id.in_(ids)))).all()
        name_map = {uid: uname for uid, uname in rows}
    for a in assistants:
        a.created_by_name = name_map.get(a.created_by, "")
        a.updated_by_name = name_map.get(a.updated_by, "")
    return assistants


async def resolve_model(
    db: AsyncSession, model_entry_id: str, require_purpose: str | None = "production"
) -> tuple[Provider, ModelEntry]:
    model = (await db.execute(select(ModelEntry).where(ModelEntry.id == model_entry_id))).scalar_one_or_none()
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Модель {model_entry_id} не найдена")
    provider = (await db.execute(select(Provider).where(Provider.id == model.provider_id))).scalar_one_or_none()
    if provider is None or not provider.enabled:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Провайдер модели {model.model_id} отключён")
    if require_purpose == "production" and provider.purpose != "production":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Провайдер «{provider.name}» доступен только как архитектор промптов, не для проверки и генерации",
        )
    return provider, model


async def resolve_architect(db: AsyncSession) -> tuple[Provider, ModelEntry]:
    """Фоновая модель-архитектор (настроена на сервере). Преподаватель её не выбирает."""
    provider = (
        await db.execute(
            select(Provider).where(Provider.purpose == "architect", Provider.enabled.is_(True)).order_by(Provider.created_at)
        )
    ).scalars().first()
    if provider is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Модель-архитектор не настроена на сервере — обратитесь к администратору платформы",
        )
    model = (
        await db.execute(
            select(ModelEntry).where(ModelEntry.provider_id == provider.id, ModelEntry.enabled.is_(True))
        )
    ).scalars().first()
    if model is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "У модели-архитектора нет доступной модели")
    return provider, model


@router.get("", response_model=list[AssistantOut])
async def list_assistants(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)) -> list[Assistant]:
    assistants = (await db.execute(select(Assistant).order_by(Assistant.discipline, Assistant.name))).scalars()
    return await _attach_names(db, assistants)


@router.post("", response_model=AssistantOut)
async def create_assistant(
    body: AssistantCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> Assistant:
    assistant = Assistant(
        name=body.name,
        discipline=body.discipline,
        description=body.description,
        audience=body.audience,
        language=body.language,
        topics=body.topics,
        criteria=[c.model_dump() for c in body.criteria],
        nuances=body.nuances,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(assistant)
    await db.commit()
    await db.refresh(assistant)
    return (await _attach_names(db, [assistant]))[0]


@router.get("/{assistant_id}", response_model=AssistantOut)
async def get_assistant(
    assistant_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> Assistant:
    assistant = await get_assistant_or_404(assistant_id, db)
    return (await _attach_names(db, [assistant]))[0]


@router.patch("/{assistant_id}", response_model=AssistantOut)
async def update_assistant(
    assistant_id: str, body: AssistantUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> Assistant:
    assistant = await get_assistant_or_404(assistant_id, db)
    payload = body.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(assistant, field, value)
    assistant.updated_by = user.id
    await db.commit()
    await db.refresh(assistant)
    return (await _attach_names(db, [assistant]))[0]


@router.post("/{assistant_id}/nuances", response_model=AssistantOut)
async def add_nuance(
    assistant_id: str, body: NuanceAdd, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> Assistant:
    assistant = await get_assistant_or_404(assistant_id, db)
    text = body.text.strip()
    nuances = list(assistant.nuances or [])
    if text and text not in nuances:
        nuances.append(text)
    assistant.nuances = nuances
    assistant.updated_by = user.id
    await db.commit()
    await db.refresh(assistant)
    return (await _attach_names(db, [assistant]))[0]


@router.delete("/{assistant_id}")
async def delete_assistant(
    assistant_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> dict:
    assistant = await get_assistant_or_404(assistant_id, db)
    await db.delete(assistant)
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- courses


@router.get("/{assistant_id}/courses", response_model=list[CourseOut])
async def list_courses(
    assistant_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> list[Course]:
    await get_assistant_or_404(assistant_id, db)
    return list(
        (
            await db.execute(
                select(Course).where(Course.assistant_id == assistant_id).order_by(Course.created_at.desc())
            )
        ).scalars()
    )


@router.post("/{assistant_id}/courses", response_model=CourseOut)
async def create_course(
    assistant_id: str, body: CourseCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> Course:
    await get_assistant_or_404(assistant_id, db)
    course = Course(assistant_id=assistant_id, created_by=user.id, **body.model_dump())
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return course


@router.patch("/{assistant_id}/courses/{course_id}", response_model=CourseOut)
async def update_course(
    assistant_id: str,
    course_id: str,
    body: CourseUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Course:
    course = (
        await db.execute(select(Course).where(Course.id == course_id, Course.assistant_id == assistant_id))
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Курс не найден")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(course, field, value)
    await db.commit()
    await db.refresh(course)
    return course


@router.delete("/{assistant_id}/courses/{course_id}")
async def delete_course(
    assistant_id: str, course_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> dict:
    course = (
        await db.execute(select(Course).where(Course.id == course_id, Course.assistant_id == assistant_id))
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Курс не найден")
    await db.delete(course)
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- prompts


@router.get("/{assistant_id}/prompts", response_model=list[PromptVersionOut])
async def list_prompts(
    assistant_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> list[PromptVersion]:
    await get_assistant_or_404(assistant_id, db)
    return list(
        (
            await db.execute(
                select(PromptVersion)
                .where(PromptVersion.assistant_id == assistant_id)
                .order_by(PromptVersion.role, PromptVersion.version.desc())
            )
        ).scalars()
    )


async def _next_version(db: AsyncSession, assistant_id: str, role: str) -> int:
    versions = (
        await db.execute(
            select(PromptVersion.version)
            .where(PromptVersion.assistant_id == assistant_id, PromptVersion.role == role)
            .order_by(PromptVersion.version.desc())
        )
    ).scalars().first()
    return (versions or 0) + 1


@router.post("/{assistant_id}/prompts", response_model=PromptVersionOut)
async def create_prompt(
    assistant_id: str,
    body: PromptVersionCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PromptVersion:
    await get_assistant_or_404(assistant_id, db)
    prompt = PromptVersion(
        assistant_id=assistant_id,
        role=body.role,
        version=await _next_version(db, assistant_id, body.role),
        system_prompt=body.system_prompt,
        notes=body.notes,
        source="manual",
        target_family=body.target_family,
    )
    db.add(prompt)
    await db.commit()
    await db.refresh(prompt)
    return prompt


@router.post("/{assistant_id}/prompts/generate", response_model=PromptVersionOut)
async def generate_prompt(
    assistant_id: str,
    body: PromptGenerateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PromptVersion:
    assistant = await get_assistant_or_404(assistant_id, db)
    _, target_model = await resolve_model(db, body.target_model_entry_id)
    architect_provider, architect_model = await resolve_architect(db)
    try:
        system_prompt, design_notes = await generate_system_prompt(
            architect_provider,
            architect_model,
            assistant,
            body.role,
            target_model.family,
            body.extra_instructions,
        )
    except llm.LlmError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err))
    prompt = PromptVersion(
        assistant_id=assistant_id,
        role=body.role,
        version=await _next_version(db, assistant_id, body.role),
        system_prompt=system_prompt,
        notes=design_notes,
        source="generated",
        target_family=target_model.family,
        architect_model=f"{architect_provider.name}/{architect_model.model_id}",
    )
    db.add(prompt)
    await db.commit()
    await db.refresh(prompt)
    return prompt


@router.post("/{assistant_id}/prompts/{prompt_id}/activate", response_model=PromptVersionOut)
async def activate_prompt(
    assistant_id: str, prompt_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> PromptVersion:
    prompt = (
        await db.execute(
            select(PromptVersion).where(PromptVersion.id == prompt_id, PromptVersion.assistant_id == assistant_id)
        )
    ).scalar_one_or_none()
    if prompt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Версия промпта не найдена")
    siblings = (
        await db.execute(
            select(PromptVersion).where(
                PromptVersion.assistant_id == assistant_id,
                PromptVersion.role == prompt.role,
                PromptVersion.status == "active",
            )
        )
    ).scalars()
    for sibling in siblings:
        sibling.status = "archived"
    prompt.status = "active"
    await db.commit()
    await db.refresh(prompt)
    return prompt


@router.delete("/{assistant_id}/prompts/{prompt_id}")
async def delete_prompt(
    assistant_id: str, prompt_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
) -> dict:
    prompt = (
        await db.execute(
            select(PromptVersion).where(PromptVersion.id == prompt_id, PromptVersion.assistant_id == assistant_id)
        )
    ).scalar_one_or_none()
    if prompt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Версия промпта не найдена")
    await db.delete(prompt)
    await db.commit()
    return {"ok": True}
