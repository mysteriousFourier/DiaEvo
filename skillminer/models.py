from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    except ValueError:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result


@dataclass(slots=True)
class TraceRecord:
    id: str
    task: str
    project_language: str = ""
    frameworks: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    outcome: str = "unknown"
    error_type: str = ""
    used_skills: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    retries: int = 0
    feedback: str = ""
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: dict[str, Any], index: int = 0) -> "TraceRecord":
        project = value.get("project") or {}
        if not isinstance(project, dict):
            project = {"language": str(project)}
        trace_id = str(value.get("id") or f"T{index:04d}")
        task = str(value.get("task") or value.get("prompt") or "").strip()
        if not task:
            raise ValueError(f"Trace {trace_id} is missing task text")
        return cls(
            id=trace_id,
            task=task,
            project_language=str(project.get("language") or value.get("project_language") or "").strip(),
            frameworks=_as_list(project.get("frameworks") or value.get("frameworks")),
            files=_as_list(project.get("files") or value.get("files")),
            tools=_as_list(value.get("tools") or value.get("tool_sequence")),
            commands=_as_list(value.get("commands")),
            outcome=str(value.get("outcome") or "unknown").strip().lower(),
            error_type=str(value.get("error_type") or "").strip().lower(),
            used_skills=_as_list(value.get("used_skills")),
            duration_sec=_as_float(value.get("duration_sec")),
            retries=_as_int(value.get("retries")),
            feedback=str(value.get("feedback") or "").strip().lower(),
            tags=_as_list(value.get("tags")),
            raw=dict(value),
        )

    @property
    def success(self) -> bool:
        return self.outcome in {"success", "passed", "accepted"}

    @property
    def file_extensions(self) -> list[str]:
        extensions: list[str] = []
        for file_name in self.files:
            suffix = Path(file_name).suffix.lower().lstrip(".")
            if suffix:
                extensions.append(suffix)
        return sorted(set(extensions))

    @property
    def document(self) -> str:
        parts = [
            self.task,
            self.project_language,
            " ".join(self.frameworks),
            " ".join(self.files),
            " ".join(self.tools),
            self.error_type,
            " ".join(self.tags),
        ]
        return " ".join(part for part in parts if part)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "project": {
                "language": self.project_language,
                "frameworks": self.frameworks,
                "files": self.files,
            },
            "tools": self.tools,
            "commands": self.commands,
            "outcome": self.outcome,
            "error_type": self.error_type,
            "used_skills": self.used_skills,
            "duration_sec": self.duration_sec,
            "retries": self.retries,
            "feedback": self.feedback,
            "tags": self.tags,
        }


@dataclass(slots=True)
class SkillRecord:
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    path: str = ""
    permissions: list[str] = field(default_factory=list)
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used: str = ""
    risk: float = 0.0
    cost: float = 0.0
    source: str = "registry"
    installed: bool = True

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "SkillRecord":
        return cls(
            name=str(value.get("name") or "").strip(),
            description=str(value.get("description") or "").strip(),
            tags=_as_list(value.get("tags")),
            path=str(value.get("path") or "").strip(),
            permissions=_as_list(value.get("permissions")),
            usage_count=_as_int(value.get("usage_count")),
            success_count=_as_int(value.get("success_count")),
            failure_count=_as_int(value.get("failure_count")),
            last_used=str(value.get("last_used") or "").strip(),
            risk=max(0.0, min(1.0, _as_float(value.get("risk")))),
            cost=max(0.0, min(1.0, _as_float(value.get("cost")))),
            source=str(value.get("source") or "registry").strip(),
            installed=bool(value.get("installed", True)),
        )

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total <= 0:
            return 0.5
        return self.success_count / total

    @property
    def document(self) -> str:
        return " ".join([self.name, self.description, " ".join(self.tags), " ".join(self.permissions)])

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "path": self.path,
            "permissions": self.permissions,
            "usage_count": self.usage_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_used": self.last_used,
            "risk": self.risk,
            "cost": self.cost,
            "source": self.source,
            "installed": self.installed,
        }


@dataclass(slots=True)
class PluginRecord:
    name: str
    description: str
    commands: list[str] = field(default_factory=list)
    version: str = ""
    source: str = ""
    installed: bool = False
    trust_score: float = 0.5
    risk: float = 0.5

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "PluginRecord":
        return cls(
            name=str(value.get("name") or "").strip(),
            description=str(value.get("description") or "").strip(),
            commands=_as_list(value.get("commands")),
            version=str(value.get("version") or "").strip(),
            source=str(value.get("source") or "").strip(),
            installed=bool(value.get("installed", False)),
            trust_score=max(0.0, min(1.0, _as_float(value.get("trust_score"), 0.5))),
            risk=max(0.0, min(1.0, _as_float(value.get("risk"), 0.5))),
        )

    def as_skill(self) -> SkillRecord:
        description = f"Plugin-backed capability: {self.description}"
        return SkillRecord(
            name=f"plugin:{self.name}",
            description=description,
            tags=["plugin", *self.commands],
            path=self.source,
            permissions=["external-plugin"],
            usage_count=0,
            success_count=1 if self.installed else 0,
            failure_count=0,
            risk=self.risk,
            cost=max(0.2, 1.0 - self.trust_score),
            source="plugin",
            installed=self.installed,
        )
