from __future__ import annotations

import shlex
import getpass
from dataclasses import dataclass
from typing import Callable

from diaevo.cli import main as cli_main
from diaevo.deepseek_chat import chat_completion, config_from_env, extract_assistant_text
from diaevo.env import write_env_value
from diaevo.tool_layer import execute_tool, parse_tool_arg_pairs, parse_tool_args, tool_schemas
from diaevo.tool_chat import (
    assistant_message_for_history,
    chat_tool_schemas,
    extract_assistant_message,
    requested_tool_calls,
    tool_result_message_for_call,
)

from .cli_style import maybe_show_trust_dialog
from .output_policy import print_assistant, print_status
from .prompt_bar import is_command_input, read_prompt
from .progress import status
from .terminal_home import render_plain
from .tool_render import render_tool_result

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
  /model <name>            设置 DEEPSEEK_MODEL 并重绘仪表盘
  /baseurl <url>           设置 DEEPSEEK_BASE_URL
  /key <api-key>           设置 DEEPSEEK_API_KEY，后续不回显密钥
  /home                    重绘仪表盘
  /help                    显示帮助
  /exit                    退出

其他输入会作为普通聊天消息发送给 DeepSeek。
""".strip()

MAX_TOOL_ROUNDS = 5


class ChatConfigState:
    def __init__(self) -> None:
        self.value = None

    def reset(self) -> None:
        self.value = None


@dataclass
class KGAnswerMode:
    enabled: bool = False
    vector_backend: str = "dense"
    strict: bool = True
    max_paths: int = 5


def _run(argv: list[str]) -> None:
    label = argv[0] if argv else "command"
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


def _dispatch_command(command: str, chat_state: ChatConfigState, kg_mode: KGAnswerMode | None = None) -> bool:
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
    if name == "model":
        _set_env_command("DEEPSEEK_MODEL", " ".join(rest), chat_state, prompt="DEEPSEEK_MODEL")
        print(render_plain())
        print(HOME_PROMPT_GAP, end="")
        return True
    if name == "baseurl":
        _set_env_command("DEEPSEEK_BASE_URL", " ".join(rest), chat_state, prompt="DEEPSEEK_BASE_URL")
        return True
    if name == "key":
        _set_env_command("DEEPSEEK_API_KEY", " ".join(rest), chat_state, prompt="DEEPSEEK_API_KEY", secret=True)
        return True
    if name in shortcuts:
        _run(shortcuts[name](rest))
        return True

    print(f"未知命令：/{name}")
    print("输入 `/help` 查看可用命令")
    return True


def _approval_prompt(tool_name: str) -> bool:
    answer = input(f"批准执行 {tool_name}？[y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _execute_model_tool_call(call, *, turn_id: str) -> dict[str, object]:
    if "__parse_error__" in call.args:
        result = {"status": "error", "tool": call.name, "error": call.args["__parse_error__"]}
        print(render_tool_result(result))
        return result

    with status(f"正在执行 {call.name}"):
        result = execute_tool(call.name, call.args, turn_id=turn_id)
    print(render_tool_result(result))
    if result.get("status") != "requires_approval":
        return result

    if not _approval_prompt(call.name):
        denied = {
            "status": "denied",
            "tool": call.name,
            "message": "User denied approval for this tool call.",
            "preview": result.get("preview", {}),
        }
        print(render_tool_result(denied))
        return denied

    with status(f"正在执行 {call.name}"):
        approved = execute_tool(call.name, call.args, approve=True, turn_id=turn_id)
    print(render_tool_result(approved))
    return approved


def _chat_turn_with_tools(messages: list[dict[str, object]], chat_state: ChatConfigState) -> str:
    if chat_state.value is None:
        chat_state.value = config_from_env(max_tokens=4096, no_thinking=True)

    tools = chat_tool_schemas()
    for round_index in range(MAX_TOOL_ROUNDS):
        with status("正在请求模型"):
            response = chat_completion(messages, chat_state.value, tools=tools)
        message = extract_assistant_message(response)
        calls = requested_tool_calls(message)
        if not calls:
            answer = extract_assistant_text(response)
            messages.append({"role": "assistant", "content": answer})
            return answer

        messages.append(assistant_message_for_history(message))
        turn_id = str(response.get("id") or f"chat-turn-{round_index}")
        for call in calls:
            result = _execute_model_tool_call(call, turn_id=turn_id)
            messages.append(tool_result_message_for_call(call, result))

    answer = "tool loop stopped: too many consecutive tool rounds."
    messages.append({"role": "assistant", "content": answer})
    return answer


def _kg_answer_turn(prompt: str, kg_mode: KGAnswerMode) -> str:
    args = {
        "query": prompt,
        "strict": kg_mode.strict,
        "max_paths": kg_mode.max_paths,
        "vector_backend": kg_mode.vector_backend,
    }
    with status("正在执行 kg_answer"):
        result = execute_tool("kg_answer", args)
    print(render_tool_result(result))
    return str(result.get("answer") or "")

def main() -> int:
    if not maybe_show_trust_dialog():
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
                "/baseurl <url>、/key <api-key>、/home、/help、/exit。"
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
            return 0
        if not command:
            continue
        if is_command_input(command):
            if not _dispatch_command(command, chat_state, kg_mode):
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
