from __future__ import annotations

import getpass
import asyncio
import json
import os
import queue
import shlex
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

from diaevo.cli import main as cli_main
from diaevo.deepseek_chat import (
    DeepSeekConfig,
    chat_completion,
    chat_completion_stream,
    config_from_env,
    extract_assistant_text,
    vision_chat_once,
    vision_config_from_env,
)
from diaevo.env import write_env_value
from diaevo.skill_context import load_skill_context, recommend_skill_contexts, render_skill_context_message
from diaevo.tool_layer import execute_tool, parse_tool_arg_pairs, parse_tool_args, tool_schemas
from diaevo.tool_layer import REPEAT_FAILURE_HINT
from diaevo.qq_bridge import (
    QQBridgeConfig,
    QQBridgeError,
    QQInteractiveBridge,
    config_from_env_vars as qq_config_from_env,
    prepare_onebot_service,
    run_interactive_bridge,
)
from diaevo.tool_chat import (
    RequestedToolCall,
    assistant_message_for_history,
    chat_tool_schemas,
    extract_assistant_message,
    requested_tool_calls,
    tool_result_message,
    tool_result_message_for_call,
)

from .action_report import active_skill_names, build_turn_report, short_value, tool_reason
from .cli_style import ANSI_RE, CYAN, DIM, GLYPHS, RESET, maybe_show_trust_dialog
from .flow_input import FlowInputController, FlowInputEvent, msvcrt
from .output_policy import print_assistant, print_status
from .prompt_bar import PLAN_MODE_PREFIX, _erase_lines, is_command_input, read_prompt
from .progress import status
from .terminal_home import render_plain
from .tool_render import render_tool_result
from .window_title import set_title_state, start_title_monitor, stop_title_monitor, title_activity

DEFAULT_RECOMMEND_TASK = "给当前项目生成测试修复 skill"
HOME_PROMPT_GAP = "\n\n"
QQ_COMPLETION_NOTICE = "已完成，请在电脑查看结果。"
PLAN_QUESTION_TYPE = "plan_question"

HELP_TEXT = """
常用操作：
  直接输入任务              让 DiaEvo 读代码、选工具并处理问题
  /skill [名称]             查看或选择已有 skill
  /learn                   从最近任务中总结一个候选 skill
  /status                  查看工作区、模型和最近学习结果
  /kg                      打开可编辑知识图谱工作台
  /qq                      启用 QQ 远程入口并允许登录
  /qqquit                  退出 QQ 远程入口
  /talk <问题>              不打断主任务，快速问一句
  /image <路径> <问题>      让视觉模型理解图片
  /home                    回到首页
  /help                    显示这份说明
  /exit                    退出

高级调试：
  /debug                   查看内部流水线命令
  /debug <命令> [参数]      运行内部命令，例如 /debug mine

其他输入会作为普通聊天消息发送给 DeepSeek。
""".strip()

DEBUG_HELP_TEXT = """
高级调试命令：
  /debug ingest [参数]       导入 data/sample_traces.jsonl
  /debug mine [参数]         运行挖掘流程
  /debug recommend <任务>    按任务推荐技能
  /debug generate <簇ID>     生成候选 SKILL.md；必须显式提供簇 ID
  /debug verify <路径>       验证候选技能
  /debug self-evolve [簇ID]  运行本地自进化
  /debug feedback [参数]     将工具事件回灌为轨迹
  /debug demo [参数]         运行完整演示

这些命令保留给排查和脚本兼容。日常直接描述任务，或运行 /learn。
""".strip()

APPROVAL_ALLOW_ONCE = "allow_once"
APPROVAL_ALLOW_SESSION = "allow_session"
APPROVAL_DENY = "deny"
APPROVAL_PROPOSE = "propose"
FLOW_INPUT = FlowInputController()
FLOW_INPUT_QUEUE = FLOW_INPUT.queue
FLOW_INPUT_ACTIVE = FLOW_INPUT.active
FLOW_INPUT_PAUSED = FLOW_INPUT.paused
FLOW_INTERRUPT_EVENT = FLOW_INPUT.interrupt_event
FLOW_FORCE_TERMINATE_EVENT = FLOW_INPUT.force_terminate_event
FLOW_PROMPT_VISIBLE = FLOW_INPUT.prompt_visible
QQ_COMMAND_QUEUE: "queue.Queue[str]" = queue.Queue()
_TALK_THREADS: set[threading.Thread] = set()
_TALK_THREADS_LOCK = threading.Lock()
QQ_INTERACTIVE_BRIDGE: QQInteractiveBridge | None = None
QQ_INTERACTIVE_THREAD: threading.Thread | None = None
QQ_INTERACTIVE_STOP_EVENT: threading.Event | None = None
SEARCH_CONTEXT_TOOLS = {"web_search", "arxiv_search"}
RAW_INPUT_ENV_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ApprovalDecision:
    action: str
    feedback: str = ""


@dataclass(frozen=True)
class TransientChoiceOption:
    value: str
    label: str
    aliases: tuple[str, ...] = ()


class ModelTurnInterrupted(RuntimeError):
    pass


class ChatConfigState:
    def __init__(self) -> None:
        self.value = None
        self.vision_value = None
        self.approved_tools: set[str] = set()
        self.last_failed_shell_command: str = ""

    def reset(self) -> None:
        self.value = None
        self.vision_value = None

    def approve_tool_for_session(self, tool_name: str) -> None:
        self.approved_tools.add(tool_name)

    def is_tool_approved_for_session(self, tool_name: str) -> bool:
        return tool_name in self.approved_tools


@dataclass
class KGAnswerMode:
    enabled: bool = False
    vector_backend: str = "dense"
    strict: bool = True
    max_paths: int = 5


def _ensure_chat_config(chat_state: ChatConfigState, *, max_tokens: int = 4096, no_thinking: bool = True):
    if chat_state.value is None:
        chat_state.value = config_from_env(max_tokens=max_tokens, no_thinking=no_thinking)
    return chat_state.value


def _ensure_vision_config(chat_state: ChatConfigState, *, max_tokens: int = 4096):
    if chat_state.vision_value is None:
        chat_state.vision_value = vision_config_from_env(max_tokens=max_tokens)
    return chat_state.vision_value


def _message_text_for_talk(message: dict[str, object]) -> str:
    role = str(message.get("role") or "message")
    content = message.get("content")
    if content is None:
        content = "[tool call or empty content]"
    text = " ".join(str(content).split())
    if len(text) > 600:
        text = text[:600].rstrip() + "... <truncated>"
    return f"{role}: {text}"


def _talk_context_snapshot(messages: list[dict[str, object]] | None, *, max_messages: int = 8) -> str:
    if not messages:
        return ""
    items = [_message_text_for_talk(item) for item in messages[-max_messages:] if isinstance(item, dict)]
    return "\n".join(item for item in items if item).strip()


def _talk_once(
    prompt: str,
    chat_state: ChatConfigState,
    *,
    show_status: bool = True,
    context: str = "",
) -> str:
    config = _ensure_chat_config(chat_state, max_tokens=2048, no_thinking=True)
    context_text = context.strip()
    messages = [
        {
            "role": "system",
            "content": (
                "你是 DiaEvo 的旁路问答助手。回答当前问题，不要调用工具，不要改变主会话计划；"
                "你可以使用随请求提供的主会话上下文快照，但不要把旁路回答写入主会话；"
                "回答方式要像用户在问一个正在工作的人：先说明主线正在做什么或最近进展，"
                "再回答问题；不要要求主任务停下来，也不要声称已经修改主线状态；"
                "如果问题需要的上下文仍缺失，直接说明缺少哪些信息。"
            ),
        },
    ]
    if context_text:
        messages.append({"role": "system", "content": f"主会话上下文快照：\n{context_text}"})
    messages.append({"role": "user", "content": prompt})
    if show_status:
        with title_activity("running"):
            with _flow_status("正在旁路提问"):
                response = chat_completion(messages, config)
    else:
        response = chat_completion(messages, config)
    return extract_assistant_text(response)


def _start_talk_thread(
    prompt: str,
    chat_state: ChatConfigState,
    *,
    context: str = "",
    reply_to_user_id: str = "",
) -> threading.Thread | None:
    prompt = prompt.strip()
    if not prompt:
        return None

    thread = threading.Thread(
        target=_talk_thread_worker,
        args=(prompt, chat_state, context, reply_to_user_id),
        daemon=True,
    )
    with _TALK_THREADS_LOCK:
        _TALK_THREADS.add(thread)
    thread.start()
    return thread


def _talk_thread_worker(
    prompt: str,
    chat_state: ChatConfigState,
    context: str = "",
    reply_to_user_id: str = "",
) -> None:
    try:
        answer = _talk_once(prompt, chat_state, show_status=False, context=context)
    except Exception as exc:
        answer = f"旁路提问失败：{exc}"
    try:
        _print_talk_answer(answer, reply_to_user_id=reply_to_user_id)
    finally:
        thread = threading.current_thread()
        with _TALK_THREADS_LOCK:
            _TALK_THREADS.discard(thread)


def _print_talk_answer(answer: str, *, reply_to_user_id: str = "") -> None:
    _begin_flow_output()
    print("\ntalk>")
    print_assistant(answer)
    if reply_to_user_id:
        _qq_send_to_user(reply_to_user_id, answer)
    _show_flow_prompt(force=True)


def _image_once(image_path: str, prompt: str, chat_state: ChatConfigState) -> str:
    config = _ensure_vision_config(chat_state, max_tokens=4096)
    system = (
        "你是 DiaEvo 的图像理解助手。请用中文分析用户提供的图片，只描述图片中可见证据；"
        "如果无法确定，明确说明不确定。不要调用工具，不要编造图片外的信息。"
    )
    with title_activity("running"):
        with _flow_status("正在理解图片"):
            answer, _response = vision_chat_once(prompt, [image_path], system, config)
    return answer


def _print_image_answer(answer: str) -> None:
    _begin_flow_output()
    print("\nimage>")
    print_assistant(answer)
    _show_flow_prompt(force=True)


def _print_tool_result(result: dict[str, object]) -> None:
    _begin_flow_output()
    rendered = render_tool_result(result)
    print(rendered)
    if _tool_result_should_send_to_qq(result):
        _qq_send(QQ_COMPLETION_NOTICE)
    _show_flow_prompt(force=True)


def _begin_flow_output() -> None:
    FLOW_INPUT.begin_output()


def _print_assistant_flow(text: str) -> None:
    _begin_flow_output()
    print_assistant(text)
    _qq_send(text)
    _show_flow_prompt(force=True)


def _print_status_flow(text: str, *, send_to_qq: bool = False) -> None:
    _begin_flow_output()
    print_status(text)
    if send_to_qq:
        _qq_send(text)
    _show_flow_prompt(force=True)


def _qq_send(text: str) -> None:
    bridge = QQ_INTERACTIVE_BRIDGE
    if bridge is None:
        return
    try:
        bridge.send_to_last_user(text)
    except Exception:
        return


def _qq_send_to_user(user_id: str, text: str) -> None:
    bridge = QQ_INTERACTIVE_BRIDGE
    if bridge is None or not user_id:
        return
    try:
        bridge.send_to_user(user_id, text)
    except Exception:
        return


def _tool_result_should_send_to_qq(result: dict[str, object]) -> bool:
    return False


def _enqueue_qq_text(text: str, user_id: str = "") -> None:
    clean = text.strip()
    lowered = clean.lower()
    if clean.startswith("/") and not lowered.startswith("/talk ") and lowered not in {"/approve", "/deny"}:
        QQ_COMMAND_QUEUE.put(clean)
        return
    if not _raw_flow_input_enabled():
        event = _qq_flow_event(text, user_id=user_id)
        FLOW_INPUT_QUEUE.put(event)
        if event.interrupt or event.hard_interrupt:
            FLOW_FORCE_TERMINATE_EVENT.set()
            FLOW_INTERRUPT_EVENT.set()
        return
    FLOW_INPUT.queue_external_text(text, source="QQ", reply_to_user_id=user_id)


def _start_qq_interactive_bridge() -> bool:
    global QQ_INTERACTIVE_BRIDGE, QQ_INTERACTIVE_THREAD, QQ_INTERACTIVE_STOP_EVENT
    if QQ_INTERACTIVE_THREAD is not None and QQ_INTERACTIVE_THREAD.is_alive():
        print(f"{DIM}QQ 远程入口已在运行。{RESET}")
        return True
    try:
        config = qq_config_from_env()
    except Exception as exc:
        print(f"{DIM}QQ 远程配置读取失败：{exc}{RESET}")
        return False
    if not config.enabled:
        print(f"{DIM}QQ 远程入口未启用：请在 .env 设置 DIAEVO_QQ_ENABLED=true。{RESET}")
        return False
    try:
        startup = prepare_onebot_service(config)
    except QQBridgeError as exc:
        print(f"{DIM}QQ 远程入口启动失败：{exc}{RESET}")
        return False
    startup_status = str(startup.get("status") or "")
    if startup_status == "started":
        print(f"{DIM}{startup.get('message')}{RESET}")
    elif startup_status in {"not_running", "missing_command", "exited", "timeout"}:
        print(f"{DIM}QQ 远程入口提示：{startup.get('message')}{RESET}")
        if startup_status in {"not_running", "missing_command", "exited"}:
            return False
    stop_event = threading.Event()
    QQ_INTERACTIVE_STOP_EVENT = stop_event
    QQ_INTERACTIVE_BRIDGE = QQInteractiveBridge(config, enqueue_text=_enqueue_qq_text)

    def worker() -> None:
        try:
            asyncio.run(run_interactive_bridge(config, QQ_INTERACTIVE_BRIDGE, stop_event=stop_event))
        except QQBridgeError as exc:
            _print_status_flow(f"QQ 远程入口启动失败：{exc}", send_to_qq=False)
        except Exception as exc:
            _print_status_flow(f"QQ 远程入口退出：{exc}", send_to_qq=False)

    QQ_INTERACTIVE_THREAD = threading.Thread(target=worker, name="diaevo-qq-bridge", daemon=True)
    QQ_INTERACTIVE_THREAD.start()
    print(f"{DIM}QQ 远程入口已启用：{', '.join(sorted(config.allowed_users))}{RESET}")
    return True


def _stop_qq_interactive_bridge() -> bool:
    global QQ_INTERACTIVE_BRIDGE, QQ_INTERACTIVE_THREAD, QQ_INTERACTIVE_STOP_EVENT
    running = QQ_INTERACTIVE_THREAD is not None and QQ_INTERACTIVE_THREAD.is_alive()
    if QQ_INTERACTIVE_STOP_EVENT is not None:
        QQ_INTERACTIVE_STOP_EVENT.set()
    if QQ_INTERACTIVE_THREAD is not None:
        QQ_INTERACTIVE_THREAD.join(timeout=2)
    QQ_INTERACTIVE_BRIDGE = None
    QQ_INTERACTIVE_THREAD = None
    QQ_INTERACTIVE_STOP_EVENT = None
    return running


def _event_to_command(event: FlowInputEvent, chat_state: ChatConfigState) -> str:
    if event.talk:
        if event.text:
            _start_talk_thread(event.text, chat_state, reply_to_user_id=event.reply_to_user_id)
        return ""
    if event.plan and event.text.strip().lower() == "/learn":
        return "/learn --plan"
    if event.plan and event.text.strip().lower().startswith("/learn "):
        text = event.text.strip()
        if "--plan" not in shlex.split(text, posix=False)[1:]:
            return f"{text} --plan"
    text = event.text.strip()
    if event.plan and text and not text.startswith("/"):
        return PLAN_MODE_PREFIX + text
    return text


def _qq_flow_event(text: str, *, user_id: str = "") -> FlowInputEvent:
    clean = text.strip()
    talk = clean.lower().startswith("/talk ")
    payload = clean[6:].strip() if talk else clean
    return FlowInputEvent(
        payload,
        interrupt=False if talk else True,
        talk=talk,
        source="QQ",
        reply_to_user_id=user_id,
    )


def _raw_flow_input_enabled() -> bool:
    return os.environ.get("DIAEVO_FLOW_INPUT", "").strip().lower() in RAW_INPUT_ENV_VALUES


def _next_queued_command(chat_state: ChatConfigState) -> str:
    try:
        command = QQ_COMMAND_QUEUE.get_nowait()
    except queue.Empty:
        command = ""
    if command:
        return command
    while True:
        try:
            event = FLOW_INPUT_QUEUE.get_nowait()
        except queue.Empty:
            return ""
        command = _event_to_command(event, chat_state)
        if command:
            return command


def _read_next_command(chat_state: ChatConfigState) -> str:
    if not _raw_flow_input_enabled():
        command = _next_queued_command(chat_state)
        if command:
            return command
        return read_prompt()

    listener_enabled = _start_flow_input_listener()
    if listener_enabled:
        _show_flow_prompt(force=True)
        while True:
            try:
                command = QQ_COMMAND_QUEUE.get_nowait()
            except queue.Empty:
                command = ""
            if command:
                _begin_flow_output()
                return command
            try:
                event = FLOW_INPUT_QUEUE.get(timeout=0.1)
            except queue.Empty:
                continue
            with FLOW_INPUT._render_lock:
                if FLOW_INPUT.queued_preview:
                    FLOW_INPUT.queued_preview = FLOW_INPUT.queued_preview[1:]
            command = _event_to_command(event, chat_state)
            if command:
                _begin_flow_output()
                return command
        # unreachable
    return read_prompt()


@contextmanager
def _flow_status(message: str) -> object:
    """动效刷新输入栏上一行，输入栏本身只由用户输入驱动。"""
    frames = "-\\|/"
    stopped = threading.Event()
    started_at = time.monotonic()

    def render(index: int) -> None:
        frame_index["value"] = index
        FLOW_INPUT.update_status_line(status_text())

    def status_text() -> str:
        elapsed = _fmt_elapsed_compact(int(time.monotonic() - started_at))
        return f"{frames[frame_index['value'] % len(frames)]} Working ({elapsed} • esc to interrupt) · {message}"

    def animate() -> None:
        index = 1
        while not stopped.is_set():
            render(index)
            index += 1
            time.sleep(0.12)

    frame_index = {"value": 0}
    _show_flow_prompt(force=True)
    FLOW_INPUT.set_status_line_renderer(status_text)
    render(0)
    thread = threading.Thread(target=animate, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=0.5)
        FLOW_INPUT.clear_status_line()


def _fmt_elapsed_compact(elapsed_secs: int) -> str:
    elapsed_secs = max(0, int(elapsed_secs))
    if elapsed_secs < 60:
        return f"{elapsed_secs}s"
    if elapsed_secs < 3600:
        minutes, seconds = divmod(elapsed_secs, 60)
        return f"{minutes}m {seconds:02}s"
    hours, remainder = divmod(elapsed_secs, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02}m {seconds:02}s"


def _start_flow_input_listener() -> bool:
    raw_enabled = _raw_flow_input_enabled()
    return FLOW_INPUT.start(listen=raw_enabled, toolkit=not raw_enabled)


def _stop_flow_input_listener(enabled: bool) -> None:
    FLOW_INPUT.stop(enabled)


@contextmanager
def _flow_input_session() -> object:
    raw_enabled = _raw_flow_input_enabled()
    with FLOW_INPUT.session(listen=raw_enabled, toolkit=not raw_enabled):
        yield


def _show_flow_prompt(label: str = "next", *, force: bool = False) -> None:
    if not FLOW_INPUT_ACTIVE.is_set():
        return
    shown_label = "" if label == "next" else label
    FLOW_INPUT.show_prompt(shown_label, force=force)


@contextmanager
def _pause_flow_input() -> object:
    with FLOW_INPUT.pause():
        yield


def _drain_flow_inputs() -> list[FlowInputEvent]:
    return FLOW_INPUT.drain()


def _append_interrupted_flow_inputs(
    messages: list[dict[str, object]],
    chat_state: ChatConfigState,
    *,
    talk_context: str,
) -> bool:
    appended = False
    for event in _drain_flow_inputs():
        if event.talk and event.text:
            _start_talk_thread(
                event.text,
                chat_state,
                context=talk_context,
                reply_to_user_id=event.reply_to_user_id,
            )
            continue
        if event.text:
            messages.append({"role": "user", "content": event.text})
            appended = True
    return appended


def _start_contextual_talk_thread(
    event: FlowInputEvent,
    chat_state: ChatConfigState,
    messages: list[dict[str, object]] | None,
) -> threading.Thread | None:
    context = _talk_context_snapshot(messages)
    if context:
        return _start_talk_thread(event.text, chat_state, context=context, reply_to_user_id=event.reply_to_user_id)
    return _start_talk_thread(event.text, chat_state, reply_to_user_id=event.reply_to_user_id)


def _handle_flow_inputs(messages: list[dict[str, object]], chat_state: ChatConfigState) -> bool:
    return FLOW_INPUT.handle_queued(messages, lambda event: _start_contextual_talk_thread(event, chat_state, messages))


@contextmanager
def _flow_talk_pump(messages: list[dict[str, object]], chat_state: ChatConfigState) -> object:
    stopped = threading.Event()

    def pump() -> None:
        while not stopped.wait(0.1):
            FLOW_INPUT.handle_talk_queued(lambda event: _start_contextual_talk_thread(event, chat_state, messages))

    thread = threading.Thread(target=pump, name="diaevo-talk-pump", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=0.5)
        FLOW_INPUT.handle_talk_queued(lambda event: _start_contextual_talk_thread(event, chat_state, messages))


def _interrupted_tool_result(call: RequestedToolCall) -> dict[str, object]:
    return {
        "status": "interrupted",
        "tool": call.name,
        "message": "Tool call skipped because the user supplied new input before it ran.",
    }


def _tool_result_message_for_main_context(
    call: RequestedToolCall,
    result: dict[str, object],
    *,
    messages: list[dict[str, object]],
    chat_state: ChatConfigState,
) -> dict[str, object]:
    context_result = _search_result_for_main_context(call, result, messages=messages, chat_state=chat_state)
    return tool_result_message(call.id, context_result, name=call.name, legacy=call.legacy)


def _search_result_for_main_context(
    call: RequestedToolCall,
    result: dict[str, object],
    *,
    messages: list[dict[str, object]],
    chat_state: ChatConfigState,
) -> dict[str, object]:
    if call.name not in SEARCH_CONTEXT_TOOLS or str(result.get("status") or "ok") != "ok":
        return result
    try:
        return _sidecar_filter_search_result(call.name, result, messages, chat_state)
    except Exception as exc:
        fallback = _local_search_context_summary(call.name, result)
        fallback["filter_error"] = str(exc)
        return fallback


def _sidecar_filter_search_result(
    tool_name: str,
    result: dict[str, object],
    messages: list[dict[str, object]],
    chat_state: ChatConfigState,
) -> dict[str, object]:
    config = _ensure_chat_config(chat_state, max_tokens=4096, no_thinking=True)
    context = _talk_context_snapshot(messages, max_messages=10) or "(no main conversation snapshot)"
    candidates = _search_candidates_for_filter(result)
    if not candidates:
        return _local_search_context_summary(tool_name, result)
    filter_messages = [
        {
            "role": "system",
            "content": (
                "你是搜索结果旁路筛选器。你不回答用户问题，只判断搜索结果中哪些对主会话当前任务有用。"
                "不要把原始搜索内容整体复述进上下文。只返回 JSON 对象，字段：reason 字符串，"
                "relevant_results 数组。每个 relevant_results 元素只包含 title、url、why、summary；"
                "summary 最多两句，保留和主任务直接相关的事实。无用结果返回空数组。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"主会话记忆快照：\n{context}\n\n"
                f"搜索工具：{tool_name}\n"
                f"查询：{result.get('query') or result.get('search_query') or ''}\n"
                f"候选结果 JSON：\n{json.dumps(candidates, ensure_ascii=False)}"
            ),
        },
    ]
    response = chat_completion(filter_messages, config)
    filtered = _parse_json_object(extract_assistant_text(response))
    relevant = filtered.get("relevant_results")
    if not isinstance(relevant, list):
        relevant = []
    return {
        "status": "ok",
        "tool": tool_name,
        "query": result.get("query") or result.get("search_query") or "",
        "context_mode": "sidecar_filtered_search",
        "filter_reason": str(filtered.get("reason") or "").strip(),
        "source_result_count": len(candidates),
        "relevant_results": [_clean_filtered_search_item(item) for item in relevant if isinstance(item, dict)][:6],
    }


def _search_candidates_for_filter(result: dict[str, object]) -> list[dict[str, object]]:
    raw_items = result.get("results")
    if not isinstance(raw_items, list):
        return []
    candidates: list[dict[str, object]] = []
    for index, item in enumerate(raw_items[:10], start=1):
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "index": index,
                "title": _short_context_text(item.get("title"), 180),
                "url": _short_context_text(item.get("url") or item.get("abs_url") or item.get("pdf_url"), 260),
                "snippet": _short_context_text(
                    item.get("content_excerpt") or item.get("snippet") or item.get("summary") or item.get("content"),
                    700,
                ),
                "authors": item.get("authors") if isinstance(item.get("authors"), list) else [],
                "published": _short_context_text(item.get("published") or item.get("updated"), 80),
                "category": _short_context_text(item.get("primary_category"), 80),
            }
        )
    return candidates


def _local_search_context_summary(tool_name: str, result: dict[str, object]) -> dict[str, object]:
    candidates = _search_candidates_for_filter(result)
    return {
        "status": "ok",
        "tool": tool_name,
        "query": result.get("query") or result.get("search_query") or "",
        "context_mode": "local_compacted_search",
        "filter_reason": "旁路筛选不可用，已仅保留标题、链接和短摘录。",
        "source_result_count": len(candidates),
        "relevant_results": [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "why": "候选搜索结果，需由主模型结合任务再判断。",
                "summary": item.get("snippet", ""),
            }
            for item in candidates[:5]
        ],
    }


def _parse_json_object(text: str) -> dict[str, object]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.strip("`").strip()
        if clean.lower().startswith("json"):
            clean = clean[4:].strip()
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(clean[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("sidecar search filter did not return a JSON object")
    return value


def _clean_filtered_search_item(item: dict[str, object]) -> dict[str, str]:
    return {
        "title": _short_context_text(item.get("title"), 180),
        "url": _short_context_text(item.get("url"), 260),
        "why": _short_context_text(item.get("why"), 240),
        "summary": _short_context_text(item.get("summary"), 700),
    }


def _short_context_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _handle_flow_inputs_before_pending_tool_calls(
    messages: list[dict[str, object]],
    chat_state: ChatConfigState,
    pending_calls: list[RequestedToolCall],
) -> bool:
    if not pending_calls:
        return _handle_flow_inputs(messages, chat_state)

    events = FLOW_INPUT.drain()
    if not events:
        return False

    user_events: list[FlowInputEvent] = []
    interrupted = False
    for event in events:
        if event.talk:
            if event.text:
                _start_contextual_talk_thread(event, chat_state, messages)
            continue
        if event.hard_interrupt and not event.text:
            interrupted = True
            continue
        if event.text:
            user_events.append(event)
            interrupted = True
        if event.interrupt:
            interrupted = True

    if not interrupted:
        return False

    for call in pending_calls:
        messages.append(tool_result_message_for_call(call, _interrupted_tool_result(call)))
    for event in user_events:
        messages.append({"role": "user", "content": event.text})
    return True


def _short_value(value: object, limit: int = 80) -> str:
    return short_value(value, limit)


def _tool_reason(call: RequestedToolCall) -> str:
    return tool_reason(call)


def _print_tool_reason(call: RequestedToolCall) -> None:
    _print_status_flow(f"行动  调用 {call.name}：{_tool_reason(call)}")


def _run(argv: list[str]) -> None:
    label = argv[0] if argv else "command"
    cli_argv = ["--plain", *argv] if argv and argv[0] not in {"--plain", "--json"} else argv
    output = ""
    error_output = ""
    with _flow_input_session():
        _show_flow_prompt()
        with title_activity("running"):
            with _flow_status(f"正在运行 {label}"):
                import contextlib
                import io

                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = cli_main(cli_argv)
                output = stdout.getvalue().strip()
                error_output = stderr.getvalue().strip()
    if output:
        _begin_flow_output()
        print(output)
        if code == 0:
            _qq_send(QQ_COMPLETION_NOTICE)
    if error_output:
        _begin_flow_output()
        print(error_output, file=sys.stderr)
    if code:
        _print_status_flow(f"命令退出，状态码：{code}")


def _chat_completion_interruptible(
    messages: list[dict[str, object]],
    chat_state: ChatConfigState,
    *,
    tools: list[dict[str, object]],
    round_index: int = 0,
):
    if not isinstance(chat_state.value, DeepSeekConfig):
        return chat_completion(messages, chat_state.value, tools=tools)

    started_at = time.monotonic()
    stats = {"events": 0, "text_chars": 0, "tool_deltas": 0}
    stopped = threading.Event()
    FLOW_FORCE_TERMINATE_EVENT.clear()

    def progress_text() -> str:
        elapsed = _fmt_elapsed_compact(int(time.monotonic() - started_at))
        detail = f"stream={stats['events']}"
        if stats["text_chars"]:
            detail += f"，chars={stats['text_chars']}"
        if stats["tool_deltas"]:
            detail += f"，tool_delta={stats['tool_deltas']}"
        return (
            f"~ 正在请求模型 ({elapsed} • esc to interrupt) · "
            f"第 {round_index + 1} 轮，messages={len(messages)}，tools={len(tools)}，{detail}"
        )

    def check_interrupt() -> None:
        FLOW_INPUT.handle_talk_queued(lambda event: _start_contextual_talk_thread(event, chat_state, messages))
        if FLOW_FORCE_TERMINATE_EVENT.is_set():
            FLOW_FORCE_TERMINATE_EVENT.clear()
            raise ModelTurnInterrupted("model request terminated")

    def update_status() -> None:
        FLOW_INPUT.update_status_line(progress_text())

    def refresh_status() -> None:
        while not stopped.wait(0.25):
            check_interrupt()
            update_status()

    def on_delta(event: dict[str, object]) -> None:
        stats["events"] += 1
        choices = event.get("choices") if isinstance(event, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            delta = choices[0].get("delta")
            if isinstance(delta, dict):
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    stats["tool_deltas"] += len(tool_calls)
        check_interrupt()
        update_status()

    def on_text(text: str) -> None:
        stats["text_chars"] += len(text)
        update_status()

    FLOW_INPUT.set_status_line_renderer(progress_text)
    update_status()
    thread = threading.Thread(target=refresh_status, name="diaevo-model-stream-status", daemon=True)
    thread.start()
    try:
        check_interrupt()
        response = chat_completion_stream(messages, chat_state.value, tools=tools, on_text=on_text, on_delta=on_delta)
        return response
    finally:
        stopped.set()
        thread.join(timeout=0.5)
        FLOW_INPUT.set_status_line_renderer(None)
        FLOW_INPUT.clear_status_line()


def _set_env_command(
    key: str,
    value: str,
    chat_state: ChatConfigState,
    *,
    prompt: str,
    secret: bool = False,
) -> None:
    value = value.strip()
    if not value:
        if secret:
            value = getpass.getpass(f"{prompt}: ")
        else:
            value = input(f"{prompt}: ").strip()
    if not value:
        print(f"usage: /{key.lower().replace('deepseek_', '').replace('_', '')} <value>")
        return
    write_env_value(key, value)
    chat_state.reset()
    shown = "***" if secret else value
    print(f"{key} = {shown}")


def _dispatch_command(
    command: str,
    chat_state: ChatConfigState,
    kg_mode: KGAnswerMode | None = None,
    messages: list[dict[str, object]] | None = None,
) -> bool:
    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        print(f"parse error: {exc}")
        return True
    if not parts:
        return True

    name, rest = parts[0].lower().removeprefix("/"), parts[1:]
    shortcuts: dict[str, Callable[[list[str]], list[str]]] = {
        "learn": lambda args: ["learn", *args],
        "status": lambda args: ["status", *args],
        "kg": lambda args: ["kg", *args],
        "chat": lambda args: ["chat-test", "--interactive", *args],
    }
    debug_shortcuts: dict[str, Callable[[list[str]], list[str]]] = {
        "ingest": lambda args: ["ingest", "--input", "data/sample_traces.jsonl", *args],
        "mine": lambda args: ["mine", *args],
        "recommend": lambda args: ["recommend", "--task", " ".join(args) if args else DEFAULT_RECOMMEND_TASK],
        "generate": lambda args: ["generate", "--cluster-id", args[0], *args[1:]],
        "verify": lambda args: ["verify", "--skill", args[0], *args[1:]],
        "self-evolve": lambda args: ["self-evolve", *args],
        "self_evolve": lambda args: ["self-evolve", *args],
        "demo": lambda args: ["demo", *args],
        "feedback": lambda args: ["feedback", *args],
    }

    if name in {"exit", "quit", "q"}:
        return False
    if name == "qq":
        _start_qq_interactive_bridge()
        return True
    if name in {"qqquit", "qq-quit", "qqlogout", "qq_logout"}:
        stopped = _stop_qq_interactive_bridge()
        if stopped:
            print(f"{DIM}QQ 远程入口已退出。{RESET}")
        else:
            print(f"{DIM}QQ 远程入口未在运行。{RESET}")
        return True
    if name in {"help", "?"}:
        print(HELP_TEXT)
        return True
    if name == "debug":
        if not rest:
            print(DEBUG_HELP_TEXT)
            return True
        debug_name = rest[0].lower().removeprefix("/")
        debug_args = rest[1:]
        if debug_name not in debug_shortcuts:
            print(f"未知调试命令：{debug_name}")
            print(DEBUG_HELP_TEXT)
            return True
        if debug_name in {"generate", "verify"} and not debug_args:
            print(f"usage: /debug {debug_name} <cluster-id/path>")
            return True
        _run(debug_shortcuts[debug_name](debug_args))
        return True
    if name in {"home", "dashboard"}:
        print(render_plain())
        print(HOME_PROMPT_GAP, end="")
        return True
    if name == "tools":
        for spec in tool_schemas():
            gate = "approval" if spec["approval_required"] else "direct"
            mode = "read" if spec["read_only"] else "write"
            print(f"{spec['name']}  {mode}  {gate}  {spec['description']}")
        return True
    if name == "tool":
        if not rest:
            print("usage: /tool <name> <json-args> [--approve]")
            return True
        approve = "--approve" in rest
        rest = [item for item in rest if item != "--approve"]
        tool_name = rest[0]
        raw_args = rest[1:]
        try:
            if raw_args and all("=" in item for item in raw_args):
                tool_args = parse_tool_arg_pairs(raw_args)
            else:
                tool_args = parse_tool_args(" ".join(raw_args) if raw_args else "{}")
        except Exception as exc:
            print(f"tool args error: {exc}")
            return True
        with _flow_input_session():
            _show_flow_prompt()
            with title_activity("running"):
                with _flow_status(f"正在执行 {tool_name}"):
                    result = execute_tool(tool_name, tool_args, approve=approve)
        _print_tool_result(result)
        return True
    if name in {"kg_answer", "kg-answer", "kganswer"}:
        if kg_mode is None:
            print("KG answer mode is not available in this context.")
            return True
        action = rest[0].lower() if rest else "status"
        if action in {"on", "enable", "enabled", "1", "true"}:
            kg_mode.enabled = True
            print(f"KG answer mode: on (strict=true, vector_backend={kg_mode.vector_backend})")
            return True
        if action in {"off", "disable", "disabled", "0", "false"}:
            kg_mode.enabled = False
            print("KG answer mode: off")
            return True
        if action in {"status", ""}:
            state = "on" if kg_mode.enabled else "off"
            print(f"KG answer mode: {state} (strict=true, vector_backend={kg_mode.vector_backend})")
            return True
        print("usage: /kg_answer on|off|status")
        return True
    if name == "skill":
        skill_name = " ".join(rest).strip()
        if not skill_name:
            print("usage: /skill <skill-name>")
            return True
        context = load_skill_context(skill_name)
        if context.get("status") != "ok":
            print(f"skill load error: {context.get('error')}")
            return True
        if messages is not None:
            messages.append({"role": "system", "content": render_skill_context_message(context)})
            message = f"{DIM}思考  已选择 skill：{context.get('name')}。{RESET}"
            print(message)
            _qq_send(f"已选择 skill：{context.get('name')}")
            return True
        print(f"{context.get('name')}\n{context.get('description')}\n{context.get('skill_file')}")
        return True
    if name == "talk":
        prompt = " ".join(rest).strip()
        if not prompt:
            print("usage: /talk <问题>")
            return True
        _start_contextual_talk_thread(FlowInputEvent(prompt, talk=True), chat_state, messages)
        return True
    if name == "image":
        if len(rest) < 2:
            print("usage: /image <图片路径或URL> <问题>")
            return True
        image_path = rest[0].strip().strip('"').strip("'")
        prompt = " ".join(rest[1:]).strip()
        answer = _image_once(image_path, prompt, chat_state)
        _print_image_answer(answer)
        if messages is not None:
            messages.append({"role": "user", "content": f"[图片理解] 图片：{image_path}\n问题：{prompt}"})
            messages.append({"role": "assistant", "content": f"[图片理解结果]\n{answer}"})
        return True
    if name == "model":
        _set_env_command("DEEPSEEK_MODEL", " ".join(rest), chat_state, prompt="DEEPSEEK_MODEL")
        print(render_plain())
        print(HOME_PROMPT_GAP, end="")
        return True
    if name in {"vision-model", "vision_model", "visionmodel"}:
        _set_env_command("GLM_VISION_MODEL", " ".join(rest), chat_state, prompt="GLM_VISION_MODEL")
        return True
    if name == "baseurl":
        _set_env_command("DEEPSEEK_BASE_URL", " ".join(rest), chat_state, prompt="DEEPSEEK_BASE_URL")
        return True
    if name in {"vision-baseurl", "vision_baseurl", "visionbaseurl"}:
        _set_env_command("GLM_VISION_BASE_URL", " ".join(rest), chat_state, prompt="GLM_VISION_BASE_URL")
        return True
    if name == "key":
        _set_env_command("DEEPSEEK_API_KEY", " ".join(rest), chat_state, prompt="DEEPSEEK_API_KEY", secret=True)
        return True
    if name in {"vision-key", "vision_key", "visionkey"}:
        _set_env_command("GLM_VISION_API_KEY", " ".join(rest), chat_state, prompt="GLM_VISION_API_KEY", secret=True)
        return True
    if name in shortcuts:
        _run(shortcuts[name](rest))
        return True
    if name in debug_shortcuts:
        if name == "generate" and not rest:
            print("请使用 /learn 自动选择任务，或用 /debug generate <簇ID> 显式指定。")
            return True
        if name == "verify" and not rest:
            print("请提供候选 skill 路径，例如 /debug verify outputs/candidate_skills/C01。")
            return True
        _run(debug_shortcuts[name](rest))
        return True

    print(f"未知命令：/{name}")
    print("输入 `/help` 查看可用命令")
    return True


def _read_transient_choice(
    prompt: str,
    *,
    valid_chars: set[str],
    default_on_enter: str = "",
    send_to_qq: bool = True,
) -> str:
    if send_to_qq:
        _qq_send(_plain_transient_prompt(prompt))
    if not _should_poll_transient_input(send_to_qq=send_to_qq):
        return input(prompt)

    with _pause_flow_input():
        sys.stdout.write(prompt)
        sys.stdout.flush()
        rendered_lines = prompt.count("\n") + 1
        while True:
            queued = _read_qq_transient_text()
            if queued:
                char = _transient_choice_from_text(queued, default_on_enter=default_on_enter)
                if char in valid_chars:
                    _erase_lines(rendered_lines)
                    sys.stdout.flush()
                    return char
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            char = msvcrt.getwch()
            if char == "\003":
                continue
            if char == "\x1b":
                if default_on_enter:
                    _erase_lines(rendered_lines)
                    sys.stdout.flush()
                    return default_on_enter
                continue
            if char in {"\x00", "\xe0"}:
                msvcrt.getwch()
                continue
            if char in {"\r", "\n"} and default_on_enter:
                char = default_on_enter
            if char in valid_chars:
                _erase_lines(rendered_lines)
                sys.stdout.flush()
                return char


def _read_transient_menu_choice(
    title: str,
    options: list[TransientChoiceOption],
    *,
    default_value: str = "",
    send_to_qq: bool = True,
) -> str:
    if not options:
        return default_value

    selected_index = _transient_default_index(options, default_value)
    rendered = _render_transient_choice_menu(title, options, selected_index, default_value=default_value)
    if send_to_qq:
        _qq_send(_plain_transient_prompt(rendered))

    if not _should_poll_transient_menu_input():
        print(rendered)
        while True:
            raw = input("选择：").strip()
            if not raw and default_value:
                return default_value
            value = _transient_menu_value_from_text(raw, options, default_value=default_value)
            if value:
                return value
            print("请输入有效选项。")

    with _pause_flow_input():
        rendered_lines = 0

        def redraw() -> None:
            nonlocal rendered_lines
            if rendered_lines:
                _erase_lines(rendered_lines)
            menu = _render_transient_choice_menu(title, options, selected_index, default_value=default_value)
            rendered_lines = menu.count("\n") + 1
            sys.stdout.write(menu)
            sys.stdout.flush()

        def finish(value: str) -> str:
            if rendered_lines:
                _erase_lines(rendered_lines)
            sys.stdout.flush()
            return value

        redraw()
        while True:
            queued = _read_qq_transient_text()
            if queued:
                value = _transient_menu_value_from_text(queued, options, default_value=default_value)
                if value:
                    return finish(value)
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            char = msvcrt.getwch()
            if char == "\003":
                continue
            if char == "\x1b":
                if default_value:
                    return finish(default_value)
                continue
            if char in {"\x00", "\xe0"}:
                key = msvcrt.getwch()
                if key == "H":
                    selected_index = (selected_index - 1) % len(options)
                    redraw()
                elif key == "P":
                    selected_index = (selected_index + 1) % len(options)
                    redraw()
                continue
            if char in {"\r", "\n"}:
                return finish(options[selected_index].value)
            if char == "\t" and options[selected_index].value == "\t":
                return finish("\t")
            value = _transient_menu_value_from_text(char, options, default_value=default_value)
            if value:
                return finish(value)


def _read_transient_multi_choice(
    title: str,
    options: list[TransientChoiceOption],
    *,
    send_to_qq: bool = True,
) -> list[str]:
    if not options:
        return []

    selected_index = 0
    selected_values: set[str] = set()
    rendered = _render_transient_multi_choice_menu(title, options, selected_index, selected_values)
    if send_to_qq:
        _qq_send(_plain_transient_prompt(rendered))

    if not _should_poll_transient_menu_input():
        print(rendered)
        raw = input("选择编号（可用逗号分隔，回车跳过）：").strip()
        return [options[index].value for index in _parse_transient_indices(raw, len(options))]

    with _pause_flow_input():
        rendered_lines = 0

        def redraw() -> None:
            nonlocal rendered_lines
            if rendered_lines:
                _erase_lines(rendered_lines)
            menu = _render_transient_multi_choice_menu(title, options, selected_index, selected_values)
            rendered_lines = menu.count("\n") + 1
            sys.stdout.write(menu)
            sys.stdout.flush()

        def finish() -> list[str]:
            if rendered_lines:
                _erase_lines(rendered_lines)
            sys.stdout.flush()
            return [option.value for option in options if option.value in selected_values]

        redraw()
        while True:
            queued = _read_qq_transient_text()
            if queued:
                values = [options[index].value for index in _parse_transient_indices(queued, len(options))]
                if rendered_lines:
                    _erase_lines(rendered_lines)
                sys.stdout.flush()
                return values
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            char = msvcrt.getwch()
            if char == "\003":
                continue
            if char == "\x1b":
                selected_values.clear()
                return finish()
            if char in {"\x00", "\xe0"}:
                key = msvcrt.getwch()
                if key == "H":
                    selected_index = (selected_index - 1) % len(options)
                    redraw()
                elif key == "P":
                    selected_index = (selected_index + 1) % len(options)
                    redraw()
                continue
            if char in {"\r", "\n"}:
                return finish()
            if char in {" ", "\t"}:
                value = options[selected_index].value
                if value in selected_values:
                    selected_values.remove(value)
                else:
                    selected_values.add(value)
                redraw()
                continue
            indices = _parse_transient_indices(char, len(options))
            if indices:
                value = options[indices[0]].value
                if value in selected_values:
                    selected_values.remove(value)
                else:
                    selected_values.add(value)
                selected_index = indices[0]
                redraw()


def _read_transient_text(prompt: str, *, send_to_qq: bool = True) -> str:
    if send_to_qq:
        _qq_send(_plain_transient_prompt(prompt))
    if not _should_poll_transient_input(send_to_qq=send_to_qq):
        return input(prompt)

    with _pause_flow_input():
        sys.stdout.write(prompt)
        sys.stdout.flush()
        rendered_lines = prompt.count("\n") + 1
        value = ""
        while True:
            queued = _read_qq_transient_text()
            if queued:
                _erase_lines(rendered_lines)
                sys.stdout.flush()
                return queued
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            char = msvcrt.getwch()
            if char == "\003":
                continue
            if char == "\x1b":
                _erase_lines(rendered_lines)
                sys.stdout.flush()
                return ""
            if char in {"\x00", "\xe0"}:
                msvcrt.getwch()
                continue
            if char in {"\r", "\n"}:
                _erase_lines(rendered_lines)
                sys.stdout.flush()
                return value
            if char == "\b":
                if value:
                    value = value[:-1]
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if char.isprintable():
                value += char
                sys.stdout.write(char)
                sys.stdout.flush()


def _should_poll_transient_input(*, send_to_qq: bool) -> bool:
    if msvcrt is None or not sys.stdin.isatty():
        return False
    if _raw_flow_input_enabled():
        return True
    return send_to_qq and QQ_INTERACTIVE_BRIDGE is not None


def _should_poll_transient_menu_input() -> bool:
    return msvcrt is not None and sys.stdin.isatty()


def _read_qq_transient_text() -> str:
    with FLOW_INPUT_QUEUE.mutex:
        if not FLOW_INPUT_QUEUE.queue:
            return ""
        event = FLOW_INPUT_QUEUE.queue.popleft()
        if FLOW_INPUT.queued_preview:
            FLOW_INPUT.queued_preview = FLOW_INPUT.queued_preview[1:]
    if event.talk:
        if event.text:
            _start_talk_thread(event.text, ChatConfigState(), reply_to_user_id=event.reply_to_user_id)
        return ""
    return event.text.strip()


def _transient_choice_from_text(text: str, *, default_on_enter: str) -> str:
    normalized = text.strip().lower()
    if not normalized:
        return default_on_enter
    if normalized.startswith("/approve"):
        return "1"
    if normalized.startswith("/deny"):
        return "3"
    if normalized in {"tab", "propose", "different", "换方案", "换成别的方案"}:
        return "\t"
    if normalized in {"approve", "allow", "yes", "y", "确认", "同意", "允许"}:
        return "1"
    if normalized in {"session", "always", "本轮", "一直允许"}:
        return "2"
    if normalized in {"deny", "no", "n", "拒绝"}:
        return "3"
    return normalized[0]


def _read_plan_question_answer(question: str, options: list[str]) -> str:
    clean_options = [" ".join(str(option).split()) for option in options if str(option).strip()]
    clean_options = clean_options[:3]
    while len(clean_options) < 3:
        clean_options.append(f"采用方案 {len(clean_options) + 1}")
    choice_options = [
        TransientChoiceOption(str(index), label)
        for index, label in enumerate(clean_options, start=1)
    ]
    choice_options.append(TransientChoiceOption("\t", "自定义答案", aliases=("4", "custom", "other", "自定义")))
    selected = _read_transient_menu_choice(
        question.strip() or "请选择下一步",
        choice_options,
        default_value="1",
    )
    if selected == "\t":
        custom = _read_transient_text("自定义答案：", send_to_qq=True).strip()
        return custom or clean_options[0]
    try:
        index = int(selected) - 1
    except ValueError:
        return selected
    if 0 <= index < len(clean_options):
        return clean_options[index]
    return selected


def _parse_plan_question(text: str) -> dict[str, object] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
            raw = "\n".join(lines[1:-1]).strip()
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or value.get("type") != PLAN_QUESTION_TYPE:
        return None
    question = str(value.get("question") or "").strip()
    options = value.get("options")
    if not question or not isinstance(options, list):
        return None
    clean_options = [str(item).strip() for item in options if str(item).strip()]
    if len(clean_options) < 1:
        return None
    return {"question": question, "options": clean_options[:3]}


def _plain_transient_prompt(prompt: str) -> str:
    clean = ANSI_RE.sub("", prompt.replace(DIM, "").replace(RESET, ""))
    return "\n".join(line.rstrip() for line in clean.splitlines()).strip()


def _transient_default_index(options: list[TransientChoiceOption], default_value: str) -> int:
    if default_value:
        for index, option in enumerate(options):
            if option.value == default_value:
                return index
    return 0


def _render_transient_choice_menu(
    title: str,
    options: list[TransientChoiceOption],
    selected_index: int,
    *,
    default_value: str = "",
) -> str:
    selected_index = max(0, min(selected_index, len(options) - 1))
    lines = [title.rstrip(), ""]
    for index, option in enumerate(options):
        marker = GLYPHS["prompt"] if index == selected_index else " "
        option_number = index + 1
        suffix = " [默认]" if option.value == default_value else ""
        line = f"{marker} {option_number}. {option.label}{suffix}"
        if index == selected_index:
            line = f"{CYAN}{line}{RESET}"
        else:
            line = f"  {line}"
        lines.append(line)
    lines.append("")
    if any(option.value == "\t" for option in options):
        lines.append(f"{DIM}上下键选择 {GLYPHS['dot']} Enter 确认 {GLYPHS['dot']} 选中自定义后 Tab 输入{RESET}")
    else:
        lines.append(f"{DIM}上下键选择 {GLYPHS['dot']} Enter 确认 {GLYPHS['dot']} 数字仍可直选{RESET}")
    return "\n".join(lines)


def _render_transient_multi_choice_menu(
    title: str,
    options: list[TransientChoiceOption],
    selected_index: int,
    selected_values: set[str],
) -> str:
    selected_index = max(0, min(selected_index, len(options) - 1))
    lines = [title.rstrip(), ""]
    for index, option in enumerate(options):
        marker = GLYPHS["prompt"] if index == selected_index else " "
        checked = "x" if option.value in selected_values else " "
        line = f"{marker} [{checked}] {index + 1}. {option.label}"
        if index == selected_index:
            line = f"{CYAN}{line}{RESET}"
        else:
            line = f"  {line}"
        lines.append(line)
    lines.append("")
    lines.append(f"{DIM}上下键选择 {GLYPHS['dot']} 空格勾选 {GLYPHS['dot']} Enter 确认/跳过{RESET}")
    return "\n".join(lines)


def _transient_menu_value_from_text(
    text: str,
    options: list[TransientChoiceOption],
    *,
    default_value: str = "",
) -> str:
    if text == "\t":
        return "\t" if any(option.value == "\t" for option in options) else ""
    normalized = text.strip().lower()
    mapped = _transient_choice_from_text(normalized, default_on_enter=default_value)
    candidates = {mapped, normalized}
    for option in options:
        option_values = {option.value.lower(), *(alias.lower() for alias in option.aliases)}
        if candidates.intersection(option_values):
            return option.value
    if mapped.isdigit():
        index = int(mapped) - 1
        if 0 <= index < len(options):
            return options[index].value
    return ""


def _parse_transient_indices(text: str, option_count: int) -> list[int]:
    indices: list[int] = []
    for part in text.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            index = int(part) - 1
        except ValueError:
            continue
        if 0 <= index < option_count and index not in indices:
            indices.append(index)
    return indices


def _read_approval_choice() -> str:
    return _read_transient_menu_choice(
        "选择工具授权方式",
        _approval_options(),
        default_value="3",
    )


def _approval_options() -> list[TransientChoiceOption]:
    return [
        TransientChoiceOption("1", "允许一次", aliases=("y", "yes", "approve", "allow", "确认", "同意", "允许")),
        TransientChoiceOption("2", "本轮不再询问这个工具", aliases=("a", "always", "session", "本轮", "一直允许")),
        TransientChoiceOption("3", "拒绝", aliases=("n", "no", "deny", "拒绝")),
        TransientChoiceOption("\t", "让模型换方案", aliases=("4", "p", "propose", "different", "tab", "换方案")),
    ]


def _approval_prompt(tool_name: str) -> ApprovalDecision:
    FLOW_INPUT.begin_output()
    set_title_state("confirmation")
    try:
        raw_answer = _read_transient_menu_choice(
            f"确认  {tool_name} 需要授权",
            _approval_options(),
            default_value="3",
        )
        answer = raw_answer.lower() if raw_answer == "\t" else raw_answer.strip().lower()

        if answer in {"1", "y", "yes"}:
            return ApprovalDecision(APPROVAL_ALLOW_ONCE)
        if answer in {"2", "a", "always", "session", "yes-session", "yes dont ask again", "yes,don't ask again"}:
            return ApprovalDecision(APPROVAL_ALLOW_SESSION)
        if answer in {"\t", "4", "p", "propose", "different", "tab"}:
            feedback = _read_transient_text("换成什么方案：", send_to_qq=True).strip()
            return ApprovalDecision(APPROVAL_PROPOSE, feedback=feedback)
        return ApprovalDecision(APPROVAL_DENY)
    finally:
        set_title_state("running")


def _denied_tool_result(call, decision: ApprovalDecision) -> dict[str, object]:
    message = "用户拒绝了这次工具调用。"
    if decision.action == APPROVAL_PROPOSE and decision.feedback:
        message = f"用户拒绝了这次工具调用，并要求换方案：{decision.feedback}"
    result: dict[str, object] = {
        "status": "denied",
        "tool": call.name,
        "message": message,
    }
    if decision.feedback:
        result["feedback"] = decision.feedback
    return result


def _execute_model_tool_call(call, *, turn_id: str, chat_state: ChatConfigState) -> dict[str, object]:
    if "__parse_error__" in call.args:
        result = {"status": "error", "tool": call.name, "error": call.args["__parse_error__"]}
        _print_tool_result(result)
        return result

    if chat_state.is_tool_approved_for_session(call.name):
        with _flow_status(f"正在执行 {call.name}"):
            approved = execute_tool(
                call.name,
                call.args,
                approve=True,
                turn_id=turn_id,
                cancel_event=FLOW_INTERRUPT_EVENT,
            )
        _print_tool_result(approved)
        return approved

    with _flow_status(f"正在执行 {call.name}"):
        result = execute_tool(call.name, call.args, turn_id=turn_id, cancel_event=FLOW_INTERRUPT_EVENT)
    if call.name == "run_shell":
        command = str(call.args.get("command") or "").strip()
        if result.get("status") in {"error", "timeout", "interrupted"}:
            if chat_state.last_failed_shell_command == command and command:
                result["note"] = REPEAT_FAILURE_HINT
            chat_state.last_failed_shell_command = command
        elif result.get("status") == "ok":
            chat_state.last_failed_shell_command = ""
    _print_tool_result(result)
    if result.get("status") != "requires_approval":
        return result

    decision = _approval_prompt(call.name)
    if decision.action in {APPROVAL_DENY, APPROVAL_PROPOSE}:
        denied = _denied_tool_result(call, decision)
        denied["preview"] = result.get("preview", {})
        _print_tool_result(denied)
        return denied
    if decision.action == APPROVAL_ALLOW_SESSION:
        chat_state.approve_tool_for_session(call.name)

    with _flow_status(f"正在执行 {call.name}"):
        approved = execute_tool(
            call.name,
            call.args,
            approve=True,
            turn_id=turn_id,
            cancel_event=FLOW_INTERRUPT_EVENT,
        )
    _print_tool_result(approved)
    return approved


def _active_skill_names(messages: list[dict[str, object]]) -> list[str]:
    return active_skill_names(messages)


def _print_turn_preamble(messages: list[dict[str, object]], round_index: int) -> None:
    report = build_turn_report(messages, round_index, queued_inputs=FLOW_INPUT_QUEUE.qsize())
    _print_status_flow(report.render())


def _chat_turn_with_tools(messages: list[dict[str, object]], chat_state: ChatConfigState) -> str:
    _ensure_chat_config(chat_state, max_tokens=4096, no_thinking=True)

    tools = chat_tool_schemas()
    round_index = 0
    listener_enabled = _start_flow_input_listener()
    set_title_state("running")
    try:
        with _flow_talk_pump(messages, chat_state):
            while True:
                _show_flow_prompt()
                _handle_flow_inputs(messages, chat_state)
                _print_turn_preamble(messages, round_index)
                response = _chat_completion_interruptible(messages, chat_state, tools=tools, round_index=round_index)
                message = extract_assistant_message(response)
                calls = requested_tool_calls(message)
                if not calls:
                    answer = extract_assistant_text(response)
                    plan_question = _parse_plan_question(answer)
                    if plan_question is not None:
                        messages.append({"role": "assistant", "content": answer})
                        selected_answer = _read_plan_question_answer(
                            str(plan_question.get("question") or ""),
                            [str(item) for item in plan_question.get("options", [])],
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": f"我选择：{selected_answer}",
                            }
                        )
                        round_index += 1
                        continue
                    messages.append({"role": "assistant", "content": answer})
                    return answer

                messages.append(assistant_message_for_history(message))
                content = str(message.get("content") or "").strip()
                if content:
                    _print_assistant_flow(content)
                if _handle_flow_inputs_before_pending_tool_calls(messages, chat_state, calls):
                    round_index += 1
                    continue
                turn_id = str(response.get("id") or f"chat-turn-{round_index}")
                interrupted = False
                for call_index, call in enumerate(calls):
                    FLOW_INTERRUPT_EVENT.clear()
                    _print_tool_reason(call)
                    _show_flow_prompt()
                    result = _execute_model_tool_call(call, turn_id=turn_id, chat_state=chat_state)
                    messages.append(
                        _tool_result_message_for_main_context(call, result, messages=messages, chat_state=chat_state)
                    )
                    interrupted = _handle_flow_inputs_before_pending_tool_calls(
                        messages,
                        chat_state,
                        calls[call_index + 1 :],
                    )
                    if interrupted:
                        break
                round_index += 1
    finally:
        _stop_flow_input_listener(listener_enabled)
        set_title_state("completed")


def _kg_answer_turn(prompt: str, kg_mode: KGAnswerMode) -> str:
    args = {
        "query": prompt,
        "strict": kg_mode.strict,
        "max_paths": kg_mode.max_paths,
        "vector_backend": kg_mode.vector_backend,
    }
    with _flow_input_session():
        _show_flow_prompt()
        with title_activity("running"):
            with _flow_status("正在执行 kg_answer"):
                result = execute_tool("kg_answer", args)
    _print_tool_result(result)
    return str(result.get("answer") or "")


def _select_skill_contexts_for_prompt(prompt: str) -> list[dict[str, object]]:
    recommendations = recommend_skill_contexts(prompt, top_k=5)
    if not recommendations:
        return []
    options = []
    for index, item in enumerate(recommendations, start=1):
        description = _short_value(item.get("description", ""), 110)
        options.append(TransientChoiceOption(str(index), f"{item.get('name')}  {DIM}{description}{RESET}"))
    selected_values = _read_transient_multi_choice(
        f"{DIM}思考  找到可选 skill；勾选后注入，直接回车跳过。{RESET}",
        options,
    )
    selected_indices = _parse_transient_indices(",".join(selected_values), len(recommendations))
    return [recommendations[index] for index in selected_indices]


def _append_skill_context_messages(messages: list[dict[str, object]], prompt: str, selected: list[dict[str, object]]) -> None:
    for item in selected:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        context = load_skill_context(name, task=prompt)
        if context.get("status") != "ok":
            print(f"skill load error: {context.get('error')}")
            continue
        messages.append({"role": "system", "content": render_skill_context_message(context)})
        print(f"{DIM}思考  已注入 skill：{context.get('name')}。{RESET}")


def main() -> int:
    start_title_monitor()
    if not maybe_show_trust_dialog():
        stop_title_monitor()
        return 1

    print(render_plain())
    print(HOME_PROMPT_GAP, end="")
    messages = [
        {
            "role": "system",
            "content": (
                "你是 DiaEvo 的终端助手。DiaEvo 用任务轨迹挖掘 Agent SKILL.md 工作流，"
                "用于归纳可复用操作模式、推荐已有技能、生成候选技能草稿并执行本地验证。"
                "请优先使用中文回答；如果用户明确使用其他语言，再切换到用户语言。"
                "回答要简洁、可执行，不要编造不存在的命令。"
                "不要在任何对话、代码、注释、列表或工具说明中使用 emoji。"
                "如果任务可能受益于专门工作流，先调用 recommend_skills；需要使用某个 skill 时调用 load_skill_context 并遵循其 SKILL.md。"
                "调用任何写入、删除、补丁、shell 执行或网络工具前，必须先用一句话说明为什么做、将影响什么。"
                "当前常用斜杠命令包括：/learn、/skill、/status、/kg、/talk <问题>、"
                "/image <path> <问题>、/qq、/qqquit、/model <name>、/baseurl <url>、/key <api-key>、/home、/help、/exit。"
                "内部流水线命令只作为高级调试入口保留在 /debug 中；不要把 cluster id 当作普通用户必须理解的步骤。"
                "你可以通过工具调用请求 list_files、read_file、write_file、edit_file、delete_file、apply_patch、run_shell、web_search、web_fetch、arxiv_search、recommend_skills 或 load_skill_context。"
                "不要自行选择知识图谱约束回答；严格 KG 回答是用户手动模式，只有用户运行 answer-kg --strict 或明确要求 KG 严格回答时才使用。"
                "需要审批的工具会先显示预览，只有用户同意后才会执行。"
                "脚本式入口是 diaevo，例如 diaevo demo "
                "或 diaevo chat-test --interactive。"
            ),
        }
    ]
    chat_state = ChatConfigState()
    kg_mode = KGAnswerMode()
    pending_command: str | None = None

    while True:
        try:
            command = pending_command if pending_command is not None else _read_next_command(chat_state)
            pending_command = None
        except (EOFError, KeyboardInterrupt):
            if sys.stdin.isatty():
                print("输入 /exit 退出。")
                continue
            print()
            _stop_qq_interactive_bridge()
            stop_title_monitor()
            return 0
        if not command:
            continue
        plan_mode_turn = command.startswith(PLAN_MODE_PREFIX)
        if plan_mode_turn:
            command = command[len(PLAN_MODE_PREFIX) :].strip()
        if is_command_input(command):
            if not _dispatch_command(command, chat_state, kg_mode, messages):
                _stop_qq_interactive_bridge()
                stop_title_monitor()
                return 0
            continue

        if kg_mode.enabled:
            _kg_answer_turn(command, kg_mode)
            continue

        history_len = len(messages)
        selected_skills = _select_skill_contexts_for_prompt(command)
        _append_skill_context_messages(messages, command, selected_skills)
        if plan_mode_turn:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "本轮处于 plan mode。先产出简洁计划、需要澄清的问题和拟使用的验证方式；"
                        "如果存在关键未知点，先只输出一个 JSON 对象："
                        "{\"type\":\"plan_question\",\"question\":\"一个必须澄清的问题\","
                        "\"options\":[\"选项一\",\"选项二\",\"选项三\"]}。"
                        "前三个选项由你给出，界面会提供第四个自定义答案入口；不要在这个 JSON 外输出其他文字。"
                        "除非用户已经明确要求执行并且计划中的关键未知点已解决，否则不要调用写入、删除、"
                        "shell、网络或补丁工具。失败归因只能作为执行规则/恢复约束，不要把它当作新的 skill 工作流。"
                    ),
                }
            )
        messages.append({"role": "user", "content": command})
        try:
            answer = _chat_turn_with_tools(messages, chat_state)
        except ModelTurnInterrupted:
            _print_status_flow("当前任务已中断")
            interrupted_talk_context = _talk_context_snapshot(messages)
            if _append_interrupted_flow_inputs(messages, chat_state, talk_context=interrupted_talk_context):
                try:
                    answer = _chat_turn_with_tools(messages, chat_state)
                except ModelTurnInterrupted:
                    continue
                except Exception as exc:
                    print(f"chat error: {exc}")
                    continue
                if answer:
                    _print_assistant_flow(answer)
            continue
        except Exception as exc:
            print(f"chat error: {exc}")
            del messages[history_len:]
            continue
        if answer:
            _print_assistant_flow(answer)
