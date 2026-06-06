from __future__ import annotations

import getpass
import asyncio
import multiprocessing
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
    chat_completion,
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
    tool_result_message_for_call,
)

from .action_report import active_skill_names, build_turn_report, short_value, tool_reason
from .cli_style import DIM, RESET, maybe_show_trust_dialog
from .flow_input import FlowInputController, FlowInputEvent, msvcrt
from .output_policy import print_assistant, print_status
from .prompt_bar import _erase_lines, is_command_input, read_prompt
from .progress import status
from .terminal_home import render_plain
from .tool_render import render_tool_result
from .window_title import set_title_state, start_title_monitor, stop_title_monitor, title_activity

DEFAULT_RECOMMEND_TASK = "给当前项目生成测试修复 skill"
HOME_PROMPT_GAP = "\n\n"

HELP_TEXT = """
常用操作：
  直接输入任务              让 DiaEvo 读代码、选工具并处理问题
  /skill [名称]             查看或选择已有 skill
  /learn                   从最近任务中总结一个候选 skill
  /status                  查看工作区、模型和最近学习结果
  /kg                      打开可编辑知识图谱工作台
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
_TALK_THREADS: set[threading.Thread] = set()
_TALK_THREADS_LOCK = threading.Lock()
QQ_INTERACTIVE_BRIDGE: QQInteractiveBridge | None = None
QQ_INTERACTIVE_THREAD: threading.Thread | None = None


@dataclass(frozen=True)
class ApprovalDecision:
    action: str
    feedback: str = ""


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
) -> threading.Thread | None:
    prompt = prompt.strip()
    if not prompt:
        return None

    thread = threading.Thread(target=_talk_thread_worker, args=(prompt, chat_state, context), daemon=True)
    with _TALK_THREADS_LOCK:
        _TALK_THREADS.add(thread)
    thread.start()
    return thread


def _talk_thread_worker(prompt: str, chat_state: ChatConfigState, context: str = "") -> None:
    try:
        answer = _talk_once(prompt, chat_state, show_status=False, context=context)
    except Exception as exc:
        answer = f"旁路提问失败：{exc}"
    try:
        _print_talk_answer(answer)
    finally:
        thread = threading.current_thread()
        with _TALK_THREADS_LOCK:
            _TALK_THREADS.discard(thread)


def _print_talk_answer(answer: str) -> None:
    _begin_flow_output()
    print("\ntalk>")
    print_assistant(answer)
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
    _qq_send(rendered)
    _show_flow_prompt(force=True)


def _begin_flow_output() -> None:
    FLOW_INPUT.begin_output()


def _print_assistant_flow(text: str) -> None:
    _begin_flow_output()
    print_assistant(text)
    _qq_send(text)
    _show_flow_prompt(force=True)


def _print_status_flow(text: str) -> None:
    _begin_flow_output()
    print_status(text)
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


def _enqueue_qq_text(text: str) -> None:
    FLOW_INPUT.queue_external_text(text, source="QQ")


def _start_qq_interactive_bridge() -> None:
    global QQ_INTERACTIVE_BRIDGE, QQ_INTERACTIVE_THREAD
    if QQ_INTERACTIVE_THREAD is not None and QQ_INTERACTIVE_THREAD.is_alive():
        return
    try:
        config = qq_config_from_env()
    except Exception as exc:
        print(f"{DIM}QQ 远程配置读取失败：{exc}{RESET}")
        return
    if not config.enabled:
        return
    try:
        startup = prepare_onebot_service(config)
    except QQBridgeError as exc:
        print(f"{DIM}QQ 远程入口启动失败：{exc}{RESET}")
        return
    startup_status = str(startup.get("status") or "")
    if startup_status == "started":
        print(f"{DIM}{startup.get('message')}{RESET}")
    elif startup_status in {"not_running", "missing_command", "exited", "timeout"}:
        print(f"{DIM}QQ 远程入口提示：{startup.get('message')}{RESET}")
    QQ_INTERACTIVE_BRIDGE = QQInteractiveBridge(config, enqueue_text=_enqueue_qq_text)

    def worker() -> None:
        try:
            asyncio.run(run_interactive_bridge(config, QQ_INTERACTIVE_BRIDGE))
        except QQBridgeError as exc:
            _print_status_flow(f"QQ 远程入口启动失败：{exc}")
        except Exception as exc:
            _print_status_flow(f"QQ 远程入口退出：{exc}")

    QQ_INTERACTIVE_THREAD = threading.Thread(target=worker, name="diaevo-qq-bridge", daemon=True)
    QQ_INTERACTIVE_THREAD.start()
    print(f"{DIM}QQ 远程入口已启用：{', '.join(sorted(config.allowed_users))}{RESET}")


def _event_to_command(event: FlowInputEvent, chat_state: ChatConfigState) -> str:
    if event.talk:
        if event.text:
            _start_talk_thread(event.text, chat_state)
        return ""
    return event.text.strip()


def _read_next_command(chat_state: ChatConfigState) -> str:
    listener_enabled = _start_flow_input_listener()
    if listener_enabled:
        _show_flow_prompt(force=True)
        while True:
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

    def animate() -> None:
        index = 0
        while not stopped.is_set():
            FLOW_INPUT.update_status_line(f"{frames[index % len(frames)]} {message}")
            index += 1
            time.sleep(0.12)

    _show_flow_prompt(force=True)
    thread = threading.Thread(target=animate, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=0.5)
        FLOW_INPUT.clear_status_line()


def _start_flow_input_listener() -> bool:
    return FLOW_INPUT.start()


def _stop_flow_input_listener(enabled: bool) -> None:
    FLOW_INPUT.stop(enabled)


@contextmanager
def _flow_input_session() -> object:
    with FLOW_INPUT.session():
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


def _start_contextual_talk_thread(
    prompt: str,
    chat_state: ChatConfigState,
    messages: list[dict[str, object]] | None,
) -> threading.Thread | None:
    context = _talk_context_snapshot(messages)
    if context:
        return _start_talk_thread(prompt, chat_state, context=context)
    return _start_talk_thread(prompt, chat_state)


def _handle_flow_inputs(messages: list[dict[str, object]], chat_state: ChatConfigState) -> bool:
    return FLOW_INPUT.handle_queued(messages, lambda text: _start_contextual_talk_thread(text, chat_state, messages))


def _interrupted_tool_result(call: RequestedToolCall) -> dict[str, object]:
    return {
        "status": "interrupted",
        "tool": call.name,
        "message": "Tool call skipped because the user supplied new input before it ran.",
    }


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
                _start_contextual_talk_thread(event.text, chat_state, messages)
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
        _qq_send(output)
    if error_output:
        _begin_flow_output()
        print(error_output, file=sys.stderr)
        _qq_send(error_output)
    if code:
        _print_status_flow(f"命令退出，状态码：{code}")


def _model_request_worker(
    output: "multiprocessing.Queue[dict[str, object]]",
    messages: list[dict[str, object]],
    config,
    tools: list[dict[str, object]],
) -> None:
    try:
        response = chat_completion(messages, config, tools=tools)
    except BaseException as exc:
        output.put({"status": "error", "error": repr(exc)})
        return
    output.put({"status": "ok", "response": response})


def _chat_completion_interruptible(
    messages: list[dict[str, object]],
    chat_state: ChatConfigState,
    *,
    tools: list[dict[str, object]],
):
    if msvcrt is None or not sys.stdin.isatty():
        return chat_completion(messages, chat_state.value, tools=tools)

    FLOW_FORCE_TERMINATE_EVENT.clear()
    output: "multiprocessing.Queue[dict[str, object]]" = multiprocessing.Queue(maxsize=1)
    process = multiprocessing.Process(target=_model_request_worker, args=(output, messages, chat_state.value, tools))
    process.start()
    try:
        while process.is_alive():
            FLOW_INPUT.handle_talk_queued(lambda text: _start_contextual_talk_thread(text, chat_state, messages))
            if FLOW_FORCE_TERMINATE_EVENT.is_set():
                process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
                FLOW_FORCE_TERMINATE_EVENT.clear()
                raise ModelTurnInterrupted("model request terminated")
            time.sleep(0.05)
        process.join()
        try:
            payload = output.get_nowait()
        except queue.Empty as exc:
            raise RuntimeError("model request process exited without a response") from exc
        if payload.get("status") == "error":
            raise RuntimeError(str(payload.get("error") or "model request failed"))
        return payload["response"]
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        output.close()


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
        _start_contextual_talk_thread(prompt, chat_state, messages)
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
    if msvcrt is None or not sys.stdin.isatty():
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
                raise KeyboardInterrupt
            if char in {"\x00", "\xe0"}:
                msvcrt.getwch()
                continue
            if char in {"\r", "\n"} and default_on_enter:
                char = default_on_enter
            if char in valid_chars:
                _erase_lines(rendered_lines)
                sys.stdout.flush()
                return char


def _read_transient_text(prompt: str, *, send_to_qq: bool = True) -> str:
    if send_to_qq:
        _qq_send(_plain_transient_prompt(prompt))
    if msvcrt is None or not sys.stdin.isatty():
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
                raise KeyboardInterrupt
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


def _read_qq_transient_text() -> str:
    with FLOW_INPUT_QUEUE.mutex:
        if not FLOW_INPUT_QUEUE.queue:
            return ""
        event = FLOW_INPUT_QUEUE.queue.popleft()
        if FLOW_INPUT.queued_preview:
            FLOW_INPUT.queued_preview = FLOW_INPUT.queued_preview[1:]
    if event.talk:
        if event.text:
            _start_talk_thread(event.text, ChatConfigState())
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
    if normalized in {"approve", "allow", "yes", "y", "确认", "同意", "允许"}:
        return "1"
    if normalized in {"session", "always", "本轮", "一直允许"}:
        return "2"
    if normalized in {"deny", "no", "n", "拒绝"}:
        return "3"
    return normalized[0]


def _plain_transient_prompt(prompt: str) -> str:
    clean = prompt.replace(DIM, "").replace(RESET, "")
    return "\n".join(line.rstrip() for line in clean.splitlines()).strip()


def _read_approval_choice() -> str:
    prompt = "选择  1 允许一次 / 2 本轮不再询问 / 3 拒绝 / Tab 换方案："
    return _read_transient_choice(
        prompt,
        valid_chars={"\t", "1", "2", "3", "4", "y", "Y", "a", "A", "n", "N", "p", "P"},
        default_on_enter="3",
    )


def _approval_prompt(tool_name: str) -> ApprovalDecision:
    FLOW_INPUT.begin_output()
    set_title_state("confirmation")
    try:
        if msvcrt is not None and sys.stdin.isatty():
            approval_text = (
                f"确认  {tool_name} 需要授权\n"
                "1 允许一次\n"
                "2 本轮不再询问这个工具\n"
                "3 拒绝\n"
                "回复数字即可；也可回复 /approve 或 /deny。"
            )
            raw_answer = _read_transient_choice(
                approval_text + "\n选择：",
                valid_chars={"\t", "1", "2", "3", "4", "y", "Y", "a", "A", "n", "N", "p", "P"},
                default_on_enter="3",
            )
        else:
            approval_text = (
                f"确认  {tool_name} 需要授权\n"
                "1 允许一次\n"
                "2 本轮不再询问这个工具\n"
                "3 拒绝\n"
                "Tab 让模型换方案"
            )
            print(approval_text)
            _qq_send(approval_text)
            raw_answer = _read_approval_choice()
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
        while True:
            _show_flow_prompt()
            _handle_flow_inputs(messages, chat_state)
            _print_turn_preamble(messages, round_index)
            with _flow_status("正在请求模型"):
                response = _chat_completion_interruptible(messages, chat_state, tools=tools)
            message = extract_assistant_message(response)
            calls = requested_tool_calls(message)
            if not calls:
                answer = extract_assistant_text(response)
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
                messages.append(tool_result_message_for_call(call, result))
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
    lines = [f"{DIM}思考  找到可选 skill；输入编号注入，直接回车跳过。{RESET}"]
    for index, item in enumerate(recommendations, start=1):
        description = _short_value(item.get("description", ""), 110)
        lines.append(f"  {index}. {item.get('name')}  {DIM}{description}{RESET}")
    lines.append("选择 skill 编号（可用逗号分隔，回车跳过）：")
    rendered = "\n".join(lines)
    if msvcrt is not None and sys.stdin.isatty():
        raw = _read_transient_text(rendered).strip()
    else:
        print("\n".join(lines[:-1]))
        raw = input(lines[-1]).strip()
    if not raw:
        return []
    selected: list[dict[str, object]] = []
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            index = int(part)
        except ValueError:
            continue
        if 1 <= index <= len(recommendations):
            selected.append(recommendations[index - 1])
    return selected


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
    _start_qq_interactive_bridge()
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
                "/image <path> <问题>、/model <name>、/baseurl <url>、/key <api-key>、/home、/help、/exit。"
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
            command = pending_command if pending_command is not None else read_prompt()
            pending_command = None
        except (EOFError, KeyboardInterrupt):
            print()
            stop_title_monitor()
            return 0
        if not command:
            continue
        if is_command_input(command):
            if not _dispatch_command(command, chat_state, kg_mode, messages):
                stop_title_monitor()
                return 0
            continue

        if kg_mode.enabled:
            _kg_answer_turn(command, kg_mode)
            continue

        history_len = len(messages)
        selected_skills = _select_skill_contexts_for_prompt(command)
        _append_skill_context_messages(messages, command, selected_skills)
        messages.append({"role": "user", "content": command})
        try:
            answer = _chat_turn_with_tools(messages, chat_state)
        except ModelTurnInterrupted:
            _print_status_flow("当前任务已中断")
            interrupted_talk_context = _talk_context_snapshot(messages)
            del messages[history_len:]
            drained = _drain_flow_inputs()
            for event in drained:
                if event.talk and event.text:
                    _start_talk_thread(event.text, chat_state, context=interrupted_talk_context)
                    continue
                if event.text and pending_command is None:
                    pending_command = event.text
            continue
        except Exception as exc:
            print(f"chat error: {exc}")
            del messages[history_len:]
            continue
        _print_assistant_flow(answer)
