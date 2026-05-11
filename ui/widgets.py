from __future__ import annotations

from typing import Any


def compact_stat(label: str, value: Any) -> str:
    return f"{label}: {value}"


def ascii_logo() -> str:
    return r"""
 ____  _    _ _ _ __  __ _
/ ___|| | _(_) | |  \/  (_)_ __   ___ _ __
\___ \| |/ / | | | |\/| | | '_ \ / _ \ '__|
 ___) |   <| | | | |  | | | | | |  __/ |
|____/|_|\_\_|_|_|_|  |_|_|_| |_|\___|_|
""".strip("\n")


def table(headers: list[str], rows: list[list[Any]], max_width: int = 28) -> str:
    values = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in values:
        for index, cell in enumerate(row):
            widths[index] = min(max_width, max(widths[index], len(cell)))
    def trim(text: str, width: int) -> str:
        return text if len(text) <= width else text[: max(0, width - 1)] + "…"
    header_line = " | ".join(trim(header, widths[index]).ljust(widths[index]) for index, header in enumerate(headers))
    sep = "-+-".join("-" * width for width in widths)
    body = [
        " | ".join(trim(cell, widths[index]).ljust(widths[index]) for index, cell in enumerate(row))
        for row in values
    ]
    return "\n".join([header_line, sep, *body])
