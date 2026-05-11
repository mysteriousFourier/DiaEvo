from __future__ import annotations

import shlex
import getpass
from typing import Callable

from skillminer.cli import main as cli_main
from skillminer.deepseek_chat import chat_completion, config_from_env, extract_assistant_text
from skillminer.env import write_env_value

from .cli_style import maybe_show_trust_dialog
from .prompt_bar import is_command_input, read_prompt
from .terminal_home import render_plain

DEFAULT_RECOMMEND_TASK = "给当前项目生成测试修复 skill"

HELP_TEXT = """
Commands:
  /ingest                  Load data/sample_traces.jsonl
  /mine                    Run mining pipeline
  /recommend <task>        Recommend skills for a task
  /generate <cluster-id>   Generate candidate SKILL.md
  /verify <cluster-id/path> Verify candidate skill
  /demo                    Run full MVP demo
  /model <name>            Set DEEPSEEK_MODEL and redraw dashboard
  /baseurl <url>           Set DEEPSEEK_BASE_URL
  /key <api-key>           Set DEEPSEEK_API_KEY without echoing it later
  /home                    Redraw dashboard
  /help                    Show this help
  /exit                    Quit

Anything else is sent to DeepSeek as a normal chat message.
""".strip()


class ChatConfigState:
    def __init__(self) -> None:
        self.value = None

    def reset(self) -> None:
        self.value = None


def _run(argv: list[str]) -> None:
    code = cli_main(argv)
    if code:
        print(f"command exited with code {code}")


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


def _dispatch_command(command: str, chat_state: ChatConfigState) -> bool:
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
        "recommend": lambda args: ["recommend", "--task", " ".join(args) if args else DEFAULT_RECOMMEND_TASK],
        "generate": lambda args: ["generate", "--cluster-id", args[0] if args else "C03"],
        "verify": lambda args: ["verify", "--skill", args[0] if args else "outputs/candidate_skills/C03"],
        "demo": lambda args: ["demo", *args],
        "chat": lambda args: ["chat-test", "--interactive", *args],
    }

    if name in {"exit", "quit", "q"}:
        return False
    if name in {"help", "?"}:
        print(HELP_TEXT)
        return True
    if name in {"home", "dashboard"}:
        print(render_plain())
        return True
    if name == "model":
        _set_env_command("DEEPSEEK_MODEL", " ".join(rest), chat_state, prompt="DEEPSEEK_MODEL")
        print(render_plain())
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

    print(f"unknown command: /{name}")
    print("type `/help` for commands")
    return True


def main() -> int:
    if not maybe_show_trust_dialog():
        return 1

    print(render_plain())
    messages = [
        {
            "role": "system",
            "content": (
                "You are SkillMiner's terminal assistant. SkillMiner mines Agent SKILL.md workflows from "
                "task traces; it is not a recruiting or resume tool. Answer in the user's language, be "
                "concise, and never invent command names. The exact local interactive slash commands are: "
                "/ingest, /mine, /recommend <task>, /generate <cluster-id>, /verify <cluster-id/path>, "
                "/demo, /home, /help, /exit. The exact scriptable PowerShell launcher is .\\skillminer.ps1, "
                "for example .\\skillminer.ps1 demo or .\\skillminer.ps1 chat-test --interactive."
            ),
        }
    ]
    chat_state = ChatConfigState()

    while True:
        try:
            command = read_prompt()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not command:
            continue
        if is_command_input(command):
            if not _dispatch_command(command, chat_state):
                return 0
            continue

        if chat_state.value is None:
            try:
                chat_state.value = config_from_env(max_tokens=4096, no_thinking=True)
            except Exception as exc:
                print(f"chat unavailable: {exc}")
                print("Use `/help` for local commands, or fix `.env` and try again.")
                continue

        messages.append({"role": "user", "content": command})
        try:
            response = chat_completion(messages, chat_state.value)
            answer = extract_assistant_text(response)
        except Exception as exc:
            print(f"chat error: {exc}")
            messages.pop()
            continue
        messages.append({"role": "assistant", "content": answer})
        print(answer)
