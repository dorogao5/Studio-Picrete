import json
import re
import time
from dataclasses import dataclass, field

import httpx

from app.config import get_settings
from app.models import ModelEntry, Provider
from app.security import decrypt_secret

NO_SAMPLING_FAMILIES = {"gpt"}


@dataclass
class LlmResult:
    text: str
    duration_ms: int
    tokens_total: int | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    raw: dict = field(default_factory=dict)


class LlmError(Exception):
    pass


def _apply_family_params(payload: dict, model: ModelEntry, temperature: float | None, thinking: str | None) -> None:
    family = model.family
    if family == "deepseek":
        mode = thinking or "enabled"
        payload["thinking"] = {"type": mode}
        if mode == "disabled" and temperature is not None:
            payload["temperature"] = temperature
        return
    if family in NO_SAMPLING_FAMILIES:
        return
    if temperature is not None:
        if family in ("yandexgpt", "alice"):
            temperature = min(max(temperature, 0.0), 1.0)
        payload["temperature"] = temperature


async def chat(
    provider: Provider,
    model: ModelEntry,
    system_prompt: str,
    user_content: str | list,
    temperature: float | None = 0.1,
    max_tokens: int | None = None,
    json_mode: bool = False,
    thinking: str | None = None,
    timeout: float | None = None,
) -> LlmResult:
    payload: dict = {
        "model": model.model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    _apply_family_params(payload, model, temperature, thinking)
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if json_mode and model.supports_json:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {decrypt_secret(provider.api_key_encrypted)}"}
    headers.update(provider.extra_headers or {})

    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    started = time.monotonic()

    async with httpx.AsyncClient(timeout=timeout or get_settings().llm_request_timeout) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as err:
            raise LlmError(f"Сетевая ошибка при запросе к {provider.name}: {err}") from err

    duration_ms = int((time.monotonic() - started) * 1000)

    try:
        body = response.json()
    except json.JSONDecodeError:
        raise LlmError(f"{provider.name} вернул не-JSON ответ (HTTP {response.status_code}): {response.text[:500]}")

    if response.status_code >= 400:
        message = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        raise LlmError(f"{provider.name} HTTP {response.status_code}: {message or response.text[:500]}")

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LlmError(f"Ответ {provider.name} без choices[0].message.content: {str(body)[:500]}")

    usage = body.get("usage") or {}
    return LlmResult(
        text=content or "",
        duration_ms=duration_ms,
        tokens_total=usage.get("total_tokens"),
        tokens_prompt=usage.get("prompt_tokens"),
        tokens_completion=usage.get("completion_tokens"),
        raw=body,
    )


def extract_json(text: str) -> dict:
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            raise LlmError(f"В ответе модели не найден JSON: {text[:300]}")
        candidate = candidate[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as err:
        raise LlmError(f"Невалидный JSON в ответе модели: {err}") from err
    if not isinstance(parsed, dict):
        raise LlmError("Модель вернула JSON, но это не объект")
    return parsed
