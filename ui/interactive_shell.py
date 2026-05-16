from __future__ import annotations

import getpass
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
from diaevo.tool_layer import execute_tool, parse_tool_arg_pairs, parse_tool_args, tool_schemas
from diaevo.tool_chat import (
    RequestedToolCall,
    assistant_message_for_history,
    chat_tool_schemas,
    extract_assistant_message,
    requested_tool_calls,
    tool_result_message_for_call,
)

from .cli_style import DIM, GLYPHS, RESET, maybe_show_trust_dialog
from .output_policy import print_assistant, print_status
from .prompt_bar import is_command_input, read_prompt
from .progress import status
from .terminal_home import render_plain
from .tool_render import render_tool_result
from .window_title import set_title_state, start_title_monitor, stop_title_monitor, title_activity

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows is the primary target.
    msvcrt = None

DEFAULT_RECOMMEND_TASK = "给当前项目生成测试修复 skill"
HOME_PROMPT_GAP = "\n\n"

HELP_TEXT = """
命令：
  /ingest                  导入 data/sample_traces.jsonl
  /mine                    运行挖掘流程
  /kg                      打开可编辑知识图谱工作台
  /kg_answer on|off        开关严格 KG 图向量检索回答模式
  /recommend <任务>        按任务推荐技能
  /generate <cluster-id>   生成候选 SKILL.md
  /verify <cluster-id/path> 验证候选技能
  /demo                    运行完整 MVP 演示
  /feedback                将工具事件回灌为处理后的轨迹
  /tools                   列出本地工具 schema
  /tool <name> <json|key=value...> 运行本地工具；需要审批的工具请加 --approve
  /talk <问题>              不打断主会话和工具流，向模型快速提问
  /image <路径> <问题>      使用 GLM 视觉模型理解图片；默认模型 glm-4.6v-flash，并发 1
  /model <name>            设置 DEEPSEEK_MODEL 并重绘仪表盘
  /vision-model <name>     设置 GLM_VISION_MODEL
  /vision-baseurl <url>    设置 GLM_VISION_BASE_URL
  /vision-key <api-key>    设置 GLM_VISION_API_KEY，后续不回显密钥
  /baseurl <url>           设置 DEEPSEEK_BASE_URL
  /key <api-key>           设置 DEEPSEEK_API_KEY，后续不回显密钥
  /home                    重绘仪表盘
  /help                    显示帮助
  /exit                    退出

其他输入会作为普通聊天消息发送给 DeepSeek。
""".strip()

APPROVAL_ALLOW_ONCE = "allow_once"
APPROVAL_ALLOW_SESSION = "allow_session"
APPROVAL_DENY = "deny"
APPROVAL_PROPOSE = "propose"
FLOW_INPUT_QUEUE: "queue.Queue[FlowInputEvent]" = queue.Queue()
FLOW_INPUT_ACTIVE = threading.Event()
FLOW_INPUT_PAUSED = threading.Event()
FLOW_INPUT_THREAD: threading.Thread | None = None
FLOW_INPUT_LOCK = threading.Lock()
FLOW_INTERRUPT_EVENT = threading.Event()
FLOW_PROMPT_VISIBLE = threading.Event()


@dataclass(frozen=True)
class ApprovalDecision:
    action: str
    feedback: str = ""


@dataclass(frozen=True)
class FlowInputEvent:
    text: str
    interrupt: bool = False
    talk: bool = False


class ChatConfigState:
    def __init__(self) -> None:
        self.value = None
        self.vision_value = None
        self.approved_tools: set[str] = set()

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


def _talk_once(prompt: str, chat_state: ChatConfigState) -> str:
    config = _ensure_chat_config(chat_state, max_tokens=2048, no_thinking=True)
    messages = [
        {
            "role": "system",
            "content": (
                "你是 DiaEvo 的旁路问答助手。回答当前问题，不要调用工具，不要改变主会话计划；"
                "如果问题需要主会话上下文缺失，直接说明缺少哪些信息。"
            ),
        },
        {"role": "user", "content": prompt},
    ]
    with title_activity("running"):
        with status("正在旁路提问"):
            response = chat_completion(messages, config)
    return extract_assistant_text(response)


def _print_talk_answer(answer: str) -> None:
    print("\ntalk>")
    print_assistant(answer)


def _image_once(image_path: str, prompt: str, chat_state: ChatConfigState) -> str:
    config = _ensure_vision_config(chat_state, max_tokens=4096)
    system = (
        "你是 DiaEvo 的图像理解助手。请用中文分析用户提供的图片，只描述图片中可见证据；"
        "如果无法确定，明确说明不确定。不要调用工具，不要编造图片外的信息。"
    )
    with title_activity("running"):
        with status("正在理解图片"):
            answer, _response = vision_chat_once(prompt, [image_path], system, config)
    return answer


def _print_image_answer(answer: str) -> None:
    print("\nimage>")
    print_assistant(answer)


def _flow_input_worker() -> None:
    buffer = ""
    interrupt = False
    while True:
        if not FLOW_INPUT_ACTIVE.is_set() or msvcrt is None:
            time.sleep(0.05)
            continue
        if FLOW_INPUT_PAUSED.is_set():
            time.sleep(0.05)
            continue
        if not msvcrt.kbhit():
            time.sleep(0.05)
            continue
        char = msvcrt.getwch()
        if char in {"\x00", "\xe0"}:
            msvcrt.getwch()
            continue
        if char == "\003":
            FLOW_INPUT_QUEUE.put(FlowInputEvent("", interrupt=True))
            FLOW_INTERRUPT_EVENT.set()
            continue
        if char == "\x1b":
            text = buffer.strip()
            if text:
                talk = text.lower().startswith("/talk ")
                payload = text[6:].strip() if talk else text
                FLOW_INPUT_QUEUE.put(FlowInputEvent(payload, interrupt=True, talk=talk))
                FLOW_INTERRUPT_EVENT.set()
                buffer = ""
                interrupt = False
                sys.stdout.write("\n")
                sys.stdout.flush()
                continue
            interrupt = True
            buffer = ""
            FLOW_INTERRUPT_EVENT.set()
            FLOW_PROMPT_VISIBLE.clear()
            sys.stdout.write(f"\ninterrupt {GLYPHS['prompt']} ")
            sys.stdout.flush()
            continue
        if char in {"\r", "\n"}:
            text = buffer.strip()
            buffer = ""
            if not text:
                interrupt = False
                continue
            talk = text.lower().startswith("/talk ")
            payload = text[6:].strip() if talk else text
            FLOW_INPUT_QUEUE.put(FlowInputEvent(payload, interrupt=interrupt, talk=talk))
            sys.stdout.write("\n")
            sys.stdout.flush()
            FLOW_PROMPT_VISIBLE.clear()
            interrupt = False
            continue
        if char == "\b":
            buffer = buffer[:-1]
            sys.stdout.write("\b \b")
            sys.stdout.flush()
            continue
        if char.isprintable():
            if not buffer:
                if not FLOW_PROMPT_VISIBLE.is_set():
                    label = "interrupt" if interrupt else "next"
                    sys.stdout.write(f"\n{label} {GLYPHS['prompt']} ")
                    FLOW_PROMPT_VISIBLE.set()
            buffer += char
            sys.stdout.write(char)
            sys.stdout.flush()


def _start_flow_input_listener() -> bool:
    global FLOW_INPUT_THREAD
    if msvcrt is None or not sys.stdin.isatty():
        return False
    with FLOW_INPUT_LOCK:
        if FLOW_INPUT_THREAD is None or not FLOW_INPUT_THREAD.is_alive():
            FLOW_INPUT_THREAD = threading.Thread(target=_flow_input_worker, daemon=True)
            FLOW_INPUT_THREAD.start()
    FLOW_INPUT_ACTIVE.set()
    return True


def _stop_flow_input_listener(enabled: bool) -> None:
    if enabled:
        FLOW_INPUT_ACTIVE.clear()
        FLOW_INTERRUPT_EVENT.clear()
        FLOW_PROMPT_VISIBLE.clear()


def _show_flow_prompt(label: str = "next") -> None:
    if msvcrt is None or not sys.stdin.isatty() or FLOW_INPUT_PAUSED.is_set():
        return
    if FLOW_PROMPT_VISIBLE.is_set():
        return
    sys.stdout.write(f"\n{DIM}{label} {GLYPHS['prompt']} {RESET}")
    sys.stdout.flush()
    FLOW_PROMPT_VISIBLE.set()


@contextmanager
def _pause_flow_input() -> object:
    FLOW_INPUT_PAUSED.set()
    try:
        yield
    finally:
        FLOW_INPUT_PAUSED.clear()


def _drain_flow_inputs() -> list[FlowInputEvent]:
    events: list[FlowInputEvent] = []
    while True:
        try:
            events.append(FLOW_INPUT_QUEUE.get_nowait())
        except queue.Empty:
            return events


def _handle_flow_inputs(messages: list[dict[str, object]], chat_state: ChatConfigState) -> bool:
    interrupted = False
    for event in _drain_flow_inputs():
        if event.talk:
            _print_talk_answer(_talk_once(event.text, chat_state))
            continue
        if event.text:
            messages.append({"role": "user", "content": event.text})
        if event.interrupt:
            interrupted = True
    return interrupted


def _short_value(value: object, limit: int = 80) -> str:
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _tool_reason(call: RequestedToolCall) -> str:
    args = call.args
    if call.name == "list_files":
        return f"查看目录结构，确认接下来该读哪些文件。path={_short_value(args.get('path', '.'))}"
    if call.name == "read_file":
        return f"读取相关文件内容，用现有代码和文档支撑下一步判断。path={_short_value(args.get('path', ''))}"
    if call.name == "write_file":
        return f"写入文件以落地当前修改。path={_short_value(args.get('path', ''))}"
    if call.name == "edit_file":
        return f"按定位到的片段做局部替换。path={_short_value(args.get('path', ''))}"
    if call.name == "delete_file":
        return f"删除不再需要的文件或目录。path={_short_value(args.get('path', ''))}"
    if call.name == "apply_patch":
        return "应用补丁，把已确定的代码改动写入工作区。"
    if call.name == "run_shell":
        return f"运行本地命令验证或获取结果。command={_short_value(args.get('command', ''))}"
    if call.name == "web_fetch":
        return f"获取指定网页内容作为外部证据。url={_short_value(args.get('url', ''))}"
    if call.name == "web_search":
        return f"搜索外部资料，找到可参考的信息来源。query={_short_value(args.get('query', ''))}"
    return f"调用 {call.name} 获取完成任务所需的信息或执行结果。"


def _print_tool_reason(call: RequestedToolCall) -> None:
    print(f"{DIM}thinking> {_tool_reason(call)}{RESET}")


def _run(argv: list[str]) -> None:
    label = argv[0] if argv else "command"
    with title_activity("running"):
        with status(f"正在运行 {label}"):
            code = cli_main(argv)
    if code:
        print_status(f"命令退出，状态码：{code}")


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
        "ingest": lambda args: ["ingest", "--input", "data/sample_traces.jsonl", *args],
        "mine": lambda args: ["mine", *args],
        "kg": lambda args: ["kg", *args],
        "recommend": lambda args: ["recommend", "--task", " ".join(args) if args else DEFAULT_RECOMMEND_TASK],
        "generate": lambda args: ["generate", "--cluster-id", args[0] if args else "C03"],
        "verify": lambda args: ["verify", "--skill", args[0] if args else "outputs/candidate_skills/C03"],
        "demo": lambda args: ["demo", *args],
        "feedback": lambda args: ["feedback", *args],
        "chat": lambda args: ["chat-test", "--interactive", *args],
    }

    if name in {"exit", "quit", "q"}:
        return False
    if name in {"help", "?"}:
        print(HELP_TEXT)
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
        with title_activity("running"):
            with status(f"正在执行 {tool_name}"):
                result = execute_tool(tool_name, tool_args, approve=approve)
        print(render_tool_result(result))
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
    if name == "talk":
        prompt = " ".join(rest).strip()
        if not prompt:
            print("usage: /talk <问题>")
            return True
        answer = _talk_once(prompt, chat_state)
        _print_talk_answer(answer)
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

    print(f"未知命令：/{name}")
    print("输入 `/help` 查看可用命令")
    return True


def _read_approval_choice() -> str:
    prompt = "选择 [1 Yes / 2 Yes, don't ask again / 3 No / Tab propose different]: "
    if msvcrt is None or not sys.stdin.isatty():
        return input(prompt)

    with _pause_flow_input():
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            char = msvcrt.getwch()
            if char == "\003":
                raise KeyboardInterrupt
            if char in {"\x00", "\xe0"}:
                msvcrt.getwch()
                continue
            if char == "\r":
                char = "3"
            if char in {"\t", "1", "2", "3", "4", "y", "Y", "a", "A", "n", "N", "p", "P"}:
                shown = "Tab" if char == "\t" else char
                sys.stdout.write(f"{shown}\n")
                sys.stdout.flush()
                return char


def _approval_prompt(tool_name: str) -> ApprovalDecision:
    FLOW_PROMPT_VISIBLE.clear()
    set_title_state("confirmation")
    try:
        print(f"批准执行 {tool_name}？")
        print("  1. Yes")
        print(f"  2. Yes, don't ask again for {tool_name} in this session")
        print("  3. No")
        print("  Tab. No, propose a different approach")
        raw_answer = _read_approval_choice()
        answer = raw_answer.lower() if raw_answer == "\t" else raw_answer.strip().lower()

        if answer in {"1", "y", "yes"}:
            return ApprovalDecision(APPROVAL_ALLOW_ONCE)
        if answer in {"2", "a", "always", "session", "yes-session", "yes dont ask again", "yes,don't ask again"}:
            return ApprovalDecision(APPROVAL_ALLOW_SESSION)
        if answer in {"\t", "4", "p", "propose", "different", "tab"}:
            feedback = input("告诉模型需要改成什么方案：").strip()
            return ApprovalDecision(APPROVAL_PROPOSE, feedback=feedback)
        return ApprovalDecision(APPROVAL_DENY)
    finally:
        set_title_state("running")


def _denied_tool_result(call, decision: ApprovalDecision) -> dict[str, object]:
    message = "User denied approval for this tool call."
    if decision.action == APPROVAL_PROPOSE and decision.feedback:
        message = f"User denied approval and proposed a different approach: {decision.feedback}"
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
        print(render_tool_result(result))
        return result

    if chat_state.is_tool_approved_for_session(call.name):
        with status(f"正在执行 {call.name}"):
            approved = execute_tool(
                call.name,
                call.args,
                approve=True,
                turn_id=turn_id,
                cancel_event=FLOW_INTERRUPT_EVENT,
            )
        print(render_tool_result(approved))
        return approved

    with status(f"正在执行 {call.name}"):
        result = execute_tool(call.name, call.args, turn_id=turn_id, cancel_event=FLOW_INTERRUPT_EVENT)
    print(render_tool_result(result))
    if result.get("status") != "requires_approval":
        return result

    decision = _approval_prompt(call.name)
    if decision.action in {APPROVAL_DENY, APPROVAL_PROPOSE}:
        denied = _denied_tool_result(call, decision)
        denied["preview"] = result.get("preview", {})
        print(render_tool_result(denied))
        return denied
    if decision.action == APPROVAL_ALLOW_SESSION:
        chat_state.approve_tool_for_session(call.name)

    with status(f"正在执行 {call.name}"):
        approved = execute_tool(
            call.name,
            call.args,
            approve=True,
            turn_id=turn_id,
            cancel_event=FLOW_INTERRUPT_EVENT,
        )
    print(render_tool_result(approved))
    return approved


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
            with status("正在请求模型"):
                response = chat_completion(messages, chat_state.value, tools=tools)
            message = extract_assistant_message(response)
            calls = requested_tool_calls(message)
            if not calls:
                answer = extract_assistant_text(response)
                messages.append({"role": "assistant", "content": answer})
                return answer

            messages.append(assistant_message_for_history(message))
            if _handle_flow_inputs(messages, chat_state):
                round_index += 1
                continue
            turn_id = str(response.get("id") or f"chat-turn-{round_index}")
            interrupted = False
            for call in calls:
                FLOW_INTERRUPT_EVENT.clear()
                _print_tool_reason(call)
                _show_flow_prompt()
                result = _execute_model_tool_call(call, turn_id=turn_id, chat_state=chat_state)
                messages.append(tool_result_message_for_call(call, result))
                interrupted = _handle_flow_inputs(messages, chat_state)
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
    with title_activity("running"):
        with status("正在执行 kg_answer"):
            result = execute_tool("kg_answer", args)
    print(render_tool_result(result))
    return str(result.get("answer") or "")


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
                "当前交互式斜杠命令包括：/ingest、/mine、/kg、/recommend <task>、"
                "/kg_answer on|off、/generate <cluster-id>、/verify <cluster-id/path>、/demo、/tools、/tool、/model <name>、"
                "/talk <问题>、/image <path> <问题>、/baseurl <url>、/key <api-key>、/home、/help、/exit。"
                "你可以通过工具调用请求 list_files、read_file、write_file、edit_file、delete_file、apply_patch、run_shell、web_search 或 web_fetch。"
                "不要自行选择知识图谱约束回答；严格 KG 回答是用户手动模式，只有用户运行 answer-kg --strict 或明确要求 KG 严格回答时才使用。"
                "需要审批的工具会先显示预览，只有用户同意后才会执行。"
                "脚本式入口是 diaevo，例如 diaevo demo "
                "或 diaevo chat-test --interactive。"
            ),
        }
    ]
    chat_state = ChatConfigState()
    kg_mode = KGAnswerMode()

    while True:
        try:
            command = read_prompt()
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
        messages.append({"role": "user", "content": command})
        try:
            answer = _chat_turn_with_tools(messages, chat_state)
        except Exception as exc:
            print(f"chat error: {exc}")
            del messages[history_len:]
            continue
        print_assistant(answer)
