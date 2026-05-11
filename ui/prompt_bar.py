from __future__ import annotations

import sys

from .cli_style import PURPLE, DIM, GLYPHS, RESET, _display_width, _fit, _term_width

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows is the primary target.
    msvcrt = None

COMMANDS = [
    ("/ingest", "Load data/sample_traces.jsonl"),
    ("/mine", "Run mining pipeline"),
    ("/recommend", "Recommend skills for a task"),
    ("/generate", "Generate candidate SKILL.md"),
    ("/verify", "Verify candidate skill"),
    ("/demo", "Run full MVP demo"),
    ("/tools", "List local tool schemas"),
    ("/tool", "Run a local tool with JSON args"),
    ("/model", "Set DEEPSEEK_MODEL for this project"),
    ("/baseurl", "Set DEEPSEEK_BASE_URL for this project"),
    ("/key", "Set DEEPSEEK_API_KEY for this project"),
    ("/home", "Redraw dashboard"),
    ("/help", "Show local commands"),
    ("/exit", "Quit"),
]
COMMAND_NAMES = tuple(name for name, _ in COMMANDS)


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
    return [(name, desc) for name, desc in COMMANDS if name.startswith(query)][:9]


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


def render_prompt_line(value: str = "") -> str:
    width = min(max(72, _term_width() - 4), 144)
    inner_width = width - 2
    value_lines = value.split("\n") or [""]
    rendered = [f"{DIM}{GLYPHS['h'] * width}{RESET}"]
    for index, line in enumerate(value_lines):
        prefix = f"{GLYPHS['prompt']} " if index == 0 else "  "
        visible_line = _highlight_command_line(line) if index == 0 else line
        rendered.append(_fit(f"{prefix}{visible_line}", inner_width))
    rendered.append(f"{DIM}{GLYPHS['h'] * width}{RESET}")
    return "\n".join(rendered)


def render_footer() -> str:
    return f"  {DIM}Enter runs command or selected menu {GLYPHS['dot']} Ctrl+J newline {GLYPHS['dot']} ? for shortcuts{RESET}"


def render_command_menu(value: str, selected_index: int = 0) -> str:
    matches = _matching_commands(value)
    if not matches:
        return ""
    selected_index = max(0, min(selected_index, len(matches) - 1))
    width = min(max(72, _term_width() - 4), 144)
    name_width = max(_display_width(name) for name, _ in matches) + 2
    lines = []
    for index, (name, description) in enumerate(matches):
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
    value_lines = value.split("\n") or [""]
    last_line = value_lines[-1]
    lines_below_input = max(0, rendered_lines - 1 - len(value_lines))
    prefix = f"{GLYPHS['prompt']} " if len(value_lines) == 1 else "  "
    right_moves = _display_width(f"{prefix}{last_line}")
    return f"\033[{lines_below_input}A\r\033[{right_moves}C"


def _cursor_to_bottom(rendered_lines: int, value: str) -> str:
    value_lines = value.split("\n") or [""]
    lines_below_input = max(0, rendered_lines - 1 - len(value_lines))
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
                selected_index = (selected_index - 1) % len(matches)
                redraw()
            elif matches and key == "P":
                selected_index = (selected_index + 1) % len(matches)
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
