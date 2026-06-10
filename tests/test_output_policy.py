from __future__ import annotations

import time

from ui import prompt_bar
from ui.cli_style import ANSI_RE
from ui.interactive_shell import (
    FLOW_INPUT_QUEUE,
    ApprovalDecision,
    ChatConfigState,
    FlowInputEvent,
    APPROVAL_PROPOSE,
    _denied_tool_result,
    _tool_reason,
)
from ui.action_report import build_turn_report
from ui.flow_input import FlowInputController
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


def test_tool_result_uses_compact_lifecycle_header_without_frame() -> None:
    rendered = render_tool_result({"status": "ok", "tool": "read_file", "content": "hello"})
    lines = ANSI_RE.sub("", rendered).splitlines()

    assert lines[0] == "工具  read_file  完成"
    assert "hello" in rendered
    assert not any(line.startswith("─" * 8) for line in lines)
    assert not any(char in rendered for char in "╭╮╰╯│")


def test_tool_denial_can_include_proposed_alternative() -> None:
    call = type("Call", (), {"name": "run_shell"})()

    result = _denied_tool_result(call, ApprovalDecision(APPROVAL_PROPOSE, "use read_file first"))

    assert result["status"] == "denied"
    assert result["feedback"] == "use read_file first"
    assert "换方案" in result["message"]


def test_tool_reason_explains_why_tool_is_used() -> None:
    reason = _tool_reason(RequestedToolCall(id="call", name="read_file", args={"path": "README.md"}))

    assert "读取相关文件内容" in reason
    assert "README.md" in reason


def test_tool_reason_omits_empty_path() -> None:
    reason = _tool_reason(RequestedToolCall(id="call", name="write_file", args={"path": ""}))

    assert "写入文件" in reason
    assert "path=" not in reason


def test_web_fetch_reason_shows_host_instead_of_long_url() -> None:
    reason = _tool_reason(
        RequestedToolCall(
            id="call",
            name="web_fetch",
            args={"url": "https://arxiv.org/search/?query=%22long+encoded+query%22&searchtype=all&start=0"},
        )
    )

    assert "来源 arxiv.org" in reason
    assert "query=" not in reason


def test_turn_report_renders_natural_workflow_status() -> None:
    messages = [{"role": "user", "content": "重构终端交互"}]

    rendered = build_turn_report(messages, 0, queued_inputs=2, tools="list_files, read_file").render()

    assert rendered.startswith("思考  ")
    assert "report>" not in rendered
    assert "重构终端交互" not in rendered
    assert "先判断是否需要工具" in rendered
    assert "目标：" not in rendered
    assert "文件：" not in rendered
    assert "工具：" not in rendered
    assert "另有 2 条输入排队" in rendered


def test_flow_input_enter_queues_interrupting_next_input() -> None:
    controller = FlowInputController()
    controller.draft = "继续检查 README"

    controller._queue_enter()
    events = controller.drain()

    assert events == [FlowInputEvent("继续检查 README", interrupt=True)]
    assert controller.draft == ""


def test_flow_input_escape_interrupt_takes_over_typed_input() -> None:
    controller = FlowInputController()
    controller.draft = "新的任务"

    controller._queue_escape_interrupt()
    events = controller.drain()

    assert events == [FlowInputEvent("新的任务", interrupt=True, hard_interrupt=True)]
    assert controller.force_terminate_event.is_set()
    assert controller.interrupt_event.is_set()


def test_flow_input_prompt_can_stay_visible(monkeypatch) -> None:
    controller = FlowInputController()
    writes = []

    monkeypatch.setattr("ui.flow_input.msvcrt", object())
    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("ui.flow_input.sys.stdout.write", lambda text: writes.append(text))
    monkeypatch.setattr("ui.flow_input.sys.stdout.flush", lambda: None)
    monkeypatch.setattr("ui.prompt_bar._term_width", lambda: 80)

    controller.show_prompt()
    controller.show_prompt()
    controller.show_prompt(force=True)

    rendered = "".join(writes)
    assert rendered.count("❯ ") == 2
    assert "Enter 发送" in rendered
    assert "Tab 补全" in rendered
    assert not any("next" in item for item in writes)


def test_tool_result_status_labels_are_chinese() -> None:
    rendered = ANSI_RE.sub("", render_tool_result({"status": "requires_approval", "tool": "web_search"}))

    assert rendered.splitlines()[0] == "工具  web_search  待确认"
    assert "requires_approval" not in rendered


def test_flow_prompt_only_renders_while_listener_is_active(monkeypatch) -> None:
    from ui import interactive_shell

    calls = []
    was_active = interactive_shell.FLOW_INPUT_ACTIVE.is_set()
    monkeypatch.setattr(interactive_shell.FLOW_INPUT, "show_prompt", lambda *args, **kwargs: calls.append((args, kwargs)))

    try:
        interactive_shell.FLOW_INPUT_ACTIVE.clear()
        interactive_shell._show_flow_prompt(force=True)
        assert calls == []

        interactive_shell.FLOW_INPUT_ACTIVE.set()
        interactive_shell._show_flow_prompt(force=True)
        assert len(calls) == 1
    finally:
        if was_active:
            interactive_shell.FLOW_INPUT_ACTIVE.set()
        else:
            interactive_shell.FLOW_INPUT_ACTIVE.clear()


def test_flow_input_stop_erases_visible_prompt(monkeypatch) -> None:
    controller = FlowInputController()
    writes = []

    monkeypatch.setattr("ui.flow_input.msvcrt", object())
    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("ui.flow_input.sys.stdout.write", lambda text: writes.append(text))
    monkeypatch.setattr("ui.flow_input.sys.stdout.flush", lambda: None)
    monkeypatch.setattr("ui.prompt_bar._term_width", lambda: 80)

    controller.show_prompt()
    rendered_lines = controller._rendered_lines
    controller.stop(enabled=True)

    assert rendered_lines > 1
    assert "\r\033[2K" in writes
    assert not controller.prompt_visible.is_set()
    assert not controller.status_visible.is_set()
    assert controller._rendered_lines == 0


def test_flow_input_output_clears_prompt_and_status(monkeypatch) -> None:
    controller = FlowInputController()
    writes = []

    monkeypatch.setattr("ui.flow_input.msvcrt", object())
    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("ui.flow_input.sys.stdout.write", lambda text: writes.append(text))
    monkeypatch.setattr("ui.flow_input.sys.stdout.flush", lambda: None)
    monkeypatch.setattr("ui.prompt_bar._term_width", lambda: 80)

    controller.show_prompt()
    controller.update_status_line("正在请求模型")
    controller.begin_output()

    assert "正在请求模型" in "".join(writes)
    assert "\033[1A\r\033[2K" in writes
    assert not controller.prompt_visible.is_set()
    assert not controller.status_visible.is_set()


def test_flow_input_shows_same_command_menu_as_prompt_bar(monkeypatch) -> None:
    controller = FlowInputController()
    writes = []

    monkeypatch.setattr("ui.flow_input.msvcrt", object())
    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("ui.flow_input.sys.stdout.write", lambda text: writes.append(text))
    monkeypatch.setattr("ui.flow_input.sys.stdout.flush", lambda: None)
    monkeypatch.setattr("ui.prompt_bar._term_width", lambda: 80)

    controller.draft = "/m"
    controller.show_prompt()

    rendered = "".join(writes)
    assert "/model" in rendered
    assert "/mine" not in rendered
    assert "Enter 发送" in rendered


def test_flow_input_enter_selects_current_command_menu_item() -> None:
    controller = FlowInputController()
    controller.draft = "/"
    controller.selected_index = len(prompt_bar.COMMANDS) - 1

    controller._queue_enter()
    events = controller.drain()

    assert events == [FlowInputEvent("/exit", interrupt=True)]


def test_flow_input_command_enter_clears_prompt_until_command_output(monkeypatch) -> None:
    controller = FlowInputController()
    writes = []

    monkeypatch.setattr("ui.flow_input.msvcrt", object())
    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("ui.flow_input.sys.stdout.write", lambda text: writes.append(text))
    monkeypatch.setattr("ui.flow_input.sys.stdout.flush", lambda: None)
    monkeypatch.setattr("ui.prompt_bar._term_width", lambda: 80)

    controller.show_prompt()
    controller.draft = "/learn"
    controller.cursor_index = len(controller.draft)
    controller._queue_enter()
    events = controller.drain()

    assert events == [FlowInputEvent("/learn", interrupt=True)]
    assert not controller.prompt_visible.is_set()
    assert controller._rendered_lines == 0
    assert "".join(writes).count("❯ ") == 1


def test_flow_input_talk_enter_keeps_prompt_editable(monkeypatch) -> None:
    controller = FlowInputController()
    writes = []

    monkeypatch.setattr("ui.flow_input.msvcrt", object())
    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("ui.flow_input.sys.stdout.write", lambda text: writes.append(text))
    monkeypatch.setattr("ui.flow_input.sys.stdout.flush", lambda: None)
    monkeypatch.setattr("ui.prompt_bar._term_width", lambda: 80)

    controller.show_prompt()
    controller.draft = "/talk 解释当前状态"
    controller._queue_enter()
    events = controller.drain()

    assert events == [FlowInputEvent("解释当前状态", interrupt=False, talk=True)]
    assert controller.draft == ""
    assert controller.prompt_visible.is_set()
    assert controller._rendered_lines > 0
    assert "".join(writes).count("❯ ") >= 2


def test_qq_talk_external_text_does_not_request_interruption() -> None:
    controller = FlowInputController()

    event = controller.queue_external_text("/talk 当前进度", source="QQ")

    assert event == FlowInputEvent("当前进度", interrupt=False, talk=True)
    assert event.reply_to_user_id == ""
    assert controller.force_terminate_event.is_set() is False
    assert controller.interrupt_event.is_set() is False


def test_qq_slash_command_queues_without_interrupting_main_flow() -> None:
    from ui import interactive_shell

    while not interactive_shell.QQ_COMMAND_QUEUE.empty():
        interactive_shell.QQ_COMMAND_QUEUE.get_nowait()
    while not FLOW_INPUT_QUEUE.empty():
        FLOW_INPUT_QUEUE.get_nowait()
    interactive_shell.FLOW_INTERRUPT_EVENT.clear()
    interactive_shell.FLOW_FORCE_TERMINATE_EVENT.clear()

    interactive_shell._enqueue_qq_text("/help")

    assert interactive_shell.QQ_COMMAND_QUEUE.get_nowait() == "/help"
    assert FLOW_INPUT_QUEUE.empty()
    assert interactive_shell.FLOW_INTERRUPT_EVENT.is_set() is False
    assert interactive_shell.FLOW_FORCE_TERMINATE_EVENT.is_set() is False


def test_qq_approval_command_still_uses_flow_queue() -> None:
    from ui import interactive_shell

    while not interactive_shell.QQ_COMMAND_QUEUE.empty():
        interactive_shell.QQ_COMMAND_QUEUE.get_nowait()
    while not FLOW_INPUT_QUEUE.empty():
        FLOW_INPUT_QUEUE.get_nowait()

    interactive_shell._enqueue_qq_text("/approve")

    assert interactive_shell.QQ_COMMAND_QUEUE.empty()
    event = FLOW_INPUT_QUEUE.get_nowait()
    assert event.text == "/approve"
    assert event.interrupt is True
    interactive_shell.FLOW_INTERRUPT_EVENT.clear()
    interactive_shell.FLOW_FORCE_TERMINATE_EVENT.clear()


def test_qq_talk_keeps_reply_user_on_flow_event() -> None:
    from ui import interactive_shell

    while not FLOW_INPUT_QUEUE.empty():
        FLOW_INPUT_QUEUE.get_nowait()

    interactive_shell._enqueue_qq_text("/talk 当前进度", "10001")

    event = FLOW_INPUT_QUEUE.get_nowait()
    assert event.talk is True
    assert event.text == "当前进度"
    assert event.reply_to_user_id == "10001"


def test_flow_input_tab_completes_current_command_menu_item(monkeypatch) -> None:
    controller = FlowInputController()
    writes = []

    monkeypatch.setattr("ui.flow_input.sys.stdout.write", lambda text: writes.append(text))
    monkeypatch.setattr("ui.flow_input.sys.stdout.flush", lambda: None)
    monkeypatch.setattr("ui.prompt_bar._term_width", lambda: 80)

    controller.draft = "/"
    controller.selected_index = len(prompt_bar.COMMANDS) - 1
    controller._complete_selected_command()

    assert controller.draft == "/exit "
    assert controller.selected_index == 0


def test_flow_input_can_drain_only_talk_events() -> None:
    controller = FlowInputController()
    started = []
    controller.queue.put(FlowInputEvent("普通输入", interrupt=True))
    controller.queue.put(FlowInputEvent("旁路问题", talk=True))

    handled = controller.handle_talk_queued(lambda event: started.append(event.text))

    assert handled == 1
    assert started == ["旁路问题"]
    assert controller.drain() == [FlowInputEvent("普通输入", interrupt=True)]


def test_flow_status_animates_status_line_without_touching_draft(monkeypatch, capsys) -> None:
    from ui import interactive_shell

    updates = []
    monkeypatch.setattr(interactive_shell, "_show_flow_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(interactive_shell.FLOW_INPUT, "update_status_line", lambda text: updates.append(text))
    monkeypatch.setattr(interactive_shell.FLOW_INPUT, "clear_status_line", lambda: updates.append(""))

    with interactive_shell._flow_status("正在请求模型"):
        print("inside")
        interactive_shell.FLOW_INPUT.draft = "abc"

    captured = capsys.readouterr()
    assert "inside" in captured.out
    assert any("正在请求模型" in item for item in updates)
    assert any("Working (0s • esc to interrupt)" in item for item in updates)
    assert updates[-1] == ""
    assert interactive_shell.FLOW_INPUT.draft == "abc"
    assert captured.err == ""


def test_flow_status_renderer_keeps_elapsed_after_redraw(monkeypatch) -> None:
    from ui import interactive_shell

    now = {"value": 100.0}
    monkeypatch.setattr(interactive_shell.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(interactive_shell, "_show_flow_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(interactive_shell.FLOW_INPUT, "update_status_line", lambda text: None)

    with interactive_shell._flow_status("正在请求模型"):
        renderer = interactive_shell.FLOW_INPUT._status_renderer
        assert renderer is not None
        assert "Working (0s • esc to interrupt)" in renderer()

        now["value"] = 165.0

        assert "Working (1m 05s • esc to interrupt)" in renderer()

    assert interactive_shell.FLOW_INPUT._status_renderer is None


def test_flow_status_renders_bottom_prompt_when_raw_input_disabled(monkeypatch) -> None:
    from ui import interactive_shell

    writes = []
    monkeypatch.delenv("DIAEVO_FLOW_INPUT", raising=False)
    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("ui.flow_input.sys.stdout.write", lambda text: writes.append(text))
    monkeypatch.setattr("ui.flow_input.sys.stdout.flush", lambda: None)
    monkeypatch.setattr("ui.prompt_bar._term_width", lambda: 80)

    with interactive_shell._flow_input_session():
        assert interactive_shell.FLOW_INPUT_ACTIVE.is_set()
        interactive_shell._show_flow_prompt(force=True)
        with interactive_shell._flow_status("正在请求模型"):
            pass

    rendered = ANSI_RE.sub("", "".join(writes))
    assert "❯ " in rendered
    assert "Working (0s • esc to interrupt)" in rendered
    assert "正在请求模型" in rendered
    assert not interactive_shell.FLOW_INPUT_ACTIVE.is_set()


def test_flow_input_listener_uses_toolkit_by_default(monkeypatch) -> None:
    from ui import interactive_shell

    calls = []
    monkeypatch.delenv("DIAEVO_FLOW_INPUT", raising=False)
    monkeypatch.setattr(
        interactive_shell.FLOW_INPUT,
        "start",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    assert interactive_shell._start_flow_input_listener() is True
    assert calls == [{"listen": False, "toolkit": True}]


def test_flow_status_elapsed_format_matches_codex_style() -> None:
    from ui import interactive_shell

    assert interactive_shell._fmt_elapsed_compact(0) == "0s"
    assert interactive_shell._fmt_elapsed_compact(59) == "59s"
    assert interactive_shell._fmt_elapsed_compact(60) == "1m 00s"
    assert interactive_shell._fmt_elapsed_compact(61) == "1m 01s"
    assert interactive_shell._fmt_elapsed_compact(3600) == "1h 00m 00s"
    assert interactive_shell._fmt_elapsed_compact(3661) == "1h 01m 01s"


def test_read_next_command_uses_plain_prompt_by_default(monkeypatch) -> None:
    from ui import interactive_shell

    monkeypatch.delenv("DIAEVO_FLOW_INPUT", raising=False)
    monkeypatch.setattr(
        interactive_shell,
        "_start_flow_input_listener",
        lambda: (_ for _ in ()).throw(AssertionError("flow listener should be opt-in for idle input")),
    )
    monkeypatch.setattr(interactive_shell, "read_prompt", lambda: "hello")

    assert interactive_shell._read_next_command(ChatConfigState()) == "hello"


def test_read_next_command_consumes_qq_flow_queue_when_raw_disabled(monkeypatch) -> None:
    from ui import interactive_shell

    monkeypatch.delenv("DIAEVO_FLOW_INPUT", raising=False)
    while not FLOW_INPUT_QUEUE.empty():
        FLOW_INPUT_QUEUE.get_nowait()
    FLOW_INPUT_QUEUE.put(FlowInputEvent("远程回复", interrupt=True, source="QQ"))
    monkeypatch.setattr(
        interactive_shell,
        "read_prompt",
        lambda: (_ for _ in ()).throw(AssertionError("queued QQ input should win over local prompt")),
    )

    assert interactive_shell._read_next_command(ChatConfigState()) == "远程回复"


def test_transient_inputs_use_plain_input_by_default(monkeypatch) -> None:
    from ui import interactive_shell

    monkeypatch.delenv("DIAEVO_FLOW_INPUT", raising=False)
    monkeypatch.setattr(interactive_shell, "_qq_send", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        interactive_shell.sys,
        "stdin",
        type("FakeStdin", (), {"isatty": lambda self: True})(),
    )
    monkeypatch.setattr(interactive_shell, "msvcrt", object())
    answers = iter(["2", "反馈"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    assert interactive_shell._read_transient_choice("选择：", valid_chars={"1", "2"}) == "2"
    assert interactive_shell._read_transient_text("换方案：") == "反馈"


def test_qq_approval_reply_is_consumed_by_transient_choice_when_raw_disabled(monkeypatch) -> None:
    from ui import interactive_shell

    monkeypatch.delenv("DIAEVO_FLOW_INPUT", raising=False)
    monkeypatch.setattr(interactive_shell, "_qq_send", lambda *args, **kwargs: None)
    monkeypatch.setattr(interactive_shell, "QQ_INTERACTIVE_BRIDGE", object())
    monkeypatch.setattr(
        interactive_shell.sys,
        "stdin",
        type("FakeStdin", (), {"isatty": lambda self: True})(),
    )
    monkeypatch.setattr(
        interactive_shell,
        "msvcrt",
        type("FakeMsvcrt", (), {"kbhit": lambda self: False})(),
    )
    while not FLOW_INPUT_QUEUE.empty():
        FLOW_INPUT_QUEUE.get_nowait()
    FLOW_INPUT_QUEUE.put(FlowInputEvent("/approve", interrupt=True, source="QQ"))

    assert interactive_shell._read_transient_choice("选择：", valid_chars={"1", "2", "3"}) == "1"


def test_qq_text_reply_is_consumed_by_transient_text_when_raw_disabled(monkeypatch) -> None:
    from ui import interactive_shell

    monkeypatch.delenv("DIAEVO_FLOW_INPUT", raising=False)
    monkeypatch.setattr(interactive_shell, "_qq_send", lambda *args, **kwargs: None)
    monkeypatch.setattr(interactive_shell, "QQ_INTERACTIVE_BRIDGE", object())
    monkeypatch.setattr(
        interactive_shell.sys,
        "stdin",
        type("FakeStdin", (), {"isatty": lambda self: True})(),
    )
    monkeypatch.setattr(
        interactive_shell,
        "msvcrt",
        type("FakeMsvcrt", (), {"kbhit": lambda self: False})(),
    )
    while not FLOW_INPUT_QUEUE.empty():
        FLOW_INPUT_QUEUE.get_nowait()
    FLOW_INPUT_QUEUE.put(FlowInputEvent("改成只读方案", interrupt=True, source="QQ"))

    assert interactive_shell._read_transient_text("换方案：") == "改成只读方案"


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


def test_model_request_stream_updates_status_line_without_printing_progress(monkeypatch) -> None:
    from ui import interactive_shell
    from diaevo.deepseek_chat import DeepSeekConfig

    status_lines: list[str] = []
    printed_status: list[str] = []

    def fake_chat_completion_stream(messages, config, *, tools=None, tool_choice=None, on_text=None, on_delta=None):
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "list_files"}}]}}]}
        if on_delta is not None:
            on_delta(event)
        return {"choices": [{"message": {"content": "done"}}]}

    monkeypatch.setattr(interactive_shell, "chat_completion_stream", fake_chat_completion_stream)
    monkeypatch.setattr(interactive_shell.FLOW_INPUT, "update_status_line", lambda text: status_lines.append(text))
    monkeypatch.setattr(interactive_shell.FLOW_INPUT, "set_status_line_renderer", lambda renderer: None)
    monkeypatch.setattr(interactive_shell.FLOW_INPUT, "clear_status_line", lambda: status_lines.append(""))
    monkeypatch.setattr(interactive_shell, "_print_status_flow", lambda text, **kwargs: printed_status.append(text))

    state = ChatConfigState()
    state.value = DeepSeekConfig(api_key="sk-test")
    response = interactive_shell._chat_completion_interruptible(
        [{"role": "user", "content": "long task"}],
        state,
        tools=[{"type": "function"}],
        round_index=2,
    )

    assert response["choices"][0]["message"]["content"] == "done"
    assert printed_status == []
    assert any("正在请求模型" in item for item in status_lines)
    assert any("第 3 轮" in item and "tool_delta=1" in item for item in status_lines)


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


def test_tool_loop_completes_pending_tool_messages_before_user_interrupt(monkeypatch) -> None:
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
                            },
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                            },
                        ],
                    }
                }
            ],
        }

    def fake_execute_tool(name, args, **kwargs):
        FLOW_INPUT_QUEUE.put(FlowInputEvent("先处理新需求", interrupt=True))
        return {"status": "ok", "tool": name, "entries": []}

    monkeypatch.setattr(interactive_shell, "chat_completion", fake_chat_completion)
    monkeypatch.setattr(interactive_shell, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(interactive_shell, "_start_flow_input_listener", lambda: False)
    monkeypatch.setattr(interactive_shell, "_stop_flow_input_listener", lambda enabled: None)

    state = ChatConfigState()
    state.value = object()
    answer = interactive_shell._chat_turn_with_tools([{"role": "user", "content": "list"}], state)

    second_messages = calls[1]
    assistant_index = next(index for index, item in enumerate(second_messages) if item.get("role") == "assistant")
    following = second_messages[assistant_index + 1 : assistant_index + 4]

    assert answer == "updated"
    assert [item.get("tool_call_id") for item in following[:2]] == ["call_1", "call_2"]
    assert '"status": "interrupted"' in following[1]["content"]
    assert following[2] == {"role": "user", "content": "先处理新需求"}


def test_flow_talk_starts_background_thread_without_main_history(monkeypatch) -> None:
    from ui import interactive_shell

    started = []

    monkeypatch.setattr(interactive_shell, "_start_talk_thread", lambda text, state, **kwargs: started.append(text))

    state = ChatConfigState()
    messages: list[dict[str, object]] = []
    FLOW_INPUT_QUEUE.put(FlowInputEvent("旁路问题", talk=True))

    interrupted = interactive_shell._handle_flow_inputs(messages, state)

    assert interrupted is False
    assert messages == []
    assert started == ["旁路问题"]


def test_flow_talk_pump_drains_talk_without_waiting_for_main_turn(monkeypatch) -> None:
    from ui import interactive_shell

    while not FLOW_INPUT_QUEUE.empty():
        FLOW_INPUT_QUEUE.get_nowait()
    started: list[str] = []

    def fake_start(event, state, messages):
        started.append(event.text)

    monkeypatch.setattr(interactive_shell, "_start_contextual_talk_thread", fake_start)

    state = ChatConfigState()
    messages = [{"role": "user", "content": "main"}]
    FLOW_INPUT_QUEUE.put(FlowInputEvent("旁路问题", talk=True))

    with interactive_shell._flow_talk_pump(messages, state):
        deadline = time.monotonic() + 1
        while not started and time.monotonic() < deadline:
            time.sleep(0.02)

    assert started == ["旁路问题"]
    assert FLOW_INPUT_QUEUE.empty()


def test_talk_command_starts_background_thread(monkeypatch) -> None:
    from ui import interactive_shell

    started = []

    monkeypatch.setattr(interactive_shell, "_start_talk_thread", lambda text, state, **kwargs: started.append(text))

    state = ChatConfigState()
    keep_running = interactive_shell._dispatch_command("/talk 快速解释一下", state, messages=[])

    assert keep_running is True
    assert started == ["快速解释一下"]


def test_interrupted_turn_keeps_existing_messages_when_continuing(monkeypatch) -> None:
    from ui import interactive_shell

    while not FLOW_INPUT_QUEUE.empty():
        FLOW_INPUT_QUEUE.get_nowait()
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old context"},
        {"role": "user", "content": "current task"},
        {"role": "assistant", "content": "partial context"},
    ]
    started_talk: list[tuple[str, str]] = []

    monkeypatch.setattr(
        interactive_shell,
        "_start_talk_thread",
        lambda text, state, **kwargs: started_talk.append((text, kwargs.get("context", ""))),
    )
    FLOW_INPUT_QUEUE.put(FlowInputEvent("旁路问题", talk=True))
    FLOW_INPUT_QUEUE.put(FlowInputEvent("new requirement", interrupt=True))

    appended = interactive_shell._append_interrupted_flow_inputs(
        messages,
        ChatConfigState(),
        talk_context="saved context",
    )

    assert appended is True
    assert started_talk == [("旁路问题", "saved context")]
    assert messages == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old context"},
        {"role": "user", "content": "current task"},
        {"role": "assistant", "content": "partial context"},
        {"role": "user", "content": "new requirement"},
    ]


def test_help_hides_internal_pipeline_commands() -> None:
    from ui import interactive_shell

    assert "/learn" in interactive_shell.HELP_TEXT
    assert "/debug" in interactive_shell.HELP_TEXT
    assert "/mine" not in interactive_shell.HELP_TEXT
    assert "/generate <cluster-id>" not in interactive_shell.HELP_TEXT
    assert "/debug mine" in interactive_shell.DEBUG_HELP_TEXT


def test_generate_without_cluster_points_to_learn(monkeypatch, capsys) -> None:
    from ui import interactive_shell

    calls = []
    monkeypatch.setattr(interactive_shell, "_run", lambda argv: calls.append(argv))

    keep_running = interactive_shell._dispatch_command("/generate", ChatConfigState(), messages=[])
    captured = capsys.readouterr()

    assert keep_running is True
    assert calls == []
    assert "/learn" in captured.out
    assert "C03" not in captured.out


def test_qq_command_starts_bridge(monkeypatch) -> None:
    from ui import interactive_shell

    calls = []
    monkeypatch.setattr(interactive_shell, "_start_qq_interactive_bridge", lambda: calls.append("start") or True)

    keep_running = interactive_shell._dispatch_command("/qq", ChatConfigState(), messages=[])

    assert keep_running is True
    assert calls == ["start"]


def test_qqquit_command_stops_bridge(monkeypatch, capsys) -> None:
    from ui import interactive_shell

    calls = []
    monkeypatch.setattr(interactive_shell, "_stop_qq_interactive_bridge", lambda: calls.append("stop") or True)

    keep_running = interactive_shell._dispatch_command("/qqquit", ChatConfigState(), messages=[])
    captured = capsys.readouterr()

    assert keep_running is True
    assert calls == ["stop"]
    assert "已退出" in captured.out


def test_main_does_not_autostart_qq(monkeypatch) -> None:
    from ui import interactive_shell

    starts = []
    stops = []
    reads = iter(["/exit"])
    monkeypatch.setattr(interactive_shell, "start_title_monitor", lambda: None)
    monkeypatch.setattr(interactive_shell, "stop_title_monitor", lambda: None)
    monkeypatch.setattr(interactive_shell, "maybe_show_trust_dialog", lambda: True)
    monkeypatch.setattr(interactive_shell, "render_plain", lambda: "")
    monkeypatch.setattr(interactive_shell, "_read_next_command", lambda state: next(reads))
    monkeypatch.setattr(interactive_shell, "_start_qq_interactive_bridge", lambda: starts.append("start") or True)
    monkeypatch.setattr(interactive_shell, "_stop_qq_interactive_bridge", lambda: stops.append("stop") or False)

    assert interactive_shell.main() == 0
    assert starts == []
    assert stops == ["stop"]


def test_skill_selection_appends_context_message(monkeypatch) -> None:
    from ui import interactive_shell

    messages: list[dict[str, object]] = []
    monkeypatch.setattr(
        interactive_shell,
        "load_skill_context",
        lambda name, task="": {
            "status": "ok",
            "name": name,
            "skill_file": f"skills/{name}/SKILL.md",
            "skill_text": "workflow body",
            "references": [],
        },
    )

    interactive_shell._append_skill_context_messages(
        messages,
        "做一个前端页面",
        [{"name": "web-design-engineer"}],
    )

    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert "[Loaded skill: web-design-engineer]" in str(messages[0]["content"])
    assert "workflow body" in str(messages[0]["content"])


def test_skill_command_appends_context_message(monkeypatch) -> None:
    from ui import interactive_shell

    messages: list[dict[str, object]] = []
    monkeypatch.setattr(
        interactive_shell,
        "load_skill_context",
        lambda name, task="": {
            "status": "ok",
            "name": name,
            "skill_file": f"skills/{name}/SKILL.md",
            "skill_text": "selected workflow",
            "references": [],
        },
    )

    keep_running = interactive_shell._dispatch_command("/skill web-design-engineer", ChatConfigState(), messages=messages)

    assert keep_running is True
    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert "[Loaded skill: web-design-engineer]" in str(messages[0]["content"])


def test_successful_tool_result_does_not_send_qq_completion_notice(monkeypatch, capsys) -> None:
    from ui import interactive_shell

    sent = []
    monkeypatch.setattr(interactive_shell, "_qq_send", lambda text: sent.append(text))
    monkeypatch.setattr(interactive_shell, "_show_flow_prompt", lambda *args, **kwargs: None)

    interactive_shell._print_tool_result({"status": "ok", "tool": "read_file", "content": "done"})

    assert sent == []
    captured = capsys.readouterr()
    assert "read_file" in captured.out


def test_run_shell_repeated_failure_marks_note(monkeypatch) -> None:
    from ui import interactive_shell

    calls = []

    def fake_execute_tool(name, args, **kwargs):
        calls.append((name, args))
        return {"status": "error", "tool": name, "command": args.get("command"), "stderr": "boom"}

    monkeypatch.setattr(interactive_shell, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(interactive_shell, "_approval_prompt", lambda tool_name: interactive_shell.ApprovalDecision(interactive_shell.APPROVAL_DENY))

    state = interactive_shell.ChatConfigState()
    call = RequestedToolCall(id="1", name="run_shell", args={"command": "pytest -q"})

    first = interactive_shell._execute_model_tool_call(call, turn_id="turn1", chat_state=state)
    second = interactive_shell._execute_model_tool_call(call, turn_id="turn2", chat_state=state)

    assert first["status"] == "error"
    assert "连续失败" in second["note"]
    assert len(calls) == 2


def test_web_search_result_renders_titles_links_and_source() -> None:
    rendered = render_tool_result(
        {
            "status": "ok",
            "tool": "web_search",
            "query": "DiaEvo",
            "backend": "duckduckgo_html",
            "results": [
                {
                    "title": "DiaEvo docs",
                    "url": "https://example.com/diaevo",
                    "snippet": "Project documentation",
                    "source": "duckduckgo_html",
                    "fetch_status": "not_fetched",
                }
            ],
        }
    )

    assert "查询  DiaEvo" in rendered
    assert "来源  duckduckgo_html" in rendered
    assert "DiaEvo docs" in rendered
    assert "example.com/diaevo" in rendered
    assert "Project documentation" in rendered
    assert "未抓取" in rendered


def test_web_fetch_result_renders_url_metadata_and_content() -> None:
    rendered = render_tool_result(
        {
            "status": "ok",
            "tool": "web_fetch",
            "url": "https://example.com/page",
            "final_url": "https://example.com/final",
            "status_code": 200,
            "content_type": "text/html",
            "truncated": False,
            "content": "Example",
        }
    )

    assert "来源  example.com/final" in rendered
    assert "https://example.com/page" not in rendered
    assert "HTTP 200" in rendered
    assert "text/html" in rendered
    assert "Example" in rendered


def test_arxiv_result_renders_title_links_and_summary() -> None:
    rendered = render_tool_result(
        {
            "status": "ok",
            "tool": "arxiv_search",
            "query": "retrieval",
            "source": "arxiv_api",
            "total_results": 1,
            "results": [
                {
                    "title": "Retrieval Paper",
                    "authors": ["Ada Lovelace", "Alan Turing"],
                    "published": "2024-01-02T00:00:00Z",
                    "primary_category": "cs.CL",
                    "summary": "A paper about retrieval.",
                    "abs_url": "http://arxiv.org/abs/2401.01234v1",
                    "pdf_url": "http://arxiv.org/pdf/2401.01234v1",
                }
            ],
        }
    )

    assert "Retrieval Paper" in rendered
    assert "Ada Lovelace, Alan Turing" in rendered
    assert "abs: http://arxiv.org/abs/2401.01234v1" in rendered
    assert "pdf: http://arxiv.org/pdf/2401.01234v1" in rendered
    assert "A paper about retrieval." in rendered


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


def test_talk_command_includes_main_context_without_appending(monkeypatch) -> None:
    from ui import interactive_shell

    captured = {}

    def fake_chat_completion(messages, config, *, tools=None, tool_choice=None):
        captured["messages"] = messages
        return {"choices": [{"message": {"content": "side answer"}}]}

    monkeypatch.setattr(interactive_shell, "chat_completion", fake_chat_completion)

    state = ChatConfigState()
    state.value = object()
    main_messages: list[dict[str, object]] = [{"role": "user", "content": "主线正在修 /talk 输入"}]
    answer = interactive_shell._talk_once(
        "当前在做什么",
        state,
        context=interactive_shell._talk_context_snapshot(main_messages),
    )

    assert answer == "side answer"
    assert "正在工作的人" in str(captured["messages"][0]["content"])
    assert "主线正在修 /talk 输入" in str(captured["messages"][1]["content"])
    assert main_messages == [{"role": "user", "content": "主线正在修 /talk 输入"}]


def test_talk_answer_is_sent_to_target_qq_user_when_printed(monkeypatch, capsys) -> None:
    from ui import interactive_shell

    sent = []
    monkeypatch.setattr(interactive_shell, "_qq_send_to_user", lambda user_id, text: sent.append((user_id, text)))
    monkeypatch.setattr(interactive_shell, "_show_flow_prompt", lambda *args, **kwargs: None)

    interactive_shell._print_talk_answer("旁路回答", reply_to_user_id="10001")

    assert sent == [("10001", "旁路回答")]
    captured = capsys.readouterr()
    assert "talk>" in captured.out
    assert "旁路回答" in captured.out


def test_local_talk_answer_does_not_send_to_qq(monkeypatch, capsys) -> None:
    from ui import interactive_shell

    sent = []
    monkeypatch.setattr(interactive_shell, "_qq_send_to_user", lambda user_id, text: sent.append((user_id, text)))
    monkeypatch.setattr(interactive_shell, "_show_flow_prompt", lambda *args, **kwargs: None)

    interactive_shell._print_talk_answer("本机旁路回答")

    assert sent == []
    captured = capsys.readouterr()
    assert "本机旁路回答" in captured.out


def test_search_result_message_uses_sidecar_filtered_context(monkeypatch) -> None:
    from ui import interactive_shell
    import json

    captured = {}

    def fake_chat_completion(messages, config, *, tools=None, tool_choice=None):
        captured["messages"] = messages
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "reason": "保留和当前任务相关的官方文档。",
                                "relevant_results": [
                                    {
                                        "title": "Relevant docs",
                                        "url": "https://example.com/relevant",
                                        "why": "匹配主线任务",
                                        "summary": "可用于实现当前修复。",
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(interactive_shell, "chat_completion", fake_chat_completion)

    state = ChatConfigState()
    state.value = object()
    call = RequestedToolCall(id="call_search", name="web_search", args={"query": "fix talk"})
    raw_result = {
        "status": "ok",
        "tool": "web_search",
        "query": "fix talk",
        "results": [
            {
                "title": "Relevant docs",
                "url": "https://example.com/relevant",
                "content_excerpt": "raw search excerpt that should stay out of the main context",
            },
            {
                "title": "Irrelevant page",
                "url": "https://example.com/other",
                "content_excerpt": "unrelated raw excerpt",
            },
        ],
    }
    main_messages: list[dict[str, object]] = [{"role": "user", "content": "主线正在修 talk 不中断"}]

    message = interactive_shell._tool_result_message_for_main_context(
        call,
        raw_result,
        messages=main_messages,
        chat_state=state,
    )
    content = json.loads(str(message["content"]))

    assert content["context_mode"] == "sidecar_filtered_search"
    assert "results" not in content
    assert "raw search excerpt" not in message["content"]
    assert content["relevant_results"] == [
        {
            "title": "Relevant docs",
            "url": "https://example.com/relevant",
            "why": "匹配主线任务",
            "summary": "可用于实现当前修复。",
        }
    ]
    assert "主线正在修 talk 不中断" in str(captured["messages"][1]["content"])


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
