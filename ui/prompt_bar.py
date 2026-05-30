from __future__ import annotations

import sys

from .cli_style import PURPLE, DIM, GLYPHS, RESET, _char_width, _display_width, _fit, _term_width

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows is the primary target.
    msvcrt = None

COMMANDS = [
    ("/ingest", "导入 data/sample_traces.jsonl"),
    ("/mine", "运行挖掘流程"),
    ("/kg", "打开可编辑知识图谱"),
    ("/kg_answer", "开关 KG 图向量检索回答"),
    ("/recommend", "按任务推荐技能"),
    ("/generate", "生成候选 SKILL.md"),
    ("/verify", "验证候选技能"),
    ("/demo", "运行完整 MVP 演示"),
    ("/feedback", "将工具事件回灌为轨迹"),
    ("/tools", "列出本地工具说明"),
    ("/tool", "用 JSON 参数运行本地工具"),
    ("/talk", "不中断主会话快速提问"),
    ("/model", "设置本项目的 DEEPSEEK_MODEL"),
    ("/baseurl", "设置本项目的 DEEPSEEK_BASE_URL"),
    ("/key", "设置本项目的 DEEPSEEK_API_KEY"),
    ("/home", "重绘仪表盘"),
    ("/help", "显示本地命令"),
    ("/exit", "退出"),
]
COMMAND_NAMES = tuple(name for name, _ in COMMANDS)
COMMAND_MENU_PAGE_SIZE = 9


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
    return f"  {DIM}Enter 运行命令或当前菜单项 {GLYPHS['dot']} Ctrl+J 换行 {GLYPHS['dot']} ? 查看快捷键{RESET}"


def render_command_menu(value: str, selected_index: int = 0) -> str:
    matches = _matching_commands(value)
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
            lines.append(f"{PURPLE}{name}{padding}{desc}{RESET}")
        else:
            lines.append(f"{name}{padding}{DIM}{desc}{RESET}")
    return "\n".join(lines)


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


def _cursor_to_input(rendered_lines: int, value: str) -> str:
    input_lines = _wrapped_prompt_lines(value)
    prefix, last_line, _ = input_lines[-1]
    lines_below_input = max(0, rendered_lines - len(input_lines))
    right_moves = _display_width(f"{prefix}{last_line}")
    return f"\033[{lines_below_input}A\r\033[{right_moves}C"


def _cursor_to_bottom(rendered_lines: int, value: str) -> str:
    input_line_count = len(_wrapped_prompt_lines(value))
    lines_below_input = max(0, rendered_lines - input_line_count)
    return f"\033[{lines_below_input}B\r"


def read_prompt() -> str:
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
            value = _submit_value(value, selected_index)
            sys.stdout.write(_cursor_to_bottom(rendered_lines, value))
            sys.stdout.write("\n")
            sys.stdout.flush()
            return value
        if char == "\003":
            raise KeyboardInterrupt
        if char == "\032":
            raise EOFError
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
            matches = _matching_commands(value)
            if matches and key == "H":
                selected_index = _move_menu_selection(selected_index, len(matches), -1)
                redraw()
            elif matches and key == "P":
                selected_index = _move_menu_selection(selected_index, len(matches), 1)
                redraw()
            continue
        if char == "\t":
            matches = _matching_commands(value)
            if matches:
                value = matches[selected_index][0] + " "
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
