import asyncio
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


# Семейства, у OpenAI-совместимых API которых поддерживается stream_options.include_usage.
STREAM_USAGE_FAMILIES = {"deepseek", "qwen", "gpt", "generic"}
RETRYABLE_ATTEMPTS = 3


async def _stream_completion(client: httpx.AsyncClient, url: str, payload: dict, headers: dict, provider_name: str) -> tuple[str, dict]:
    text_parts: list[str] = []
    usage: dict = {}
    async with client.stream("POST", url, json=payload, headers=headers) as response:
        if response.status_code >= 400:
            body_bytes = await response.aread()
            try:
                body = json.loads(body_bytes)
                message = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
            except json.JSONDecodeError:
                message = body_bytes.decode("utf-8", errors="replace")[:500]
            raise LlmError(f"{provider_name} HTTP {response.status_code}: {message}")
        async for line in response.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if event.get("usage"):
                usage = event["usage"]
            choices = event.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    text_parts.append(piece)
    return "".join(text_parts), usage


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
        "stream": True,
    }
    _apply_family_params(payload, model, temperature, thinking)
    if model.family in STREAM_USAGE_FAMILIES:
        payload["stream_options"] = {"include_usage": True}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if json_mode and model.supports_json:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {decrypt_secret(provider.api_key_encrypted)}"}
    headers.update(provider.extra_headers or {})

    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    started = time.monotonic()
    read_timeout = timeout or get_settings().llm_request_timeout
    # В стриме read-таймаут действует на каждый чанк, а не на весь ответ — поток не даёт
    # промежуточным узлам (VPN, прокси, nginx) закрыть «молчащее» соединение.
    timeouts = httpx.Timeout(connect=30.0, read=read_timeout, write=60.0, pool=30.0)

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeouts) as client:
        for attempt in range(RETRYABLE_ATTEMPTS):
            try:
                text, usage = await _stream_completion(client, url, payload, headers, provider.name)
                break
            except httpx.HTTPError as err:
                last_error = err
                if attempt == RETRYABLE_ATTEMPTS - 1:
                    raise LlmError(f"Сетевая ошибка при запросе к {provider.name}: {err}") from err
                await asyncio.sleep(2**attempt)
        else:
            raise LlmError(f"Сетевая ошибка при запросе к {provider.name}: {last_error}")

    duration_ms = int((time.monotonic() - started) * 1000)
    if not text:
        raise LlmError(f"{provider.name} вернул пустой ответ (стрим без content)")

    return LlmResult(
        text=text,
        duration_ms=duration_ms,
        tokens_total=usage.get("total_tokens"),
        tokens_prompt=usage.get("prompt_tokens"),
        tokens_completion=usage.get("completion_tokens"),
        raw={"usage": usage},
    )



# \t,\r,\n,\b,\f — валидные JSON-эскейпы, которыми модели нечаянно начинают LaTeX-команды
# (\times, \rho, \nabla, \beta, \frac). Различаем по продолжению: управляющий символ перед
# буквами команды не пишет ни один автор — значит, это LaTeX и бэкслеш надо экранировать.
_LATEX_T_RE = re.compile(r"t(?:imes|ext|frac|heta|herefore|ilde|riangle|au(?![a-zA-Z])|anh?(?![a-zA-Z])|o(?![a-zA-Z]))")
_LATEX_R_RE = re.compile(r"r(?:ho(?![a-zA-Z])|ight|angle|ceil|floor)")
_LATEX_N_RE = re.compile(r"n(?:abla|otin|eq(?![a-zA-Z]))")
_LATEX_BF_RE = re.compile(r"[bf][a-zA-Z]")
_UNICODE_ESC_RE = re.compile(r"u[0-9a-fA-F]{4}")


def _fix_latex_escapes(raw: str) -> str:
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        j = i
        while j < n and raw[j] == "\\":
            j += 1
        run = j - i
        out.append("\\" * (run - run % 2))  # пары \\ уже экранированы корректно
        i = j
        if run % 2 == 0:
            continue
        rest = raw[j : j + 12]
        nxt = rest[:1]
        if nxt in '"/\\' or (nxt == "u" and _UNICODE_ESC_RE.match(rest)):
            keep = True
        elif nxt in "bfnrt":
            latex = (
                (nxt in "bf" and _LATEX_BF_RE.match(rest))
                or (nxt == "t" and _LATEX_T_RE.match(rest))
                or (nxt == "r" and _LATEX_R_RE.match(rest))
                or (nxt == "n" and _LATEX_N_RE.match(rest))
            )
            keep = not latex
        else:
            keep = False  # невалидный JSON-эскейп (\alpha, \sigma, \Gamma...) — это LaTeX
        out.append("\\" if keep else "\\\\")
    return "".join(out)


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
        parsed = json.loads(_fix_latex_escapes(candidate))
    except json.JSONDecodeError:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as err:
            raise LlmError(f"Невалидный JSON в ответе модели: {err}") from err
    if not isinstance(parsed, dict):
        raise LlmError("Модель вернула JSON, но это не объект")
    return parsed
