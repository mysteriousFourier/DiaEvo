from __future__ import annotations

import re
import shutil
import textwrap
import unicodedata
import os
from pathlib import Path

from skillminer.paths import PROJECT_ROOT, REPORTS_DIR
from skillminer.env import load_env
from skillminer.storage import read_json, write_json

ESC = "\033["
DIM = f"{ESC}2m"
BOLD = f"{ESC}1m"
ITALIC = f"{ESC}3m"
RESET = f"{ESC}0m"
BLUE = f"{ESC}38;5;111m"
PURPLE = f"{ESC}38;5;141m"
WHITE = f"{ESC}38;5;255m"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

GLYPHS = {
    "h": "\u2500",
    "v": "\u2502",
    "tl": "\u256d",
    "tr": "\u256e",
    "bl": "\u2570",
    "br": "\u256f",
    "mid": "\u2502",
    "prompt": "\u276f",
    "dot": "\u00b7",
}


def _term_width(default: int = 120) -> int:
    return max(80, shutil.get_terminal_size((default, 30)).columns)


def _char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


def _display_width(text: str) -> int:
    clean = ANSI_RE.sub("", text)
    return sum(_char_width(char) for char in clean)


def _plain_len(text: str) -> int:
    return _display_width(text)


def _pad(text: str, width: int, *, align: str = "left") -> str:
    visible = _plain_len(text)
    missing = max(0, width - visible)
    if align == "center":
        left = missing // 2
        return f"{' ' * left}{text}{' ' * (missing - left)}"
    if align == "right":
        return f"{' ' * missing}{text}"
    return f"{text}{' ' * missing}"


def _truncate(text: str, width: int) -> str:
    clean = ANSI_RE.sub("", str(text))
    if _display_width(clean) <= width:
        return clean
    result = []
    used = 0
    suffix_width = 3
    for char in clean:
        char_width = _char_width(char)
        if used + char_width + suffix_width > width:
            break
        result.append(char)
        used += char_width
    return "".join(result) + "..."


def _fit(text: str, width: int) -> str:
    if _plain_len(text) <= width:
        return text
    return _truncate(text, width)


def _frame_line(left: str, right: str, width: int, title: str = "") -> str:
    if title:
        title_width = _plain_len(title)
        tail = GLYPHS["h"] * max(0, width - title_width - 4)
        return f"{BLUE}{left}{GLYPHS['h']} {RESET}{title}{BLUE}{tail}{right}{RESET}"
    return f"{BLUE}{left}{GLYPHS['h'] * (width - 2)}{right}{RESET}"


def whale_lines() -> list[str]:
    return [
        f"{BLUE}    \u2580\u2588\u2580{RESET}",
        f"{BLUE} \u259f\u2599 \u259f\u259b\u2588\u2588\u2588\u2588\u259c\u258c{RESET}",
        f"{BLUE} \u259c\u259b{WHITE}\u259d\u259c\u2588\u2588\u2588\u2588\u2588\u259b\u2598{RESET}"
    ]


def _load_stats() -> tuple[dict, dict, dict]:
    ingest = read_json(REPORTS_DIR / "ingest_summary.json", default={}) or {}
    mining = read_json(REPORTS_DIR / "mining_report.json", default={}) or {}
    recommendations = read_json(REPORTS_DIR / "recommendations.json", default={}) or {}
    return ingest, mining, recommendations


def _feed_lines(ingest: dict, mining: dict, recommendations: dict) -> list[str]:
    clusters = mining.get("clusters", []) if isinstance(mining.get("clusters", []), list) else []
    recs = recommendations.get("recommendations", []) if isinstance(recommendations.get("recommendations", []), list) else []
    trace_count = ingest.get("processed_count") or mining.get("trace_count") or 0
    rule_count = len(mining.get("association_rules", []) or [])
    sequence_count = len(mining.get("frequent_sequences", []) or [])

    lines = [
        f"{BLUE}{BOLD}Tips for getting started{RESET}",
        "Run /ingest to load traces, then /mine to build skill memory",
        "",
        f"{BLUE}{BOLD}Current workspace{RESET}",
        f"Traces: {trace_count}    Clusters: {len(clusters)}",
        f"Rules: {rule_count}     Sequences: {sequence_count}",
        "",
    ]
    if clusters:
        lines.append(f"{BLUE}{BOLD}Mining snapshot{RESET}")
        for cluster in clusters[:3]:
            lines.append(
                f"{cluster.get('id', '')}  gap {cluster.get('coverage_gap', '')}  "
                f"{_truncate(cluster.get('representative_task', ''), 48)}"
            )
    else:
        lines.extend(
            [
                f"{BLUE}{BOLD}What's new{RESET}",
                "Claude Code-style trust dialog and startup card",
                "DeepSeek V4 Pro chat is available through .env",
                f"{ITALIC}/demo for more{RESET}",
            ]
        )

    if recs:
        lines.extend(["", f"{BLUE}{BOLD}Recommended skills{RESET}"])
        for rec in recs[:2]:
            lines.append(f"{rec.get('skill', '')}  score {rec.get('score', '')}")
    return lines


def render_logo_card() -> str:
    load_env()
    ingest, mining, recommendations = _load_stats()
    model_name = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro"
    width = min(max(72, _term_width() - 4), 144)
    left_width = 38
    right_width = width - left_width - 5
    title = f"{BLUE}{BOLD}SkillMiner{RESET} {DIM}v0.1.0{RESET}"
    divider = f"{BLUE}{GLYPHS['mid']}{RESET}"
    left = [
        "",
        f"{BOLD}Welcome back!{RESET}",
        "",
        *whale_lines(),
        "",
        f"{DIM}{_truncate(model_name, left_width - 18)} {GLYPHS['dot']} Skill Mining{RESET}",
        f"{DIM}{_truncate(str(PROJECT_ROOT), left_width - 2)}{RESET}",
    ]
    feed = _feed_lines(ingest, mining, recommendations)
    height = max(len(left), len(feed), 10)

    lines = [_frame_line(GLYPHS["tl"], GLYPHS["tr"], width, title)]
    for index in range(height):
        left_text = left[index] if index < len(left) else ""
        right_text = feed[index] if index < len(feed) else ""
        lines.append(
            f"{BLUE}{GLYPHS['v']}{RESET}{_pad(left_text, left_width, align='center')} "
            f"{divider} {_pad(_fit(right_text, right_width), right_width)}{BLUE}{GLYPHS['v']}{RESET}"
        )
    lines.append(_frame_line(GLYPHS["bl"], GLYPHS["br"], width))
    return "\n".join(lines)


def render_prompt_box() -> str:
    width = min(max(72, _term_width() - 4), 144)
    lines = [
        f"{DIM}{GLYPHS['h'] * width}{RESET}",
        f"{GLYPHS['v']} {GLYPHS['prompt']} {' ' * (width - 4)}{GLYPHS['v']}",
        f"{DIM}{GLYPHS['h'] * width}{RESET}",
        f"  {DIM}? for shortcuts{RESET}",
    ]
    return "\n".join(lines)


def render_home() -> str:
    return render_logo_card()


def trust_state_path() -> Path:
    return PROJECT_ROOT / ".skillminer" / "trust.json"


def has_trusted_workspace() -> bool:
    state = read_json(trust_state_path(), default={}) or {}
    return state.get("trusted") is True and state.get("path") == str(PROJECT_ROOT)


def save_trusted_workspace() -> None:
    write_json(trust_state_path(), {"trusted": True, "path": str(PROJECT_ROOT)})


def render_trust_dialog(selected: int = 1) -> str:
    width = min(max(72, _term_width() - 4), 120)
    body_width = width - 4
    title = f"{BLUE}Accessing workspace:{RESET}"
    paragraphs = [
        str(PROJECT_ROOT),
        "",
        "Quick safety check: Is this a project you created or one you trust? "
        "(Like your own code, a well-known open source project, or work from your team). "
        "If not, take a moment to review what's in this folder first.",
        "",
        "SkillMiner can read project traces, generate candidate skills, and run local verification commands here.",
        "",
        "Security guide",
        "",
        f"{GLYPHS['prompt'] if selected == 1 else ' '} 1. Yes, I trust this folder",
        f"{GLYPHS['prompt'] if selected == 2 else ' '} 2. No, exit",
        "",
        f"{DIM}Enter to confirm {GLYPHS['dot']} Esc/Ctrl+C to cancel{RESET}",
    ]

    lines = [_frame_line(GLYPHS["tl"], GLYPHS["tr"], width, title)]
    for paragraph in paragraphs:
        wrapped = textwrap.wrap(paragraph, width=body_width) if paragraph else [""]
        for line in wrapped:
            lines.append(f"{BLUE}{GLYPHS['v']}{RESET} {_pad(line, body_width)} {BLUE}{GLYPHS['v']}{RESET}")
    lines.append(_frame_line(GLYPHS["bl"], GLYPHS["br"], width))
    return "\n".join(lines)


def maybe_show_trust_dialog() -> bool:
    if has_trusted_workspace():
        return True
    print(render_trust_dialog())
    while True:
        choice = input("Select 1 or 2 [1]: ").strip().lower()
        if choice in {"", "1", "y", "yes"}:
            save_trusted_workspace()
            return True
        if choice in {"2", "n", "no", "exit", "q"}:
            return False
        print("Please choose 1 or 2.")
