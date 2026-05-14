from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .tool_layer import tool_schemas

MAX_TOOL_RESULT_CHARS = 12_000


@dataclass(frozen=True, slots=True)
class RequestedToolCall:
    id: str
    name: str
    args: dict[str, Any]
    legacy: bool = False


def chat_tool_schemas() -> list[dict[str, Any]]:
    """Return OpenAI-compatible chat tool schemas for the local tool layer."""
    result: list[dict[str, Any]] = []
    for spec in tool_schemas():
        if spec["name"] == "kg_answer":
            continue
        approval = "Requires explicit user approval." if spec["approval_required"] else "Runs without approval."
        result.append(
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": f"{spec['description']} {approval}",
                    "parameters": spec["input_schema"],
                },
            }
        )
    return result


def extract_assistant_message(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("DeepSeek API response did not include choices")
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        raise RuntimeError("DeepSeek API response message was not an object")
    return dict(message)


def requested_tool_calls(message: dict[str, Any]) -> list[RequestedToolCall]:
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list):
        calls = []
    result: list[RequestedToolCall] = []
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        raw_args = function.get("arguments") or "{}"
        call_id = str(call.get("id") or f"tool-call-{index}")
        if not name:
            continue
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError as exc:
            args = {"__parse_error__": str(exc), "__raw_arguments__": str(raw_args)}
        if not isinstance(args, dict):
            args = {"__parse_error__": "tool arguments must be a JSON object", "__raw_arguments__": args}
        result.append(RequestedToolCall(id=call_id, name=name, args=args))
    function_call = message.get("function_call")
    if isinstance(function_call, dict):
        name = str(function_call.get("name") or "").strip()
        if name:
            raw_args = function_call.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError as exc:
                args = {"__parse_error__": str(exc), "__raw_arguments__": str(raw_args)}
            if not isinstance(args, dict):
                args = {"__parse_error__": "tool arguments must be a JSON object", "__raw_arguments__": args}
            result.append(RequestedToolCall(id="function_call", name=name, args=args, legacy=True))
    return result


def assistant_message_for_history(message: dict[str, Any]) -> dict[str, Any]:
    history_message: dict[str, Any] = {"role": "assistant"}
    content = message.get("content")
    history_message["content"] = content if content is not None else ""
    if message.get("tool_calls"):
        history_message["tool_calls"] = message["tool_calls"]
    if message.get("function_call"):
        history_message["function_call"] = message["function_call"]
    return history_message


def tool_result_message(
    call_id: str,
    result: dict[str, Any],
    *,
    name: str | None = None,
    legacy: bool = False,
) -> dict[str, Any]:
    content = summarize_tool_result(result)
    if legacy:
        return {
            "role": "function",
            "name": name or call_id,
            "content": content,
        }
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": content,
    }


def tool_result_message_for_call(call: RequestedToolCall, result: dict[str, Any]) -> dict[str, Any]:
    return tool_result_message(call.id, result, name=call.name, legacy=call.legacy)


def summarize_tool_result(result: dict[str, Any], *, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    safe_result = _bounded(result)
    text = json.dumps(safe_result, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    overflow = len(text) - limit
    return text[:limit] + f"... <truncated {overflow} chars>"


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "<max depth reached>"
    if isinstance(value, dict):
        return {str(key): _bounded(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        items = [_bounded(item, depth=depth + 1) for item in value[:80]]
        if len(value) > 80:
            items.append(f"... {len(value) - 80} more items")
        return items
    if isinstance(value, str):
        if len(value) > 4_000:
            return value[:4_000] + f"... <truncated {len(value) - 4_000} chars>"
        return value
    return value
