"""Баланс аккаунта у LLM-провайдера — где API это позволяет.

DeepSeek — документированный GET /user/balance. Для прочих OpenAI-совместимых
пробуем распространённые паттерны (Moonshot, SiliconFlow, OpenRouter). У Яндекса и
Алибабы баланс по API-ключу инференса недоступен (только консоль/биллинг-API с другими кредами).
"""

import re
from typing import Any

import httpx

from app.models import Provider
from app.security import decrypt_secret

UNSUPPORTED = {
    "yandexgpt": "Yandex Cloud не отдаёт баланс по API-ключу — смотрите в консоли биллинга",
    "alice": "Yandex Cloud не отдаёт баланс по API-ключу — смотрите в консоли биллинга",
    "qwen": "Alibaba Model Studio не отдаёт баланс/квоты по API-ключу — смотрите в консоли DashScope",
}


def _root(base_url: str) -> str:
    return re.sub(r"/v\d+$", "", base_url.rstrip("/"))


def _fmt(amount: str | float, currency: str) -> str:
    symbols = {"USD": "$", "CNY": "¥", "RUB": "₽", "EUR": "€"}
    try:
        value = f"{float(amount):,.2f}".replace(",", " ")
    except (TypeError, ValueError):
        value = str(amount)
    sym = symbols.get(currency.upper(), "")
    return f"{sym}{value}" if sym else f"{value} {currency}"


def _parse_known(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    # DeepSeek: {"is_available":…, "balance_infos":[{"currency","total_balance",…}]}
    infos = data.get("balance_infos")
    if isinstance(infos, list) and infos and isinstance(infos[0], dict):
        parts = [_fmt(i.get("total_balance", "?"), str(i.get("currency", ""))) for i in infos]
        return " + ".join(parts)
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    # Moonshot: {"data":{"available_balance":…}} (CNY)
    if "available_balance" in inner:
        return _fmt(inner["available_balance"], str(inner.get("currency", "CNY")))
    # SiliconFlow: {"data":{"totalBalance":"…"}} (CNY)
    if "totalBalance" in inner:
        return _fmt(inner["totalBalance"], "CNY")
    # OpenRouter: {"data":{"total_credits":…,"total_usage":…}} (USD)
    if "total_credits" in inner:
        try:
            remaining = float(inner["total_credits"]) - float(inner.get("total_usage", 0))
            return _fmt(remaining, "USD")
        except (TypeError, ValueError):
            return None
    # Общие ключи
    for key in ("balance", "credit", "credits", "amount"):
        if isinstance(inner.get(key), (int, float, str)):
            return _fmt(inner[key], str(inner.get("currency", "")))
    return None


def _candidates(provider: Provider) -> list[str]:
    base = provider.base_url.rstrip("/")
    root = _root(base)
    if provider.kind == "deepseek" or "deepseek" in base:
        return [f"{root}/user/balance"]
    if "openrouter" in base:
        return [f"{root}/api/v1/credits"]
    # Generic OpenAI-like: распространённые варианты
    urls = [f"{root}/user/balance", f"{base}/users/me/balance", f"{base}/user/info", f"{root}/api/v1/credits"]
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


def _unsupported_reason(provider: Provider) -> str | None:
    if provider.kind in UNSUPPORTED:
        return UNSUPPORTED[provider.kind]
    base = provider.base_url.lower()
    if "aliyuncs" in base or "dashscope" in base:
        return UNSUPPORTED["qwen"]
    if "yandex" in base or "llm.api.cloud" in base:
        return UNSUPPORTED["yandexgpt"]
    return None


async def fetch_balance(provider: Provider) -> dict:
    reason = _unsupported_reason(provider)
    if reason:
        return {"supported": False, "ok": False, "balance": "", "message": reason}
    if not provider.api_key_encrypted:
        return {"supported": True, "ok": False, "balance": "", "message": "API-ключ не задан"}
    headers = {"Authorization": f"Bearer {decrypt_secret(provider.api_key_encrypted)}"}
    headers.update(provider.extra_headers or {})
    last_error = "эндпоинт баланса не найден"
    async with httpx.AsyncClient(timeout=20.0) as client:
        for url in _candidates(provider):
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError as err:
                last_error = f"сеть: {err}"
                continue
            if response.status_code in (401, 403):
                return {"supported": True, "ok": False, "balance": "", "message": f"ключ отклонён (HTTP {response.status_code})"}
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                continue
            try:
                parsed = _parse_known(response.json())
            except ValueError:
                last_error = "не-JSON ответ"
                continue
            if parsed:
                return {"supported": True, "ok": True, "balance": parsed, "message": ""}
            last_error = "неизвестный формат ответа"
    return {"supported": False, "ok": False, "balance": "", "message": f"Баланс у этого провайдера получить не удалось ({last_error})"}
