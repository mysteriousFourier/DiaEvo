from __future__ import annotations

import os
import re
import sys
from typing import Literal


OutputMode = Literal["terminal", "plain", "json"]

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*")
LIST_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+")
QUOTE_RE = re.compile(r"^\s{0,3}>\s?")
TABLE_BORDER_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
INLINE_MARKDOWN_RE = re.compile(r"(`+|\*\*?|__?|~~)")

EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"  # flags
    "\U0001F300-\U0001F5FF"  # pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport/map
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"  # miscellaneous symbols often rendered as emoji
    "\u2705"
    "\u274C"
    "\u2753-\u2755"
    "\u2757"
    "\u2764"
    "\u2B50"
    "\u3030"
    "\u303D"
    "\u3297"
    "\u3299"
    "]+"
)
VARIATION_SELECTOR_RE = re.compile("[\ufe0e\ufe0f]")
ZERO_WIDTH_JOINER_RE = re.compile("\u200d")


def output_mode(default: OutputMode = "terminal") -> OutputMode:
    value = os.environ.get("DIAEVO_OUTPUT", default).strip().lower()
    if value in {"terminal", "plain", "json"}:
        return value  # type: ignore[return-value]
    return default


def sanitize_no_emoji(text: object) -> str:
    value = str(text)
    value = EMOJI_RE.sub("", value)
    value = VARIATION_SELECTOR_RE.sub("", value)
    return ZERO_WIDTH_JOINER_RE.sub("", value)


def strip_markdown(text: str) -> str:
    lines: list[str] = []
    in_fence = False
    for raw_line in sanitize_no_emoji(text).splitlines():
        if FENCE_RE.match(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            lines.append(raw_line)
            continue
        if TABLE_BORDER_RE.match(raw_line):
            continue
        line = HEADING_RE.sub("", raw_line)
        line = QUOTE_RE.sub("", line)
        line = LIST_RE.sub("", line)
        if "|" in line and raw_line.strip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            line = "  ".join(cell for cell in cells if cell)
        line = INLINE_MARKDOWN_RE.sub("", line)
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def render_assistant_text(text: str, *, mode: OutputMode | None = None) -> str:
    selected = output_mode() if mode is None else mode
    clean = sanitize_no_emoji(text)
    if selected == "plain" or selected == "json" or not sys.stdout.isatty():
        return strip_markdown(clean)
    return clean


def print_assistant(text: str, *, mode: OutputMode | None = None) -> None:
    selected = output_mode() if mode is None else mode
    clean = sanitize_no_emoji(text)
    if selected == "terminal" and sys.stdout.isatty():
        try:
            from rich.console import Console
            from rich.markdown import Markdown

            Console().print(Markdown(clean))
            return
        except Exception:
            pass
    print(strip_markdown(clean))


def print_status(text: str) -> None:
    print(sanitize_no_emoji(text))
