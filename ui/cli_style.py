from __future__ import annotations

import re
import shutil
import unicodedata
import os
from pathlib import Path

from diaevo.paths import DIAEVO_DIR, WORKSPACE_ROOT, REPORTS_DIR
from diaevo.env import load_env
from diaevo.storage import read_json, write_json

ESC = "\033["
DIM = f"{ESC}2m"
BOLD = f"{ESC}1m"
ITALIC = f"{ESC}3m"
RESET = f"{ESC}0m"
BLUE = f"{ESC}38;5;111m"
PURPLE = f"{ESC}38;5;141m"
WHITE = f"{ESC}38;5;255m"
CYAN = f"{ESC}38;5;45m"
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


def _wrap_display_line(text: str, width: int) -> list[str]:
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
    return lines + (["".join(current)] if current else [])


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _metric(name: str, value: object) -> str:
    return f"{name} {value}"


def _percent(value: object) -> str:
    return f"{_as_float(value) * 100:.0f}%"


def _top_items(value: object, *, limit: int = 3) -> str:
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: (-_as_int(item[1]), str(item[0])))[:limit]
        return ", ".join(f"{key} {count}" for key, count in items)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value[:limit] if str(item).strip())
    return ""


def _frame_line(left: str, right: str, width: int, title: str = "") -> str:
    if title:
        title_width = _plain_len(title)
        tail = GLYPHS["h"] * max(0, width - title_width - 4)
        return f"{BLUE}{left}{GLYPHS['h']} {RESET}{title}{BLUE}{tail}{right}{RESET}"
    return f"{BLUE}{left}{GLYPHS['h'] * (width - 2)}{right}{RESET}"


def whale_lines() -> list[str]:
    return [
        f"{CYAN}    \u2580\u2588\u2580{RESET}",
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
    trace_count = ingest.get("trace_count") or ingest.get("processed_count") or mining.get("trace_count") or 0
    success_rate = ingest.get("success_rate", "")
    rule_count = len(mining.get("association_rules", []) or [])
    sequence_count = len(mining.get("frequent_sequences", []) or [])
    top_languages = _top_items(ingest.get("languages", {}), limit=2)
    top_tools = _top_items(ingest.get("top_tools", {}), limit=3)

    lines = [
        f"{BLUE}{BOLD}开始使用{RESET}",
        "先运行 /ingest 导入轨迹，再运行 /mine 构建技能记忆",
        "",
        f"{BLUE}{BOLD}当前工作区{RESET}",
        "  ".join(
            [
                _metric("轨迹", trace_count),
                _metric("簇", len(clusters)),
                _metric("规则", rule_count),
                _metric("序列", sequence_count),
            ]
        ),
        f"成功率 {_percent(success_rate) if success_rate != '' else '--'}"
        + (f"  语言 {top_languages}" if top_languages else ""),
        f"工具 {top_tools}" if top_tools else "工具 --",
        "",
        f"{BLUE}{BOLD}下一步{RESET}",
        "/mine 刷新挖掘报告",
        "/recommend <任务> 推荐可复用技能",
        "/tools 查看本地工具说明",
    ]
    return lines


def render_logo_card() -> str:
    load_env()
    ingest, mining, recommendations = _load_stats()
    model_name = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro"
    width = min(max(72, _term_width() - 4), 144)
    left_width = 38
    column_gap = "   "
    right_width = width - left_width - len(column_gap)
    title = f"{BLUE}{BOLD}DiaEvo{RESET} {DIM}v0.1.0{RESET}"
    left = [
        "",
        f"{BOLD}欢迎回来{RESET}",
        "",
        *whale_lines(),
        f"{DIM}{_truncate(model_name, left_width - 2)}{RESET}",
        f"{DIM}{_truncate(str(WORKSPACE_ROOT), left_width - 2)}{RESET}",
        title,
        "",
    ]
    feed = _feed_lines(ingest, mining, recommendations)
    height = max(len(left), len(feed), 10)

    lines = []
    for index in range(height):
        left_text = left[index] if index < len(left) else ""
        right_text = feed[index] if index < len(feed) else ""
        lines.append(
            f"{_pad(left_text, left_width, align='center')}"
            f"{column_gap}{_pad(_fit(right_text, right_width), right_width)}"
        )
    return "\n".join(lines)


def render_prompt_box() -> str:
    width = min(max(72, _term_width() - 4), 144)
    lines = [
        f"{GLYPHS['prompt']} {' ' * max(0, width - 2)}",
        f"  {DIM}? 查看快捷键{RESET}",
    ]
    return "\n".join(lines)


def render_home() -> str:
    return render_logo_card()


def trust_state_path() -> Path:
    return DIAEVO_DIR / "trust.json"


def has_trusted_workspace() -> bool:
    state = read_json(trust_state_path(), default={}) or {}
    return state.get("trusted") is True and state.get("path") == str(WORKSPACE_ROOT)


def save_trusted_workspace() -> None:
    write_json(trust_state_path(), {"trusted": True, "path": str(WORKSPACE_ROOT)})


def render_trust_dialog(selected: int = 1) -> str:
    width = min(max(72, _term_width() - 4), 120)
    body_width = width - 4
    title = f"{BLUE}正在访问工作区：{RESET}"
    paragraphs = [
        str(WORKSPACE_ROOT),
        "",
        "安全确认：这是你创建或信任的项目吗？",
        "例如你自己的代码、知名开源项目，或团队内部工作区；如果不确定，请先检查目录内容。",
        "",
        "DiaEvo 会在这里读取项目轨迹、生成候选技能，并可能运行本地验证命令。",
        "",
        "安全选择",
        "",
        f"{GLYPHS['prompt'] if selected == 1 else ' '} 1. 是，我信任这个目录",
        f"{GLYPHS['prompt'] if selected == 2 else ' '} 2. 否，退出",
        "",
        f"{DIM}Enter 确认 {GLYPHS['dot']} Esc/Ctrl+C 取消{RESET}",
    ]

    lines = [_frame_line(GLYPHS["tl"], GLYPHS["tr"], width, title)]
    for paragraph in paragraphs:
        wrapped = _wrap_display_line(paragraph, body_width) if paragraph else [""]
        for line in wrapped:
            lines.append(f"{BLUE}{GLYPHS['v']}{RESET} {_pad(line, body_width)} {BLUE}{GLYPHS['v']}{RESET}")
    lines.append(_frame_line(GLYPHS["bl"], GLYPHS["br"], width))
    return "\n".join(lines)


def maybe_show_trust_dialog() -> bool:
    if has_trusted_workspace():
        return True
    print(render_trust_dialog())
    while True:
        choice = input("选择 1 或 2 [1]: ").strip().lower()
        if choice in {"", "1", "y", "yes"}:
            save_trusted_workspace()
            return True
        if choice in {"2", "n", "no", "exit", "q"}:
            return False
        print("请输入 1 或 2。")
