from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .env import load_env
from ui.output_policy import print_assistant, sanitize_no_emoji
from ui.progress import status


Message = dict[str, Any]
NO_EMOJI_SYSTEM_RULE = "Do not use emoji in any user-facing text, code, comments, lists, or tool explanations."


@dataclass(slots=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    max_tokens: int = 4096
    temperature: float = 0.3
    reasoning_effort: str = "high"
    thinking: str = "enabled"
    timeout: float = 60.0

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


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
        timeout=float(os.environ.get("DEEPSEEK_TIMEOUT", "60")),
    )


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
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DeepSeek API request failed: {exc.reason}") from exc
    data = json.loads(raw)
    if "error" in data:
        raise RuntimeError(f"DeepSeek API error: {data['error']}")
    return data


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
        with status("正在请求模型"):
            response = chat_completion(messages, config)
        answer = extract_assistant_text(response)
        messages.append({"role": "assistant", "content": answer})
        print("\nassistant>")
        print_assistant(answer)


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
) -> int:
    config = config_from_env(
        env_path=env_path,
        model=model,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=temperature,
        no_thinking=no_thinking,
    )
    if interactive:
        return interactive_chat(system, config)
    with status("正在请求模型"):
        answer, response = chat_once(prompt, system, config)
    print_assistant(answer)
    usage = response.get("usage")
    if usage:
        print(f"\nusage: {json.dumps(usage, ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
    return 0
