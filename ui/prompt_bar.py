from __future__ import annotations

import sys

from .claude_style import BLUE, DIM, GLYPHS, RESET, _display_width, _fit, _term_width

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
    ("/home", "Redraw dashboard"),
    ("/help", "Show local commands"),
    ("/exit", "Quit"),
]


def _erase_lines(count: int) -> None:
    if count <= 0:
        return
    sys.stdout.write("\r\033[2K")
    for _ in range(count - 1):
        sys.stdout.write("\033[1A\r\033[2K")


def _matching_commands(value: str) -> list[tuple[str, str]]:
    query = value.strip().lower()
    if not query.startswith("/"):
        return []
    return [(name, desc) for name, desc in COMMANDS if name.startswith(query)][:9]


def render_prompt_line(value: str = "") -> str:
    width = min(max(72, _term_width() - 4), 144)
    inner_width = width - 2
    visible = f"{GLYPHS['prompt']} {value}"
    return f"{DIM}{GLYPHS['h'] * width}{RESET}\n{_fit(visible, inner_width)}\n{DIM}{GLYPHS['h'] * width}{RESET}"


def render_footer() -> str:
    return f"  {DIM}? for shortcuts{RESET}"


def render_command_menu(value: str) -> str:
    matches = _matching_commands(value)
    if not matches:
        return ""
    width = min(max(72, _term_width() - 4), 144)
    name_width = max(_display_width(name) for name, _ in matches) + 2
    lines = []
    for index, (name, description) in enumerate(matches):
        padding = " " * max(1, name_width - _display_width(name))
        desc = _fit(description, width - name_width - 1)
        if index == 0:
            lines.append(f"{BLUE}{name}{padding}{desc}{RESET}")
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


def read_prompt() -> str:
    if msvcrt is None or not sys.stdin.isatty():
        return input(f"{GLYPHS['prompt']} ").strip()

    value = ""
    rendered_lines = 0

    def redraw() -> None:
        nonlocal rendered_lines
        if rendered_lines:
            _erase_lines(rendered_lines)
        rendered = render_prompt(value)
        rendered_lines = rendered.count("\n") + 1
        sys.stdout.write(rendered)
        sys.stdout.flush()

    redraw()
    while True:
        char = msvcrt.getwch()
        if char in {"\r", "\n"}:
            sys.stdout.write("\n")
            sys.stdout.flush()
            return value.strip()
        if char == "\003":
            raise KeyboardInterrupt
        if char == "\032":
            raise EOFError
        if char == "\b":
            value = value[:-1]
            redraw()
            continue
        if char in {"\x00", "\xe0"}:
            msvcrt.getwch()
            continue
        if char == "\t":
            matches = _matching_commands(value)
            if matches:
                value = matches[0][0] + " "
                redraw()
            continue
        if char == "\x1b":
            value = ""
            redraw()
            continue
        if char.isprintable():
            value += char
            redraw()
