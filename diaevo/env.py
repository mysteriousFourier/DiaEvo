from __future__ import annotations

import os
from pathlib import Path

from .paths import INSTALL_ROOT, WORKSPACE_ROOT


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env(path: str | Path | None = None, override: bool = False) -> dict[str, str]:
    """Load a small dotenv file without adding a runtime dependency."""
    target = Path(path) if path else WORKSPACE_ROOT / ".env"
    if path is None and not target.exists():
        target = INSTALL_ROOT / ".env"
    loaded: dict[str, str] = {}
    if not target.exists():
        return loaded
    for line_no, raw_line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ValueError(f"Invalid .env line {target}:{line_no}")
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = _strip_quotes(value)
        if not key:
            raise ValueError(f"Invalid empty .env key {target}:{line_no}")
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def write_env_value(key: str, value: str, path: str | Path | None = None) -> None:
    """Update or append a single dotenv key while preserving unrelated lines."""
    target = Path(path) if path else WORKSPACE_ROOT / ".env"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    updated = False
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        candidate = stripped[len("export ") :].strip() if stripped.startswith("export ") else stripped
        if candidate.startswith("#") or "=" not in candidate:
            output.append(line)
            continue
        existing_key, _ = candidate.split("=", 1)
        if existing_key.strip().lstrip("\ufeff") == key:
            output.append(f"{key}={value}")
            updated = True
        else:
            output.append(line)
    if not updated:
        output.append(f"{key}={value}")
    target.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.environ[key] = value
