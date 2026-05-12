from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .features import FeatureStore, cosine
from .ingest import load_skill_registry
from .paths import CANDIDATE_SKILLS_DIR


@dataclass(slots=True)
class SkillText:
    name: str
    text: str
    source: str
    path: str = ""


SECTION_ALIASES = {
    "when to use": "when_to_use",
    "trigger signals": "trigger_signals",
    "operating steps": "operating_steps",
    "failure fallbacks": "failure_fallbacks",
    "verification suggestions": "verification_suggestions",
    "safety constraints": "safety_constraints",
    "mined evidence": "mined_evidence",
}

MERGE_SECTIONS = ("failure_fallbacks", "safety_constraints")


def _resolved_excludes(paths: list[Path] | None) -> set[Path]:
    if not paths:
        return set()
    return {path.resolve(strict=False) for path in paths}


def collect_skill_texts(
    *,
    exclude_paths: list[Path] | None = None,
    registry_path: str | Path | None = None,
    include_registry: bool = True,
    include_candidates: bool = True,
) -> list[SkillText]:
    values: list[SkillText] = []
    if include_registry:
        for skill in load_skill_registry(registry_path):
            values.append(SkillText(name=skill.name, text=skill.document, source="registry", path=skill.path))
    if not include_candidates or not CANDIDATE_SKILLS_DIR.exists():
        return values
    excludes = _resolved_excludes(exclude_paths)
    for skill_path in sorted(CANDIDATE_SKILLS_DIR.glob("**/SKILL.md")):
        if skill_path.resolve(strict=False) in excludes:
            continue
        try:
            text = skill_path.read_text(encoding="utf-8")
        except OSError:
            continue
        source = "evolved_candidate" if skill_path.parent.name == "evolved" else "candidate"
        name = skill_path.parent.parent.name if source == "evolved_candidate" else skill_path.parent.name
        values.append(SkillText(name=name, text=text, source=source, path=str(skill_path)))
    return values


def duplicate_action(similarity: float) -> tuple[str, str]:
    if similarity >= 0.92:
        return "reject_duplicate", "nearest skill is too similar to promote as a separate candidate"
    if similarity >= 0.82:
        return "merge", "nearest skill is similar enough that reviewer should merge useful sections"
    if similarity >= 0.70:
        return "specialize", "candidate should state a narrower activation rule before promotion"
    return "keep", "no actionable duplicate risk detected"


def _section_key(title: str) -> str:
    normalized = re.sub(r"\s+", " ", title.strip().lower())
    return SECTION_ALIASES.get(normalized, normalized.replace(" ", "_").replace("-", "_"))


def extract_skill_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = _section_key(match.group(1))
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def _frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}\s*:\s*['\"]?([^'\"\n]+)", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _trace_ids(text: str) -> list[str]:
    values: set[str] = set()
    for match in re.finditer(r"Trace ids:\s*`([^`]*)`", text, flags=re.IGNORECASE):
        for item in match.group(1).split(","):
            item = item.strip()
            if item:
                values.add(item)
    return sorted(values)


def _content_lines(text: str) -> list[str]:
    values: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in {"-", "1."}:
            continue
        values.append(stripped)
    return values


def _normalized_line(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower().strip("-0123456789. ")).strip()


def _merge_unique_lines(candidate: str, nearest: str, limit: int = 10) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for line in [*_content_lines(nearest), *_content_lines(candidate)]:
        normalized = _normalized_line(line)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(line)
        if len(merged) >= limit:
            break
    return merged


def _unique_candidate_lines(candidate: str, nearest: str, limit: int = 8) -> list[str]:
    nearest_lines = {_normalized_line(line) for line in _content_lines(nearest)}
    values: list[str] = []
    seen: set[str] = set()
    for line in _content_lines(candidate):
        normalized = _normalized_line(line)
        if not normalized or normalized in nearest_lines or normalized in seen:
            continue
        seen.add(normalized)
        values.append(line)
        if len(values) >= limit:
            break
    return values


def _tool_tokens(*texts: str) -> list[str]:
    tokens: set[str] = set()
    for text in texts:
        for value in re.findall(r"`([^`]+)`", text):
            normalized = value.strip().lower()
            if not normalized or normalized.startswith(".") or " " in normalized:
                continue
            if re.search(r"[a-z]", normalized):
                tokens.add(normalized)
    return sorted(tokens)


def _tool_paths_compatible(candidate_sections: dict[str, str], nearest_sections: dict[str, str]) -> bool:
    candidate_tools = set(
        _tool_tokens(candidate_sections.get("trigger_signals", ""), candidate_sections.get("operating_steps", ""))
    )
    nearest_tools = set(_tool_tokens(nearest_sections.get("trigger_signals", ""), nearest_sections.get("operating_steps", "")))
    if not candidate_tools or not nearest_tools:
        return True
    return bool(candidate_tools.intersection(nearest_tools))


def section_review_proposal(candidate_text: str, nearest_text: str = "", *, action: str = "keep") -> dict[str, Any]:
    candidate_sections = extract_skill_sections(candidate_text)
    nearest_sections = extract_skill_sections(nearest_text)
    compatible_steps = _tool_paths_compatible(candidate_sections, nearest_sections)
    candidate_cluster = _frontmatter_value(candidate_text, "source_cluster")
    nearest_cluster = _frontmatter_value(nearest_text, "source_cluster")
    evidence = {
        "candidate_source_cluster": candidate_cluster,
        "nearest_source_cluster": nearest_cluster,
        "trace_ids": sorted(set(_trace_ids(candidate_text) + _trace_ids(nearest_text))),
        "candidate_tools": _tool_tokens(candidate_sections.get("trigger_signals", ""), candidate_sections.get("operating_steps", "")),
        "nearest_tools": _tool_tokens(nearest_sections.get("trigger_signals", ""), nearest_sections.get("operating_steps", "")),
    }
    proposal: dict[str, Any] = {
        "action": action,
        "summary": "No section rewrite is needed before review.",
        "evidence": evidence,
        "operating_steps_compatible": compatible_steps,
        "proposed_edits": {},
        "preserve": {
            "trace_ids": evidence["trace_ids"],
            "source_clusters": [value for value in [candidate_cluster, nearest_cluster] if value],
            "mined_evidence": _merge_unique_lines(
                candidate_sections.get("mined_evidence", ""), nearest_sections.get("mined_evidence", ""), limit=12
            ),
        },
    }
    if action == "specialize":
        unique_triggers = _unique_candidate_lines(
            candidate_sections.get("trigger_signals", ""), nearest_sections.get("trigger_signals", "")
        )
        proposal["summary"] = "Specialize before promotion by narrowing activation rules around candidate-only signals."
        proposal["proposed_edits"] = {
            "when_to_use": [
                "Require at least one candidate-only trigger signal and one matching tool, file, or failure signal.",
                "State explicitly when the nearest existing skill should be used instead.",
            ],
            "trigger_signals": unique_triggers
            or ["Add a candidate-only trigger signal; current text is too close to the nearest skill."],
        }
    elif action == "merge":
        merged_sections = {
            section: _merge_unique_lines(candidate_sections.get(section, ""), nearest_sections.get(section, ""))
            for section in MERGE_SECTIONS
        }
        if compatible_steps:
            merged_sections["operating_steps"] = _merge_unique_lines(
                candidate_sections.get("operating_steps", ""), nearest_sections.get("operating_steps", "")
            )
        proposal["summary"] = "Merge useful sections with the nearest skill; keep operating steps only when tool paths are compatible."
        proposal["proposed_edits"] = merged_sections
        if not compatible_steps:
            proposal["blocked_sections"] = {
                "operating_steps": "Tool paths differ, so operating steps need manual reconciliation."
            }
    elif action == "reject_duplicate":
        proposal["summary"] = "Reject as a standalone skill unless a reviewer can identify a narrower non-overlapping scope."
        proposal["proposed_edits"] = {
            "reviewer_note": ["Candidate is above the duplicate threshold for standalone promotion."]
        }
    return proposal


def nearest_duplicate(text: str, known_texts: list[SkillText]) -> dict[str, Any]:
    if not known_texts:
        action, reason = duplicate_action(0.0)
        return {
            "similarity": 0.0,
            "nearest": "",
            "nearest_source": "",
            "nearest_path": "",
            "recommended_action": action,
            "reason": reason,
            "section_review": section_review_proposal(text, action=action),
        }
    documents = [text] + [item.text for item in known_texts]
    store = FeatureStore.from_documents(documents)
    ranked = sorted(
        ((item, cosine(store.vectors[0], store.vectors[index])) for index, item in enumerate(known_texts, start=1)),
        key=lambda item: item[1],
        reverse=True,
    )
    nearest, similarity = ranked[0]
    action, reason = duplicate_action(similarity)
    return {
        "similarity": round(similarity, 4),
        "nearest": nearest.name,
        "nearest_source": nearest.source,
        "nearest_path": nearest.path,
        "recommended_action": action,
        "reason": reason,
        "section_review": section_review_proposal(text, nearest.text, action=action),
    }


def similarity_pairs(texts: list[SkillText]) -> list[dict[str, Any]]:
    if len(texts) < 2:
        return []
    store = FeatureStore.from_documents([item.text for item in texts])
    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(texts):
        for right_index in range(left_index + 1, len(texts)):
            right = texts[right_index]
            similarity = cosine(store.vectors[left_index], store.vectors[right_index])
            action, reason = duplicate_action(similarity)
            review = section_review_proposal(left.text, right.text, action=action)
            pairs.append(
                {
                    "left": left.name,
                    "left_source": left.source,
                    "right": right.name,
                    "right_source": right.source,
                    "similarity": round(similarity, 4),
                    "recommended_action": action,
                    "reason": reason,
                    "section_review": review,
                }
            )
    return sorted(pairs, key=lambda item: (-float(item["similarity"]), item["left"], item["right"]))
