from __future__ import annotations

import os
import sys
from contextlib import nullcontext
from typing import Any

from .cli_style import CYAN, PURPLE, DIM, GLYPHS, RESET, _char_width, _display_width, _fit, _term_width

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows is the primary target.
    msvcrt = None

COMMANDS = [
    ("/learn", "从最近任务中总结候选 skill"),
    ("/skill", "查看或选择已有 skill"),
    ("/status", "查看工作区和最近学习结果"),
    ("/kg", "打开可编辑知识图谱"),
    ("/talk", "不中断主会话快速提问"),
    ("/image", "让视觉模型理解图片"),
    ("/debug", "查看高级调试命令"),
    ("/model", "设置本项目的 DEEPSEEK_MODEL"),
    ("/home", "重绘仪表盘"),
    ("/help", "显示本地命令"),
    ("/exit", "退出"),
]
HIDDEN_COMMAND_NAMES = (
    "/ingest",
    "/mine",
    "/kg_answer",
    "/recommend",
    "/generate",
    "/verify",
    "/self-evolve",
    "/self_evolve",
    "/demo",
    "/feedback",
    "/tools",
    "/tool",
    "/baseurl",
    "/key",
    "/vision-model",
    "/vision_model",
    "/visionmodel",
    "/vision-baseurl",
    "/vision_baseurl",
    "/visionbaseurl",
    "/vision-key",
    "/vision_key",
    "/visionkey",
)
COMMAND_NAMES = tuple(name for name, _ in COMMANDS) + HIDDEN_COMMAND_NAMES
COMMAND_MENU_PAGE_SIZE = 9
COMMANDS_REQUIRING_ARGUMENTS = {"/skill"}
_SKILL_MENU_CACHE: list[tuple[str, str]] | None = None
_PROMPT_SESSION: Any | None = None
_PROMPT_TOOLKIT_DISABLED = {"0", "false", "no", "off"}
_RAW_PROMPT_ENABLED = {"1", "true", "yes", "on"}


def _erase_lines(count: int) -> None:
    if count <= 0:
        return
    sys.stdout.write("\r\033[2K")
    for _ in range(count - 1):
        sys.stdout.write("\033[1A\r\033[2K")


def _matching_commands(value: str) -> list[tuple[str, str]]:
    query = value.splitlines()[0].lower() if value else ""
    if not query.startswith("/"):
        return []
    if any(char.isspace() for char in query):
        return []
    return [(name, desc) for name, desc in COMMANDS if name.startswith(query)]


def _load_skill_menu_items() -> list[tuple[str, str]]:
    global _SKILL_MENU_CACHE
    if _SKILL_MENU_CACHE is not None:
        return _SKILL_MENU_CACHE
    try:
        from diaevo.skill_context import skill_menu_items

        _SKILL_MENU_CACHE = skill_menu_items()
    except Exception:
        _SKILL_MENU_CACHE = []
    return _SKILL_MENU_CACHE


def _set_skill_menu_cache_for_tests(items: list[tuple[str, str]] | None) -> None:
    global _SKILL_MENU_CACHE
    _SKILL_MENU_CACHE = items


def _skill_query(value: str) -> str | None:
    first_line = value.splitlines()[0] if value else ""
    lower_line = first_line.lower()
    if lower_line == "/skill":
        return ""
    if lower_line.startswith("/skill "):
        return first_line[len("/skill ") :].strip()
    return None


def _selected_skill_description(value: str) -> str:
    query = _skill_query(value)
    if query is None or not query:
        return ""
    lowered = query.lower()
    for name, description in _load_skill_menu_items():
        if name.lower() == lowered:
            return description
    return ""


def _matching_skill_items(value: str) -> list[tuple[str, str]]:
    query = _skill_query(value)
    if query is None:
        return []
    lowered = query.lower()
    items = _load_skill_menu_items()
    if query and any(name.lower() == lowered for name, _ in items):
        return []
    if not lowered:
        return items
    return [
        (name, description)
        for name, description in items
        if lowered in name.lower() or lowered in description.lower()
    ]


def _menu_matches(value: str) -> list[tuple[str, str]]:
    skill_items = _matching_skill_items(value)
    if skill_items:
        return skill_items
    return _matching_commands(value)


def _menu_match_count(value: str) -> int:
    return len(_menu_matches(value))


def _command_menu_window(
    matches: list[tuple[str, str]],
    selected_index: int,
) -> tuple[int, list[tuple[str, str]], int]:
    if not matches:
        return 0, [], 0
    selected_index = max(0, min(selected_index, len(matches) - 1))
    visible_count = min(COMMAND_MENU_PAGE_SIZE, len(matches))
    max_offset = len(matches) - visible_count
    offset = max(0, selected_index - visible_count + 1)
    offset = min(offset, max_offset)
    return offset, matches[offset : offset + visible_count], selected_index


def _move_menu_selection(selected_index: int, match_count: int, delta: int) -> int:
    if match_count <= 0:
        return 0
    return (selected_index + delta) % match_count


def active_command_name(value: str) -> str:
    first_line = value.splitlines()[0] if value else ""
    lower_line = first_line.lower()
    for name in sorted(COMMAND_NAMES, key=len, reverse=True):
        if lower_line == name or lower_line.startswith(f"{name} "):
            return first_line[: len(name)]
    return ""


def is_command_input(value: str) -> bool:
    return bool(active_command_name(value))


def _submit_value(value: str, selected_index: int = 0) -> str:
    matches = _matching_commands(value)
    if matches and not active_command_name(value):
        selected_index = max(0, min(selected_index, len(matches) - 1))
        return matches[selected_index][0]
    return value.rstrip("\n")


def _menu_completion_value(value: str, selected_index: int = 0) -> str:
    skill_items = _matching_skill_items(value)
    if skill_items:
        selected_index = max(0, min(selected_index, len(skill_items) - 1))
        return f"/skill {skill_items[selected_index][0]}"
    matches = _matching_commands(value)
    if not matches or active_command_name(value):
        return value
    selected_index = max(0, min(selected_index, len(matches) - 1))
    return matches[selected_index][0] + " "


def _should_complete_menu_selection(value: str, selected_index: int = 0) -> bool:
    if _matching_skill_items(value):
        return True
    matches = _matching_commands(value)
    if not matches or active_command_name(value):
        return False
    selected_index = max(0, min(selected_index, len(matches) - 1))
    return matches[selected_index][0] in COMMANDS_REQUIRING_ARGUMENTS


def _highlight_command_line(line: str) -> str:
    name = active_command_name(line)
    if not name:
        return line
    return f"{PURPLE}{name}{RESET}{line[len(name):]}"


def _prompt_inner_width() -> int:
    width = min(max(72, _term_width() - 4), 144)
    return width - 2


def _wrap_plain_line(text: str, width: int) -> list[str]:
    if width <= 0:
        return [text]

    lines: list[str] = []
    current: list[str] = []
    used = 0
    for char in text:
        char_width = _char_width(char)
        if current and used + char_width > width:
            lines.append("".join(current))
            current = [char]
            used = char_width
            continue
        current.append(char)
        used += char_width
    lines.append("".join(current))
    return lines


def _wrapped_prompt_lines(value: str = "") -> list[tuple[str, str, bool]]:
    inner_width = _prompt_inner_width()
    value_lines = value.split("\n") or [""]
    rendered: list[tuple[str, str, bool]] = []
    for index, line in enumerate(value_lines):
        first_prefix = f"{GLYPHS['prompt']} " if index == 0 else "  "
        content_width = max(1, inner_width - _display_width(first_prefix))
        wrapped = _wrap_plain_line(line, content_width)
        for wrapped_index, segment in enumerate(wrapped):
            is_first_input_line = index == 0 and wrapped_index == 0
            prefix = first_prefix if wrapped_index == 0 else "  "
            rendered.append((prefix, segment, is_first_input_line))
    return rendered


def render_prompt_line(value: str = "") -> str:
    rendered = []
    for prefix, line, is_first_input_line in _wrapped_prompt_lines(value):
        visible_line = _highlight_command_line(line) if is_first_input_line else line
        rendered.append(f"{prefix}{visible_line}")
    return "\n".join(rendered)


def render_footer() -> str:
    return f"  {DIM}Enter 发送 {GLYPHS['dot']} Tab 补全 {GLYPHS['dot']} Esc 清空菜单{RESET}"


def render_plain_footer() -> str:
    return "Enter 发送 · Tab 补全 · /exit 退出"


def render_command_menu(value: str, selected_index: int = 0) -> str:
    matches = _menu_matches(value)
    if not matches:
        return ""
    offset, visible_matches, selected_index = _command_menu_window(matches, selected_index)
    width = min(max(72, _term_width() - 4), 144)
    name_width = max(_display_width(name) for name, _ in visible_matches) + 2
    lines = []
    for visible_index, (name, description) in enumerate(visible_matches):
        index = offset + visible_index
        padding = " " * max(1, name_width - _display_width(name))
        desc = _fit(description, width - name_width - 1)
        if index == selected_index:
            lines.append(f"{CYAN}{name}{RESET}{padding}{desc}")
        else:
            lines.append(f"{name}{padding}{DIM}{desc}{RESET}")
    return "\n".join(lines)


def render_skill_description(value: str) -> str:
    description = _selected_skill_description(value)
    if not description:
        return ""
    return f"{DIM}说明  {_fit(description, min(max(72, _term_width() - 4), 144) - 6)}{RESET}"


def render_prompt(value: str = "") -> str:
    menu = render_command_menu(value)
    pieces = [render_prompt_line(value)]
    if menu:
        pieces.append(menu)
    pieces.append(render_footer())
    return "\n".join(pieces)


def render_prompt_state(value: str = "", selected_index: int = 0) -> str:
    menu = render_command_menu(value, selected_index)
    pieces = [render_prompt_line(value)]
    if menu:
        pieces.append(menu)
    pieces.append(render_footer())
    return "\n".join(pieces)


def _cursor_position_in_wrapped_value(value: str, cursor_index: int | None = None) -> tuple[int, int]:
    cursor_index = len(value) if cursor_index is None else max(0, min(cursor_index, len(value)))
    before_cursor = value[:cursor_index]
    wrapped_before_cursor = _wrapped_prompt_lines(before_cursor)
    prefix, cursor_line, _ = wrapped_before_cursor[-1]
    return len(wrapped_before_cursor), _display_width(f"{prefix}{cursor_line}")


def _cursor_to_input(
    rendered_lines: int,
    value: str,
    lines_above_input: int = 0,
    cursor_index: int | None = None,
) -> str:
    input_lines = _wrapped_prompt_lines(value)
    cursor_line_number, right_moves = _cursor_position_in_wrapped_value(value, cursor_index)
    lines_below_input = max(0, rendered_lines - len(input_lines) - lines_above_input)
    lines_after_cursor = max(0, len(input_lines) - cursor_line_number)
    up_moves = lines_below_input + lines_after_cursor
    pieces = []
    if up_moves:
        pieces.append(f"\033[{up_moves}A")
    pieces.append("\r")
    if right_moves:
        pieces.append(f"\033[{right_moves}C")
    return "".join(pieces)


def _cursor_to_bottom(
    rendered_lines: int,
    value: str,
    lines_above_input: int = 0,
    cursor_index: int | None = None,
) -> str:
    input_line_count = len(_wrapped_prompt_lines(value))
    cursor_line_number, _right_moves = _cursor_position_in_wrapped_value(value, cursor_index)
    lines_below_input = max(0, rendered_lines - input_line_count - lines_above_input)
    lines_after_cursor = max(0, input_line_count - cursor_line_number)
    down_moves = lines_below_input + lines_after_cursor
    if down_moves:
        return f"\033[{down_moves}B\r"
    return "\r"


def read_prompt() -> str:
    if os.environ.get("DIAEVO_RAW_PROMPT", "").strip().lower() in _RAW_PROMPT_ENABLED:
        return _read_prompt_raw()
    if _prompt_toolkit_enabled():
        value = _read_prompt_toolkit()
        if value is not None:
            return value
    return input(f"{GLYPHS['prompt']} ")


def _prompt_toolkit_enabled() -> bool:
    value = os.environ.get("DIAEVO_PROMPT_TOOLKIT", "").strip().lower()
    return value not in _PROMPT_TOOLKIT_DISABLED


def _read_prompt_toolkit() -> str | None:
    try:
        session = _prompt_session()
    except Exception:
        return None
    while True:
        try:
            try:
                with _prompt_stdout_patch():
                    text = session.prompt()
            except Exception:
                text = session.prompt()
        except (EOFError, KeyboardInterrupt):
            print("输入 /exit 退出。")
            continue
        return _submit_value(text, 0).strip()


def _prompt_stdout_patch():
    try:
        from prompt_toolkit.patch_stdout import patch_stdout
    except Exception:
        return nullcontext()
    return patch_stdout()


def _prompt_session():
    global _PROMPT_SESSION
    if _PROMPT_SESSION is not None:
        return _PROMPT_SESSION
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.completion import CompleteEvent
        from prompt_toolkit.document import Document
    except Exception as exc:  # pragma: no cover - dependency may be absent in minimal installs.
        raise RuntimeError("prompt_toolkit is not available") from exc

    class DiaEvoCompleter(Completer):
        def get_completions(self, document: Document, complete_event: CompleteEvent):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            if "\n" in text:
                return
            for value, description in _completion_items(text):
                yield Completion(value, start_position=-len(text), display=value, display_meta=description)

    _PROMPT_SESSION = PromptSession(
        message=f"{GLYPHS['prompt']} ",
        completer=DiaEvoCompleter(),
        complete_while_typing=True,
        bottom_toolbar=render_plain_footer,
        reserve_space_for_menu=8,
    )
    return _PROMPT_SESSION


def _completion_items(value: str) -> list[tuple[str, str]]:
    skill_query = _skill_query(value)
    if skill_query is not None:
        return [(f"/skill {name}", description) for name, description in _matching_skill_items(value)]
    if any(char.isspace() for char in value):
        return []
    return [(name, description) for name, description in _matching_commands(value)]


def _read_prompt_raw() -> str:
    if msvcrt is None or not sys.stdin.isatty():
        return input(f"{GLYPHS['prompt']} ")

    value = ""
    rendered_value = ""
    selected_index = 0
    rendered_lines = 0

    def redraw() -> None:
        nonlocal rendered_lines, rendered_value
        if rendered_lines:
            sys.stdout.write(_cursor_to_bottom(rendered_lines, rendered_value))
            _erase_lines(rendered_lines)
        rendered = render_prompt_state(value, selected_index)
        rendered_lines = rendered.count("\n") + 1
        rendered_value = value
        sys.stdout.write(rendered)
        sys.stdout.write(_cursor_to_input(rendered_lines, value))
        sys.stdout.flush()

    redraw()
    while True:
        char = msvcrt.getwch()
        if char == "\r":
            if not value.strip():
                redraw()
                continue
            if _should_complete_menu_selection(value, selected_index):
                value = _menu_completion_value(value, selected_index)
                selected_index = 0
                redraw()
                continue
            value = _submit_value(value, selected_index)
            sys.stdout.write(_cursor_to_bottom(rendered_lines, rendered_value))
            _erase_lines(rendered_lines)
            sys.stdout.flush()
            return value
        if char in {"\003", "\032"}:
            redraw()
            continue
        if char == "\n":
            if not value or value.endswith("\n"):
                continue
            value += "\n"
            selected_index = 0
            redraw()
            continue
        if char == "\b":
            value = value[:-1]
            selected_index = 0
            redraw()
            continue
        if char in {"\x00", "\xe0"}:
            key = msvcrt.getwch()
            match_count = _menu_match_count(value)
            if match_count and key == "H":
                selected_index = _move_menu_selection(selected_index, match_count, -1)
                redraw()
            elif match_count and key == "P":
                selected_index = _move_menu_selection(selected_index, match_count, 1)
                redraw()
            continue
        if char == "\t":
            if _menu_match_count(value):
                value = _menu_completion_value(value, selected_index)
                selected_index = 0
                redraw()
            continue
        if char == "\x1b":
            value = ""
            selected_index = 0
            redraw()
            continue
        if char.isprintable():
            value += char
            selected_index = 0
            redraw()
