"""Bounded function-calling dialogue; execution belongs exclusively to the private gateway."""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.llm.client import LlmError, LlmResult, _apply_family_params
from app.security import decrypt_secret


DEFINITIONS = json.loads(Path(__file__).with_name("essential_tool_definitions.json").read_text())


def validate_arguments(name, arguments):
    if name not in DEFINITIONS or not isinstance(arguments, dict):
        raise LlmError("Unknown tool or invalid arguments")
    schema = DEFINITIONS[name]["parameters"]
    if set(arguments) - set(schema["properties"]) or any(key not in arguments for key in schema["required"]):
        raise LlmError("Invalid tool argument keys")
    for key, value in arguments.items():
        rule = schema["properties"][key]
        if rule.get("type", "string") == "string" and (not isinstance(value, str) or len(value) > rule.get("maxLength", 512)):
            raise LlmError("Invalid tool string argument")
        if "enum" in rule and value not in rule["enum"]:
            raise LlmError("Invalid tool operation")
        if rule.get("type") == "array" and (not isinstance(value, list) or len(value) > 64
                                          or any(not isinstance(s, str) or len(s) > 64 for s in value)):
            raise LlmError("Invalid tool symbols")


async def completion(client, url, headers, payload):
    message = {"role": "assistant", "content": ""}
    calls = {}
    finish = None
    usage = {}
    async with client.stream("POST", url, headers=headers, json=payload) as response:
        if response.status_code >= 400:
            detail = ""
            body = await response.aread()
            try:
                error = json.loads(body).get("error") if len(body) <= 16384 else None
                message = error.get("message") if isinstance(error, dict) else None
                if isinstance(message, str):
                    for value in headers.values():
                        if value:
                            message = message.replace(str(value), "[REDACTED]")
                            if str(value).startswith("Bearer "):
                                message = message.replace(str(value)[7:], "[REDACTED]")
                    detail = ": " + " ".join(message.split())[:500]
            except (ValueError, AttributeError):
                pass
            raise LlmError(f"Tool dialogue model HTTP {response.status_code}{detail}; no retry")
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                break
            try:
                event = json.loads(raw)
            except ValueError as err:
                raise LlmError("Malformed model stream") from err
            if event.get("error"):
                raise LlmError("Model stream error; no retry")
            usage = event.get("usage") or usage
            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish = choice.get("finish_reason") or finish
            delta = choice.get("delta") or {}
            if any(part.get(field) is not None for part in (choice, delta)
                   for field in ("refusal", "error")):
                raise LlmError("Model refused or returned an error; no retry")
            for field in ("content", "reasoning_content"):
                if delta.get(field):
                    message[field] = message.get(field, "") + delta[field]
            for part in delta.get("tool_calls") or []:
                index = part.get("index", 0)
                if index not in calls:
                    if len(calls) >= 96:
                        raise LlmError("Too many streamed tool calls")
                    calls[index] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                call = calls[index]
                if part.get("id"):
                    call["id"] += part["id"]
                for field in ("name", "arguments"):
                    call["function"][field] += (part.get("function") or {}).get(field) or ""
                if len(call["function"]["arguments"]) > 16384:
                    raise LlmError("Tool arguments too large")
    if finish not in {"stop", "tool_calls"}:
        raise LlmError(f"Model finish_reason={finish}; no retry")
    if calls:
        message["tool_calls"] = list(calls.values())
    if bool(calls) != (finish == "tool_calls"):
        raise LlmError("Inconsistent tool-call finish reason")
    return message, usage


async def chat_with_tools(provider, model, system_prompt, user_content, *, response_schema,
                          reasoning_effort, timeout, temperature, thinking, json_mode, max_tokens):
    # client.chat owns the total asyncio.timeout across model AND gateway continuations.
    settings = get_settings()
    if not settings.essential_tools_gateway_url or not settings.essential_tools_gateway_token:
        raise LlmError("Private essential tools gateway URL/token not configured")
    if (len(settings.essential_tools_gateway_token) < 32 or not settings.essential_tools_gateway_token.isascii()
            or any(c.isspace() for c in settings.essential_tools_gateway_token)):
        raise LlmError("Essential tools gateway token must be at least 32 ASCII characters")
    gateway = urlsplit(settings.essential_tools_gateway_url)
    if (gateway.scheme not in {"http", "https"} or not gateway.hostname or gateway.username
            or gateway.password or gateway.query or gateway.fragment):
        raise LlmError("Invalid private gateway URL")
    rounds, max_calls = settings.essential_tools_max_rounds, settings.essential_tools_max_calls
    if not 1 <= rounds <= 32 or not 1 <= max_calls <= 96:
        raise LlmError("Invalid essential tools budget")
    payload = {"model": model.model_id, "stream": True, "stream_options": {"include_usage": True},
               "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
               "tools": [{"type": "function", "function": {"name": name, **definition}}
                         for name, definition in DEFINITIONS.items()], "tool_choice": "auto"}
    _apply_family_params(payload, model, temperature, thinking)
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if response_schema is not None:
        if not model.supports_json:
            raise LlmError("Model does not support JSON")
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "picrete_response", "strict": True, "schema": response_schema}}
    elif json_mode and model.supports_json:
        payload["response_format"] = {"type": "json_object"}
    headers = {**(provider.extra_headers or {}), "Authorization": f"Bearer {decrypt_secret(provider.api_key_encrypted)}"}
    gateway_headers = {"Authorization": f"Bearer {settings.essential_tools_gateway_token}"}
    audit = {"transport": "essential_tools", "model_calls": 0, "usage_by_call": [], "tool_traces": []}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout or settings.llm_request_timeout) as client:
            for round_index in range(rounds + 1):
                if round_index == rounds or len(audit["tool_traces"]) >= max_calls:
                    payload["tool_choice"] = "none"
                    payload["messages"][0]["content"] += (
                        "\nTool budget exhausted. Finish in the requested format. Do not claim unperformed checks "
                        "or invent tool results; explicitly report any calculations you could not verify.")
                audit["model_calls"] += 1
                message, usage = await completion(client, provider.base_url.rstrip("/") + "/chat/completions", headers, payload)
                audit["usage_by_call"].append(usage)
                calls = message.get("tool_calls") or []
                if not calls:
                    if not message.get("content"):
                        raise LlmError("Empty final tool-dialogue answer")
                    totals = {key: (sum(u[key] for u in audit["usage_by_call"])
                                   if all(isinstance(u.get(key), int) for u in audit["usage_by_call"]) else None)
                              for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
                    audit["usage"] = totals
                    return LlmResult(text=message["content"], raw=audit,
                        duration_ms=int((time.monotonic() - started) * 1000), tokens_total=totals["total_tokens"],
                        tokens_prompt=totals["prompt_tokens"], tokens_completion=totals["completion_tokens"])
                if round_index == rounds or len(audit["tool_traces"]) + len(calls) > max_calls:
                    raise LlmError("Essential tools budget exhausted")
                prepared = []
                ids = set()
                for call in calls:
                    if not call["id"] or call["id"] in ids:
                        raise LlmError("Invalid tool call id")
                    ids.add(call["id"])
                    name = call["function"]["name"]
                    try:
                        arguments = json.loads(call["function"]["arguments"])
                        validate_arguments(name, arguments)
                        argument_error = None
                    except (ValueError, LlmError) as err:
                        if name not in DEFINITIONS:
                            raise LlmError("Unknown tool; gateway not called") from err
                        arguments = {}
                        argument_error = {"tool_name": name, "tool_version": "adapter-validation-v1",
                            "arguments": {}, "normalized_result": None, "status": "invalid_request",
                            "error": "Invalid JSON or tool arguments; correct the same request."}
                    prepared.append((call, name, arguments, argument_error))
                # Preserve DeepSeek reasoning_content only in the in-memory continuation, never in audit.
                if model.family == "qwen":
                    message = {key: value for key, value in message.items() if key != "reasoning_content"}
                payload["messages"].append(message)
                for call, name, arguments, argument_error in prepared:
                    if argument_error:
                        data = argument_error
                    else:
                        result = await client.post(settings.essential_tools_gateway_url.rstrip("/") + "/invoke",
                            headers=gateway_headers, json={"tool": name, "arguments": arguments},
                            timeout=settings.essential_tools_timeout)
                        if result.status_code != 200:
                            raise LlmError(f"Essential tools gateway HTTP {result.status_code}; no retry")
                        if len(result.content) > 262144:
                            raise LlmError("Tool result too large")
                        try:
                            data = result.json()
                        except ValueError as err:
                            raise LlmError("Malformed gateway JSON") from err
                    if not isinstance(data, dict) or data.get("tool_name") != name or data.get("arguments") != arguments:
                        raise LlmError("Gateway result identity mismatch")
                    trace = {key: data[key] for key in ("tool_name", "tool_version", "arguments", "normalized_result",
                                                       "status", "trace_id", "error_type", "error") if key in data}
                    trace["tool_call_id"] = call["id"]
                    audit["tool_traces"].append(trace)
                    if (data.get("status") not in {"success", "error", "invalid_request"}
                            or not {"tool_version", "normalized_result"} <= data.keys()
                            or (data["status"] == "success" and "trace_id" not in data)):
                        raise LlmError("Invalid tool result envelope")
                    payload["messages"].append({"role": "tool", "tool_call_id": call["id"],
                                               "content": json.dumps(trace, ensure_ascii=False)})
    except LlmError as err:
        err.raw = audit
        raise
    except httpx.HTTPError as err:
        raise LlmError("Essential tools network error; no retry", raw=audit) from err
    raise LlmError("Essential tools round budget exhausted", raw=audit)
