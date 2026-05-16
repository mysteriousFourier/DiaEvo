from __future__ import annotations

from ui.cli_style import ANSI_RE, GLYPHS, _plain_len
from ui.interactive_shell import (
    FLOW_INPUT_QUEUE,
    ApprovalDecision,
    ChatConfigState,
    FlowInputEvent,
    APPROVAL_PROPOSE,
    _denied_tool_result,
    _tool_reason,
)
from diaevo.tool_chat import RequestedToolCall
from ui.output_policy import render_assistant_text, sanitize_no_emoji, strip_markdown
from ui.progress import status
from ui.tool_render import render_tool_result


def test_sanitize_no_emoji_preserves_terminal_glyphs_and_chinese() -> None:
    text = "完成 ✅ ❯ 中文 ─ ok"

    assert sanitize_no_emoji(text) == "完成  ❯ 中文 ─ ok"


def test_strip_markdown_keeps_terminal_readable_content() -> None:
    text = """# 标题

- **步骤一**
- `python -m pytest`

```python
print("ok")
```
"""

    rendered = strip_markdown(text)

    assert "标题" in rendered
    assert "步骤一" in rendered
    assert "python -m pytest" in rendered
    assert 'print("ok")' in rendered
    assert "```" not in rendered
    assert "**" not in rendered


def test_render_assistant_text_plain_removes_markdown_and_emoji() -> None:
    rendered = render_assistant_text("## Done ✅\n- **Run tests**", mode="plain")

    assert rendered == "Done\nRun tests"


def test_tool_result_rendering_removes_emoji() -> None:
    rendered = render_tool_result({"status": "ok ✅", "tool": "read_file", "content": "hello 🚀"})

    assert "✅" not in rendered
    assert "🚀" not in rendered
    assert "hello" in rendered


def test_tool_result_uses_separator_line_without_box_frame() -> None:
    rendered = render_tool_result({"status": "ok", "tool": "read_file", "content": "hello"})
    lines = ANSI_RE.sub("", rendered).splitlines()

    assert lines[0] == "read_file ok"
    assert lines[-1] == GLYPHS["h"] * _plain_len(lines[-1])
    assert _plain_len(lines[-1]) >= 72
    assert not any(char in rendered for char in "╭╮╰╯│")


def test_tool_denial_can_include_proposed_alternative() -> None:
    call = type("Call", (), {"name": "run_shell"})()

    result = _denied_tool_result(call, ApprovalDecision(APPROVAL_PROPOSE, "use read_file first"))

    assert result["status"] == "denied"
    assert result["feedback"] == "use read_file first"
    assert "proposed a different approach" in result["message"]


def test_tool_reason_explains_why_tool_is_used() -> None:
    reason = _tool_reason(RequestedToolCall(id="call", name="read_file", args={"path": "README.md"}))

    assert "读取相关文件内容" in reason
    assert "README.md" in reason


def test_chat_config_state_tracks_session_tool_approvals() -> None:
    state = ChatConfigState()

    state.approve_tool_for_session("write_file")

    assert state.is_tool_approved_for_session("write_file")
    assert not state.is_tool_approved_for_session("run_shell")


def test_tool_loop_continues_until_model_returns_text(monkeypatch) -> None:
    from ui import interactive_shell

    calls = []

    def fake_chat_completion(messages, config, *, tools=None, tool_choice=None):
        calls.append({"tools": tools, "message_count": len(messages)})
        if len(calls) == 3:
            return {"choices": [{"message": {"content": "done from existing tool results"}}]}
        return {
            "id": "turn",
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "list_files", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ],
        }

    monkeypatch.setattr(interactive_shell, "chat_completion", fake_chat_completion)
    monkeypatch.setattr(
        interactive_shell,
        "execute_tool",
        lambda name, args, **kwargs: {"status": "ok", "tool": name, "entries": []},
    )

    state = ChatConfigState()
    state.value = object()
    answer = interactive_shell._chat_turn_with_tools([{"role": "user", "content": "list"}], state)

    assert answer == "done from existing tool results"
    assert len(calls) == 3
    assert calls[-1]["tools"] is not None


def test_tool_loop_sends_queued_input_after_tool_finishes(monkeypatch) -> None:
    from ui import interactive_shell

    while not FLOW_INPUT_QUEUE.empty():
        FLOW_INPUT_QUEUE.get_nowait()
    calls = []

    def fake_chat_completion(messages, config, *, tools=None, tool_choice=None):
        calls.append([dict(item) for item in messages])
        if len(calls) == 2:
            return {"choices": [{"message": {"content": "updated"}}]}
        return {
            "id": "turn",
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "list_files", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ],
        }

    def fake_execute_tool(name, args, **kwargs):
        FLOW_INPUT_QUEUE.put(FlowInputEvent("下一步先读 README", interrupt=False))
        return {"status": "ok", "tool": name, "entries": []}

    monkeypatch.setattr(interactive_shell, "chat_completion", fake_chat_completion)
    monkeypatch.setattr(interactive_shell, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(interactive_shell, "_start_flow_input_listener", lambda: False)
    monkeypatch.setattr(interactive_shell, "_stop_flow_input_listener", lambda enabled: None)

    state = ChatConfigState()
    state.value = object()
    answer = interactive_shell._chat_turn_with_tools([{"role": "user", "content": "list"}], state)

    assert answer == "updated"
    assert calls[1][-1] == {"role": "user", "content": "下一步先读 README"}


def test_talk_command_does_not_append_to_main_history(monkeypatch) -> None:
    from ui import interactive_shell

    captured = {}

    def fake_chat_completion(messages, config, *, tools=None, tool_choice=None):
        captured["messages"] = messages
        captured["tools"] = tools
        return {"choices": [{"message": {"content": "side answer"}}]}

    monkeypatch.setattr(interactive_shell, "chat_completion", fake_chat_completion)

    state = ChatConfigState()
    state.value = object()
    answer = interactive_shell._talk_once("quick question", state)

    assert answer == "side answer"
    assert captured["tools"] is None
    assert captured["messages"][-1] == {"role": "user", "content": "quick question"}


def test_image_command_appends_vision_result_to_history(monkeypatch) -> None:
    from ui import interactive_shell

    monkeypatch.setattr(interactive_shell, "_image_once", lambda path, prompt, state: f"看到了 {path}: {prompt}")
    monkeypatch.setattr(interactive_shell, "_print_image_answer", lambda answer: None)

    state = ChatConfigState()
    messages: list[dict[str, object]] = []

    keep_running = interactive_shell._dispatch_command('/image "shot.png" 检查页面问题', state, messages=messages)

    assert keep_running is True
    assert messages == [
        {"role": "user", "content": "[图片理解] 图片：shot.png\n问题：检查页面问题"},
        {"role": "assistant", "content": "[图片理解结果]\n看到了 shot.png: 检查页面问题"},
    ]


def test_vision_model_command_resets_config(monkeypatch) -> None:
    from ui import interactive_shell

    captured = {}

    def fake_set_env_command(key, value, chat_state, *, prompt, secret=False):
        captured["key"] = key
        captured["value"] = value
        chat_state.vision_value = "old"
        chat_state.reset()

    monkeypatch.setattr(interactive_shell, "_set_env_command", fake_set_env_command)

    state = ChatConfigState()
    state.vision_value = object()

    assert interactive_shell._dispatch_command("/vision-model glm-4.6v-flash", state)
    assert captured == {"key": "GLM_VISION_MODEL", "value": "glm-4.6v-flash"}
    assert state.vision_value is None


def test_status_is_silent_when_stderr_is_not_tty(capsys) -> None:
    with status("正在请求模型 🚀"):
        pass

    captured = capsys.readouterr()
    assert captured.err == ""
