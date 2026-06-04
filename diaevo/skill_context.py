from __future__ import annotations

import os
import re
import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ingest import load_plugins, load_skill_registry
from .paths import CANDIDATE_SKILLS_DIR, DIAEVO_DIR, WORKSPACE_ROOT
from .recommender import recommend
from .storage import read_json, write_json


MAX_SKILL_TEXT_CHARS = 28_000
MAX_REFERENCE_CHARS = 4_000
MAX_REFERENCES = 4
SKILL_MENU_SUMMARY_CACHE = DIAEVO_DIR / "skill_menu_summaries.json"


@dataclass(frozen=True, slots=True)
class SkillSource:
    name: str
    description: str
    path: Path
    source: str = "installed"
    tags: tuple[str, ...] = ()
    score: float = 0.0


def _candidate_skill_roots() -> list[Path]:
    roots = [
        WORKSPACE_ROOT / "skills",
        CANDIDATE_SKILLS_DIR,
        WORKSPACE_ROOT / "outputs" / "reports" / "gepa",
    ]
    for env_name in ("DIAEVO_SKILLS_DIR",):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value).expanduser())
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        resolved = root.resolve(strict=False)
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"\A---\s*\n(?P<body>.*?)\n---\s*\n?", text, flags=re.DOTALL)
    if not match:
        return {}, text
    raw = match.group("body")
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data, text[match.end() :]


def _skill_md(path: Path) -> Path | None:
    target = path / "SKILL.md" if path.is_dir() else path
    return target if target.exists() and target.is_file() else None


def _read_skill_source(skill_file: Path, *, source: str = "installed", score: float = 0.0) -> SkillSource | None:
    try:
        text = skill_file.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    frontmatter, body = _parse_frontmatter(text)
    name = str(frontmatter.get("name") or skill_file.parent.name).strip()
    description = str(frontmatter.get("description") or "").strip()
    if not description:
        first_text_line = next((line.strip("# ").strip() for line in body.splitlines() if line.strip()), "")
        description = first_text_line[:240]
    tags = _parse_tags(frontmatter.get("tags") or "")
    if not name:
        return None
    return SkillSource(name=name, description=description, path=skill_file.parent, source=source, tags=tags, score=score)


def _parse_tags(value: Any) -> tuple[str, ...]:
    if isinstance(value, list | tuple | set):
        return tuple(str(part).strip() for part in value if str(part).strip())
    text = str(value).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return tuple(
        part.strip().strip('"').strip("'")
        for part in re.split(r"[, ]+", text)
        if part.strip().strip('"').strip("'")
    )


def discover_skill_sources() -> list[SkillSource]:
    sources: dict[str, SkillSource] = {}
    for record in load_skill_registry():
        if not record.path:
            continue
        skill_file = _skill_md(WORKSPACE_ROOT / record.path)
        if skill_file:
            item = _read_skill_source(skill_file, source=record.source or "registry")
            if item:
                sources[item.name.lower()] = SkillSource(
                    name=record.name or item.name,
                    description=record.description or item.description,
                    path=item.path,
                    source=record.source or item.source,
                    tags=tuple(record.tags) or item.tags,
                )
    for plugin in load_plugins():
        if not plugin.name:
            continue
        lowered = plugin.name.lower()
        if lowered not in sources:
            sources[lowered] = SkillSource(
                name=plugin.name,
                description=plugin.description,
                path=Path(plugin.source) if plugin.source else WORKSPACE_ROOT,
                source="plugin",
            )
    for root in _candidate_skill_roots():
        if not root.exists():
            continue
        for skill_file in root.rglob("SKILL.md"):
            item = _read_skill_source(skill_file)
            if item:
                sources.setdefault(item.name.lower(), item)
    return sorted(sources.values(), key=lambda item: item.name.lower())


def _source_digest(source: SkillSource) -> str:
    payload = "\n".join([source.name, source.description, str(source.path), " ".join(source.tags)])
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def _fallback_menu_summary(source: SkillSource) -> str:
    text = " ".join(str(source.description or "").split())
    if not text:
        text = " ".join(source.tags[:4]) or source.source or "skill workflow"
    return text[:80]


def _llm_menu_summaries(sources: list[SkillSource]) -> dict[str, str]:
    if not sources:
        return {}
    try:
        from .deepseek_chat import chat_completion, config_from_env, extract_assistant_text

        config = config_from_env(max_tokens=1200, no_thinking=True)
        items = [
            {
                "name": source.name,
                "description": source.description,
                "tags": list(source.tags),
                "path": str(source.path),
            }
            for source in sources
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "你为 CLI 菜单生成 skill 简述。只输出 JSON 对象，键是 skill name，值是中文短句。"
                    "每个值 10 到 24 个汉字左右，说明这个 skill 适合处理什么，不要 Markdown，不要表情。"
                ),
            },
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
        ]
        text = extract_assistant_text(chat_completion(messages, config))
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(text[start : end + 1])
        else:
            parsed = json.loads(text)
        if isinstance(parsed, dict):
            return {
                str(key): " ".join(str(value).split())[:80]
                for key, value in parsed.items()
                if str(key).strip() and str(value).strip()
            }
    except Exception:
        return {}
    return {}


def skill_menu_items() -> list[tuple[str, str]]:
    sources = discover_skill_sources()
    cache = read_json(SKILL_MENU_SUMMARY_CACHE, default={})
    if not isinstance(cache, dict):
        cache = {}
    summaries = cache.get("summaries") if isinstance(cache.get("summaries"), dict) else {}
    missing = [
        source
        for source in sources
        if not isinstance(summaries.get(source.name), dict)
        or summaries[source.name].get("digest") != _source_digest(source)
        or not summaries[source.name].get("summary")
    ]
    generated = _llm_menu_summaries(missing)
    changed = False
    for source in missing:
        summary = generated.get(source.name) or _fallback_menu_summary(source)
        summaries[source.name] = {"digest": _source_digest(source), "summary": summary}
        changed = True
    if changed:
        write_json(SKILL_MENU_SUMMARY_CACHE, {"summaries": summaries})
    return [
        (source.name, str(summaries.get(source.name, {}).get("summary") or _fallback_menu_summary(source)))
        for source in sources
    ]


def _task_tokens(task: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[\w\u4e00-\u9fff-]+", task) if len(token) >= 2}


def _heuristic_score(task: str, source: SkillSource) -> float:
    tokens = _task_tokens(task)
    document = " ".join([source.name, source.description, " ".join(source.tags)]).lower()
    score = sum(1.0 for token in tokens if token in document)
    lowered = task.lower()
    if any(term in lowered for term in ("front", "web", "ui", "react", "vite", "网页", "前端", "页面", "界面")):
        if any(term in document for term in ("frontend", "web", "ui", "react", "vite", "design", "前端")):
            score += 4.0
        if source.name.lower() == "web-design-engineer":
            score += 20.0
    if any(term in lowered for term in ("image", "图片", "生成图", "海报")) and "image" in document:
        score += 3.0
    if "skill" in lowered or "技能" in lowered:
        if "skill" in document or "技能" in document:
            score += 2.0
    return score


def recommend_skill_contexts(task: str, *, top_k: int = 5) -> list[dict[str, Any]]:
    candidates = {item.name.lower(): item for item in discover_skill_sources()}
    scored: dict[str, float] = {key: _heuristic_score(task, item) for key, item in candidates.items()}
    try:
        result = recommend(task=task, top_k=max(top_k, 5))
    except Exception:
        result = {}
    for index, rec in enumerate(result.get("recommendations") or []):
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("skill") or "").strip()
        if not name:
            continue
        key = name.lower()
        path = Path(str(rec.get("path") or ""))
        resolved_path = WORKSPACE_ROOT / path if path and not path.is_absolute() else path
        skill_file = _skill_md(resolved_path) if path else None
        if key not in candidates and skill_file is None:
            continue
        score = float(rec.get("score") or 0.0)
        if key in candidates and (candidates[key].path / "SKILL.md").exists():
            score += max(0.0, top_k - index)
        scored[key] = scored.get(key, 0.0) + score
        if key not in candidates:
            candidates[key] = SkillSource(
                name=name,
                description=str(rec.get("description") or ""),
                path=skill_file.parent if skill_file else resolved_path,
                source=str(rec.get("source") or "recommender"),
            )
    ordered = sorted(candidates.values(), key=lambda item: (-scored.get(item.name.lower(), 0.0), item.name.lower()))
    return [
        {
            "name": item.name,
            "description": item.description,
            "path": str(item.path),
            "source": item.source,
            "tags": list(item.tags),
            "score": round(scored.get(item.name.lower(), 0.0), 4),
        }
        for item in ordered[:top_k]
        if (item.path / "SKILL.md").exists()
        and (scored.get(item.name.lower(), 0.0) > 0 or item.name.lower() in {"web-design-engineer"})
    ]


def _find_skill(name_or_path: str) -> SkillSource | None:
    raw = name_or_path.strip()
    if not raw:
        return None
    direct = _skill_md(Path(raw).expanduser())
    if direct:
        return _read_skill_source(direct, source="path")
    workspace_direct = _skill_md(WORKSPACE_ROOT / raw)
    if workspace_direct:
        return _read_skill_source(workspace_direct, source="path")
    lowered = raw.lower()
    for source in discover_skill_sources():
        if lowered in {source.name.lower(), source.path.name.lower(), str(source.path).lower()}:
            return source
    return None


def _summarize_references(skill_dir: Path, task: str) -> list[dict[str, str]]:
    references = skill_dir / "references"
    if not references.exists() or not references.is_dir():
        return []
    tokens = _task_tokens(task)
    files = [path for path in references.rglob("*") if path.is_file() and path.suffix.lower() in {".md", ".txt"}]
    files.sort(
        key=lambda path: (
            -sum(1 for token in tokens if token in path.name.lower()),
            len(path.relative_to(references).parts),
            path.name.lower(),
        )
    )
    result: list[dict[str, str]] = []
    for path in files[:MAX_REFERENCES]:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        rel = path.relative_to(skill_dir).as_posix()
        result.append({"path": rel, "summary": text[:MAX_REFERENCE_CHARS]})
    return result


def load_skill_context(name_or_path: str, *, task: str = "") -> dict[str, Any]:
    source = _find_skill(name_or_path)
    if source is None:
        return {"status": "error", "error": f"skill not found: {name_or_path}"}
    skill_file = source.path / "SKILL.md"
    try:
        text = skill_file.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return {"status": "error", "error": str(exc)}
    frontmatter, body = _parse_frontmatter(text)
    truncated = len(text) > MAX_SKILL_TEXT_CHARS
    context_text = text[:MAX_SKILL_TEXT_CHARS]
    references = _summarize_references(source.path, task)
    return {
        "status": "ok",
        "name": source.name,
        "description": source.description,
        "path": str(source.path),
        "skill_file": str(skill_file),
        "frontmatter": frontmatter,
        "body_summary": body[:8_000],
        "skill_text": context_text,
        "truncated": truncated,
        "references_routing": "Load files under references/ only when the current task needs that specific guidance.",
        "references": references,
    }


def render_skill_context_message(context: dict[str, Any]) -> str:
    if context.get("status") != "ok":
        return f"[Skill load failed]\n{context.get('error')}"
    parts = [
        f"[Loaded skill: {context.get('name')}]",
        f"Path: {context.get('skill_file')}",
        "Use this skill's workflow as active instructions for the current user task.",
        "",
        "SKILL.md:",
        str(context.get("skill_text") or ""),
    ]
    references = context.get("references") if isinstance(context.get("references"), list) else []
    if references:
        parts.append("")
        parts.append("Relevant references excerpts:")
        for item in references:
            if isinstance(item, dict):
                parts.append(f"\n## {item.get('path')}\n{item.get('summary')}")
    return "\n".join(parts)
