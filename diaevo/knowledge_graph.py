from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .features import FeatureStore, cosine, tokenize
from .ingest import DEFAULT_TOOL_EVENTS_PATH, load_traces
from .miner import mine
from .paths import DATA_DIR, ensure_project_dirs
from .storage import read_json, read_jsonl, write_json, write_jsonl


KG_ROOT = DATA_DIR / "knowledge_graph"
KG_CURRENT_DIR = KG_ROOT / "current"
KG_DELTA_DIR = KG_ROOT / "deltas"
KG_DOMAIN_DIR = KG_ROOT / "domains"
KG_DOMAIN_REGISTRY_PATH = KG_ROOT / "domain_registry.jsonl"
KG_REVIEW_QUEUE_PATH = KG_ROOT / "review_queue.jsonl"
KG_WORKBENCH_HOST = "127.0.0.1"
KG_WORKBENCH_PORT = 8765
DEFAULT_KG_VECTOR_BACKEND = "tfidf"
DEFAULT_KG_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"
KG_VECTOR_BACKEND_ENV = "DIAEVO_KG_VECTOR_BACKEND"
KG_EMBEDDING_MODEL_ENV = "DIAEVO_KG_EMBEDDING_MODEL"
KG_HF_ENDPOINT_ENV = "DIAEVO_HF_ENDPOINT"

KG_REVIEW_STATUSES = {
    "pending",
    "accepted",
    "rejected",
    "needs_source",
    "low_confidence",
    "conflict",
    "stale",
}

TRACE_CONFIDENCE = 0.95
VALIDATED_CONFIDENCE = 0.9
WEB_FETCH_CONFIDENCE = 0.75
WEB_SEARCH_CONFIDENCE = 0.55
TEXT_MENTION_CONFIDENCE = 0.6
DOMAIN_MATCH_THRESHOLD = 0.18

_KG_SERVER_PROCESS: subprocess.Popen[Any] | None = None
_SENTENCE_TRANSFORMER_FACTORY: Callable[[str], Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_stamp(value: str | None = None) -> str:
    if value:
        text = value.strip()
        if len(text) == 6 and text.isdigit():
            return text
        return datetime.fromisoformat(text).strftime("%y%m%d")
    return datetime.now().strftime("%y%m%d")


def _stable_hash(*parts: Any, length: int = 12) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def _slug(value: Any, limit: int = 56) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.:-]+", "-", text).strip("-")
    if not text:
        text = "unknown"
    if len(text) <= limit:
        return text
    return f"{text[:limit - 13].rstrip('-')}-{_stable_hash(value, length=12)}"


def _short_text(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 15].rstrip() + " ...<truncated>"


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_properties(item: dict[str, Any]) -> dict[str, Any]:
    properties = item.get("properties")
    return properties if isinstance(properties, dict) else {}


def _domain_root_for_current(current_dir: Path) -> Path:
    return current_dir.parent if current_dir.name == "current" else current_dir.parent


def _domain_registry_path(current_dir: Path) -> Path:
    if current_dir == KG_CURRENT_DIR:
        return KG_DOMAIN_REGISTRY_PATH
    return _domain_root_for_current(current_dir) / "domain_registry.jsonl"


def _domain_store_root(current_dir: Path) -> Path:
    if current_dir == KG_CURRENT_DIR:
        return KG_DOMAIN_DIR
    return _domain_root_for_current(current_dir) / "domains"


def _domain_key(domain_id: str) -> str:
    text = str(domain_id or "").strip()
    if text.startswith("domain:"):
        text = text.split(":", 1)[1]
    return _slug(text or "general", limit=64)


def _domain_id_from_label(label: str) -> str:
    tokens = [token for token in tokenize(label) if re.search(r"[a-z0-9]", token)]
    base = "-".join(tokens[:5])
    if not base:
        base = f"d-{_stable_hash(label, length=12)}"
    return f"domain:{_slug(base, limit=64)}"


def _domain_current_dir(current_dir: Path, domain: str) -> Path:
    return _domain_store_root(current_dir) / _domain_key(domain)


def _domain_matches(requested: str, candidate: str) -> bool:
    requested_text = str(requested or "").strip()
    candidate_text = str(candidate or "").strip()
    if not requested_text or not candidate_text:
        return False
    return requested_text == candidate_text or _domain_key(requested_text) == _domain_key(candidate_text)


def _resolve_current_dir_for_domain(current_dir: Path, domain: str | None) -> Path:
    if not domain:
        return current_dir
    return _domain_current_dir(current_dir, domain)


def _domain_keywords(text: str, limit: int = 8) -> list[str]:
    counts = Counter(tokenize(text))
    return [token for token, _count in counts.most_common(limit)]


def _domain_match_text(record: dict[str, Any]) -> str:
    keywords = " ".join(str(value) for value in _safe_list(record.get("keywords")))
    return " ".join(
        [
            str(record.get("id") or ""),
            str(record.get("label") or ""),
            str(record.get("summary") or ""),
            keywords,
        ]
    ).strip()


def _conversation_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or message.get("text") or "").strip()
        if content:
            parts.append(content)
    return "\n".join(parts)


def _new_domain_record(text: str, *, generated_at: str, source_id: str = "") -> dict[str, Any]:
    keywords = _domain_keywords(text)
    label = " / ".join(keywords[:3]) if keywords else "general"
    domain_id = _domain_id_from_label(label or text)
    return {
        "id": domain_id,
        "label": label,
        "summary": _short_text(text, 260),
        "keywords": keywords,
        "classifier": "local_tfidf",
        "domain_confidence": 1.0,
        "match_score": 0.0,
        "created_at": generated_at,
        "updated_at": generated_at,
        "source_ids": [source_id] if source_id else [],
        "conversation_count": 1 if source_id else 0,
        "fact_count": 0,
        "entity_count": 0,
        "evidence_path_count": 0,
    }


def _load_domain_registry(current_dir: Path) -> list[dict[str, Any]]:
    return _read_jsonl_if_exists(_domain_registry_path(current_dir))


def _classify_domain(
    text: str,
    registry: list[dict[str, Any]],
    *,
    generated_at: str,
    source_id: str = "",
) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        return _new_domain_record("general", generated_at=generated_at, source_id=source_id)
    usable_registry = [item for item in registry if isinstance(item, dict) and item.get("id")]
    if usable_registry:
        documents = [_domain_match_text(item) for item in usable_registry]
        store = FeatureStore.from_documents(documents, max_features=1000)
        nearest = store.nearest(cleaned, limit=1)
        if nearest:
            index, score = nearest[0]
            if score >= DOMAIN_MATCH_THRESHOLD:
                matched = dict(usable_registry[index])
                existing_keywords = [str(value) for value in _safe_list(matched.get("keywords"))]
                merged_keywords = list(dict.fromkeys([*existing_keywords, *_domain_keywords(cleaned)]))[:12]
                matched["keywords"] = merged_keywords
                matched["summary"] = _short_text(str(matched.get("summary") or "") or cleaned, 260)
                matched["classifier"] = "local_tfidf"
                matched["domain_confidence"] = round(float(score), 4)
                matched["match_score"] = round(float(score), 4)
                if source_id:
                    matched["source_ids"] = list(dict.fromkeys([*[
                        str(value) for value in _safe_list(matched.get("source_ids"))
                    ], source_id]))
                return matched
    return _new_domain_record(cleaned, generated_at=generated_at, source_id=source_id)


def _item_domain_ids(item: dict[str, Any]) -> list[str]:
    properties = _safe_properties(item)
    values = [str(value) for value in _safe_list(properties.get("domain_ids")) if str(value).strip()]
    primary = str(properties.get("primary_domain_id") or "").strip()
    if primary:
        values.insert(0, primary)
    return list(dict.fromkeys(values))


def _domain_properties(domain: dict[str, Any] | None) -> dict[str, Any]:
    if not domain:
        return {}
    domain_id = str(domain.get("id") or "").strip()
    if not domain_id:
        return {}
    return {
        "primary_domain_id": domain_id,
        "domain_ids": [domain_id],
        "domain_confidence": domain.get("domain_confidence", 1.0),
        "domain_classifier": domain.get("classifier", "local_tfidf"),
    }


def _domain_entity_record(domain: dict[str, Any], created_at: str) -> dict[str, Any]:
    domain_id = str(domain.get("id") or "").strip()
    return {
        "id": domain_id,
        "kind": "domain",
        "label": str(domain.get("label") or _domain_key(domain_id)),
        "properties": {
            "summary": str(domain.get("summary") or ""),
            "keywords": [str(value) for value in _safe_list(domain.get("keywords"))],
            "classifier": str(domain.get("classifier") or "local_tfidf"),
        },
        "created_at": str(domain.get("created_at") or created_at),
    }


def _read_jsonl_if_exists(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    target = Path(path)
    if not target.exists():
        return []
    return read_jsonl(target)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _find_available_port(host: str, preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 50)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) != 0:
                return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _wait_for_http_server(host: str, port: int, timeout_sec: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.05)
    return False


def _serve_directory_url(
    root: Path,
    relative_path: str,
    *,
    host: str = KG_WORKBENCH_HOST,
    port: int | None = None,
) -> dict[str, Any]:
    global _KG_SERVER_PROCESS
    selected_port = _find_available_port(host, port or KG_WORKBENCH_PORT)
    root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "http.server",
        str(selected_port),
        "--bind",
        host,
        "--directory",
        str(root),
    ]
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    _KG_SERVER_PROCESS = subprocess.Popen(cmd, **kwargs)
    ready = _wait_for_http_server(host, selected_port)
    quoted_path = "/".join(part for part in relative_path.replace("\\", "/").split("/") if part)
    return {
        "url": f"http://{host}:{selected_port}/{quoted_path}",
        "host": host,
        "port": selected_port,
        "server_pid": _KG_SERVER_PROCESS.pid,
        "server_ready": ready,
        "served_dir": str(root),
    }


def _open_browser(url: str) -> bool:
    return bool(webbrowser.open(url, new=2))


class KGBuilder:
    def __init__(self, generated_at: str) -> None:
        self.generated_at = generated_at
        self.entities: dict[str, dict[str, Any]] = {}
        self.triples: dict[str, dict[str, Any]] = {}
        self.claims: dict[str, dict[str, Any]] = {}
        self.evidence: dict[str, dict[str, Any]] = {}

    def entity(
        self,
        kind: str,
        key: Any,
        label: str | None = None,
        *,
        properties: dict[str, Any] | None = None,
    ) -> str:
        if kind in {"trace", "tool", "skill", "cluster", "status", "outcome", "tag", "role", "risk"}:
            entity_id = f"{kind}:{_slug(key)}"
        elif kind == "web_source":
            entity_id = f"{kind}:{_stable_hash(key, length=16)}"
        else:
            entity_id = f"{kind}:{_stable_hash(key, length=14)}"
        current = self.entities.get(entity_id)
        if current is None:
            current = {
                "id": entity_id,
                "kind": kind,
                "label": label or str(key),
                "properties": properties or {},
                "created_at": self.generated_at,
            }
            self.entities[entity_id] = current
        elif properties:
            current["properties"] = {**current.get("properties", {}), **properties}
        return entity_id

    def evidence_path(
        self,
        source_type: str,
        source_id: str,
        summary: str,
        *,
        path: str = "",
        url: str = "",
        confidence: float = TRACE_CONFIDENCE,
    ) -> str:
        evidence_id = f"evidence:{_stable_hash(source_type, source_id, summary, path, url, length=16)}"
        self.evidence.setdefault(
            evidence_id,
            {
                "id": evidence_id,
                "source_type": source_type,
                "source_id": source_id,
                "summary": _short_text(summary, 300),
                "path": path,
                "url": url,
                "confidence": round(float(confidence), 4),
                "created_at": self.generated_at,
            },
        )
        return evidence_id

    def triple(
        self,
        subject: str,
        predicate: str,
        object_id: str,
        *,
        confidence: float,
        evidence_ids: list[str],
        source_type: str,
        status: str = "pending",
        properties: dict[str, Any] | None = None,
    ) -> str:
        triple_id = f"triple:{_stable_hash(subject, predicate, object_id, source_type, length=16)}"
        self.triples.setdefault(
            triple_id,
            {
                "id": triple_id,
                "subject": subject,
                "predicate": predicate,
                "object": object_id,
                "confidence": round(float(confidence), 4),
                "evidence": sorted(set(evidence_ids)),
                "source_type": source_type,
                "status": status,
                "properties": properties or {},
                "created_at": self.generated_at,
            },
        )
        return triple_id

    def claim(
        self,
        text: str,
        *,
        subject: str = "",
        predicate: str = "",
        object_id: str = "",
        confidence: float,
        evidence_ids: list[str],
        source_type: str,
        status: str = "pending",
        properties: dict[str, Any] | None = None,
    ) -> str:
        claim_id = f"claim:{_stable_hash(text, subject, predicate, object_id, source_type, length=16)}"
        self.claims.setdefault(
            claim_id,
            {
                "id": claim_id,
                "text": _short_text(text, 500),
                "subject": subject,
                "predicate": predicate,
                "object": object_id,
                "confidence": round(float(confidence), 4),
                "evidence": sorted(set(evidence_ids)),
                "source_type": source_type,
                "status": status,
                "properties": properties or {},
                "created_at": self.generated_at,
            },
        )
        return claim_id


def _add_trace_knowledge(builder: KGBuilder, traces_path: Path) -> None:
    traces = load_traces(traces_path)
    for trace in traces:
        trace_id = builder.entity(
            "trace",
            trace.id,
            trace.id,
            properties={"source": trace.source, "success": trace.success, "outcome": trace.outcome},
        )
        evidence_id = builder.evidence_path(
            "trace",
            trace.id,
            f"Trace {trace.id}: {trace.task}",
            path=str(traces_path),
            confidence=TRACE_CONFIDENCE,
        )
        task_id = builder.entity("task", f"{trace.id}:{trace.task}", trace.task)
        builder.triple(
            trace_id,
            "DESCRIBES_TASK",
            task_id,
            confidence=TRACE_CONFIDENCE,
            evidence_ids=[evidence_id],
            source_type="trace",
        )
        outcome_id = builder.entity("outcome", trace.outcome or "unknown", trace.outcome or "unknown")
        builder.triple(
            trace_id,
            "HAS_OUTCOME",
            outcome_id,
            confidence=TRACE_CONFIDENCE,
            evidence_ids=[evidence_id],
            source_type="trace",
        )
        for tool in trace.tools:
            tool_id = builder.entity("tool", tool, tool)
            builder.triple(
                trace_id,
                "USES_TOOL",
                tool_id,
                confidence=TRACE_CONFIDENCE,
                evidence_ids=[evidence_id],
                source_type="trace",
            )
        for skill in trace.used_skills:
            skill_id = builder.entity("skill", skill, skill)
            builder.triple(
                trace_id,
                "USES_SKILL",
                skill_id,
                confidence=TRACE_CONFIDENCE,
                evidence_ids=[evidence_id],
                source_type="trace",
            )
        for file_name in trace.files:
            file_id = builder.entity("file", file_name, file_name)
            builder.triple(
                trace_id,
                "TOUCHES_FILE",
                file_id,
                confidence=0.9,
                evidence_ids=[evidence_id],
                source_type="trace",
            )
        for command in trace.commands:
            command_id = builder.entity("command", command, command)
            builder.triple(
                trace_id,
                "RUNS_COMMAND",
                command_id,
                confidence=0.9,
                evidence_ids=[evidence_id],
                source_type="trace",
            )
        for tag in trace.tags:
            tag_id = builder.entity("tag", tag, tag)
            builder.triple(
                trace_id,
                "HAS_TAG",
                tag_id,
                confidence=0.8,
                evidence_ids=[evidence_id],
                source_type="trace",
            )


def _add_tool_event_knowledge(builder: KGBuilder, tool_events_path: Path | None) -> None:
    for event in _read_jsonl_if_exists(tool_events_path):
        event_raw_id = str(event.get("id") or _stable_hash(event))
        tool = str(event.get("tool") or "unknown_tool")
        status = str(event.get("status") or "unknown")
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        event_id = builder.entity(
            "tool_event",
            event_raw_id,
            event_raw_id,
            properties={
                "tool": tool,
                "status": status,
                "approved": bool(event.get("approved")),
                "approval_required": bool(event.get("approval_required")),
                "risk": str(event.get("risk") or ""),
            },
        )
        evidence_id = builder.evidence_path(
            "tool_event",
            event_raw_id,
            f"Tool event {event_raw_id} ran {tool} with status {status}.",
            path=str(tool_events_path or DEFAULT_TOOL_EVENTS_PATH),
            confidence=VALIDATED_CONFIDENCE,
        )
        tool_id = builder.entity("tool", tool, tool)
        builder.triple(
            event_id,
            "EXECUTED_TOOL",
            tool_id,
            confidence=VALIDATED_CONFIDENCE,
            evidence_ids=[evidence_id],
            source_type="tool_event",
        )
        status_id = builder.entity("status", status, status)
        builder.triple(
            event_id,
            "HAS_STATUS",
            status_id,
            confidence=0.8,
            evidence_ids=[evidence_id],
            source_type="tool_event",
        )
        risk = str(event.get("risk") or "")
        if risk:
            risk_id = builder.entity("risk", risk, risk)
            builder.triple(
                event_id,
                "HAS_RISK_LEVEL",
                risk_id,
                confidence=0.8,
                evidence_ids=[evidence_id],
                source_type="tool_event",
            )
        query = str(args.get("query") or result.get("query") or "").strip()
        url = str(args.get("url") or result.get("url") or "").strip()
        if query:
            query_id = builder.entity("query", query, query)
            builder.triple(
                event_id,
                "HAS_QUERY",
                query_id,
                confidence=TEXT_MENTION_CONFIDENCE,
                evidence_ids=[evidence_id],
                source_type="tool_event",
            )
        if tool == "web_search":
            for index, item in enumerate(_safe_list(result.get("results")), start=1):
                if not isinstance(item, dict):
                    continue
                source_url = str(item.get("url") or "").strip()
                if not source_url:
                    continue
                title = str(item.get("title") or source_url).strip()
                source_id = builder.entity(
                    "web_source",
                    source_url,
                    title,
                    properties={"url": source_url, "source_rank": index, "query": query},
                )
                web_evidence = builder.evidence_path(
                    "web_search",
                    event_raw_id,
                    f"Search result {index} for '{query}': {title}",
                    path=str(tool_events_path or DEFAULT_TOOL_EVENTS_PATH),
                    url=source_url,
                    confidence=WEB_SEARCH_CONFIDENCE,
                )
                builder.triple(
                    event_id,
                    "RETURNED_WEB_SOURCE",
                    source_id,
                    confidence=WEB_SEARCH_CONFIDENCE,
                    evidence_ids=[web_evidence],
                    source_type="web_search",
                    properties={"rank": index},
                )
                builder.claim(
                    f"web_search returned '{title}' for query '{query}'.",
                    subject=event_id,
                    predicate="RETURNED_WEB_SOURCE",
                    object_id=source_id,
                    confidence=WEB_SEARCH_CONFIDENCE,
                    evidence_ids=[web_evidence],
                    source_type="web_search",
                    properties={"url": source_url, "rank": index},
                )
        if tool == "web_fetch" and url:
            source_id = builder.entity(
                "web_source",
                url,
                url,
                properties={
                    "url": url,
                    "content_type": str(result.get("content_type") or ""),
                    "status_code": result.get("status_code", ""),
                    "truncated": bool(result.get("truncated")),
                },
            )
            content_summary = _short_text(result.get("content") or "", 260)
            web_evidence = builder.evidence_path(
                "web_fetch",
                event_raw_id,
                f"Fetched {url}: {content_summary}",
                path=str(tool_events_path or DEFAULT_TOOL_EVENTS_PATH),
                url=url,
                confidence=WEB_FETCH_CONFIDENCE,
            )
            builder.triple(
                event_id,
                "FETCHED_WEB_SOURCE",
                source_id,
                confidence=WEB_FETCH_CONFIDENCE,
                evidence_ids=[web_evidence],
                source_type="web_fetch",
            )
            builder.claim(
                f"web_fetch retrieved {url} with status {result.get('status_code', 'unknown')}.",
                subject=event_id,
                predicate="FETCHED_WEB_SOURCE",
                object_id=source_id,
                confidence=WEB_FETCH_CONFIDENCE,
                evidence_ids=[web_evidence],
                source_type="web_fetch",
                properties={"url": url},
            )


def _add_conversation_knowledge(
    builder: KGBuilder,
    conversation_path: Path | None,
    *,
    domain: dict[str, Any] | None = None,
) -> None:
    domain_id = str(domain.get("id") or "").strip() if domain else ""
    domain_fact_properties = _domain_properties(domain)
    if domain_id:
        builder.entities.setdefault(domain_id, _domain_entity_record(domain or {}, builder.generated_at))
    for index, message in enumerate(_read_jsonl_if_exists(conversation_path), start=1):
        role = str(message.get("role") or "user").strip() or "user"
        content = str(message.get("content") or message.get("text") or "").strip()
        if not content:
            continue
        message_key = str(message.get("id") or f"{index}:{role}:{content}")
        message_id = builder.entity(
            "message",
            message_key,
            f"{role}: {_short_text(content, 80)}",
            properties={"role": role, "created_at": str(message.get("created_at") or "")},
        )
        evidence_id = builder.evidence_path(
            "conversation",
            message_key,
            f"{role} message: {content}",
            path=str(conversation_path or ""),
            confidence=TEXT_MENTION_CONFIDENCE,
        )
        role_id = builder.entity("role", role, role)
        builder.triple(
            message_id,
            "HAS_ROLE",
            role_id,
            confidence=TEXT_MENTION_CONFIDENCE,
            evidence_ids=[evidence_id],
            source_type="conversation",
            properties=domain_fact_properties,
        )
        if domain_id:
            builder.triple(
                message_id,
                "CONTRIBUTES_TO_DOMAIN",
                domain_id,
                confidence=float(domain.get("domain_confidence") or TEXT_MENTION_CONFIDENCE) if domain else TEXT_MENTION_CONFIDENCE,
                evidence_ids=[evidence_id],
                source_type="conversation",
                properties=domain_fact_properties,
            )
        if role.lower() == "user":
            intent_id = builder.entity("task", f"conversation:{message_key}:{content}", _short_text(content, 180))
            builder.triple(
                message_id,
                "EXPRESSES_USER_INTENT",
                intent_id,
                confidence=TEXT_MENTION_CONFIDENCE,
                evidence_ids=[evidence_id],
                source_type="conversation",
                properties=domain_fact_properties,
            )
            if domain_id:
                builder.triple(
                    intent_id,
                    "BELONGS_TO_DOMAIN",
                    domain_id,
                    confidence=float(domain.get("domain_confidence") or TEXT_MENTION_CONFIDENCE) if domain else TEXT_MENTION_CONFIDENCE,
                    evidence_ids=[evidence_id],
                    source_type="conversation",
                    properties=domain_fact_properties,
                )
            builder.claim(
                f"User stated intent: {content}",
                subject=message_id,
                predicate="EXPRESSES_USER_INTENT",
                object_id=intent_id,
                confidence=TEXT_MENTION_CONFIDENCE,
                evidence_ids=[evidence_id],
                source_type="conversation",
                properties=domain_fact_properties,
            )


def _add_mining_knowledge(builder: KGBuilder, traces_path: Path, registry_path: str | Path | None, plugin_path: str | Path | None, k: int | None) -> None:
    report = mine(traces_path, registry_path=registry_path, plugin_path=plugin_path, k=k)
    mining_evidence = builder.evidence_path(
        "mining_report",
        str(traces_path),
        f"Mining report over {report.get('trace_count', 0)} traces.",
        path="outputs/reports/mining_report.json",
        confidence=VALIDATED_CONFIDENCE,
    )
    for cluster in _safe_list(report.get("clusters")):
        if not isinstance(cluster, dict):
            continue
        cluster_key = str(cluster.get("id") or "")
        if not cluster_key:
            continue
        cluster_id = builder.entity(
            "cluster",
            cluster_key,
            cluster_key,
            properties={
                "size": cluster.get("size", 0),
                "coverage_gap": cluster.get("coverage_gap", 0),
                "failure_rate": cluster.get("failure_rate", 0),
                "representative_task": cluster.get("representative_task", ""),
            },
        )
        for trace_id_raw in _safe_list(cluster.get("trace_ids")):
            trace_id = builder.entity("trace", trace_id_raw, str(trace_id_raw))
            builder.triple(
                cluster_id,
                "CONTAINS_TRACE",
                trace_id,
                confidence=0.85,
                evidence_ids=[mining_evidence],
                source_type="mining_report",
            )
        for tool in _safe_list(cluster.get("top_tools"))[:8]:
            tool_id = builder.entity("tool", tool, str(tool))
            builder.triple(
                cluster_id,
                "HAS_TOP_TOOL",
                tool_id,
                confidence=0.8,
                evidence_ids=[mining_evidence],
                source_type="mining_report",
            )
        coverage_gap = float(cluster.get("coverage_gap") or 0.0)
        if coverage_gap > 0:
            builder.claim(
                f"Cluster {cluster_key} has coverage gap {coverage_gap:.4f}.",
                subject=cluster_id,
                predicate="HAS_COVERAGE_GAP",
                confidence=0.8,
                evidence_ids=[mining_evidence],
                source_type="mining_report",
                properties={"coverage_gap": round(coverage_gap, 4)},
            )
    for index, rule in enumerate(_safe_list(report.get("association_rules"))[:30], start=1):
        if not isinstance(rule, dict):
            continue
        skill = str(rule.get("skill") or rule.get("consequent") or "").strip()
        if not skill:
            continue
        rule_id = builder.entity(
            "rule",
            f"{index}:{rule}",
            f"rule:{index}",
            properties={
                "antecedent": rule.get("antecedent", []),
                "consequent": rule.get("consequent", ""),
                "support": rule.get("support", 0),
                "confidence": rule.get("confidence", 0),
                "lift": rule.get("lift", 0),
            },
        )
        skill_id = builder.entity("skill", skill, skill)
        confidence = max(0.0, min(1.0, float(rule.get("confidence") or 0.0)))
        builder.triple(
            rule_id,
            "SUGGESTS_SKILL",
            skill_id,
            confidence=confidence,
            evidence_ids=[mining_evidence],
            source_type="association_rule",
        )
        builder.claim(
            f"Association rule suggests skill '{skill}' with confidence {confidence:.4f}.",
            subject=rule_id,
            predicate="SUGGESTS_SKILL",
            object_id=skill_id,
            confidence=confidence,
            evidence_ids=[mining_evidence],
            source_type="association_rule",
            properties={"support": rule.get("support", 0), "lift": rule.get("lift", 0)},
        )
    for index, sequence in enumerate(_safe_list(report.get("frequent_sequences"))[:30], start=1):
        if not isinstance(sequence, dict):
            continue
        values = [str(item) for item in _safe_list(sequence.get("sequence"))]
        if not values:
            continue
        sequence_id = builder.entity(
            "sequence",
            f"{index}:{values}",
            " -> ".join(values),
            properties={"support": sequence.get("support", 0), "support_rate": sequence.get("support_rate", 0)},
        )
        builder.claim(
            f"Frequent sequence {' -> '.join(values)} has support {sequence.get('support', 0)}.",
            subject=sequence_id,
            predicate="HAS_SUPPORT",
            confidence=0.75,
            evidence_ids=[mining_evidence],
            source_type="sequence_mining",
            properties={"support": sequence.get("support", 0), "support_rate": sequence.get("support_rate", 0)},
        )


def _load_active_ids(current_dir: Path) -> set[str]:
    ids: set[str] = set()
    for name in ("triples.jsonl", "claims.jsonl"):
        for item in _read_jsonl_if_exists(current_dir / name):
            if item.get("id"):
                ids.add(str(item["id"]))
    return ids


def _queue_entry(kind: str, item: dict[str, Any], builder: KGBuilder, generated_at: str) -> dict[str, Any]:
    properties = _safe_properties(item)
    domain_ids = _item_domain_ids(item)
    entity_ids = sorted(
        {
            value
            for value in [item.get("subject"), item.get("object"), *domain_ids]
            if isinstance(value, str) and value in builder.entities
        }
    )
    evidence_ids = [value for value in _safe_list(item.get("evidence")) if isinstance(value, str)]
    return {
        "review_id": item["id"],
        "kind": kind,
        "status": "pending",
        "item": item,
        "entities": [builder.entities[entity_id] for entity_id in entity_ids],
        "evidence_paths": [builder.evidence[evidence_id] for evidence_id in evidence_ids if evidence_id in builder.evidence],
        "primary_domain_id": str(properties.get("primary_domain_id") or (domain_ids[0] if domain_ids else "")),
        "domain_ids": domain_ids,
        "domain_confidence": properties.get("domain_confidence", ""),
        "generated_at": generated_at,
        "reviewed_at": "",
        "reviewer": "",
        "review_note": "",
    }


def _merge_review_queue(
    builder: KGBuilder,
    *,
    queue_path: Path,
    current_dir: Path,
    generated_at: str,
) -> tuple[list[dict[str, Any]], int]:
    existing = _read_jsonl_if_exists(queue_path)
    existing_ids = {str(item.get("review_id")) for item in existing if item.get("review_id")}
    active_ids = _load_active_ids(current_dir)
    additions: list[dict[str, Any]] = []
    for item in list(builder.triples.values()) + list(builder.claims.values()):
        item_id = str(item["id"])
        if item_id in existing_ids or item_id in active_ids:
            continue
        kind = "triple" if item_id.startswith("triple:") else "claim"
        additions.append(_queue_entry(kind, item, builder, generated_at))
    merged = [*existing, *additions]
    write_jsonl(queue_path, merged)
    return additions, len(merged)


def build_kg_delta(
    *,
    traces_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    plugin_path: str | Path | None = None,
    tool_events_path: str | Path | None = None,
    conversation_path: str | Path | None = None,
    k: int | None = None,
    include_mining: bool = True,
    queue_path: str | Path | None = None,
    current_dir: str | Path | None = None,
    delta_dir: str | Path | None = None,
) -> dict[str, Any]:
    ensure_project_dirs()
    generated_at = _now()
    trace_source = Path(traces_path) if traces_path else DATA_DIR / "processed_traces.jsonl"
    if not trace_source.exists():
        trace_source = DATA_DIR / "sample_traces.jsonl"
    event_source = Path(tool_events_path) if tool_events_path else DEFAULT_TOOL_EVENTS_PATH
    conversation_source = Path(conversation_path) if conversation_path else None
    queue_target = Path(queue_path) if queue_path else KG_REVIEW_QUEUE_PATH
    current_target = Path(current_dir) if current_dir else KG_CURRENT_DIR
    delta_target = Path(delta_dir) if delta_dir else KG_DELTA_DIR
    current_target.mkdir(parents=True, exist_ok=True)
    delta_target.mkdir(parents=True, exist_ok=True)

    builder = KGBuilder(generated_at)
    domains: list[dict[str, Any]] = []
    conversation_domain: dict[str, Any] | None = None
    if conversation_source:
        conversation_messages = _read_jsonl_if_exists(conversation_source)
        conversation_text = _conversation_text(conversation_messages)
        if conversation_text.strip():
            conversation_domain = _classify_domain(
                conversation_text,
                _load_domain_registry(current_target),
                generated_at=generated_at,
                source_id=str(conversation_source),
            )
            domains.append(conversation_domain)
    _add_trace_knowledge(builder, trace_source)
    _add_tool_event_knowledge(builder, event_source)
    _add_conversation_knowledge(builder, conversation_source, domain=conversation_domain)
    if include_mining:
        _add_mining_knowledge(builder, trace_source, registry_path, plugin_path, k)

    additions, queue_count = _merge_review_queue(
        builder,
        queue_path=queue_target,
        current_dir=current_target,
        generated_at=generated_at,
    )
    delta_id = f"kg-delta-{_stable_hash(generated_at, trace_source, event_source, conversation_source, length=10)}"
    delta_path = delta_target / f"{delta_id}.json"
    delta = {
        "id": delta_id,
        "generated_at": generated_at,
        "trace_source": str(trace_source),
        "tool_events_path": str(event_source),
        "conversation_path": str(conversation_source) if conversation_source else "",
        "include_mining": include_mining,
        "domain_count": len(domains),
        "domains": sorted(domains, key=lambda item: str(item.get("id") or "")),
        "entity_count": len(builder.entities),
        "triple_count": len(builder.triples),
        "claim_count": len(builder.claims),
        "evidence_path_count": len(builder.evidence),
        "queued_count": len(additions),
        "review_queue_count": queue_count,
        "entities": sorted(builder.entities.values(), key=lambda item: item["id"]),
        "triples": sorted(builder.triples.values(), key=lambda item: item["id"]),
        "claims": sorted(builder.claims.values(), key=lambda item: item["id"]),
        "evidence_paths": sorted(builder.evidence.values(), key=lambda item: item["id"]),
    }
    write_json(delta_path, delta)
    return {
        "status": "ok",
        "delta_id": delta_id,
        "delta_path": str(delta_path),
        "review_queue_path": str(queue_target),
        "queued_count": len(additions),
        "review_queue_count": queue_count,
        "domain_count": len(domains),
        "domains": sorted(domains, key=lambda item: str(item.get("id") or "")),
        "domain_registry_path": str(_domain_registry_path(current_target)),
        "entity_count": len(builder.entities),
        "triple_count": len(builder.triples),
        "claim_count": len(builder.claims),
        "evidence_path_count": len(builder.evidence),
    }


def review_kg_delta(
    review_id: str | None = None,
    *,
    status: str = "accepted",
    note: str = "",
    reviewer: str = "",
    queue_path: str | Path | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    queue_target = Path(queue_path) if queue_path else KG_REVIEW_QUEUE_PATH
    queue = _read_jsonl_if_exists(queue_target)
    if not review_id:
        pending = [item for item in queue if item.get("status") == "pending"]
        return {
            "status": "ok",
            "review_queue_path": str(queue_target),
            "pending_count": len(pending),
            "items": pending[: max(1, limit)],
        }
    if status not in KG_REVIEW_STATUSES:
        raise ValueError(f"unknown KG review status: {status}")
    reviewed_at = _now()
    updated: dict[str, Any] | None = None
    for item in queue:
        if item.get("review_id") == review_id:
            item["status"] = status
            item["reviewed_at"] = reviewed_at
            item["reviewer"] = reviewer
            item["review_note"] = note
            if isinstance(item.get("item"), dict):
                item["item"]["status"] = status
            updated = item
            break
    if updated is None:
        raise ValueError(f"KG review id not found: {review_id}")
    write_jsonl(queue_target, queue)
    return {
        "status": "ok",
        "review_queue_path": str(queue_target),
        "review_id": review_id,
        "review_status": status,
        "item": updated,
    }


def _load_current_records(current_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        _read_jsonl_if_exists(current_dir / "entities.jsonl"),
        _read_jsonl_if_exists(current_dir / "triples.jsonl"),
        _read_jsonl_if_exists(current_dir / "claims.jsonl"),
        _read_jsonl_if_exists(current_dir / "evidence_paths.jsonl"),
    )


def _append_unique(records: list[dict[str, Any]], additions: list[dict[str, Any]]) -> int:
    seen = {str(item.get("id")) for item in records if item.get("id")}
    added = 0
    for item in additions:
        item_id = str(item.get("id") or "")
        if item_id and item_id not in seen:
            records.append(item)
            seen.add(item_id)
            added += 1
    return added


def _entry_domain_ids(entry: dict[str, Any], item: dict[str, Any] | None = None) -> list[str]:
    values = [str(value) for value in _safe_list(entry.get("domain_ids")) if str(value).strip()]
    primary = str(entry.get("primary_domain_id") or "").strip()
    if primary:
        values.insert(0, primary)
    if item:
        values.extend(_item_domain_ids(item))
    return list(dict.fromkeys(values))


def _domain_record_from_entry(entry: dict[str, Any], domain_id: str, applied_at: str) -> dict[str, Any]:
    domain_entity = next(
        (
            entity
            for entity in _safe_list(entry.get("entities"))
            if isinstance(entity, dict) and str(entity.get("id") or "") == domain_id
        ),
        {},
    )
    properties = _safe_properties(domain_entity)
    evidence_paths = [item for item in _safe_list(entry.get("evidence_paths")) if isinstance(item, dict)]
    source_ids = list(
        dict.fromkeys(
            str(item.get("path") or item.get("source_id") or "")
            for item in evidence_paths
            if item.get("path") or item.get("source_id")
        )
    )
    return {
        "id": domain_id,
        "label": str(domain_entity.get("label") or _domain_key(domain_id)),
        "summary": str(properties.get("summary") or ""),
        "keywords": [str(value) for value in _safe_list(properties.get("keywords"))],
        "classifier": str(properties.get("classifier") or "local_tfidf"),
        "domain_confidence": entry.get("domain_confidence") or _safe_properties(entry.get("item") or {}).get("domain_confidence") or "",
        "match_score": entry.get("domain_confidence") or "",
        "created_at": str(domain_entity.get("created_at") or applied_at),
        "updated_at": applied_at,
        "source_ids": source_ids,
        "conversation_count": len(source_ids),
        "fact_count": 0,
        "entity_count": 0,
        "evidence_path_count": 0,
    }


def _write_domain_entry(current_dir: Path, entry: dict[str, Any]) -> set[str]:
    item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
    if not item:
        return set()
    domain_ids = _entry_domain_ids(entry, item)
    if not domain_ids:
        return set()
    touched: set[str] = set()
    for domain_id in domain_ids:
        domain_target = _domain_current_dir(current_dir, domain_id)
        domain_target.mkdir(parents=True, exist_ok=True)
        entities, triples, claims, evidence = _load_current_records(domain_target)
        entry_entities = [entity for entity in _safe_list(entry.get("entities")) if isinstance(entity, dict)]
        if not any(str(entity.get("id") or "") == domain_id for entity in entry_entities):
            entry_entities.append(_domain_entity_record({"id": domain_id, "label": _domain_key(domain_id)}, str(item.get("created_at") or _now())))
        _append_unique(entities, entry_entities)
        _append_unique(evidence, [path for path in _safe_list(entry.get("evidence_paths")) if isinstance(path, dict)])
        item_id = str(item.get("id") or "")
        if entry.get("kind") == "claim" or item_id.startswith("claim:"):
            _append_unique(claims, [item])
        else:
            _append_unique(triples, [item])
        write_jsonl(domain_target / "entities.jsonl", sorted(entities, key=lambda value: str(value.get("id") or "")))
        write_jsonl(domain_target / "triples.jsonl", sorted(triples, key=lambda value: str(value.get("id") or "")))
        write_jsonl(domain_target / "claims.jsonl", sorted(claims, key=lambda value: str(value.get("id") or "")))
        write_jsonl(domain_target / "evidence_paths.jsonl", sorted(evidence, key=lambda value: str(value.get("id") or "")))
        touched.add(domain_id)
    return touched


def _refresh_domain_registry(current_dir: Path, applied_entries: list[dict[str, Any]], applied_at: str) -> list[dict[str, Any]]:
    if not applied_entries:
        return _load_domain_registry(current_dir)
    registry_path = _domain_registry_path(current_dir)
    records_by_id = {
        str(item.get("id") or ""): dict(item)
        for item in _read_jsonl_if_exists(registry_path)
        if item.get("id")
    }
    touched: set[str] = set()
    for entry in applied_entries:
        item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
        for domain_id in _entry_domain_ids(entry, item):
            touched.add(domain_id)
            incoming = _domain_record_from_entry(entry, domain_id, applied_at)
            current = records_by_id.get(domain_id, {})
            existing_keywords = [str(value) for value in _safe_list(current.get("keywords"))]
            incoming_keywords = [str(value) for value in _safe_list(incoming.get("keywords"))]
            existing_sources = [str(value) for value in _safe_list(current.get("source_ids"))]
            incoming_sources = [str(value) for value in _safe_list(incoming.get("source_ids"))]
            records_by_id[domain_id] = {
                **incoming,
                **current,
                "id": domain_id,
                "label": str(current.get("label") or incoming.get("label") or _domain_key(domain_id)),
                "summary": str(current.get("summary") or incoming.get("summary") or ""),
                "keywords": list(dict.fromkeys([*existing_keywords, *incoming_keywords]))[:12],
                "source_ids": list(dict.fromkeys([*existing_sources, *incoming_sources])),
                "classifier": str(current.get("classifier") or incoming.get("classifier") or "local_tfidf"),
                "created_at": str(current.get("created_at") or incoming.get("created_at") or applied_at),
                "updated_at": applied_at,
            }
    for domain_id in touched:
        entities, triples, claims, evidence = _load_current_records(_domain_current_dir(current_dir, domain_id))
        record = records_by_id.get(domain_id, {"id": domain_id, "label": _domain_key(domain_id)})
        source_ids = [str(value) for value in _safe_list(record.get("source_ids"))]
        record["fact_count"] = len(triples) + len(claims)
        record["entity_count"] = len(entities)
        record["evidence_path_count"] = len(evidence)
        record["conversation_count"] = len(source_ids)
        records_by_id[domain_id] = record
    records = sorted(records_by_id.values(), key=lambda item: str(item.get("id") or ""))
    write_jsonl(registry_path, records)
    return records


def apply_kg_delta(
    *,
    queue_path: str | Path | None = None,
    current_dir: str | Path | None = None,
) -> dict[str, Any]:
    queue_target = Path(queue_path) if queue_path else KG_REVIEW_QUEUE_PATH
    current_target = Path(current_dir) if current_dir else KG_CURRENT_DIR
    current_target.mkdir(parents=True, exist_ok=True)
    queue = _read_jsonl_if_exists(queue_target)
    entities, triples, claims, evidence = _load_current_records(current_target)
    applied_at = _now()
    added_entities = added_triples = added_claims = added_evidence = 0
    applied_ids: list[str] = []
    applied_entries: list[dict[str, Any]] = []
    touched_domains: set[str] = set()
    existing_fact_ids = {str(item.get("id")) for item in triples + claims if item.get("id")}
    for entry in queue:
        if entry.get("status") != "accepted":
            continue
        item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
        item_id = str(item.get("id") or entry.get("review_id") or "")
        if not item_id or item_id in existing_fact_ids:
            continue
        item = {**item, "status": "accepted", "applied_at": applied_at}
        added_entities += _append_unique(entities, _safe_list(entry.get("entities")))
        added_evidence += _append_unique(evidence, _safe_list(entry.get("evidence_paths")))
        if entry.get("kind") == "claim" or item_id.startswith("claim:"):
            added_claims += _append_unique(claims, [item])
        else:
            added_triples += _append_unique(triples, [item])
        entry["item"] = item
        entry["applied_at"] = applied_at
        applied_entries.append(entry)
        touched_domains.update(_write_domain_entry(current_target, entry))
        applied_ids.append(item_id)
        existing_fact_ids.add(item_id)
    write_jsonl(current_target / "entities.jsonl", sorted(entities, key=lambda item: str(item.get("id") or "")))
    write_jsonl(current_target / "triples.jsonl", sorted(triples, key=lambda item: str(item.get("id") or "")))
    write_jsonl(current_target / "claims.jsonl", sorted(claims, key=lambda item: str(item.get("id") or "")))
    write_jsonl(current_target / "evidence_paths.jsonl", sorted(evidence, key=lambda item: str(item.get("id") or "")))
    domain_registry = _refresh_domain_registry(current_target, applied_entries, applied_at)
    if queue_target.exists():
        write_jsonl(queue_target, queue)
    return {
        "status": "ok",
        "current_dir": str(current_target),
        "domain_registry_path": str(_domain_registry_path(current_target)),
        "domain_count": len(domain_registry),
        "touched_domains": sorted(touched_domains),
        "applied_count": len(applied_ids),
        "applied_ids": applied_ids,
        "added_entities": added_entities,
        "added_triples": added_triples,
        "added_claims": added_claims,
        "added_evidence_paths": added_evidence,
    }


def _entity_labels(entities: list[dict[str, Any]]) -> dict[str, str]:
    return {str(item.get("id")): str(item.get("label") or item.get("id")) for item in entities}


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", value.lower()) if len(token) >= 2}


def _kg_record_text(item: dict[str, Any], labels: dict[str, str], kind: str) -> str:
    if kind == "entity":
        return " ".join(
            [
                str(item.get("id") or ""),
                str(item.get("kind") or ""),
                str(item.get("label") or ""),
            ]
        )
    if kind == "claim":
        return " ".join(
            [
                str(item.get("id") or ""),
                str(item.get("text") or ""),
                labels.get(str(item.get("subject")), str(item.get("subject") or "")),
                str(item.get("predicate") or ""),
                labels.get(str(item.get("object")), str(item.get("object") or "")),
                str(item.get("source_type") or ""),
            ]
        )
    return " ".join(
        [
            str(item.get("id") or ""),
            labels.get(str(item.get("subject")), str(item.get("subject") or "")),
            str(item.get("predicate") or ""),
            labels.get(str(item.get("object")), str(item.get("object") or "")),
            str(item.get("source_type") or ""),
            json.dumps(item.get("properties") or {}, ensure_ascii=False, sort_keys=True),
        ]
    )


def _kg_vector_documents(
    entities: list[dict[str, Any]],
    triples: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    labels: dict[str, str],
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    entity_ids_in_facts: set[str] = set()
    for item in triples:
        if strict and item.get("status") != "accepted":
            continue
        entity_ids_in_facts.update([str(item.get("subject") or ""), str(item.get("object") or "")])
        docs.append({"id": str(item.get("id") or ""), "kind": "triple", "record": item, "text": _kg_record_text(item, labels, "triple")})
    for item in claims:
        if strict and item.get("status") != "accepted":
            continue
        for field in ("subject", "object"):
            if item.get(field):
                entity_ids_in_facts.add(str(item.get(field)))
        docs.append({"id": str(item.get("id") or ""), "kind": "claim", "record": item, "text": _kg_record_text(item, labels, "claim")})
    for item in entities:
        if not strict or str(item.get("id") or "") in entity_ids_in_facts:
            docs.append({"id": str(item.get("id") or ""), "kind": "entity", "record": item, "text": _kg_record_text(item, labels, "entity")})
    return [item for item in docs if item["id"] and item["text"].strip()]


def _kg_graph_neighbors(seed_ids: set[str], triples: list[dict[str, Any]], claims: list[dict[str, Any]], *, strict: bool) -> set[str]:
    expanded = set(seed_ids)
    entity_frontier = {
        str(item_id)
        for item_id in seed_ids
        if not str(item_id).startswith(("triple:", "claim:"))
    }
    for item in triples:
        if strict and item.get("status") != "accepted":
            continue
        triple_id = str(item.get("id") or "")
        subject = str(item.get("subject") or "")
        object_id = str(item.get("object") or "")
        if triple_id in seed_ids or subject in entity_frontier or object_id in entity_frontier:
            expanded.update([triple_id, subject, object_id])
    for item in claims:
        if strict and item.get("status") != "accepted":
            continue
        claim_id = str(item.get("id") or "")
        subject = str(item.get("subject") or "")
        object_id = str(item.get("object") or "")
        if claim_id in seed_ids or subject in entity_frontier or object_id in entity_frontier:
            expanded.update(value for value in [claim_id, subject, object_id] if value)
    return expanded


def configure_embedding_model_factory(factory: Callable[[str], Any] | None) -> None:
    global _SENTENCE_TRANSFORMER_FACTORY
    _SENTENCE_TRANSFORMER_FACTORY = factory


def _normalize_vector_backend(value: str | None) -> str:
    backend = (value or os.environ.get(KG_VECTOR_BACKEND_ENV) or DEFAULT_KG_VECTOR_BACKEND).strip().lower()
    aliases = {
        "dense": "dense",
        "embedding": "dense",
        "embeddings": "dense",
        "sentence_transformers": "dense",
        "sentence-transformers": "dense",
        "tfidf": "tfidf",
        "local_tfidf": "tfidf",
        "auto": "auto",
    }
    if backend not in aliases:
        raise ValueError(f"Unsupported KG vector backend: {value}")
    return aliases[backend]


def _kg_embedding_model_name(value: str | None = None) -> str:
    return (value or os.environ.get(KG_EMBEDDING_MODEL_ENV) or DEFAULT_KG_EMBEDDING_MODEL).strip()


def _ensure_hf_endpoint(hf_endpoint: str | None = None) -> str:
    endpoint = (hf_endpoint or os.environ.get(KG_HF_ENDPOINT_ENV) or os.environ.get("HF_ENDPOINT") or DEFAULT_HF_ENDPOINT).strip()
    if endpoint and "HF_ENDPOINT" not in os.environ:
        os.environ["HF_ENDPOINT"] = endpoint
    return endpoint


def _load_sentence_transformer(model_name: str, hf_endpoint: str | None = None) -> Any:
    _ensure_hf_endpoint(hf_endpoint)
    if _SENTENCE_TRANSFORMER_FACTORY is not None:
        return _SENTENCE_TRANSFORMER_FACTORY(model_name)
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for dense KG graph-vector retrieval. "
            "Install the optional full extra or run `pip install sentence-transformers`."
        ) from exc
    return SentenceTransformer(model_name)


def _as_float_vectors(raw_vectors: Any) -> list[list[float]]:
    if hasattr(raw_vectors, "tolist"):
        raw_vectors = raw_vectors.tolist()
    return [[float(value) for value in vector] for vector in raw_vectors]


def _normalize_dense(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [value / norm for value in vector]


def _dense_encode(model: Any, texts: list[str]) -> list[list[float]]:
    try:
        encoded = model.encode(texts, normalize_embeddings=True, convert_to_numpy=False)
    except TypeError:
        encoded = model.encode(texts)
    return [_normalize_dense(vector) for vector in _as_float_vectors(encoded)]


def _dense_nearest(
    query: str,
    documents: list[dict[str, Any]],
    *,
    model_name: str,
    hf_endpoint: str | None,
    limit: int,
) -> tuple[list[tuple[int, float]], dict[str, Any]]:
    model = _load_sentence_transformer(model_name, hf_endpoint)
    texts = [item["text"] for item in documents]
    vectors = _dense_encode(model, texts)
    query_vectors = _dense_encode(model, [query])
    query_vector = query_vectors[0] if query_vectors else []
    ranked = sorted(
        ((index, cosine(query_vector, vector)) for index, vector in enumerate(vectors)),
        key=lambda item: item[1],
        reverse=True,
    )[:limit]
    dimension = len(vectors[0]) if vectors else 0
    return ranked, {
        "backend": "dense_embedding",
        "model": model_name,
        "hf_endpoint": _ensure_hf_endpoint(hf_endpoint),
        "embedding_dimension": dimension,
    }


def _tfidf_nearest(query: str, documents: list[dict[str, Any]], *, limit: int) -> tuple[list[tuple[int, float]], dict[str, Any]]:
    store = FeatureStore.from_documents([item["text"] for item in documents], max_features=1000)
    return store.nearest(query, limit=limit), {
        "backend": "local_tfidf",
        "vocabulary_size": len(store.vocabulary),
    }


def _kg_vector_nearest(
    query: str,
    documents: list[dict[str, Any]],
    *,
    backend: str,
    embedding_model: str | None,
    hf_endpoint: str | None,
    limit: int,
) -> tuple[list[tuple[int, float]], dict[str, Any]]:
    requested = _normalize_vector_backend(backend)
    model_name = _kg_embedding_model_name(embedding_model)
    if requested in {"auto", "dense"}:
        try:
            return _dense_nearest(query, documents, model_name=model_name, hf_endpoint=hf_endpoint, limit=limit)
        except Exception as exc:
            if requested == "dense":
                raise
            nearest, metadata = _tfidf_nearest(query, documents, limit=limit)
            metadata["fallback_reason"] = str(exc)
            metadata["requested_backend"] = "auto"
            metadata["requested_model"] = model_name
            return nearest, metadata
    nearest, metadata = _tfidf_nearest(query, documents, limit=limit)
    metadata["requested_backend"] = "tfidf"
    return nearest, metadata


def _retrieval_mode(vector_index: dict[str, Any]) -> str:
    if vector_index.get("backend") == "dense_embedding":
        return "graph_vector_dense"
    return "graph_vector_tfidf"


def graph_vector_search(
    query: str,
    *,
    strict: bool = True,
    include_pending: bool = False,
    current_dir: str | Path | None = None,
    domain: str | None = None,
    queue_path: str | Path | None = None,
    top_k: int = 5,
    expand_hops: int = 1,
    vector_backend: str | None = None,
    embedding_model: str | None = None,
    hf_endpoint: str | None = None,
) -> dict[str, Any]:
    base_current = Path(current_dir) if current_dir else KG_CURRENT_DIR
    current_target = _resolve_current_dir_for_domain(base_current, domain)
    entities, triples, claims, evidence = _load_current_records(current_target)
    if include_pending:
        for entry in _read_jsonl_if_exists(queue_path or KG_REVIEW_QUEUE_PATH):
            if domain and not any(_domain_matches(domain, value) for value in _entry_domain_ids(entry)):
                continue
            item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
            if not item:
                continue
            if entry.get("kind") == "claim" or str(item.get("id") or "").startswith("claim:"):
                claims.append(item)
            else:
                triples.append(item)
    labels = _entity_labels(entities)
    documents = _kg_vector_documents(entities, triples, claims, labels, strict=strict)
    if not documents:
        requested_backend = _normalize_vector_backend(vector_backend)
        model_name = _kg_embedding_model_name(embedding_model)
        return {
            "status": "empty",
            "retrieval_mode": "graph_vector_dense" if requested_backend == "dense" else "graph_vector_tfidf",
            "query": query,
            "domain": str(domain or ""),
            "current_dir": str(current_target),
            "strict": strict,
            "include_pending": include_pending,
            "seed_hits": [],
            "subgraph": {"entities": [], "triples": [], "claims": [], "evidence_paths": []},
            "vector_index": {
                "document_count": 0,
                "vocabulary_size": 0,
                "backend": "dense_embedding" if requested_backend == "dense" else "local_tfidf",
                "model": model_name if requested_backend == "dense" else "",
            },
        }
    nearest, vector_index = _kg_vector_nearest(
        query,
        documents,
        backend=vector_backend or "",
        embedding_model=embedding_model,
        hf_endpoint=hf_endpoint,
        limit=max(1, min(len(documents), top_k * 3)),
    )
    retrieval_mode = _retrieval_mode(vector_index)
    seed_hits: list[dict[str, Any]] = []
    for index, score in nearest:
        if score <= 0:
            continue
        doc = documents[index]
        seed_hits.append(
            {
                "id": doc["id"],
                "kind": doc["kind"],
                "score": round(float(score), 6),
                "text": _short_text(doc["text"], 260),
            }
        )
        if len(seed_hits) >= max(1, top_k):
            break
    if not seed_hits:
        return {
            "status": "no_match",
            "retrieval_mode": retrieval_mode,
            "query": query,
            "domain": str(domain or ""),
            "current_dir": str(current_target),
            "strict": strict,
            "include_pending": include_pending,
            "seed_hits": [],
            "subgraph": {"entities": [], "triples": [], "claims": [], "evidence_paths": []},
            "vector_index": {
                **vector_index,
                "document_count": len(documents),
            },
        }
    included_ids = {hit["id"] for hit in seed_hits}
    for _ in range(max(0, expand_hops)):
        included_ids = _kg_graph_neighbors(included_ids, triples, claims, strict=strict)
    entity_by_id = {str(item.get("id") or ""): item for item in entities}
    triple_by_id = {str(item.get("id") or ""): item for item in triples if not strict or item.get("status") == "accepted"}
    claim_by_id = {str(item.get("id") or ""): item for item in claims if not strict or item.get("status") == "accepted"}
    subgraph_triples = [item for item_id, item in triple_by_id.items() if item_id in included_ids]
    subgraph_claims = [item for item_id, item in claim_by_id.items() if item_id in included_ids]
    subgraph_entity_ids = {
        str(value)
        for item in subgraph_triples
        for value in (item.get("subject"), item.get("object"))
        if value
    }
    subgraph_entity_ids.update(
        str(value)
        for item in subgraph_claims
        for value in (item.get("subject"), item.get("object"))
        if value
    )
    subgraph_entity_ids.update(item_id for item_id in included_ids if item_id in entity_by_id)
    evidence_by_id = {str(item.get("id") or ""): item for item in evidence}
    evidence_ids: set[str] = set()
    for item in [*subgraph_triples, *subgraph_claims]:
        evidence_ids.update(str(value) for value in _safe_list(item.get("evidence")) if value)
    subgraph = {
        "entities": [entity_by_id[item_id] for item_id in sorted(subgraph_entity_ids) if item_id in entity_by_id],
        "triples": sorted(subgraph_triples, key=lambda item: str(item.get("id") or "")),
        "claims": sorted(subgraph_claims, key=lambda item: str(item.get("id") or "")),
        "evidence_paths": [evidence_by_id[item_id] for item_id in sorted(evidence_ids) if item_id in evidence_by_id],
    }
    return {
        "status": "ok",
        "retrieval_mode": retrieval_mode,
        "query": query,
        "domain": str(domain or ""),
        "current_dir": str(current_target),
        "strict": strict,
        "include_pending": include_pending,
        "seed_hits": seed_hits,
        "subgraph": subgraph,
        "vector_index": {
            **vector_index,
            "document_count": len(documents),
            "graph_expansion_hops": expand_hops,
        },
    }


def answer_kg(
    query: str,
    *,
    strict: bool = True,
    include_pending: bool = False,
    current_dir: str | Path | None = None,
    domain: str | None = None,
    queue_path: str | Path | None = None,
    max_paths: int = 5,
    vector_backend: str | None = None,
    embedding_model: str | None = None,
    hf_endpoint: str | None = None,
) -> dict[str, Any]:
    search = graph_vector_search(
        query,
        strict=strict,
        include_pending=include_pending,
        current_dir=current_dir,
        domain=domain,
        queue_path=queue_path,
        top_k=max_paths,
        expand_hops=1,
        vector_backend=vector_backend,
        embedding_model=embedding_model,
        hf_endpoint=hf_endpoint,
    )
    retrieval_mode = str(search.get("retrieval_mode") or "graph_vector_tfidf")
    subgraph = search.get("subgraph") if isinstance(search.get("subgraph"), dict) else {}
    triples = _safe_list(subgraph.get("triples"))
    claims = _safe_list(subgraph.get("claims"))
    entities = _safe_list(subgraph.get("entities"))
    evidence_paths = _safe_list(subgraph.get("evidence_paths"))
    labels = _entity_labels([item for item in entities if isinstance(item, dict)])
    facts_source = [item for item in [*triples, *claims] if isinstance(item, dict)]
    if search.get("status") != "ok" or not facts_source:
        return {
            "status": "insufficient",
            "strict": strict,
            "include_pending": include_pending,
            "domain": str(domain or ""),
            "retrieval_mode": retrieval_mode,
            "answer": "知识图谱证据不足（KG insufficient）：没有为这个问题召回已审核的事实子图。",
            "missing": ["向量召回的已审核三元组或声明", "图扩展后的已审核证据路径"],
            "evidence_paths": [],
            "facts": [],
            "retrieval": search,
        }
    facts: list[dict[str, Any]] = []
    for item in facts_source[: max(1, max_paths)]:
        fact = {
            "id": item.get("id", ""),
            "subject": labels.get(str(item.get("subject")), str(item.get("subject") or "")),
            "predicate": item.get("predicate", ""),
            "object": labels.get(str(item.get("object")), str(item.get("object") or "")),
            "text": item.get("text", ""),
            "confidence": item.get("confidence", 0),
            "status": item.get("status", ""),
        }
        facts.append(fact)
    predicate_labels = {
        "USES_SKILL": "使用技能",
        "DESCRIBES_TASK": "对应任务",
        "USES_TOOL": "使用工具",
        "RELATED_TO": "关联",
        "SUPPORTS": "支持",
    }
    fact_lines = []
    for fact in facts:
        if fact["text"]:
            fact_lines.append(f"{fact['text']}（置信度 {fact['confidence']}）")
        else:
            predicate = str(fact["predicate"] or "")
            relation_label = predicate_labels.get(predicate)
            relation = f"{relation_label}（{predicate}）" if relation_label else predicate
            fact_lines.append(
                f"{fact['subject']} {relation} {fact['object']}（置信度 {fact['confidence']}）"
            )
    return {
        "status": "ok",
        "strict": strict,
        "include_pending": include_pending,
        "domain": str(domain or ""),
        "retrieval_mode": retrieval_mode,
        "answer": "已从已审核知识图谱找到相关证据：" + "；".join(fact_lines),
        "facts": facts,
        "evidence_paths": evidence_paths[: max(1, max_paths)],
        "retrieval": search,
    }


def _confidence_bucket(value: Any) -> str:
    confidence = float(value or 0.0)
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.65:
        return "medium"
    return "low"


def _evidence_markdown(evidence: list[dict[str, Any]]) -> str:
    lines = ["# Evidence Paths", ""]
    if not evidence:
        lines.append("No accepted evidence paths are available.")
    for item in evidence:
        lines.extend(
            [
                f"## {item.get('id', '')}",
                "",
                f"- Source type: `{item.get('source_type', '')}`",
                f"- Source id: `{item.get('source_id', '')}`",
                f"- Path: `{item.get('path', '')}`",
                f"- URL: `{item.get('url', '')}`",
                f"- Confidence: `{item.get('confidence', 0)}`",
                f"- Summary: {item.get('summary', '')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _confidence_markdown(triples: list[dict[str, Any]], claims: list[dict[str, Any]]) -> str:
    counter = Counter(_confidence_bucket(item.get("confidence")) for item in [*triples, *claims])
    source_counter = Counter(str(item.get("source_type") or "unknown") for item in [*triples, *claims])
    lines = [
        "# Confidence Summary",
        "",
        "## Buckets",
        "",
        f"- High (>=0.85): `{counter.get('high', 0)}`",
        f"- Medium (>=0.65 and <0.85): `{counter.get('medium', 0)}`",
        f"- Low (<0.65): `{counter.get('low', 0)}`",
        "",
        "## Source Types",
        "",
    ]
    if not source_counter:
        lines.append("- No accepted facts are available.")
    else:
        for source, count in sorted(source_counter.items()):
            lines.append(f"- `{source}`: `{count}`")
    return "\n".join(lines) + "\n"


def _graph_vector_index_payload(
    entities: list[dict[str, Any]],
    triples: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    labels: dict[str, str],
    *,
    vector_backend: str | None = None,
    embedding_model: str | None = None,
    hf_endpoint: str | None = None,
) -> dict[str, Any]:
    documents = _kg_vector_documents(entities, triples, claims, labels, strict=True)
    store = FeatureStore.from_documents([item["text"] for item in documents], max_features=1000) if documents else None
    rows: list[dict[str, Any]] = []
    inverse = {term_index: token for token, term_index in store.vocabulary.items()} if store else {}
    for index, item in enumerate(documents):
        vector = store.vectors[index] if store else []
        ranked = sorted(enumerate(vector), key=lambda pair: pair[1], reverse=True)
        sparse_terms = [
            {"term": inverse[token_index], "weight": round(float(value), 6)}
            for token_index, value in ranked
            if value > 0
        ]
        rows.append(
            {
                "id": item["id"],
                "kind": item["kind"],
                "text": item["text"],
                "top_terms": [entry["term"] for entry in sparse_terms[:8]],
                "sparse_vector": sparse_terms,
                "nonzero_terms": sum(1 for value in vector if value > 0),
            }
        )
    return {
        "schema": "diaevo.kg_graph_vector_index.v1",
        "backend": "local_tfidf",
        "dense_backend": {
            "backend": "dense_embedding",
            "model": _kg_embedding_model_name(embedding_model),
            "hf_endpoint": _ensure_hf_endpoint(hf_endpoint),
            "runtime_backend": _normalize_vector_backend(vector_backend),
            "note": "Use graph_vector_search(..., vector_backend='dense') or answer-kg --vector-backend dense for real embedding retrieval.",
        },
        "document_count": len(documents),
        "vocabulary_size": len(store.vocabulary) if store else 0,
        "documents": rows,
        "note": "本快照保存可复现 TF-IDF 稀疏索引元数据；运行时可切换 dense embedding 检索，先向量召回种子，再沿图关系扩展证据子图。",
    }


def _graph_vector_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 图结构向量检索索引",
        "",
        "本文件说明当前 KG 的 GraphRAG-like 检索层：先把已审核节点、关系和声明转成可检索文本，运行时可用 dense embedding 或本地 TF-IDF 召回候选，再沿图结构扩展证据子图。",
        "",
        f"- 快照后端：`{payload.get('backend', '')}`",
        f"- Dense 后端：`{dict(payload.get('dense_backend') or {}).get('backend', '')}`",
        f"- Dense 模型：`{dict(payload.get('dense_backend') or {}).get('model', '')}`",
        f"- HF 镜像：`{dict(payload.get('dense_backend') or {}).get('hf_endpoint', '')}`",
        f"- 可检索文档数：`{payload.get('document_count', 0)}`",
        f"- 词表大小：`{payload.get('vocabulary_size', 0)}`",
        "",
        "## 文档样例",
        "",
    ]
    for item in _safe_list(payload.get("documents"))[:20]:
        lines.extend(
            [
                f"### {item.get('id', '')}",
                "",
                f"- 类型：`{item.get('kind', '')}`",
                f"- 非零向量项：`{item.get('nonzero_terms', 0)}`",
                f"- 主要词项：`{', '.join(str(value) for value in _safe_list(item.get('top_terms')))}`",
                f"- 文本：{_short_text(item.get('text', ''), 320)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _sample_graph_vector_queries(entities: list[dict[str, Any]], triples: list[dict[str, Any]], claims: list[dict[str, Any]], labels: dict[str, str]) -> list[str]:
    queries: list[str] = []
    for item in triples:
        if item.get("status") != "accepted":
            continue
        subject = labels.get(str(item.get("subject")), str(item.get("subject") or ""))
        predicate = str(item.get("predicate") or "")
        object_label = labels.get(str(item.get("object")), str(item.get("object") or ""))
        if subject and predicate and object_label:
            queries.append(f"{subject} {predicate} {object_label}")
        if len(queries) >= 3:
            break
    for item in claims:
        if item.get("status") == "accepted" and item.get("text"):
            queries.append(str(item.get("text")))
        if len(queries) >= 3:
            break
    return queries or [str(item.get("label") or item.get("id")) for item in entities[:1]]


def _graph_vector_retrieval_report(
    entities: list[dict[str, Any]],
    triples: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    labels: dict[str, str],
    *,
    current_dir: str | Path | None,
    vector_backend: str | None = None,
    embedding_model: str | None = None,
    hf_endpoint: str | None = None,
) -> str:
    lines = [
        "# 图结构向量检索演示",
        "",
        "本报告展示 KG 回答不是单纯关键词匹配：系统先用向量检索召回相关 KG 文档，再沿 subject-object 图关系扩展证据子图。严格回答只允许使用 accepted 事实。",
        "",
    ]
    for query in _sample_graph_vector_queries(entities, triples, claims, labels):
        result = graph_vector_search(
            query,
            strict=True,
            current_dir=current_dir,
            top_k=3,
            expand_hops=1,
            vector_backend=vector_backend,
            embedding_model=embedding_model,
            hf_endpoint=hf_endpoint,
        )
        subgraph = result.get("subgraph") if isinstance(result.get("subgraph"), dict) else {}
        lines.extend(
            [
                f"## 查询：{query}",
                "",
                f"- 检索状态：`{result.get('status', '')}`",
                f"- 检索模式：`{result.get('retrieval_mode', '')}`",
                f"- 种子命中数：`{len(_safe_list(result.get('seed_hits')))}`",
                f"- 子图实体数：`{len(_safe_list(subgraph.get('entities')) if isinstance(subgraph, dict) else [])}`",
                f"- 子图关系数：`{len(_safe_list(subgraph.get('triples')) if isinstance(subgraph, dict) else [])}`",
                f"- 子图声明数：`{len(_safe_list(subgraph.get('claims')) if isinstance(subgraph, dict) else [])}`",
                "",
                "### 向量种子",
                "",
            ]
        )
        for hit in _safe_list(result.get("seed_hits")):
            lines.append(f"- `{hit.get('kind', '')}` `{hit.get('id', '')}` score=`{hit.get('score', 0)}`")
        lines.extend(["", "### 图扩展关系", ""])
        if isinstance(subgraph, dict):
            for item in _safe_list(subgraph.get("triples")):
                lines.append(
                    "- "
                    f"{labels.get(str(item.get('subject')), str(item.get('subject') or ''))} "
                    f"`{item.get('predicate', '')}` "
                    f"{labels.get(str(item.get('object')), str(item.get('object') or ''))} "
                    f"(confidence `{item.get('confidence', 0)}`)"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _graph_visualization_html(
    *,
    stamp: str,
    entities: list[dict[str, Any]],
    triples: list[dict[str, Any]],
    labels: dict[str, str],
    claims: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    title: str | None = None,
    export_id: str | None = None,
) -> str:
    page_title = title or f"可编辑知识图谱 {stamp}"
    payload_id = export_id or stamp
    nodes = [
        {
            "id": str(item.get("id", "")),
            "label": str(item.get("label") or item.get("id") or ""),
            "kind": str(item.get("kind") or "unknown"),
            "properties": item.get("properties") if isinstance(item.get("properties"), dict) else {},
            "created_at": str(item.get("created_at") or ""),
        }
        for item in entities
    ]
    links = [
        {
            "id": str(item.get("id") or _stable_hash(item.get("subject"), item.get("predicate"), item.get("object"))),
            "source": str(item.get("subject") or ""),
            "target": str(item.get("object") or ""),
            "relation": str(item.get("predicate") or ""),
            "confidence": float(item.get("confidence") or 0.0),
            "status": str(item.get("status") or ""),
            "source_type": str(item.get("source_type") or ""),
            "evidence": _safe_list(item.get("evidence")),
            "properties": item.get("properties") if isinstance(item.get("properties"), dict) else {},
            "created_at": str(item.get("created_at") or ""),
            "applied_at": str(item.get("applied_at") or ""),
        }
        for item in triples
    ]
    payload_json = json.dumps(
        {
            "schema": "diaevo.kg_editor.v1",
            "date": payload_id,
            "entities": nodes,
            "triples": links,
            "claims": claims or [],
            "evidence_paths": evidence or [],
        },
        ensure_ascii=False,
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(page_title)}</title>
  <style>
    :root {{
      --bg: #f7f5f0;
      --panel: #ffffff;
      --ink: #1d2329;
      --muted: #65707c;
      --line: #d8dde3;
      --trace: #2563eb;
      --tool: #059669;
      --skill: #b45309;
      --task: #7c3aed;
      --other: #64748b;
      --danger: #dc2626;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    }}
    header {{
      padding: 20px 24px 12px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.86);
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; font-weight: 700; letter-spacing: 0; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    main {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 390px;
      min-height: calc(100vh - 86px);
    }}
    #canvas {{ width: 100%; min-height: 620px; background: #fbfaf7; }}
    aside {{
      border-left: 1px solid var(--line);
      background: var(--panel);
      padding: 18px;
      overflow: auto;
    }}
    .legend {{ display: grid; gap: 8px; margin: 16px 0; }}
    .legend span {{ display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }}
    .dot {{ width: 11px; height: 11px; border-radius: 50%; display: inline-block; }}
    .card {{ border: 1px solid var(--line); border-radius: 6px; padding: 12px; margin-top: 12px; }}
    .card h2 {{ margin: 0 0 8px; font-size: 15px; }}
    .card p {{ margin: 6px 0; color: var(--muted); font-size: 13px; line-height: 1.5; }}
    label {{ display: grid; gap: 5px; margin: 9px 0; color: var(--muted); font-size: 12px; }}
    input, select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 8px;
      color: var(--ink);
      background: #fff;
      font: inherit;
      font-size: 13px;
    }}
    textarea {{ min-height: 72px; resize: vertical; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    button {{
      border: 1px solid var(--line);
      border-radius: 5px;
      background: #fff;
      padding: 8px 10px;
      cursor: pointer;
      color: var(--ink);
      font: inherit;
      font-size: 13px;
    }}
    button.primary {{ background: #1f2937; color: white; border-color: #1f2937; }}
    button.danger {{ color: var(--danger); }}
    button:hover {{ border-color: #94a3b8; }}
    .status {{ color: var(--muted); font-size: 12px; margin-top: 8px; line-height: 1.5; }}
    svg text {{ font-size: 12px; fill: var(--ink); pointer-events: none; }}
    .edge {{ stroke: #97a3b3; stroke-opacity: .68; cursor: pointer; }}
    .edge.selected {{ stroke: #111827; stroke-opacity: 1; }}
    .node {{ cursor: pointer; stroke: #fff; stroke-width: 1.5; }}
    .node.selected {{ stroke: #111827; stroke-width: 3; }}
    @media (max-width: 820px) {{
      main {{ grid-template-columns: 1fr; }}
      aside {{ border-left: 0; border-top: 1px solid var(--line); }}
      #canvas {{ min-height: 520px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(page_title)}</h1>
    <div class="meta">已审核实体 {len(nodes)} 个，已审核关系 {len(links)} 条。点击节点或关系即可编辑，拖拽节点可调整布局。</div>
  </header>
  <main>
    <svg id="canvas" role="img" aria-label="可编辑知识图谱"></svg>
    <aside>
      <strong>可编辑图谱</strong>
      <div class="legend">
        <span><i class="dot" style="background: var(--trace)"></i>Trace 轨迹</span>
        <span><i class="dot" style="background: var(--tool)"></i>Tool 工具</span>
        <span><i class="dot" style="background: var(--skill)"></i>Skill 技能</span>
        <span><i class="dot" style="background: var(--task)"></i>Task 任务</span>
      </div>
      <div class="card">
        <h2>节点属性</h2>
        <label>ID<input id="nodeId" placeholder="例如 manual:idea"></label>
        <div class="row">
          <label>类型<input id="nodeKind" placeholder="task / skill / tool"></label>
          <label>标签<input id="nodeLabel" placeholder="显示名称"></label>
        </div>
        <label>属性 JSON<textarea id="nodeProps" placeholder="{{}}"></textarea></label>
        <div class="actions">
          <button class="primary" onclick="saveNode()">保存节点</button>
          <button onclick="newNode()">新增节点</button>
          <button class="danger" onclick="deleteSelection()">删除选中</button>
        </div>
      </div>
      <div class="card">
        <h2>关系属性</h2>
        <label>ID<input id="edgeId" placeholder="自动生成或手动填写"></label>
        <div class="row">
          <label>起点<input id="edgeSource" list="nodeIds"></label>
          <label>终点<input id="edgeTarget" list="nodeIds"></label>
        </div>
        <datalist id="nodeIds"></datalist>
        <label>关系<input id="edgeRelation" placeholder="例如 USES_TOOL"></label>
        <div class="row">
          <label>置信度<input id="edgeConfidence" type="number" min="0" max="1" step="0.01"></label>
          <label>状态<select id="edgeStatus"><option>accepted</option><option>pending</option><option>needs_source</option><option>rejected</option><option>conflict</option><option>stale</option></select></label>
        </div>
        <label>属性 JSON<textarea id="edgeProps" placeholder="{{}}"></textarea></label>
        <div class="actions">
          <button class="primary" onclick="saveEdge()">保存关系</button>
          <button onclick="newEdge()">新增关系</button>
        </div>
      </div>
      <div class="card">
        <h2>保存</h2>
        <p>浏览器不能直接写回项目文件。这里可以保存浏览器草稿或导出编辑 JSON；需要写回项目时运行同一个 `kg` 命令应用导出的 JSON。</p>
        <div class="actions">
          <button onclick="saveDraft()">保存浏览器草稿</button>
          <button onclick="loadDraft()">载入浏览器草稿</button>
          <button class="primary" onclick="exportEdit()">导出编辑 JSON</button>
        </div>
        <div class="status" id="status">当前只展示 active KG 中 accepted 的事实。</div>
      </div>
    </aside>
  </main>
  <script>
    const initial = {payload_json};
    const draftKey = 'DiaEvo-kg-editor-' + initial.date;
    let nodes = structuredClone(initial.entities);
    let links = structuredClone(initial.triples);
    let claims = structuredClone(initial.claims || []);
    let evidencePaths = structuredClone(initial.evidence_paths || []);
    let selected = {{ type: '', id: '' }};
    const svg = document.getElementById('canvas');
    const status = document.getElementById('status');
    const color = kind => {{
      if (kind === 'trace') return '#2563eb';
      if (kind === 'tool') return '#059669';
      if (kind === 'skill') return '#b45309';
      if (kind === 'task') return '#7c3aed';
      return '#64748b';
    }};
    const byId = () => new Map(nodes.map(node => [node.id, node]));
    const edgeId = edge => edge.id || 'triple:' + hash([edge.source, edge.relation, edge.target].join('|'));
    const hash = text => Array.from(text).reduce((acc, char) => ((acc << 5) - acc + char.charCodeAt(0)) | 0, 0).toString(16).replace('-', 'n');
    const setStatus = text => {{ status.textContent = text; }};
    const parseJson = (text, fallback={{}}) => {{
      const raw = text.trim();
      if (!raw) return fallback;
      try {{ return JSON.parse(raw); }} catch (error) {{ throw new Error('JSON 格式错误：' + error.message); }}
    }};
    function size() {{
      const box = svg.getBoundingClientRect();
      return {{ width: box.width || 900, height: box.height || 620 }};
    }}
    function initPositions() {{
      const {{ width, height }} = size();
      const cx = width / 2;
      const cy = height / 2;
      const radius = Math.min(width, height) * 0.32;
      nodes.forEach((node, index) => {{
        const angle = (Math.PI * 2 * index) / Math.max(1, nodes.length);
        node.x = cx + Math.cos(angle) * radius;
        node.y = cy + Math.sin(angle) * radius;
        node.vx = 0;
        node.vy = 0;
      }});
    }}
    function refreshNodeList() {{
      document.getElementById('nodeIds').innerHTML = nodes.map(node => `<option value="${{escapeHtml(node.id)}}">${{escapeHtml(node.label)}}</option>`).join('');
    }}
    function render() {{
      const {{ width, height }} = size();
      svg.setAttribute('viewBox', `0 0 ${{width}} ${{height}}`);
      svg.innerHTML = '';
      refreshNodeList();
      const nodeMap = byId();
      for (const link of links) {{
        const source = nodeMap.get(link.source);
        const target = nodeMap.get(link.target);
        if (!source || !target) continue;
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        const id = edgeId(link);
        line.setAttribute('class', selected.type === 'edge' && selected.id === id ? 'edge selected' : 'edge');
        line.setAttribute('x1', source.x);
        line.setAttribute('y1', source.y);
        line.setAttribute('x2', target.x);
        line.setAttribute('y2', target.y);
        line.setAttribute('stroke-width', String(1 + link.confidence * 2));
        line.addEventListener('click', event => {{ event.stopPropagation(); selectEdge(id); }});
        svg.appendChild(line);
        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', (source.x + target.x) / 2);
        label.setAttribute('y', (source.y + target.y) / 2 - 5);
        label.setAttribute('text-anchor', 'middle');
        label.textContent = link.relation;
        label.addEventListener('click', event => {{ event.stopPropagation(); selectEdge(id); }});
        svg.appendChild(label);
      }}
      for (const node of nodes) {{
        const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('class', selected.type === 'node' && selected.id === node.id ? 'node selected' : 'node');
        circle.setAttribute('cx', node.x);
        circle.setAttribute('cy', node.y);
        circle.setAttribute('r', '17');
        circle.setAttribute('fill', color(node.kind));
        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', node.x);
        label.setAttribute('y', node.y + 31);
        label.setAttribute('text-anchor', 'middle');
        label.textContent = node.label.length > 24 ? node.label.slice(0, 23) + '…' : node.label;
        group.appendChild(circle);
        group.appendChild(label);
        group.addEventListener('click', event => {{ event.stopPropagation(); selectNode(node.id); }});
        enableDrag(group, node);
        svg.appendChild(group);
      }}
    }}
    function enableDrag(group, node) {{
      let dragging = false;
      group.addEventListener('pointerdown', event => {{
        dragging = true;
        node.fixed = true;
        group.setPointerCapture(event.pointerId);
      }});
      group.addEventListener('pointermove', event => {{
        if (!dragging) return;
        const rect = svg.getBoundingClientRect();
        node.x = event.clientX - rect.left;
        node.y = event.clientY - rect.top;
        render();
      }});
      group.addEventListener('pointerup', event => {{
        dragging = false;
        node.fixed = false;
        group.releasePointerCapture(event.pointerId);
      }});
    }}
    function selectNode(id) {{
      const node = nodes.find(item => item.id === id);
      if (!node) return;
      selected = {{ type: 'node', id }};
      document.getElementById('nodeId').value = node.id;
      document.getElementById('nodeKind').value = node.kind || '';
      document.getElementById('nodeLabel').value = node.label || '';
      document.getElementById('nodeProps').value = JSON.stringify(node.properties || {{}}, null, 2);
      setStatus('正在编辑节点：' + node.id);
      render();
    }}
    function selectEdge(id) {{
      const edge = links.find(item => edgeId(item) === id);
      if (!edge) return;
      selected = {{ type: 'edge', id }};
      document.getElementById('edgeId').value = edgeId(edge);
      document.getElementById('edgeSource').value = edge.source || '';
      document.getElementById('edgeTarget').value = edge.target || '';
      document.getElementById('edgeRelation').value = edge.relation || '';
      document.getElementById('edgeConfidence').value = edge.confidence ?? 0.5;
      document.getElementById('edgeStatus').value = edge.status || 'accepted';
      document.getElementById('edgeProps').value = JSON.stringify(edge.properties || {{}}, null, 2);
      setStatus('正在编辑关系：' + edgeId(edge));
      render();
    }}
    function saveNode() {{
      try {{
        const id = document.getElementById('nodeId').value.trim() || 'manual:' + Date.now();
        const existing = nodes.find(item => item.id === id);
        const node = existing || {{ id, created_at: new Date().toISOString() }};
        node.kind = document.getElementById('nodeKind').value.trim() || 'manual';
        node.label = document.getElementById('nodeLabel').value.trim() || id;
        node.properties = parseJson(document.getElementById('nodeProps').value, {{}});
        if (!existing) nodes.push(node);
        selectNode(id);
        setStatus('节点已更新：' + id);
      }} catch (error) {{ setStatus(error.message); }}
    }}
    function newNode() {{
      const id = 'manual:' + Date.now();
      nodes.push({{ id, kind: 'manual', label: '新节点', properties: {{}}, created_at: new Date().toISOString() }});
      selectNode(id);
    }}
    function saveEdge() {{
      try {{
        const source = document.getElementById('edgeSource').value.trim();
        const target = document.getElementById('edgeTarget').value.trim();
        const relation = document.getElementById('edgeRelation').value.trim();
        if (!source || !target || !relation) throw new Error('关系必须包含起点、终点和关系名。');
        if (!byId().has(source) || !byId().has(target)) throw new Error('起点或终点节点不存在。');
        const id = document.getElementById('edgeId').value.trim() || 'triple:' + hash([source, relation, target].join('|'));
        const existing = links.find(item => edgeId(item) === id);
        const edge = existing || {{ id, created_at: new Date().toISOString(), evidence: [] }};
        edge.id = id;
        edge.source = source;
        edge.target = target;
        edge.relation = relation;
        edge.confidence = Math.max(0, Math.min(1, Number(document.getElementById('edgeConfidence').value || 0.5)));
        edge.status = document.getElementById('edgeStatus').value || 'accepted';
        edge.source_type = edge.source_type || 'manual_edit';
        edge.properties = parseJson(document.getElementById('edgeProps').value, {{}});
        if (!existing) links.push(edge);
        selectEdge(id);
        setStatus('关系已更新：' + id);
      }} catch (error) {{ setStatus(error.message); }}
    }}
    function newEdge() {{
      const source = selected.type === 'node' ? selected.id : (nodes[0]?.id || '');
      const target = nodes.find(node => node.id !== source)?.id || source;
      const edge = {{ id: 'triple:' + Date.now(), source, target, relation: 'RELATED_TO', confidence: 0.8, status: 'accepted', source_type: 'manual_edit', evidence: [], properties: {{}}, created_at: new Date().toISOString() }};
      links.push(edge);
      selectEdge(edge.id);
    }}
    function deleteSelection() {{
      if (selected.type === 'node') {{
        nodes = nodes.filter(node => node.id !== selected.id);
        links = links.filter(edge => edge.source !== selected.id && edge.target !== selected.id);
        selected = {{ type: '', id: '' }};
        setStatus('节点及相关关系已删除。');
      }} else if (selected.type === 'edge') {{
        links = links.filter(edge => edgeId(edge) !== selected.id);
        selected = {{ type: '', id: '' }};
        setStatus('关系已删除。');
      }} else {{
        setStatus('请先选择节点或关系。');
      }}
      render();
    }}
    function currentEdit() {{
      return {{
        schema: 'diaevo.kg_editor.v1',
        exported_at: new Date().toISOString(),
        entities: nodes.map(node => ({{ id: node.id, kind: node.kind, label: node.label, properties: node.properties || {{}}, created_at: node.created_at || '' }})),
        triples: links.map(edge => ({{ id: edgeId(edge), subject: edge.source, predicate: edge.relation, object: edge.target, confidence: edge.confidence, status: edge.status || 'accepted', source_type: edge.source_type || 'manual_edit', evidence: edge.evidence || [], properties: edge.properties || {{}}, created_at: edge.created_at || '', applied_at: edge.applied_at || '' }})),
        claims,
        evidence_paths: evidencePaths,
      }};
    }}
    function saveDraft() {{
      localStorage.setItem(draftKey, JSON.stringify(currentEdit()));
      setStatus('已保存到浏览器本地草稿。');
    }}
    function loadDraft() {{
      const raw = localStorage.getItem(draftKey);
      if (!raw) {{ setStatus('没有找到浏览器本地草稿。'); return; }}
      const data = JSON.parse(raw);
      nodes = data.entities || [];
      links = (data.triples || []).map(edge => ({{ id: edge.id, source: edge.subject, target: edge.object, relation: edge.predicate, confidence: edge.confidence, status: edge.status, source_type: edge.source_type, evidence: edge.evidence || [], properties: edge.properties || {{}}, created_at: edge.created_at || '', applied_at: edge.applied_at || '' }}));
      claims = data.claims || [];
      evidencePaths = data.evidence_paths || [];
      selected = {{ type: '', id: '' }};
      setStatus('已载入浏览器本地草稿。');
      initPositions();
      render();
    }}
    function exportEdit() {{
      const blob = new Blob([JSON.stringify(currentEdit(), null, 2)], {{ type: 'application/json;charset=utf-8' }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'DiaEvo_kg_edit_' + initial.date + '.json';
      link.click();
      URL.revokeObjectURL(url);
      setStatus('已导出编辑 JSON。写回项目：.\\\\diaevo.ps1 kg --apply-edit <json路径> --approve');
    }}
    function escapeHtml(value) {{
      return String(value || '').replace(/[&<>"']/g, char => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[char]));
    }}
    svg.addEventListener('click', () => {{ selected = {{ type: '', id: '' }}; render(); }});
    initPositions();
    render();
    window.addEventListener('resize', () => {{ initPositions(); render(); }});
  </script>
</body>
</html>
"""


def export_kg_snapshot(
    *,
    date: str | None = None,
    output_dir: str | Path | None = None,
    current_dir: str | Path | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    stamp = _date_stamp(date)
    base_current = Path(current_dir) if current_dir else KG_CURRENT_DIR
    current_target = _resolve_current_dir_for_domain(base_current, domain)
    snapshot_dir = Path(output_dir) if output_dir else (KG_ROOT / stamp / _domain_key(domain) if domain else KG_ROOT / stamp)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    entities, triples, claims, evidence = _load_current_records(current_target)
    labels = _entity_labels(entities)
    graph_vector_index = _graph_vector_index_payload(entities, triples, claims, labels)
    _write_csv(
        snapshot_dir / "entities.csv",
        ["id", "kind", "label", "properties"],
        [
            {
                "id": item.get("id", ""),
                "kind": item.get("kind", ""),
                "label": item.get("label", ""),
                "properties": item.get("properties", {}),
            }
            for item in entities
        ],
    )
    _write_csv(
        snapshot_dir / "triples.csv",
        ["id", "subject", "predicate", "object", "confidence", "status", "source_type", "evidence"],
        [
            {
                "id": item.get("id", ""),
                "subject": labels.get(str(item.get("subject")), str(item.get("subject", ""))),
                "predicate": item.get("predicate", ""),
                "object": labels.get(str(item.get("object")), str(item.get("object", ""))),
                "confidence": item.get("confidence", 0),
                "status": item.get("status", ""),
                "source_type": item.get("source_type", ""),
                "evidence": ", ".join(str(value) for value in _safe_list(item.get("evidence"))),
            }
            for item in triples
        ],
    )
    _write_csv(
        snapshot_dir / "claims.csv",
        ["id", "text", "subject", "predicate", "object", "confidence", "status", "source_type", "evidence"],
        [
            {
                "id": item.get("id", ""),
                "text": item.get("text", ""),
                "subject": labels.get(str(item.get("subject")), str(item.get("subject", ""))),
                "predicate": item.get("predicate", ""),
                "object": labels.get(str(item.get("object")), str(item.get("object", ""))),
                "confidence": item.get("confidence", 0),
                "status": item.get("status", ""),
                "source_type": item.get("source_type", ""),
                "evidence": ", ".join(str(value) for value in _safe_list(item.get("evidence"))),
            }
            for item in claims
        ],
    )
    _write_csv(
        snapshot_dir / "graph_edges.csv",
        ["source", "target", "relation", "confidence", "status", "source_type"],
        [
            {
                "source": item.get("subject", ""),
                "target": item.get("object", ""),
                "relation": item.get("predicate", ""),
                "confidence": item.get("confidence", 0),
                "status": item.get("status", ""),
                "source_type": item.get("source_type", ""),
            }
            for item in triples
        ],
    )
    (snapshot_dir / "evidence_paths.md").write_text(_evidence_markdown(evidence), encoding="utf-8")
    (snapshot_dir / "confidence_summary.md").write_text(_confidence_markdown(triples, claims), encoding="utf-8")
    write_json(snapshot_dir / "graph_vector_index.json", graph_vector_index)
    (snapshot_dir / "graph_vector_retrieval.md").write_text(_graph_vector_markdown(graph_vector_index), encoding="utf-8")
    (snapshot_dir / "graph_vector_demo.md").write_text(
        _graph_vector_retrieval_report(entities, triples, claims, labels, current_dir=current_target),
        encoding="utf-8",
    )
    (snapshot_dir / "graph_visualization.html").write_text(
        _graph_visualization_html(stamp=stamp, entities=entities, triples=triples, labels=labels),
        encoding="utf-8",
    )
    readme = "\n".join(
        [
            f"# {'领域' if domain else ''}知识图谱快照 {stamp}".strip(),
            "",
            "本文件夹是已审核 DiaEvo 知识图谱的可读导出，只包含 active KG 中 accepted 的事实和证据。"
            if not domain
            else f"本文件夹是已审核 DiaEvo 领域知识图谱 `{domain}` 的可读导出。",
            "",
            "## 摘要",
            "",
            f"- 已审核实体数量：`{len(entities)}`",
            f"- 已审核三元组数量：`{len(triples)}`",
            f"- 已审核声明数量：`{len(claims)}`",
            f"- 证据路径数量：`{len(evidence)}`",
            "",
            "## 文件说明",
            "",
            "- `graph_visualization.html`：可直接打开并编辑节点/关系的知识图谱工作台。",
            "- `entities.csv`：轨迹、工具、技能、消息、来源、簇、规则、序列等图节点。",
            "- `triples.csv`：带置信度和来源的已审核 subject-predicate-object 事实。",
            "- `claims.csv`：带置信度和来源的已审核文本声明。",
            "- `graph_edges.csv`：适合图可视化工具读取的边表。",
            "- `evidence_paths.md`：每条已审核事实背后的路径、URL 和摘要。",
            "- `confidence_summary.md`：置信度分桶和来源类型统计。",
            "- `graph_vector_index.json`：图结构向量检索索引，记录可检索 KG 文档、稀疏向量词项和索引元数据。",
            "- `graph_vector_retrieval.md`：图结构向量检索层说明和索引样例。",
            "- `graph_vector_demo.md`：示例查询的向量召回种子与图扩展证据子图。",
        ]
    )
    (snapshot_dir / "README.md").write_text(readme + "\n", encoding="utf-8")
    summary = {
        "status": "ok",
        "date": stamp,
        "domain": str(domain or ""),
        "snapshot_dir": str(snapshot_dir),
        "visualization_path": str(snapshot_dir / "graph_visualization.html"),
        "current_dir": str(current_target),
        "entity_count": len(entities),
        "triple_count": len(triples),
        "claim_count": len(claims),
        "evidence_path_count": len(evidence),
        "graph_vector_document_count": graph_vector_index["document_count"],
        "graph_vector_vocabulary_size": graph_vector_index["vocabulary_size"],
        "retrieval_mode": "graph_vector_tfidf",
        "files": sorted(path.name for path in snapshot_dir.iterdir() if path.is_file()),
    }
    write_json(snapshot_dir / "summary.json", summary)
    summary["files"] = sorted(path.name for path in snapshot_dir.iterdir() if path.is_file())
    write_json(snapshot_dir / "summary.json", summary)
    return summary


def _write_current_graph_workbench(
    *,
    date: str | None = None,
    current_dir: str | Path | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    stamp = _date_stamp(date)
    base_current = Path(current_dir) if current_dir else KG_CURRENT_DIR
    current_target = _resolve_current_dir_for_domain(base_current, domain)
    current_target.mkdir(parents=True, exist_ok=True)
    entities, triples, claims, evidence = _load_current_records(current_target)
    labels = _entity_labels(entities)
    visualization_path = current_target / "graph_visualization.html"
    title = f"领域可编辑知识图谱：{domain}" if domain else "总体可编辑知识图谱"
    visualization_path.write_text(
        _graph_visualization_html(
            stamp=stamp,
            entities=entities,
            triples=triples,
            labels=labels,
            title=title,
            export_id=_domain_key(domain) if domain else "current",
        ),
        encoding="utf-8",
    )
    return {
        "status": "ok",
        "date": stamp,
        "domain": str(domain or ""),
        "base_current_dir": str(base_current),
        "current_dir": str(current_target),
        "visualization_path": str(visualization_path),
        "entity_count": len(entities),
        "triple_count": len(triples),
        "claim_count": len(claims),
        "evidence_path_count": len(evidence),
        "message": "已生成 active KG 的总体可编辑知识图谱 HTML；未生成日期快照。"
        if not domain
        else f"已生成领域 KG `{domain}` 的可编辑知识图谱 HTML；未生成日期快照。",
    }


def _open_current_graph_workbench(
    *,
    date: str | None = None,
    current_dir: str | Path | None = None,
    domain: str | None = None,
    port: int | None = None,
    open_browser: bool = True,
    browser_opener: Callable[[str], bool] = _open_browser,
    server_starter: Callable[..., dict[str, Any]] = _serve_directory_url,
) -> dict[str, Any]:
    summary = _write_current_graph_workbench(date=date, current_dir=current_dir, domain=domain)
    current_target = Path(summary["current_dir"])
    server = server_starter(current_target, "graph_visualization.html", port=port)
    opened = browser_opener(str(server["url"])) if open_browser else False
    return {
        **summary,
        **server,
        "opened": opened,
        "message": "已在本地端口打开 active KG 的总体可编辑知识图谱工作台；未生成日期快照。"
        if not domain
        else f"已在本地端口打开领域 KG `{domain}` 的可编辑知识图谱工作台；未生成日期快照。",
    }


def visualize_kg(
    *,
    date: str | None = None,
    output_dir: str | Path | None = None,
    current_dir: str | Path | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    summary = export_kg_snapshot(date=date, output_dir=output_dir, current_dir=current_dir, domain=domain)
    return {
        **summary,
        "message": "已生成可编辑知识图谱 HTML。打开 visualization_path 后可直接编辑节点和关系。",
    }


def apply_kg_edit(
    edit_path: str | Path,
    *,
    current_dir: str | Path | None = None,
    approve: bool = False,
) -> dict[str, Any]:
    target = Path(edit_path)
    data = read_json(target, default={})
    if not isinstance(data, dict):
        raise ValueError(f"KG edit file must be a JSON object: {target}")
    if data.get("schema") != "diaevo.kg_editor.v1":
        raise ValueError("KG edit file schema must be diaevo.kg_editor.v1")
    entities = [item for item in _safe_list(data.get("entities")) if isinstance(item, dict)]
    triples = [item for item in _safe_list(data.get("triples")) if isinstance(item, dict)]
    claims = [item for item in _safe_list(data.get("claims")) if isinstance(item, dict)]
    evidence = [item for item in _safe_list(data.get("evidence_paths")) if isinstance(item, dict)]
    entity_ids = {str(item.get("id") or "") for item in entities if item.get("id")}
    if not entity_ids:
        raise ValueError("KG edit must contain at least one entity")
    normalized_entities: list[dict[str, Any]] = []
    for item in entities:
        entity_id = str(item.get("id") or "").strip()
        if not entity_id:
            continue
        normalized_entities.append(
            {
                "id": entity_id,
                "kind": str(item.get("kind") or "manual"),
                "label": str(item.get("label") or entity_id),
                "properties": item.get("properties") if isinstance(item.get("properties"), dict) else {},
                "created_at": str(item.get("created_at") or _now()),
            }
        )
    normalized_triples: list[dict[str, Any]] = []
    for item in triples:
        subject = str(item.get("subject") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        object_id = str(item.get("object") or "").strip()
        if not subject or not predicate or not object_id:
            continue
        if subject not in entity_ids or object_id not in entity_ids:
            raise ValueError(f"KG edit triple references missing entity: {subject} {predicate} {object_id}")
        normalized_triples.append(
            {
                "id": str(item.get("id") or f"triple:{_stable_hash(subject, predicate, object_id, 'manual_edit', length=16)}"),
                "subject": subject,
                "predicate": predicate,
                "object": object_id,
                "confidence": round(max(0.0, min(1.0, float(item.get("confidence") or 0.8))), 4),
                "status": str(item.get("status") or "accepted"),
                "source_type": str(item.get("source_type") or "manual_edit"),
                "evidence": [str(value) for value in _safe_list(item.get("evidence"))],
                "properties": item.get("properties") if isinstance(item.get("properties"), dict) else {},
                "created_at": str(item.get("created_at") or _now()),
                "applied_at": _now(),
            }
        )
    if not approve:
        return {
            "status": "requires_approval",
            "message": "KG 编辑会覆盖 active KG 的 entities/triples/claims/evidence_paths。确认后使用 --approve。",
            "edit_path": str(target),
            "entity_count": len(normalized_entities),
            "triple_count": len(normalized_triples),
            "claim_count": len(claims),
            "evidence_path_count": len(evidence),
        }
    current_target = Path(current_dir) if current_dir else KG_CURRENT_DIR
    current_target.mkdir(parents=True, exist_ok=True)
    write_jsonl(current_target / "entities.jsonl", normalized_entities)
    write_jsonl(current_target / "triples.jsonl", normalized_triples)
    write_jsonl(current_target / "claims.jsonl", sorted(claims, key=lambda item: str(item.get("id") or "")))
    write_jsonl(current_target / "evidence_paths.jsonl", sorted(evidence, key=lambda item: str(item.get("id") or "")))
    return {
        "status": "ok",
        "message": "已将编辑后的知识图谱写回 active KG。",
        "current_dir": str(current_target),
        "entity_count": len(normalized_entities),
        "triple_count": len(normalized_triples),
        "claim_count": len(claims),
        "evidence_path_count": len(evidence),
    }


def kg_workbench(
    *,
    date: str | None = None,
    output_dir: str | Path | None = None,
    current_dir: str | Path | None = None,
    domain: str | None = None,
    edit_path: str | Path | None = None,
    approve: bool = False,
    port: int | None = None,
    open_browser: bool = True,
    browser_opener: Callable[[str], bool] = _open_browser,
    server_starter: Callable[..., dict[str, Any]] = _serve_directory_url,
) -> dict[str, Any]:
    if edit_path:
        return apply_kg_edit(edit_path, current_dir=current_dir, approve=approve)
    if output_dir:
        return visualize_kg(date=date, output_dir=output_dir, current_dir=current_dir, domain=domain)
    return _open_current_graph_workbench(
        date=date,
        current_dir=current_dir,
        domain=domain,
        port=port,
        open_browser=open_browser,
        browser_opener=browser_opener,
        server_starter=server_starter,
    )
