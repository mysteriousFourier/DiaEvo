import json
import socket
import urllib.request
import threading
import time

from diaevo.tool_chat import (
    assistant_message_for_history,
    chat_tool_schemas,
    requested_tool_calls,
    summarize_tool_result,
    tool_result_message,
    tool_result_message_for_call,
)
from diaevo.deepseek_chat import (
    DeepSeekConfig,
    DeepSeekRequestTimeout,
    NO_EMOJI_SYSTEM_RULE,
    chat_completion,
    chat_once,
    image_file_to_data_url,
    multimodal_user_message,
    vision_config_from_env,
)


def test_chat_tool_schemas_are_openai_compatible() -> None:
    schemas = {item["function"]["name"]: item for item in chat_tool_schemas()}

    read_file = schemas["read_file"]
    write_file = schemas["write_file"]
    assert read_file["type"] == "function"
    assert read_file["function"]["parameters"]["type"] == "object"
    assert "Runs without approval" in read_file["function"]["description"]
    assert "Requires explicit user approval" in write_file["function"]["description"]


def test_requested_tool_calls_parse_arguments() -> None:
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "README.md", "limit": 3}'},
            }
        ],
    }

    calls = requested_tool_calls(message)

    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].name == "read_file"
    assert calls[0].args == {"path": "README.md", "limit": 3}


def test_invalid_tool_arguments_become_parse_error() -> None:
    calls = requested_tool_calls(
        {
            "tool_calls": [
                {"id": "bad", "function": {"name": "read_file", "arguments": '{"path":'}},
            ]
        }
    )

    assert calls[0].args["__parse_error__"]
    assert calls[0].args["__raw_arguments__"] == '{"path":'


def test_requested_tool_calls_support_legacy_function_call() -> None:
    calls = requested_tool_calls(
        {"function_call": {"name": "list_files", "arguments": '{"path": ".", "recursive": false}'}}
    )

    assert calls[0].id == "function_call"
    assert calls[0].name == "list_files"
    assert calls[0].args == {"path": ".", "recursive": False}
    assert calls[0].legacy is True


def test_tool_result_message_is_bounded_json() -> None:
    message = tool_result_message("call_1", {"status": "ok", "tool": "read_file", "content": "x" * 5000})
    content = json.loads(message["content"])

    assert message["role"] == "tool"
    assert message["tool_call_id"] == "call_1"
    assert content["content"].endswith("<truncated 1000 chars>")


def test_tool_result_message_for_legacy_function_call() -> None:
    call = requested_tool_calls({"function_call": {"name": "list_files", "arguments": "{}"}})[0]
    message = tool_result_message_for_call(call, {"status": "ok", "tool": "list_files"})

    assert message["role"] == "function"
    assert message["name"] == "list_files"


def test_assistant_message_for_history_preserves_tool_calls() -> None:
    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}]}

    history = assistant_message_for_history(message)

    assert history["role"] == "assistant"
    assert history["content"] == ""
    assert history["tool_calls"] == [{"id": "call_1"}]


def test_summarize_tool_result_has_total_limit() -> None:
    summary = summarize_tool_result({"content": "x" * 20_000}, limit=100)

    assert len(summary) > 100
    assert "<truncated" in summary


def test_chat_once_injects_no_emoji_system_rule(monkeypatch) -> None:
    captured = {}

    def fake_chat_completion(messages, config):
        captured["messages"] = messages
        return {"choices": [{"message": {"content": "ok ✅"}}]}

    monkeypatch.setattr("diaevo.deepseek_chat.chat_completion", fake_chat_completion)

    answer, _ = chat_once("hello", "system", object())  # type: ignore[arg-type]

    assert NO_EMOJI_SYSTEM_RULE in captured["messages"][0]["content"]
    assert answer == "ok "


def test_chat_completion_reports_read_timeout(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise socket.timeout("The read operation timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    config = DeepSeekConfig(api_key="sk-test", timeout=1)

    try:
        chat_completion([{"role": "user", "content": "hello"}], config)
    except DeepSeekRequestTimeout as exc:
        message = str(exc)
    else:
        raise AssertionError("expected DeepSeekRequestTimeout")

    assert "timed out after 1s" in message
    assert "DEEPSEEK_TIMEOUT" in message


def test_chat_completion_disables_timeout_by_default(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    chat_completion([{"role": "user", "content": "hello"}], DeepSeekConfig(api_key="sk-test"))

    assert captured["timeout"] is None


def test_vision_config_defaults_to_glm_flash(monkeypatch) -> None:
    monkeypatch.setenv("GLM_VISION_API_KEY", "glm-test")
    monkeypatch.delenv("GLM_VISION_MODEL", raising=False)
    monkeypatch.delenv("GLM_VISION_BASE_URL", raising=False)
    monkeypatch.setattr("diaevo.deepseek_chat.load_env", lambda *args, **kwargs: {})

    config = vision_config_from_env()

    assert config.api_key == "glm-test"
    assert config.model == "glm-4.6v-flash"
    assert config.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert config.reasoning_effort == ""
    assert config.thinking == ""


def test_multimodal_user_message_uses_image_url_part(tmp_path) -> None:
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    message = multimodal_user_message("描述图片", [image])

    assert message["role"] == "user"
    content = message["content"]
    assert content[0] == {"type": "text", "text": "描述图片"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert image_file_to_data_url(image).startswith("data:image/png;base64,")


def test_image_chat_completion_is_serialized(monkeypatch) -> None:
    active = 0
    max_active = 0
    lock = threading.Lock()

    class FakeResponse:
        def __enter__(self):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            return self

        def __exit__(self, exc_type, exc, tb):
            nonlocal active
            with lock:
                active -= 1
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout):
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        }
    ]

    threads = [
        threading.Thread(target=chat_completion, args=(messages, DeepSeekConfig(api_key="sk-test")))
        for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active == 1


def test_chat_test_parser_accepts_image_option() -> None:
    from diaevo.cli import build_parser

    args = build_parser().parse_args(["chat-test", "--image", "shot.png", "--prompt", "看图"])

    assert args.command == "chat-test"
    assert args.image == ["shot.png"]
    assert args.prompt == "看图"
