from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .env import load_env
from ui.output_policy import print_assistant, sanitize_no_emoji
from ui.progress import status


Message = dict[str, Any]
NO_EMOJI_SYSTEM_RULE = "Do not use emoji in any user-facing text, code, comments, lists, or tool explanations."
DEFAULT_DEEPSEEK_TIMEOUT = None
DEFAULT_GLM_VISION_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_GLM_VISION_MODEL = "glm-4.6v-flash"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
VISION_REQUEST_LOCK = threading.Lock()


class DeepSeekRequestTimeout(RuntimeError):
    """Raised when the DeepSeek request exceeds the configured socket timeout."""


@dataclass(slots=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    max_tokens: int = 4096
    temperature: float = 0.3
    reasoning_effort: str = "high"
    thinking: str = "enabled"
    timeout: float | None = DEFAULT_DEEPSEEK_TIMEOUT

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


def _env_timeout(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"0", "none", "no", "false", "off", "unlimited", "infinite"}:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number of seconds or 0 for no timeout, got {raw!r}") from exc
    if value <= 0:
        return None
    return value


def _format_seconds(value: float) -> str:
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _timeout_message(config: DeepSeekConfig) -> str:
    timeout = _format_seconds(config.timeout or 0)
    return (
        f"DeepSeek API request timed out after {timeout}s. "
        "Set DEEPSEEK_TIMEOUT=0 in .env to disable the request timeout, or reduce DEEPSEEK_MAX_TOKENS."
    )


def config_from_env(
    env_path: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    no_thinking: bool = False,
) -> DeepSeekConfig:
    load_env(env_path)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key or api_key.startswith("sk-your-"):
        raise ValueError("DEEPSEEK_API_KEY is missing. Fill it in .env before running chat-test.")
    return DeepSeekConfig(
        api_key=api_key,
        base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        max_tokens=max_tokens or int(os.environ.get("DEEPSEEK_MAX_TOKENS", "4096")),
        temperature=temperature if temperature is not None else float(os.environ.get("DEEPSEEK_TEMPERATURE", "0.3")),
        reasoning_effort="" if no_thinking else os.environ.get("DEEPSEEK_REASONING_EFFORT", "high"),
        thinking="disabled" if no_thinking else os.environ.get("DEEPSEEK_THINKING", "enabled"),
        timeout=_env_timeout("DEEPSEEK_TIMEOUT", DEFAULT_DEEPSEEK_TIMEOUT),
    )


def vision_config_from_env(
    env_path: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> DeepSeekConfig:
    load_env(env_path)
    api_key = (
        os.environ.get("GLM_VISION_API_KEY", "").strip()
        or os.environ.get("GLM_API_KEY", "").strip()
    )
    if not api_key or api_key.startswith("sk-your-"):
        raise ValueError("GLM_VISION_API_KEY is missing. Fill it in .env before using image understanding.")
    return DeepSeekConfig(
        api_key=api_key,
        base_url=base_url
        or os.environ.get("GLM_VISION_BASE_URL")
        or os.environ.get("GLM_BASE_URL")
        or DEFAULT_GLM_VISION_BASE_URL,
        model=model or os.environ.get("GLM_VISION_MODEL", DEFAULT_GLM_VISION_MODEL),
        max_tokens=max_tokens or int(os.environ.get("GLM_VISION_MAX_TOKENS", "4096")),
        temperature=temperature
        if temperature is not None
        else float(os.environ.get("GLM_VISION_TEMPERATURE", os.environ.get("DEEPSEEK_TEMPERATURE", "0.2"))),
        reasoning_effort="",
        thinking="",
        timeout=_env_timeout("GLM_VISION_TIMEOUT", _env_timeout("DEEPSEEK_TIMEOUT", DEFAULT_DEEPSEEK_TIMEOUT)),
    )


def image_file_to_data_url(path: str | Path, *, max_bytes: int = MAX_IMAGE_BYTES) -> str:
    target = Path(path).expanduser()
    if not target.exists() or not target.is_file():
        raise ValueError(f"image file not found: {target}")
    size = target.stat().st_size
    if size <= 0:
        raise ValueError(f"image file is empty: {target}")
    if size > max_bytes:
        raise ValueError(f"image file is too large: {target} ({size} bytes > {max_bytes} bytes)")
    mime_type, _ = mimetypes.guess_type(str(target))
    if mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"}:
        raise ValueError(f"unsupported image type for {target}; use png, jpg, webp, gif, or bmp")
    encoded = base64.b64encode(target.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def image_url_part(path_or_url: str | Path) -> dict[str, Any]:
    text = str(path_or_url).strip()
    if text.startswith(("http://", "https://", "data:image/")):
        url = text
    else:
        url = image_file_to_data_url(text)
    return {"type": "image_url", "image_url": {"url": url}}


def multimodal_user_message(prompt: str, image_paths: list[str | Path]) -> Message:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend(image_url_part(path) for path in image_paths)
    return {"role": "user", "content": content}


def _contains_image_part(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "image_url" or "image_url" in value:
            return True
        return any(_contains_image_part(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_image_part(item) for item in value)
    return False


def chat_completion(
    messages: list[Message],
    config: DeepSeekConfig,
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
    if config.reasoning_effort:
        payload["reasoning_effort"] = config.reasoning_effort
    if config.thinking:
        payload["thinking"] = {"type": config.thinking}
    request = urllib.request.Request(
        config.endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        if _contains_image_part(messages):
            with VISION_REQUEST_LOCK:
                with urllib.request.urlopen(request, timeout=config.timeout) as response:
                    raw = response.read().decode("utf-8")
        else:
            with urllib.request.urlopen(request, timeout=config.timeout) as response:
                raw = response.read().decode("utf-8")
    except (TimeoutError, socket.timeout) as exc:
        raise DeepSeekRequestTimeout(_timeout_message(config)) from exc
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise DeepSeekRequestTimeout(_timeout_message(config)) from exc
        raise RuntimeError(f"DeepSeek API request failed: {exc.reason}") from exc
    data = json.loads(raw)
    if "error" in data:
        raise RuntimeError(f"DeepSeek API error: {data['error']}")
    return data


def _stream_delta_text(event: dict[str, Any]) -> str:
    choices = event.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    delta = first.get("delta")
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return str(message.get("content") or "")
    return ""


def chat_completion_stream_text(
    messages: list[Message],
    config: DeepSeekConfig,
    *,
    on_text: Callable[[str], None] | None = None,
) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
    }
    if config.reasoning_effort:
        payload["reasoning_effort"] = config.reasoning_effort
    if config.thinking:
        payload["thinking"] = {"type": config.thinking}
    request = urllib.request.Request(
        config.endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    chunks: list[str] = []
    finish_reason = None
    usage: dict[str, Any] | None = None
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                if "error" in event:
                    raise RuntimeError(f"DeepSeek API error: {event['error']}")
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                choices = event.get("choices") or []
                if choices and isinstance(choices[0], dict) and choices[0].get("finish_reason"):
                    finish_reason = choices[0].get("finish_reason")
                text = sanitize_no_emoji(_stream_delta_text(event))
                if not text:
                    continue
                chunks.append(text)
                if on_text is not None:
                    on_text(text)
    except (TimeoutError, socket.timeout) as exc:
        raise DeepSeekRequestTimeout(_timeout_message(config)) from exc
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise DeepSeekRequestTimeout(_timeout_message(config)) from exc
        raise RuntimeError(f"DeepSeek API request failed: {exc.reason}") from exc
    answer = "".join(chunks)
    response: dict[str, Any] = {"choices": [{"message": {"content": answer}, "finish_reason": finish_reason}]}
    if usage:
        response["usage"] = usage
    return answer, response


def extract_assistant_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("DeepSeek API response did not include choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return sanitize_no_emoji(content)
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        return sanitize_no_emoji("".join(parts))
    return sanitize_no_emoji(str(content or ""))


def chat_once(prompt: str, system: str, config: DeepSeekConfig) -> tuple[str, dict[str, Any]]:
    messages: list[Message] = []
    if system:
        messages.append({"role": "system", "content": f"{system}\n\n{NO_EMOJI_SYSTEM_RULE}"})
    else:
        messages.append({"role": "system", "content": NO_EMOJI_SYSTEM_RULE})
    messages.append({"role": "user", "content": prompt})
    response = chat_completion(messages, config)
    return extract_assistant_text(response), response


def vision_chat_once(
    prompt: str,
    image_paths: list[str | Path],
    system: str,
    config: DeepSeekConfig,
) -> tuple[str, dict[str, Any]]:
    if not image_paths:
        return chat_once(prompt, system, config)
    messages: list[Message] = []
    if system:
        messages.append({"role": "system", "content": f"{system}\n\n{NO_EMOJI_SYSTEM_RULE}"})
    else:
        messages.append({"role": "system", "content": NO_EMOJI_SYSTEM_RULE})
    messages.append(multimodal_user_message(prompt, image_paths))
    response = chat_completion(messages, config)
    return extract_assistant_text(response), response


def interactive_chat(system: str, config: DeepSeekConfig) -> int:
    messages: list[Message] = []
    if system:
        messages.append({"role": "system", "content": f"{system}\n\n{NO_EMOJI_SYSTEM_RULE}"})
    else:
        messages.append({"role": "system", "content": NO_EMOJI_SYSTEM_RULE})
    print("DeepSeek chat-test. Type /exit to quit.")
    while True:
        try:
            prompt = input("\nuser> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            return 0
        messages.append({"role": "user", "content": prompt})
        print("\nassistant>")
        answer, _response = chat_completion_stream_text(messages, config, on_text=_write_stream_text)
        print()
        messages.append({"role": "assistant", "content": answer})


def _write_stream_text(text: str) -> None:
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()


def run_chat_test(
    prompt: str,
    system: str,
    env_path: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    no_thinking: bool = False,
    interactive: bool = False,
    image_paths: list[str] | None = None,
    stream: bool = True,
) -> int:
    if image_paths:
        config = vision_config_from_env(
            env_path=env_path,
            model=model,
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    else:
        config = config_from_env(
            env_path=env_path,
            model=model,
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
            no_thinking=no_thinking,
        )
    if interactive:
        if image_paths:
            raise ValueError("chat-test --interactive does not support --image; use a single prompt with --image.")
        return interactive_chat(system, config)
    if stream and not image_paths:
        messages: list[Message] = []
        if system:
            messages.append({"role": "system", "content": f"{system}\n\n{NO_EMOJI_SYSTEM_RULE}"})
        else:
            messages.append({"role": "system", "content": NO_EMOJI_SYSTEM_RULE})
        messages.append({"role": "user", "content": prompt})
        answer, response = chat_completion_stream_text(messages, config, on_text=_write_stream_text)
        if answer:
            print()
        usage = response.get("usage")
        if usage:
            print(f"\nusage: {json.dumps(usage, ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
        return 0
    with status("正在请求模型"):
        if image_paths:
            answer, response = vision_chat_once(prompt, image_paths, system, config)
        else:
            answer, response = chat_once(prompt, system, config)
    print_assistant(answer)
    usage = response.get("usage")
    if usage:
        print(f"\nusage: {json.dumps(usage, ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
    return 0
