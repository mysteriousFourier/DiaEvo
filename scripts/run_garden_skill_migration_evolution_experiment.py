from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import shutil
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diaevo.deepseek_chat import DeepSeekConfig, chat_completion, config_from_env, extract_assistant_text
from diaevo.paths import REPORTS_DIR, ensure_project_dirs
from diaevo.skill_adapter import adapt_external_skill
from diaevo.storage import read_json, read_jsonl, write_json, write_jsonl
from diaevo.verifier import verify_skill


REFERENCE_URL = "https://github.com/ConardLi/garden-skills.git"
REFERENCE_COMMIT = "242324434eef1ab76850bc62358b57081f7d3749"
REFERENCE_SUBDIR = "skills/web-design-engineer"
DEFAULT_REFERENCE_REPO_DIR = ROOT / ".diaevo" / "reference_repos" / "garden-skills"


@dataclass(frozen=True)
class FrontendTask:
    task_id: str
    short_title: str
    user_prompt: str
    audience: str
    required_signals: tuple[str, ...]


@dataclass(frozen=True)
class StageSpec:
    stage_id: str
    filename: str
    short_title: str
    skill_role: str


class LLMClient(Protocol):
    def generate(self, *, stage: StageSpec, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        ...


class CacheAwareLLMClient(Protocol):
    def generate_with_messages(
        self,
        *,
        stage: StageSpec,
        messages: list[dict[str, Any]],
        trace_system_prompt: str,
        trace_user_prompt: str,
    ) -> dict[str, Any]:
        ...


TASK = FrontendTask(
    task_id="logistics_ticket_ops_dashboard",
    short_title="物流客服早班工单看板",
    user_prompt="给客服主管做一个每天早上看物流工单的页面，一眼知道急单、负责人、快超时和整体忙不忙。",
    audience="客服主管",
    required_signals=("急单", "负责人", "快超时", "整体忙不忙", "SLA", "工单队列", "移动端"),
)

TASK_PRESETS = {
    "logistics": TASK,
    "photography_portfolio": FrontendTask(
        task_id="photography_portfolio_exhibition",
        short_title="摄影师作品展",
        user_prompt="假装我是一个摄影师，想做一个自己的摄影作品展网页。",
        audience="摄影师作品访客",
        required_signals=("摄影作品", "作品展", "系列", "关于", "联系", "图片", "移动端"),
    ),
}

PHOTOGRAPHY_IMAGE_CANDIDATES = (
    {
        "id": "walking_together_chicago",
        "title": "Walking together - Chicago, United States",
        "src": "https://commons.wikimedia.org/wiki/Special:FilePath/Walking_together_-_Chicago%2C_United_States_-_Black_and_white_street_photography_%2821677974155%29.jpg",
        "download_src": "https://upload.wikimedia.org/wikipedia/commons/e/e3/Walking_together_-_Chicago%2C_United_States_-_Black_and_white_street_photography_%2821677974155%29.jpg",
        "source": "https://commons.wikimedia.org/wiki/File:Walking_together_-_Chicago,_United_States_-_Black_and_white_street_photography_(21677974155).jpg",
        "author": "Giuseppe Milo",
        "license": "CC BY 2.0",
        "license_family": "creative_commons",
    },
    {
        "id": "from_the_dark_chicago",
        "title": "From the dark - Chicago, United States",
        "src": "https://commons.wikimedia.org/wiki/Special:FilePath/From_the_dark_-_Chicago%2C_United_States_-_Black_and_white_street_photography_%2818917472632%29.jpg",
        "download_src": "https://upload.wikimedia.org/wikipedia/commons/8/8f/From_the_dark_-_Chicago%2C_United_States_-_Black_and_white_street_photography_%2818917472632%29.jpg",
        "source": "https://commons.wikimedia.org/wiki/File:From_the_dark_-_Chicago,_United_States_-_Black_and_white_street_photography_(18917472632).jpg",
        "author": "Giuseppe Milo",
        "license": "CC BY 2.0",
        "license_family": "creative_commons",
    },
    {
        "id": "night_walking_london",
        "title": "A man walking at night - London, England",
        "src": "https://commons.wikimedia.org/wiki/Special:FilePath/A_man_walking_at_night_-_London%2C_England_-_Black_and_white_street_photography_%2830945945326%29.jpg",
        "download_src": "https://upload.wikimedia.org/wikipedia/commons/6/68/A_man_walking_at_night_-_London%2C_England_-_Black_and_white_street_photography_%2830945945326%29.jpg",
        "source": "https://commons.wikimedia.org/wiki/File:A_man_walking_at_night_-_London,_England_-_Black_and_white_street_photography_(30945945326).jpg",
        "author": "Giuseppe Milo",
        "license": "CC BY 2.0",
        "license_family": "creative_commons",
    },
    {
        "id": "espana_black_white",
        "title": "España, Black and White",
        "src": "https://commons.wikimedia.org/wiki/Special:FilePath/%28Espa%C3%B1a%2C_Black_and_White%2C_Photography_by_David_Adam_Kess%29.jpg",
        "download_src": "https://upload.wikimedia.org/wikipedia/commons/5/5d/%28Espa%C3%B1a%2C_Black_and_White%2C_Photography_by_David_Adam_Kess%29.jpg",
        "source": "https://commons.wikimedia.org/wiki/File:(Espa%C3%B1a,_Black_and_White,_Photography_by_David_Adam_Kess).jpg",
        "author": "David Adam Kess",
        "license": "CC BY-SA 4.0",
        "license_family": "creative_commons",
    },
)
PHOTOGRAPHY_FALLBACK_IMAGE_CANDIDATES = (
    {
        "id": "unsplash_mono_mountain",
        "title": "Unsplash black and white mountain road",
        "src": "https://images.unsplash.com/photo-1495567720989-cebdbdd97913?auto=format&fit=crop&w=1400&q=80",
        "download_src": "https://images.unsplash.com/photo-1495567720989-cebdbdd97913?auto=format&fit=crop&w=1400&q=80",
        "source": "https://unsplash.com/photos/photo-1495567720989-cebdbdd97913",
        "author": "Unsplash contributor",
        "license": "Unsplash License",
        "license_family": "free_use_not_open_source",
    },
    {
        "id": "unsplash_street_bw",
        "title": "Unsplash street photography",
        "src": "https://images.unsplash.com/photo-1517732306149-e8f829eb588a?auto=format&fit=crop&w=1400&q=80",
        "download_src": "https://images.unsplash.com/photo-1517732306149-e8f829eb588a?auto=format&fit=crop&w=1400&q=80",
        "source": "https://unsplash.com/",
        "author": "Unsplash contributor",
        "license": "Unsplash License",
        "license_family": "free_use_not_open_source",
    },
    {
        "id": "unsplash_gallery_wall",
        "title": "Unsplash gallery wall",
        "src": "https://images.unsplash.com/photo-1518998053901-5348d3961a04?auto=format&fit=crop&w=1400&q=80",
        "download_src": "https://images.unsplash.com/photo-1518998053901-5348d3961a04?auto=format&fit=crop&w=1400&q=80",
        "source": "https://unsplash.com/",
        "author": "Unsplash contributor",
        "license": "Unsplash License",
        "license_family": "free_use_not_open_source",
    },
    {
        "id": "unsplash_dark_city",
        "title": "Unsplash night city",
        "src": "https://images.unsplash.com/photo-1493246507139-91e8fad9978e?auto=format&fit=crop&w=1400&q=80",
        "download_src": "https://images.unsplash.com/photo-1493246507139-91e8fad9978e?auto=format&fit=crop&w=1400&q=80",
        "source": "https://unsplash.com/",
        "author": "Unsplash contributor",
        "license": "Unsplash License",
        "license_family": "free_use_not_open_source",
    },
)

STAGES = (
    StageSpec("stage0_baseline", "stage0_baseline.html", "基线版本", "none"),
    StageSpec("stage1_migrated_skill", "stage1_migrated_skill.html", "迁移 Skill", "migrated"),
    StageSpec("stage2_local_evolved", "stage2_local_evolved.html", "本地进化", "local_evolved"),
    StageSpec("stage3_final_adopted", "stage3_final_adopted.html", "最终采纳", "final_adopted"),
)

TOP_LEVEL_DIRS = ("frontend_html", "traces", "reports", "skills")
PROMPT_STRATEGIES = ("legacy", "cache_first")
DEFAULT_PROMPT_STRATEGY = "legacy"
PROMPT_STRATEGY_CLI_CHOICES = ("legacy", "cache-first", "both")
RUBRIC_KEYS = (
    "business_usability",
    "information_architecture",
    "visual_restraint",
    "anti_template",
    "responsive_risk",
    "verification_readiness",
    "skill_compliance",
)
SKILL_DESIGN_SOURCE_URLS = (
    "https://code.claude.com/docs/en/skills",
    "https://github.com/anthropics/skills",
    "https://github.com/openai/skills",
    "https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills",
    "https://api-docs.deepseek.com/quick_start/agent_integrations/reasonix",
    "https://api-docs.deepseek.com/guides/kv_cache",
    "https://api-docs.deepseek.com/guides/thinking_mode",
)
SKILL_STRUCTURE_SIGNALS = (
    ("frontmatter_description", "description:"),
    ("trigger_boundary", "## Trigger Boundary"),
    ("deepseek_execution_brief", "## DeepSeek Execution Brief"),
    ("progressive_disclosure", "## Progressive Disclosure"),
    ("references_routing", "## References Routing"),
    ("evaluation_contract", "## Evaluation Contract"),
    ("anti_patterns", "## Anti-patterns"),
    ("verification_suggestions", "## Verification Suggestions"),
    ("safety_constraints", "## Safety Constraints"),
)
SAFE_HTML_TITLES = {
    "stage0_baseline": "物流工单看板基线版",
    "stage1_migrated_skill": "物流工单看板迁移版",
    "stage2_local_evolved": "物流工单看板进化版",
    "stage3_final_adopted": "物流工单看板最终版",
}
PHOTOGRAPHY_SAFE_HTML_TITLES = {
    "stage0_baseline": "摄影作品展基线版",
    "stage1_migrated_skill": "摄影作品展迁移版",
    "stage2_local_evolved": "摄影作品展进化版",
    "stage3_final_adopted": "摄影作品展最终版",
}


class GitCommandError(RuntimeError):
    def __init__(self, *, args: list[str], cwd: Path | None, returncode: int, stdout: str, stderr: str):
        self.args_list = args
        self.cwd = cwd
        self.returncode = returncode
        self.stdout = stdout.strip()
        self.stderr = stderr.strip()
        command = " ".join(["git", *args])
        location = f" cwd={cwd}" if cwd else ""
        message = f"git 命令失败：{command}{location}，退出码 {returncode}"
        if self.stderr:
            message += f"，stderr: {self.stderr}"
        super().__init__(message)


GIT_COMMAND_TIMEOUT_SECONDS = 120


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_summary(started_at: str, started_perf: float) -> dict[str, Any]:
    return {
        "started_at": started_at,
        "finished_at": _now_iso(),
        "duration_seconds": round(time.perf_counter() - started_perf, 3),
    }


def _persist_report_runtime(report: dict[str, Any]) -> None:
    if not isinstance(report, dict) or report.get("status") == "dry_run":
        return
    root_value = report.get("experiment_root")
    if not root_value:
        return
    reports_dir = Path(str(root_value)) / "reports"
    final_report = reports_dir / "final_experiment_report.json"
    final_status = reports_dir / "final_status.json"
    if final_report.exists():
        write_json(final_report, report)
        write_json(REPORTS_DIR / "garden_skill_migration_evolution_latest.json", report)
    elif final_status.exists():
        write_json(final_status, report)
    elif reports_dir.exists():
        write_json(final_status, report)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_image_candidate(candidate: dict[str, Any], target_path: Path, *, timeout: int = 25) -> dict[str, Any]:
    url = str(candidate.get("download_src") or candidate.get("src") or "")
    record = {
        **candidate,
        "download_url": url,
        "local_path": str(target_path),
        "relative_path": target_path.as_posix(),
        "status": "failed",
        "error": "",
    }
    if not url:
        record["error"] = "missing download URL"
        return record
    target_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "DiaEvo/1.0 image fetch"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read(8 * 1024 * 1024)
        if not data or not content_type.lower().startswith("image/"):
            raise ValueError(f"unexpected content type or empty body: {content_type}")
        tmp = target_path.with_name(f".{target_path.name}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target_path)
        record.update(
            {
                "status": "downloaded",
                "content_type": content_type,
                "bytes": target_path.stat().st_size,
                "sha256": _sha256_file(target_path),
                "relative_path": target_path.as_posix(),
            }
        )
    except Exception as exc:
        record["error"] = str(exc)
    return record


def _prepare_photography_assets(frontend_dir: Path, reports_dir: Path) -> dict[str, Any]:
    assets_dir = frontend_dir / "assets" / "photos"
    downloaded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for candidate in PHOTOGRAPHY_IMAGE_CANDIDATES:
        target = assets_dir / f"{candidate['id']}.jpg"
        result = _download_image_candidate(candidate, target)
        if result["status"] == "downloaded":
            result["src"] = f"assets/photos/{target.name}"
            downloaded.append(result)
        else:
            failed.append(result)
    fallback_used = False
    if len(downloaded) < 3:
        fallback_used = True
        for candidate in PHOTOGRAPHY_FALLBACK_IMAGE_CANDIDATES:
            if len(downloaded) >= 4:
                break
            target = assets_dir / f"{candidate['id']}.jpg"
            result = _download_image_candidate(candidate, target)
            if result["status"] == "downloaded":
                result["src"] = f"assets/photos/{target.name}"
                downloaded.append(result)
            else:
                failed.append(result)
    manifest = {
        "schema": "diaevo.photography_assets.v1",
        "status": "ok" if downloaded else "failed",
        "downloaded_count": len(downloaded),
        "failed_count": len(failed),
        "fallback_used": fallback_used,
        "assets_dir": str(assets_dir),
        "downloaded": downloaded,
        "failed": failed,
    }
    write_json(reports_dir / "photography_assets.json", manifest)
    return manifest


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitCommandError(
            args=args,
            cwd=cwd,
            returncode=124,
            stdout=str(exc.stdout or ""),
            stderr=f"git 命令超过 {GIT_COMMAND_TIMEOUT_SECONDS} 秒未完成",
        ) from exc
    if completed.returncode != 0:
        raise GitCommandError(
            args=args,
            cwd=cwd,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    return completed.stdout.strip()


def _git_head(repo_dir: Path) -> str:
    return _run_git(["rev-parse", "HEAD"], cwd=repo_dir)


def _prepare_reference_repo(
    *,
    repo_dir: Path,
    reference_url: str,
    commit: str,
    subdir: str = REFERENCE_SUBDIR,
    refresh: bool = False,
) -> dict[str, Any]:
    repo_dir = repo_dir.resolve(strict=False)
    sparse = False
    if (repo_dir / ".git").exists():
        current = ""
        try:
            current = _git_head(repo_dir)
        except Exception:
            current = ""
        if current == commit and not refresh:
            return {
                "status": "already_at_commit",
                "reference_url": reference_url,
                "local_repo_path": str(repo_dir),
                "head": current,
                "commit": commit,
                "sparse_checkout": _is_sparse_checkout(repo_dir),
            }
        _run_git(["fetch", "--tags", "--prune", "--filter=blob:none", "origin", commit], cwd=repo_dir)
    else:
        if repo_dir.exists() and any(repo_dir.iterdir()):
            raise FileExistsError(f"reference repo dir exists but is not a git checkout: {repo_dir}")
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--filter=blob:none", "--no-checkout", reference_url, str(repo_dir)])
        sparse = True
    _configure_sparse_checkout(repo_dir, subdir)
    _run_git(["checkout", "--detach", commit], cwd=repo_dir)
    head = _git_head(repo_dir)
    if head != commit:
        raise RuntimeError(f"reference repo checkout mismatch: expected {commit}, got {head}")
    return {
        "status": "checked_out",
        "reference_url": reference_url,
        "local_repo_path": str(repo_dir),
        "head": head,
        "commit": commit,
        "sparse_checkout": sparse or _is_sparse_checkout(repo_dir),
    }


def _is_sparse_checkout(repo_dir: Path) -> bool:
    try:
        return _run_git(["config", "--bool", "core.sparseCheckout"], cwd=repo_dir).lower() == "true"
    except Exception:
        return False


def _configure_sparse_checkout(repo_dir: Path, subdir: str) -> None:
    _run_git(["sparse-checkout", "init", "--cone"], cwd=repo_dir)
    _run_git(["sparse-checkout", "set", subdir.replace("\\", "/")], cwd=repo_dir)


def _reference_repo_status(repo_dir: Path, *, reference_url: str, commit: str, subdir: str) -> dict[str, Any]:
    repo_dir = repo_dir.resolve(strict=False)
    source_dir = repo_dir / subdir
    status = "missing"
    head = ""
    try:
        if not repo_dir.exists():
            status = "missing"
        elif (repo_dir / ".git").exists():
            head = _git_head(repo_dir)
            status = "ready" if head == commit and source_dir.exists() else "checkout_needed"
        else:
            status = "not_a_git_checkout"
    except Exception as exc:
        status = f"check_failed: {exc}"
    return {
        "reference_url": reference_url,
        "local_repo_path": str(repo_dir),
        "commit": commit,
        "head": head,
        "source_subdir": subdir,
        "source_dir": str(source_dir),
        "status": status,
    }


def _reference_repo_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, GitCommandError):
        return {
            "type": "GitCommandError",
            "message": str(exc),
            "git_args": ["git", *exc.args_list],
            "cwd": str(exc.cwd) if exc.cwd else "",
            "returncode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "hint": "请检查网络、GitHub 访问权限，或先手动 clone 到 --reference-repo-dir 后用 --resume-root 续跑。",
        }
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "hint": "请检查 --reference-repo-dir 是否为空目录或有效 git checkout。",
    }


def _source_file_hashes(source_dir: Path) -> list[dict[str, Any]]:
    names = ("SKILL.md", "manifest.json", "README.md", "README.zh-CN.md")
    hashes: list[dict[str, str]] = []
    for name in names:
        path = source_dir / name
        hashes.append(
            {
                "path": name,
                "sha256": _sha256_file(path) if path.exists() else "",
                "exists": path.exists(),
            }
        )
    return hashes


def _validation_run_leftovers() -> list[dict[str, str]]:
    root = ROOT / ".tmp" / "validation-runs"
    if not root.exists():
        return []
    patterns = ("garden", "web-design", "web_design", "migration")
    rows: list[dict[str, str]] = []
    pending = [root]
    visited = 0
    while pending and len(rows) < 80 and visited < 500:
        current = pending.pop(0)
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for path in children:
            visited += 1
            text = path.as_posix().lower()
            if any(pattern in text for pattern in patterns):
                rows.append({"path": str(path), "kind": "dir" if path.is_dir() else "file"})
            if path.is_dir() and visited < 500:
                pending.append(path)
            if len(rows) >= 80 or visited >= 500:
                break
    return rows


def _copy_skill(src_dir: Path, dst_dir: Path) -> None:
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    shutil.copytree(src_dir, dst_dir)


def _visible_text(html_text: str, limit: int = 3000) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?</\1>", " ", html_text)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


def extract_full_html(model_text: str) -> str | None:
    """Extract one complete HTML document from a model response."""
    fenced = re.findall(r"```(?:html)?\s*(.*?)```", model_text, flags=re.IGNORECASE | re.DOTALL)
    candidates = fenced or [model_text]
    for candidate in candidates:
        text = candidate.strip()
        start = text.lower().find("<!doctype html")
        if start == -1:
            start = text.lower().find("<html")
        end = text.lower().rfind("</html>")
        if start != -1 and end != -1 and end > start:
            return text[start : end + len("</html>")].strip()
    return None


def _title_text(html_text: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text)
    if not match:
        return ""
    return html.unescape(re.sub(r"\s+", " ", match.group(1)).strip())


def _replace_title(html_text: str, title: str) -> str:
    safe = html.escape(title, quote=False)
    if re.search(r"(?is)<title[^>]*>.*?</title>", html_text):
        return re.sub(r"(?is)<title[^>]*>.*?</title>", f"<title>{safe}</title>", html_text, count=1)
    return re.sub(r"(?is)<head([^>]*)>", f"<head\\1>\n  <title>{safe}</title>", html_text, count=1)


def _safe_html_title(stage: StageSpec) -> str:
    if TASK.task_id == "photography_portfolio_exhibition":
        return PHOTOGRAPHY_SAFE_HTML_TITLES[stage.stage_id]
    return SAFE_HTML_TITLES[stage.stage_id]


def _prompt_leak_findings(html_text: str, prompt: str) -> list[str]:
    findings: list[str] = []
    title = _title_text(html_text)
    if prompt in title:
        findings.append("title_contains_user_prompt")
    visible = _visible_text(html_text)
    if prompt in visible[:1400]:
        findings.append("first_view_contains_full_user_prompt")
    return findings


def _sanitize_stage_html(html_text: str, stage: StageSpec) -> str:
    return _replace_title(html_text, _safe_html_title(stage))


def _signal_present(signal: str, *, lower: str, visible: str, has_mobile: bool) -> bool:
    if signal == "移动端":
        return has_mobile
    semantic_tokens = {
        "整体忙不忙": ("整体忙不忙", "队列压力", "忙碌程度", "拥堵", "整体队列", "负载压力"),
        "工单队列": ("工单队列", "待处理队列", "队列表", "工单列表"),
        "图片": ("图片", "照片", "摄影", "<img", "background-image"),
        "摄影作品": ("摄影作品", "照片", "影像", "photo", "photography"),
        "作品展": ("作品展", "展览", "exhibition", "gallery", "portfolio"),
        "联系": ("联系", "预约", "邮箱", "email", "contact"),
    }
    tokens = semantic_tokens.get(signal, (signal,))
    return any(token.lower() in lower or token in visible for token in tokens)


def _score_html(stage: StageSpec, html_text: str, *, skill_text: str = "") -> dict[str, Any]:
    lower = html_text.lower()
    visible = _visible_text(html_text)
    has_dashboard = any(token in visible for token in ("工单", "SLA", "队列", "负责人", "超时", "作品", "展览", "摄影", "系列"))
    has_table = "<table" in lower or "grid" in lower
    has_actions = any(token in visible for token in ("处理", "指派", "升级", "跟进", "待确认", "查看", "预约", "联系", "进入", "展览"))
    has_mobile = "@media" in lower and any(token in lower for token in ("overflow-wrap", "word-break", "minmax", "table-layout"))
    required_hits = sum(1 for signal in TASK.required_signals if _signal_present(signal, lower=lower, visible=visible, has_mobile=has_mobile))
    has_verification = any(token in lower for token in ("screenshot", "playwright", "验收", "验证", "build"))
    if TASK.task_id == "photography_portfolio_exhibition":
        has_verification = has_verification or any(token in lower for token in ("license", "许可证", "cc by", "wikimedia", "来源"))
    cliche_count = 0
    for token in ("一站式", "赋能", "智慧化", "炫酷", "未来感", "极致体验"):
        if token in visible and f"避免{token}" not in visible and f"不要{token}" not in visible:
            cliche_count += 1
    purple_gradient = "linear-gradient" in lower and any(token in lower for token in ("#7c3aed", "#8b5cf6", "purple"))
    restrained = not purple_gradient and cliche_count == 0
    dense = has_table and required_hits >= 4 and len(visible) > 500
    local_evolution_evidence = any(token in visible for token in ("本地进化", "非专业", "长工单号", "溢出保护"))
    prompt_leaks = _prompt_leak_findings(html_text, TASK.user_prompt)

    scores = {
        "business_usability": min(10, 4 + required_hits + int(has_actions) + int(has_dashboard) + int(local_evolution_evidence)),
        "information_architecture": min(10, 4 + required_hits + int(has_table) + int(dense) + int(local_evolution_evidence)),
        "visual_restraint": max(1, 8 + int(restrained) - cliche_count * 2 - int(purple_gradient) * 2),
        "anti_template": max(1, 8 - cliche_count * 2 - int(purple_gradient)),
        "responsive_risk": min(10, 3 + int(has_mobile) * 4 + int("viewport" in lower) + int("overflow" in lower)),
        "verification_readiness": min(10, 3 + int(has_verification) * 4 + int("checklist" in lower or "验收" in lower) * 2 + int(local_evolution_evidence)),
        "skill_compliance": 3 if stage.skill_role == "none" else min(10, 5 + int(bool(skill_text)) + int(has_mobile) + int(restrained) + int(has_verification) + int(local_evolution_evidence)),
    }
    aggregate = sum(scores[key] for key in RUBRIC_KEYS) / len(RUBRIC_KEYS)
    bad_cases: list[dict[str, str]] = []
    if required_hits < len(TASK.required_signals) - 1:
        bad_cases.append({"label": "missing_business_signals", "note": "关键业务信号覆盖不足。"})
    if cliche_count or purple_gradient:
        bad_cases.append({"label": "template_or_ai_tone", "note": "存在模板化营销措辞或高饱和渐变倾向。"})
    if not has_mobile:
        bad_cases.append({"label": "responsive_overflow_risk", "note": "移动端溢出保护证据不足。"})
    if not has_verification:
        bad_cases.append({"label": "weak_verification", "note": "缺少构建或截图验收建议。"})
    for leak in prompt_leaks:
        bad_cases.append({"label": leak, "note": "HTML 泄漏了完整用户原话。"})
    return {
        **scores,
        "aggregate": round(aggregate, 3),
        "required_signal_hits": required_hits,
        "required_signal_total": len(TASK.required_signals),
        "vision_status": "skipped",
        "prompt_leak_findings": prompt_leaks,
        "bad_cases": bad_cases,
    }


RUBRIC_FEEDBACK_NOTES = {
    "business_usability": "补足一眼可决策的业务信号和明确下一步动作。",
    "information_architecture": "强化首屏信息层级，让指标、队列、负责人和动作同时可扫描。",
    "visual_restraint": "降低模板感和高饱和装饰，保持克制、真实、可重复使用的界面气质。",
    "anti_template": "避免营销套话、通用 hero 和无依据的未来感表达。",
    "responsive_risk": "补强移动端断点、稳定尺寸、长文本换行和溢出保护证据。",
    "verification_readiness": "在页面内或交付证据里加入 build、desktop screenshot、mobile screenshot、overflow 检查。",
    "skill_compliance": "更直接体现本地进化 skill 的可验证约束，而不是只复述通用设计建议。",
}


def _score_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stage1_feedback_summary(stage1_score: dict[str, Any]) -> list[dict[str, str]]:
    aggregate = _score_value(stage1_score.get("aggregate"))
    weak_scores: list[tuple[str, float]] = []
    for key in RUBRIC_KEYS:
        value = _score_value(stage1_score.get(key))
        if value is not None and value < 10:
            weak_scores.append((key, value))
    weak_scores.sort(key=lambda item: (item[1], item[0]))
    if aggregate is None and not weak_scores:
        return []
    weakest = weak_scores[:4]
    weakest_text = "、".join(f"{key}={value:g}" for key, value in weakest) or "无低于 10 的 rubric"
    rows = [
        {
            "stage": STAGES[1].stage_id,
            "label": "stage1_score_summary",
            "note": (
                f"Stage1 aggregate={aggregate if aggregate is not None else 'unknown'}；"
                f"stage2 不能只复刻或同分，必须针对最弱 rubric 做可见改进：{weakest_text}。"
            ),
        }
    ]
    for key, value in weakest:
        rows.append(
            {
                "stage": STAGES[1].stage_id,
                "label": f"stage1_rubric_gap_{key}",
                "note": f"{key} 当前 {value:g}/10；stage2 需要可见改进：{RUBRIC_FEEDBACK_NOTES.get(key, '补强该评分项的可验证证据。')}",
            }
        )
    return rows


def _system_prompt(stage: StageSpec) -> str:
    if TASK.task_id == "photography_portfolio_exhibition":
        base = (
            "你是摄影作品展网页的视觉设计工程师。只输出一个完整单文件 HTML 文档，不要 Markdown 解释。"
            "页面必须是可直接打开的 HTML/CSS/JS，语言为中文，<title> 必须是短标题，不能写入用户原话。"
            "不要把完整用户 prompt 渲染在首屏。允许自由发挥策展叙事、影像节奏和交互，但必须显得像真实摄影师作品展。"
            "控制输出规模：HTML 总长度优先小于 32000 字符；不要生成无意义统计、模板化品牌口号或复杂依赖。"
            "如果使用外部图片，必须使用开源或明确可免费复用来源，并在页面脚注或注释中记录来源与许可证。"
            "核心内容用静态 HTML/CSS 呈现，并必须以 </html> 结束。"
        )
    else:
        base = (
            "你是企业后台前端设计工程师。只输出一个完整单文件 HTML 文档，不要 Markdown 解释。"
            "页面必须是可直接打开的 HTML/CSS/JS，语言为中文，<title> 必须是短标题，不能写入用户原话。"
            "不要把完整用户 prompt 渲染在首屏。保持界面克制、信息密度高、适合重复工作流。"
            "控制输出规模：HTML 总长度优先小于 26000 字符；不要生成大段模拟数据、随机数据生成器或复杂图表脚本。"
            "如果需要 JS，只写少量交互状态切换；核心内容用静态 HTML/CSS 呈现，并必须以 </html> 结束。"
        )
    if stage.stage_id == "stage0_baseline":
        return base + "本阶段不使用外部 skill，只按任务生成一个基线版本。"
    if stage.stage_id == "stage1_migrated_skill":
        return base + "本阶段必须遵循迁移自 Garden skills/web-design-engineer 的 DiaEvo skill，体现迁移 skill 的设计约束。"
    if stage.stage_id == "stage2_local_evolved":
        if TASK.task_id == "photography_portfolio_exhibition":
            return base + "本阶段必须遵循本地进化 skill，重点修复 bad case：低细节自由发挥、摄影作品展气质、影像素材合规、去 AI 味、响应式观看体验、验收证据。"
        return base + "本阶段必须遵循本地进化 skill，重点修复 bad case：中文非专业用户、去 AI 味、dashboard 信息密度、响应式溢出、验收证据。"
    return base + "本阶段使用最终采纳 skill，输出应体现采纳决策后的最稳版本。"


def _skill_excerpt(skill_text: str, limit: int = 7000) -> str:
    text = skill_text.strip()
    if len(text) <= limit:
        return text
    overlay_markers = ("## 本地进化说明", "## DeepSeek Execution Brief")
    starts = [text.find(marker) for marker in overlay_markers if text.find(marker) >= 0]
    if not starts:
        return text[:limit]
    start = min(starts)
    metadata = text[: min(1200, start)].rstrip()
    overlay = text[start : start + max(0, limit - len(metadata) - 80)].strip()
    return f"{metadata}\n\n<!-- local-evolution-overlay-first -->\n\n{overlay}"


def _stage_user_prompt(
    stage: StageSpec,
    *,
    skill_text: str = "",
    feedback: list[dict[str, Any]] | None = None,
    image_assets: dict[str, Any] | None = None,
) -> str:
    if TASK.task_id == "photography_portfolio_exhibition":
        constraints = [
            "输出完整 HTML：<!doctype html> 到 </html>。",
            "用户只给了低细节需求，必须自行建立摄影师个人展的视觉方向、系列结构和浏览节奏。",
            "不要做营销 SaaS 风、模板 hero、假数据仪表盘、客户 logo 墙或泛泛口号。",
            "首屏必须直接呈现摄影作品展气质：摄影师姓名或展名、代表性影像、展览/作品系列入口。",
            "页面必须包含作品系列、单张作品说明、关于摄影师、联系/预约观看入口。",
            "没有用户上传图片时，优先使用开源或明确可免费复用图片；必须记录图片来源与许可证，不要伪造作者或来源。",
            "CSS 必须显式包含 `object-fit`、稳定图片比例、`minmax(0, 1fr)`、`overflow-wrap` 或 `word-break`、以及至少一个 `@media` 窄屏规则。",
            "可以自由选择黑白、画廊白盒、杂志式或沉浸式方向，但要避免 AI 味渐变、过度圆角卡片和无关插画。",
            "HTML title 使用短标题，不能包含用户原话。",
            "页面可见内容不要直接渲染完整用户 prompt。",
        ]
    else:
        constraints = [
            "输出完整 HTML：<!doctype html> 到 </html>。",
            "优先生成精简但完整的页面，不要超过 900 行。",
            "只生成一个运营 dashboard，不要营销落地页。",
            "首屏必须能同时看到急单、负责人、快超时、队列压力和今日处置动作。",
            "首屏必须有一个明确业务标签写作“整体忙不忙”或“队列压力”，不能只在备注里暗示。",
            "移动端不能出现文字溢出；使用稳定尺寸、换行和窄屏布局。",
            "CSS 必须显式包含 `table-layout: fixed`、`minmax(0, 1fr)`、`overflow-wrap` 或 `word-break`、以及至少一个 `@media` 窄屏规则。",
            "使用 6 到 8 条静态工单样例即可，不要用 JS 批量生成数据。",
            "HTML title 使用短标题，不能包含用户原话。",
            "页面可见内容不要直接渲染完整用户 prompt。",
        ]
    payload = {
        "任务": {
            "短标题": TASK.short_title,
            "目标用户": TASK.audience,
            "用户原始需求": TASK.user_prompt,
            "必须覆盖的业务信号": list(TASK.required_signals),
        },
        "阶段": {
            "阶段标识": stage.stage_id,
            "阶段名称": stage.short_title,
            "skill_角色": stage.skill_role,
        },
        "约束": constraints,
        "skill_摘录": _skill_excerpt(skill_text),
        "阶段反馈": feedback or [],
    }
    if TASK.task_id == "photography_portfolio_exhibition":
        downloaded_images = image_assets.get("downloaded", []) if isinstance(image_assets, dict) else []
        payload["自动素材检索"] = {
            "说明": "用户未上传图片；实验 runner 已抓取可复用图片到本地实验目录。生成 HTML 时必须优先使用 local_src，不要继续热链远程图片，并保留来源/许可证记录。",
            "下载状态": image_assets or {"status": "not_prepared"},
            "图片候选": [
                {
                    "title": item.get("title"),
                    "local_src": item.get("src"),
                    "source": item.get("source"),
                    "author": item.get("author"),
                    "license": item.get("license"),
                    "license_family": item.get("license_family"),
                }
                for item in downloaded_images
            ],
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _stage_dynamic_payload(
    stage: StageSpec,
    *,
    feedback: list[dict[str, Any]] | None = None,
    image_assets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = json.loads(_stage_user_prompt(stage, skill_text="", feedback=feedback, image_assets=image_assets))
    payload.pop("skill_摘录", None)
    return payload


def _markdown_section(text: str, heading: str, *, limit: int = 1800) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    next_heading = text.find("\n## ", start + len(heading))
    section = text[start:] if next_heading < 0 else text[start:next_heading]
    return section.strip()[:limit].strip()


def _compact_skill_execution_summary(stage: StageSpec, skill_text: str) -> str:
    if not skill_text.strip():
        return ""
    digest = _sha256_text(skill_text)[:16]
    sections = [
        _markdown_section(skill_text, "## DeepSeek Execution Brief", limit=1600),
        _markdown_section(skill_text, "## Evaluation Contract", limit=900),
        _markdown_section(skill_text, "## Anti-patterns", limit=700),
    ]
    body = "\n\n".join(section for section in sections if section)
    if not body:
        body = (
            "迁移自通用 Web Design Engineer skill。执行时只保留可验证约束："
            "真实前端页面、响应式与溢出检查、反模板化、视觉克制、截图/构建验收准备。"
            "忽略源 skill 中要求解释、展示草稿或输出 Markdown 的工作流表述；本实验只输出完整 HTML。"
        )
    return f"skill_role={stage.skill_role}; skill_sha256_16={digest}\n{body}"


def _cache_first_system_prompt() -> str:
    return (
        "你是 DiaEvo 的 DeepSeek cache-first 前端生成器。"
        "遵循 Reasonix 风格上下文分区：稳定前缀只放长期规则，变化任务只读最后的动态 payload。"
        "你的回答必须从 <!doctype html> 开始，并以 </html> 结束。"
        "不要 Markdown、不要代码围栏、不要解释、不要前后导语。"
        "页面必须可直接打开，语言为中文，<title> 必须是短标题，不能写入用户原话。"
        "不要把完整用户 prompt 渲染在首屏。"
        "核心内容用静态 HTML/CSS 呈现，并必须以 </html> 结束。"
    )


def _cache_first_base_contract() -> str:
    if TASK.task_id == "photography_portfolio_exhibition":
        domain = (
            "任务域：中文摄影师作品展网页。必须像真实摄影作品展，不做 SaaS dashboard、营销模板、客户 logo 墙或泛泛个人品牌页。"
            "首屏必须直接呈现摄影作品或摄影图像区域，并给出展名/摄影师名/系列入口。"
            "必须包含作品系列、单张作品说明、关于摄影师、联系或预约观看入口。"
            "没有用户上传图片时优先使用动态 payload 中的本地素材，并记录来源和许可证。"
            "CSS 必须显式包含 object-fit、稳定图片比例、minmax(0, 1fr)、overflow-wrap 或 word-break、至少一个 @media。"
        )
    else:
        domain = (
            "任务域：中文运营 dashboard / 后台工作台。只生成运营 dashboard，不做营销落地页。"
            "首屏必须能同时看到急单、负责人、快超时、队列压力和今日处置动作。"
            "首屏必须有明确业务标签写作“整体忙不忙”或“队列压力”。"
            "CSS 必须显式包含 table-layout: fixed、minmax(0, 1fr)、overflow-wrap 或 word-break、至少一个 @media。"
            "使用 6 到 8 条静态工单样例，不要 JS 批量生成数据。"
        )
    return "\n".join(
        [
            "DiaEvo Cache-First Stable Contract",
            "",
            "本段模拟 Reasonix 的 immutable prefix：同一实验策略内保持稳定，不放动态任务反馈、素材下载状态或重试说明。",
            "输出合同：Output=single_html；Visible text=Chinese；No=emoji/Markdown explanation/full prompt leak.",
            "硬性格式：第一个可见字符序列必须是 <!doctype html>；禁止 ```html；禁止在 HTML 前后写任何说明。",
            "规模控制：优先少于 700 行；CSS 内联；JS 只用于少量状态切换；不要生成长篇讲解、设计解析或功能拆解。",
            "验收证据放进 HTML 可见区域或注释中，不要在 HTML 外部解释。",
            domain,
        ]
    )


def _cache_first_dynamic_prompt(
    stage: StageSpec,
    *,
    skill_text: str = "",
    feedback: list[dict[str, Any]] | None = None,
    image_assets: dict[str, Any] | None = None,
    retry: bool = False,
) -> str:
    payload = _stage_dynamic_payload(stage, feedback=feedback, image_assets=image_assets)
    payload["prompt_策略"] = {
        "name": "cache_first",
        "reasonix_迁移": "稳定 system/contract/skill 前缀 + 最后一条 user 动态 payload",
        "动态内容位置": "last_user_message",
    }
    if stage.stage_id == "stage2_local_evolved":
        if TASK.task_id == "photography_portfolio_exhibition":
            payload["输出压缩合同"] = {
                "目标": "先保证完整可抽取 HTML，再追求策展文案；上一轮长篇摄影叙事容易在 </html> 前截断。",
                "硬上限": "不超过 220 行；可见中文文案总量优先少于 1800 字；不写长篇单张作品解析。",
                "结构预算": [
                    "首屏：1 个代表影像区、展名/摄影师名、2 个短入口。",
                    "作品系列：4 个系列，每个说明不超过 32 字。",
                    "精选作品：最多 4 张图片，直接使用自动素材检索里的 local_src。",
                    "关于摄影师：1 段，不超过 90 字。",
                    "联系/预约与图片来源/许可证：紧凑脚注即可。",
                ],
                "删减顺序": "预算紧张时先删除 JS、动画、长故事和重复说明；绝不能删除 <!doctype html>、<html>、</html>。",
                "停止规则": "写完 </html> 立即停止，不追加解释、设计说明或 Markdown。",
            }
        else:
            payload["输出压缩合同"] = {
                "目标": "先保证完整可抽取 HTML；cache-first stage2 容易因过度打磨样式或长表格在 </html> 前截断。",
                "硬上限": "不超过 180 行；只写 6 到 8 条静态工单；不要写长篇交付说明。",
                "结构预算": [
                    "首屏：6 个短指标卡，必须含急单、负责人、快超时、整体忙不忙或队列压力。",
                    "工单队列：1 个 6 行表格，列数控制在 7 列以内。",
                    "负责人负载：1 个紧凑分组区。",
                    "今日处置：1 个短动作清单。",
                    "验收备注：1 个短注释或 aside，覆盖 build/screenshot/overflow 证据。",
                ],
                "删减顺序": "预算紧张时先删除 JS、动画、装饰和重复样例；绝不能删除 <!doctype html>、<html>、</html>。",
                "停止规则": "写完 </html> 立即停止，不追加解释、设计说明或 Markdown。",
            }
    skill_summary = _compact_skill_execution_summary(stage, skill_text)
    if skill_summary and not (retry and stage.stage_id == "stage2_local_evolved"):
        payload["skill_执行摘要"] = skill_summary
    if retry:
        payload["重试要求"] = _retry_user_instruction()
        if stage.stage_id == "stage2_local_evolved":
            minimal_version = (
                "首屏 + 4 系列 + 4 图精选 + 关于 + 联系/许可证脚注"
                if TASK.task_id == "photography_portfolio_exhibition"
                else "6 指标卡 + 6 行工单表 + 负责人负载 + 今日处置 + 验收备注"
            )
            forbidden = (
                "禁止长篇摄影散文、逐张千字解析、外部说明、代码围栏和 HTML 后附言。"
                if TASK.task_id == "photography_portfolio_exhibition"
                else "禁止长篇设计说明、大段模拟数据、复杂 JS、代码围栏和 HTML 后附言。"
            )
            payload["重试压缩指令"] = {
                "失败模式": "上一轮没有抽取到完整 HTML，通常是输出过长或在 </html> 前耗尽 token。",
                "本次只做最小完整版本": minimal_version,
                "禁止": forbidden,
                "必须": "第一个字符序列是 <!doctype html>，最后一个字符序列是 </html>。",
            }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _stage_messages(
    stage: StageSpec,
    *,
    system_prompt: str,
    user_prompt: str,
    skill_text: str,
    feedback: list[dict[str, Any]] | None = None,
    image_assets: dict[str, Any] | None = None,
    prompt_strategy: str = DEFAULT_PROMPT_STRATEGY,
    retry: bool = False,
) -> list[dict[str, Any]]:
    if prompt_strategy == "legacy":
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    if prompt_strategy != "cache_first":
        raise ValueError(f"unknown prompt strategy: {prompt_strategy}")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _cache_first_system_prompt()},
        {"role": "user", "content": _cache_first_base_contract()},
    ]
    messages.append(
        {
            "role": "user",
            "content": _cache_first_dynamic_prompt(
                stage,
                skill_text=skill_text,
                feedback=feedback,
                image_assets=image_assets,
                retry=retry,
            ),
        }
    )
    return messages


def _trace_prompt_from_messages(messages: list[dict[str, Any]], *, role: str) -> str:
    parts = [str(item.get("content", "")) for item in messages if item.get("role") == role]
    return "\n\n--- message boundary ---\n\n".join(parts)


class DeepSeekLLMClient:
    def __init__(self, config: DeepSeekConfig):
        self.config = config

    def generate(self, *, stage: StageSpec, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.generate_with_messages(
            stage=stage,
            messages=messages,
            trace_system_prompt=system_prompt,
            trace_user_prompt=user_prompt,
        )

    def generate_with_messages(
        self,
        *,
        stage: StageSpec,
        messages: list[dict[str, Any]],
        trace_system_prompt: str,
        trace_user_prompt: str,
    ) -> dict[str, Any]:
        response = chat_completion(messages, self.config)
        text = extract_assistant_text(response)
        usage = response.get("usage") if isinstance(response, dict) else None
        return {
            "status": "ok",
            "provider": "deepseek",
            "model": self.config.model,
            "base_url": self.config.base_url,
            "response_text": text,
            "response_summary": _visible_text(text, 800),
            "usage": usage if isinstance(usage, dict) else {},
            "message_count": len(messages),
            "trace_system_prompt": trace_system_prompt,
            "trace_user_prompt": trace_user_prompt,
        }


def _llm_config_status(*, env_path: str | None = None) -> tuple[DeepSeekConfig | None, dict[str, Any]]:
    try:
        config = config_from_env(env_path=env_path, max_tokens=8192, temperature=0.25)
        config.timeout = None
        return config, {
            "status": "configured",
            "provider": "deepseek",
            "model": config.model,
            "base_url": config.base_url,
            "max_tokens": config.max_tokens,
            "thinking": config.thinking,
            "reasoning_effort": config.reasoning_effort,
            "api_key_configured": True,
        }
    except Exception as exc:
        return None, {
            "status": "missing",
            "provider": "deepseek",
            "api_key_configured": False,
            "reason": str(exc),
        }


def _migration_summary(
    adaptation: dict[str, Any],
    migrated_dir: Path,
    *,
    reference_url: str,
    local_repo_path: Path,
    source_dir: Path,
    source_subdir: str,
    source_commit: str,
    git_head: str,
) -> dict[str, Any]:
    skill_path = migrated_dir / "SKILL.md"
    report_path = migrated_dir / "adaptation_report.json"
    source = adaptation.get("source") or {}
    summary = adaptation.get("adaptation_summary") or {}
    files = summary.get("files") if isinstance(summary.get("files"), list) else []
    if not files:
        report = read_json(report_path, default={}) or {}
        nested = report.get("adaptation_summary") if isinstance(report, dict) else {}
        files = nested.get("files") if isinstance(nested, dict) and isinstance(nested.get("files"), list) else []
    if not files:
        source_dir = Path(str(summary.get("source_dir") or source.get("cache_dir") or ""))
        if source_dir.exists():
            files = []
            for path in sorted(item for item in source_dir.rglob("*") if item.is_file())[:80]:
                try:
                    rel = path.relative_to(source_dir).as_posix()
                    files.append({"path": rel, "sha256": _sha256_file(path)})
                except OSError:
                    continue
    return {
        "schema": "diaevo.garden_web_design_migration.v2",
        "status": adaptation.get("status", "ok"),
        "reference_url": reference_url,
        "local_repo_path": str(local_repo_path),
        "git_head": git_head,
        "source_commit": source.get("commit") or source_commit,
        "source_subdir": source.get("subdir") or source_subdir,
        "source_dir": str(source_dir),
        "source_file_hashes": _source_file_hashes(source_dir),
        "migrated_skill": str(skill_path),
        "migrated_skill_sha256": _sha256_file(skill_path) if skill_path.exists() else "",
        "file_hashes": [
            {"path": item.get("path"), "sha256": item.get("sha256")}
            for item in files[:40]
            if isinstance(item, dict) and item.get("path") and item.get("sha256")
        ],
        "migration_summary": {
            "file_count": summary.get("file_count"),
            "signals": summary.get("signals", [])[:20] if isinstance(summary.get("signals"), list) else [],
            "optional_validation_commands": summary.get("optional_validation_commands", []),
        },
    }


def _local_evolved_text(migrated_text: str, feedback: list[dict[str, Any]]) -> str:
    if TASK.task_id == "photography_portfolio_exhibition":
        lines = [
            migrated_text.rstrip(),
            "",
            "## 本地进化说明",
            "",
            "本节由 DiaEvo Garden 迁移实验追加，用于把迁移得到的 Web 设计方法用于中文摄影师个人作品展任务。",
            "",
            "当用户只给出低细节摄影作品展、摄影集、个人 portfolio 或影像展网页需求时使用本 overlay。目标是考验 agent 自主策展、视觉判断和素材合规能力，而不是把需求补成模板落地页。",
            "",
            "需要修复的实验反馈：",
        ]
        if feedback:
            for item in feedback[:16]:
                label = str(item.get("label") or "feedback")
                note = str(item.get("note") or item.get("message") or "")
                lines.append(f"- `{label}`: {note}")
        else:
            lines.append("- Stage1 未记录阻塞 bad case；继续保持影像主导、素材合规、响应式稳定和去模板化。")
        lines.extend(
            [
                "",
                "## Trigger Boundary",
                "",
                "只在任务同时满足以下条件时启用本地进化 overlay：",
                "- 用户需要可直接打开的摄影师作品展、摄影 portfolio、影像项目页或视觉展览网页。",
                "- 用户没有提供完整品牌、图片或策展文案，需要 agent 自主建立视觉方向。",
                "- 成败取决于影像选择、作品系列结构、作品说明、摄影师介绍、联系入口和移动端观看体验。",
                "",
                "不要在企业 dashboard、SaaS 落地页、纯数据工具或无视觉展示目标的任务中套用本 overlay。",
                "",
                "## DeepSeek Execution Brief",
                "",
                "当运行时是 DeepSeek、Reasonix 或其他不具备 Claude 原生 skill 加载语义的 OpenAI-compatible 模型时，先执行这个短合同，再读取后续细节。保持本段作为稳定前缀，任务差异放在用户 payload 和 bad case 反馈里。",
                "",
                "硬规则：",
                "- 输出一个完整单文件 HTML；不要 Markdown、不要解释、不要拆多文件。",
                "- 做真实摄影作品展，不做 SaaS 风营销页、仪表盘、客户 logo 墙或泛泛个人品牌模板。",
                "- 首屏必须直接显示摄影作品或摄影图像区域，并给出展名/摄影师名/系列入口。",
                "- 页面必须包含：作品系列、单张作品说明、关于摄影师、联系或预约观看入口。",
                "- 没有用户上传图片时，使用 web search/fetch 查找开源或明确可免费复用图片；优先 CC0/CC BY/Wikimedia Commons/Openverse，可使用 Unsplash/Pexels 但必须记录其许可证不是开源许可证。",
                "- 不要热链不明来源图片；如果只能热链外部图片，必须在 HTML 注释或脚注记录 URL、来源站点、许可证和作者/署名要求。",
                "- CSS 必须显式写出：`object-fit`、稳定图片比例、`minmax(0, 1fr)`、`overflow-wrap` 或 `word-break`、至少一个 `@media`。",
                "- 允许自由选择黑白、画廊白盒、杂志式或沉浸式方向；禁止 AI 味渐变、过度圆角卡片、无关插画和假照片署名。",
                "- 交付前自检：图片是否可加载、许可证是否记录、首屏是否像摄影展、移动端是否不裁断关键文字；未实际执行的检查不能宣称已执行。",
                "",
                "输出合同：`Output=single_html`；`Visible text=Chinese photography exhibition copy`；`Image policy=licensed sources only with attribution notes`；`No=SaaS dashboard/fake logo wall/purple gradient/orb/stock-like filler`。",
                "",
                "## Progressive Disclosure",
                "",
                "保持 `SKILL.md` 作为短操作手册：先读触发边界、DeepSeek 执行合同、验收契约和反模式；只有当任务需要视觉批评、复杂布局模式或来源证据时，再打开 copied references。",
                "",
                "推荐加载顺序：",
                "- 先读本地进化说明和 `Evaluation Contract`，确认作品展目标与验收标准。",
                "- 需要视觉批评时再读 `references/critique-guide.md`。",
                "- 需要布局或交互模式时再读 `references/advanced-patterns.md`。",
                "- 如果 references 缺失，继续使用本 overlay 的确定性约束，并在报告里记录缺失。",
                "",
                "## References Routing",
                "",
                "- `references/critique-guide.md`: 用于评估 AI 味、模板化视觉、影像主导程度和信息层级。",
                "- `references/advanced-patterns.md`: 用于查找响应式画廊、作品浏览、沉浸式首屏和交互模式。",
                "- 不要把 references 当作必须全文加载的 prompt；只在触发对应子任务时读取相关段落。",
                "",
                "## Evaluation Contract",
                "",
                "生成页面后必须用同一任务至少检查以下项，后续 GEPA 或本地进化只能采纳同时改善这些项的候选：",
                "- 作品展信号：摄影作品、作品展、系列、关于、联系、图片、移动端至少命中 6 项。",
                "- 影像主导：首屏必须有代表性影像区域；不能让介绍文案、统计数字或模板 CTA 成为主角。",
                "- 素材合规：外部图片必须记录来源、许可证和作者/署名要求；未知来源不能采纳。",
                "- 响应式：移动端图片比例稳定，标题、作品说明和联系入口不溢出。",
                "- 验收：产物或交付说明包含图片加载、license notes、desktop screenshot、mobile screenshot、overflow 检查中的至少两项。",
                "- 安全：不自动安装依赖，不声明未执行的截图或构建结果，不写入真实凭据。",
                "",
                "## Anti-patterns",
                "",
                "- 把摄影作品展做成 SaaS hero、企业官网、简历页或营销模板。",
                "- 使用没有来源和许可证说明的照片，或伪造摄影师、作者、拍摄地点。",
                "- 用渐变、光斑、抽象 SVG、logo 墙、假客户评价替代真实影像。",
                "- 所有照片等比例小卡片平铺，没有系列、节奏、留白和观看路径。",
                "- final adopted 阶段重新抽样生成一个新页面；最终预览必须来自实际被采纳的阶段产物。",
                "",
            ]
        )
        return "\n".join(lines)

    lines = [
        migrated_text.rstrip(),
        "",
        "## 本地进化说明",
        "",
        "本节由 DiaEvo Garden 迁移实验追加，用于把迁移得到的 Web 设计方法收敛到中文运营看板任务。",
        "",
        "当用户需要面向一线客服、主管、运营或服务团队的中文前端页面时使用本 skill。优先生成真实工作界面，不生成营销落地页或装饰性 hero 页。",
        "",
        "需要修复的实验反馈：",
    ]
    if feedback:
        for item in feedback[:16]:
            label = str(item.get("label") or "feedback")
            note = str(item.get("note") or item.get("message") or "")
            lines.append(f"- `{label}`: {note}")
    else:
        lines.append("- Stage1 未记录阻塞 bad case；继续保持信息密度、视觉克制和验收纪律。")
    lines.extend(
        [
            "",
            "## Trigger Boundary",
            "",
            "只在任务同时满足以下条件时启用本地进化 overlay：",
            "- 用户要的是可直接使用的中文前端页面、运营看板、后台工具或一线业务界面。",
            "- 首屏成败取决于负责人、SLA、队列压力、风险等级、下一步动作等业务信号是否清楚。",
            "- 迁移来的通用 Web 设计 skill 不足以约束中文非专业用户、密集表格、长编号或移动端溢出风险。",
            "",
            "不要在品牌营销页、纯叙事长文、三维视觉实验或无 dashboard 信息架构的任务中套用本 overlay；这些任务应回到迁移 skill 的通用工作流或其他专门 skill。",
            "",
            "## DeepSeek Execution Brief",
            "",
            "当运行时是 DeepSeek、Reasonix 或其他不具备 Claude 原生 skill 加载语义的 OpenAI-compatible 模型时，先执行这个短合同，再读取后续细节。保持本段作为稳定前缀，任务差异放在用户 payload 和 bad case 反馈里。",
            "",
            "硬规则：",
            "- 输出一个完整单文件 HTML；不要 Markdown、不要解释、不要拆多文件。",
            "- 做真实运营工作台，不做 hero、品牌故事、营销落地页、抽象渐变背景或装饰性大图。",
            "- 首屏必须回答：急单多少、谁负责、哪些快超时、整体忙不忙/队列压力如何、下一步处理动作是什么。",
            "- 可见文案使用中文业务标签：负责人、SLA、队列压力、急单、快超时、升级、待确认、今日处置。",
            "- 信息密度优先于装饰卡片；用表格、分组队列、状态条和短动作按钮承载真实任务。",
            "- 首屏必须出现一个明确标签写作“整体忙不忙”或“队列压力”；不要只在长备注里写整体可控。",
            "- 响应式必须显式写出这些 CSS 证据：`viewport`、`table-layout: fixed`、`minmax(0, 1fr)`、`overflow-wrap` 或 `word-break`、至少一个 `@media`。",
            "- 使用 6 到 8 条静态工单样例；不要 JS 随机造数、复杂图表库、logo 墙或无验证价值的动画。",
            "- `<title>` 必须是短业务标题；页面不要逐字渲染完整用户 prompt。",
            "- 交付前自检业务信号、AI 味禁区、桌面/移动溢出风险和构建/截图验收准备；未实际执行的检查不能宣称已执行。",
            "",
            "输出合同：`Output=single_html`；`Visible text=Chinese operations labels`；`No=hero/marketing/orb/stock illustration/purple gradient/fake logo wall`。",
            "",
            "## Progressive Disclosure",
            "",
            "保持 `SKILL.md` 作为短操作手册：先读触发边界、操作步骤、验收契约和反模式；只有当任务需要视觉批评、复杂布局模式或来源证据时，再打开 copied references。",
            "",
            "推荐加载顺序：",
            "- 先读本地进化说明和 `Evaluation Contract`，确认业务目标与验收标准。",
            "- 需要视觉批评时再读 `references/critique-guide.md`。",
            "- 需要布局或交互模式时再读 `references/advanced-patterns.md`。",
            "- 如果 references 缺失，继续使用本 overlay 的确定性约束，并在报告里记录缺失。",
            "",
            "## References Routing",
            "",
            "- `references/critique-guide.md`: 用于评估 AI 味、模板化视觉、信息层级和业务可读性。",
            "- `references/advanced-patterns.md`: 用于查找复杂响应式布局、数据密集界面和前端展示模式。",
            "- 不要把 references 当作必须全文加载的 prompt；只在触发对应子任务时读取相关段落。",
            "",
            "本地验收门槛：",
            "- 中文非专业用户：标签必须使用负责人、SLA、队列压力、急单、下一步动作等业务语言。",
            "- 去 AI 味：避免泛化口号、装饰性 hero 文案、一站式解决方案话术和没有依据的未来感表述。",
            "- 看板密度：首屏应同时露出指标、队列、负责人、风险等级和动作清单；不要卡片套卡片。",
            "- 响应式行为：为长中文姓名、混合字母数字工单号设置稳定尺寸、表格降级、`overflow-wrap` 和移动端断点。",
            "- 验收准备：交付说明中要包含构建检查、桌面截图、移动端截图和文字溢出检查。",
            "",
            "## Evaluation Contract",
            "",
            "生成页面后必须用同一任务至少检查以下项，后续 GEPA 或本地进化只能采纳同时改善这些项的候选：",
            "- 业务信号：急单、负责人、快超时、队列压力、SLA、下一步动作至少命中 6 项。",
            "- 结构：首屏同时可见指标、队列、负责人和动作；不能把说明文字做成主要内容。",
            "- 响应式：移动端不横向挤压密集表格；长工单号和中文姓名必须换行或降级展示。",
            "- 验收：产物或交付说明包含 build、desktop screenshot、mobile screenshot、overflow 检查中的至少两项。",
            "- 安全：不自动安装依赖，不声明未执行的截图或构建结果，不写入真实凭据。",
            "",
            "## Anti-patterns",
            "",
            "- 用大 hero、渐变口号或营销卡片替代真实运营界面。",
            "- 把用户原始需求整句渲染到首屏或 `<title>`。",
            "- 为了显得完整而生成大量随机 JS 数据、复杂图表或不可验证交互。",
            "- 只在桌面端看起来规整，移动端表格溢出或文字覆盖。",
            "- final adopted 阶段重新抽样生成一个新页面；最终预览必须来自实际被采纳的阶段产物。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_local_evolved_skill(migrated_dir: Path, target_dir: Path, feedback: list[dict[str, Any]]) -> dict[str, Any]:
    _copy_skill(migrated_dir, target_dir)
    skill_path = target_dir / "SKILL.md"
    text = _local_evolved_text(_read_text(skill_path), feedback)
    skill_path.write_text(text, encoding="utf-8")
    structure = _skill_structure_score(text)
    metadata = read_json(target_dir / "metadata.json", default={}) or {}
    metadata["local_evolution"] = {
        "schema": "diaevo.local_skill_evolution.v2",
        "source_stage": "stage1_migrated_skill",
        "bad_case_count": len(feedback),
        "skill_sha256": _sha256_text(text),
        "focus": ["Chinese non-expert users", "anti-AI tone", "dashboard density", "responsive overflow", "verification readiness"],
        "skill_structure": structure,
    }
    write_json(target_dir / "metadata.json", metadata)
    return verify_skill(target_dir)


def _adoption_decision(
    *,
    stage_scores: dict[str, Any],
    verify_migrated: dict[str, Any],
    verify_local_evolved: dict[str, Any],
    verify_gepa: dict[str, Any] | None = None,
    gepa_score: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stage1 = float(stage_scores["stage1_migrated_skill"]["aggregate"])
    stage2 = float(stage_scores["stage2_local_evolved"]["aggregate"])
    reasons: list[str] = []
    if not verify_migrated.get("passed"):
        reasons.append("migrated skill verifier failed")
    if not verify_local_evolved.get("passed"):
        reasons.append("local evolved skill verifier failed")
    local_adoptable = stage2 > stage1 and verify_local_evolved.get("passed") and not reasons
    final_source = "migrated"
    final_stage = "stage1_migrated_skill"
    status = "not_adopted"
    if local_adoptable:
        final_source = "local_evolved"
        final_stage = "stage2_local_evolved"
        status = "adopted_local_evolved"
    else:
        reasons.append("stage2 did not improve over stage1 without verifier regression")

    if gepa_score and verify_gepa and verify_gepa.get("passed"):
        gepa_aggregate = float(gepa_score.get("aggregate", 0))
        if local_adoptable and gepa_aggregate > stage2:
            final_source = "gepa_evolved"
            final_stage = "stage3_final_adopted"
            status = "adopted_gepa_evolved"
    return {
        "schema": "diaevo.skill_adoption_decision.v2",
        "status": status,
        "stage1_aggregate": stage1,
        "stage2_aggregate": stage2,
        "verifier": {
            "migrated": verify_migrated,
            "local_evolved": verify_local_evolved,
            "gepa": verify_gepa or {},
        },
        "final_source": final_source,
        "final_stage": final_stage,
        "reasons": reasons,
        "rule": "仅当 stage2 分数高于 stage1 且 verifier 无回归时采纳本地进化，否则回退到 migrated",
    }


def _evolution_mappings(feedback: list[dict[str, Any]]) -> list[dict[str, str]]:
    mapping = {
        "missing_business_signals": "要求首屏覆盖急单、负责人、SLA 风险、队列压力和下一步动作。",
        "template_or_ai_tone": "拦截营销套话、装饰性 hero 表达和没有依据的未来感表述。",
        "responsive_overflow_risk": "要求稳定尺寸、overflow-wrap、表格降级和长中文/混合工单号的移动端检查。",
        "weak_verification": "要求写明构建检查、桌面截图和移动端溢出验收。",
        "title_contains_user_prompt": "要求短标题，禁止把完整用户 prompt 写入 title。",
        "first_view_contains_full_user_prompt": "要求改写成业务语言，禁止直接渲染完整用户 prompt。",
    }
    rows: list[dict[str, str]] = []
    if not feedback:
        return [
            {
                "stage": "stage1_migrated_skill",
                "bad_case": "none_recorded",
                "note": "Stage1 未记录阻塞 bad case。",
                "local_evolution": "保留迁移 skill 的核心约束，并继续强化中文看板密度、响应式溢出和验收证据。",
            }
        ]
    for item in feedback:
        label = str(item.get("label") or "feedback")
        if label == "stage1_score_summary":
            local_evolution = "把 Stage1 的低分 rubric 转成 stage2 的显式改进目标，避免只生成同分版本。"
        elif label.startswith("stage1_rubric_gap_"):
            local_evolution = "针对 Stage1 的具体 rubric 缺口补强页面可见证据，并在 stage2 输出中体现。"
        else:
            local_evolution = mapping.get(label, "保留迁移 skill 的主约束，并为该反馈增加本地看板验收门槛。")
        rows.append(
            {
                "stage": str(item.get("stage") or "stage1_migrated_skill"),
                "bad_case": label,
                "note": str(item.get("note") or ""),
                "local_evolution": local_evolution,
            }
        )
    return rows


def _skill_structure_score(skill_text: str) -> dict[str, Any]:
    hits = {
        key: token.lower() in skill_text.lower()
        for key, token in SKILL_STRUCTURE_SIGNALS
    }
    reference_count = len(re.findall(r"(?im)^\s*-\s+`?references/", skill_text))
    script_count = len(re.findall(r"(?im)^\s*-\s+`?scripts/", skill_text))
    compact_body = len(skill_text) <= 32_000
    score = (sum(1 for value in hits.values() if value) + int(reference_count > 0) + int(compact_body)) / (len(hits) + 2)
    return {
        "schema": "diaevo.skill_structure_score.v1",
        "score": round(score, 4),
        "signals": hits,
        "reference_count": reference_count,
        "script_count": script_count,
        "compact_body": compact_body,
        "design_sources": list(SKILL_DESIGN_SOURCE_URLS),
    }


def _stage_trace(
    *,
    stage: StageSpec,
    system_prompt: str,
    user_prompt: str,
    skill_input: str,
    llm_result: dict[str, Any],
    output_path: Path | None,
    score: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema": "diaevo.frontend_stage_trace.v2",
        "created_at": _now_iso(),
        "stage": stage.stage_id,
        "task": {"short_title": TASK.short_title, "user_prompt": TASK.user_prompt},
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "skill_input": skill_input[:7000],
        "model": {
            "provider": llm_result.get("provider"),
            "model": llm_result.get("model"),
            "base_url": llm_result.get("base_url"),
            "status": llm_result.get("status"),
            "response_summary": llm_result.get("response_summary"),
            "usage": llm_result.get("usage", {}),
            "error": llm_result.get("error"),
        },
        "output_path": str(output_path) if output_path else "",
        "score": score or {},
        "outcome": "success" if output_path and score else "failed",
    }


def _stage_score_path(reports_dir: Path, stage: StageSpec) -> Path:
    return reports_dir / f"{stage.stage_id}_score.json"


def _stage_status_path(reports_dir: Path, stage: StageSpec) -> Path:
    return reports_dir / f"{stage.stage_id}_status.json"


def _stage_trace_path(traces_dir: Path, stage: StageSpec) -> Path:
    return traces_dir / f"{stage.stage_id}_trace.json"


def _read_resume_stage(stage: StageSpec, *, frontend_dir: Path, reports_dir: Path, traces_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    html_path = frontend_dir / stage.filename
    score_path = _stage_score_path(reports_dir, stage)
    status_path = _stage_status_path(reports_dir, stage)
    if not html_path.exists() or not score_path.exists() or not status_path.exists():
        return None
    html_text = _read_text(html_path)
    if extract_full_html(html_text) is None:
        return None
    score = read_json(score_path, default=None)
    status = read_json(status_path, default=None)
    if not isinstance(score, dict) or not isinstance(status, dict) or status.get("status") != "ok":
        return None
    trace = read_json(_stage_trace_path(traces_dir, stage), default={}) or {}
    if not isinstance(trace, dict) or not trace:
        trace = {
            "schema": "diaevo.frontend_stage_trace.v2",
            "created_at": _now_iso(),
            "stage": stage.stage_id,
            "task": {"short_title": TASK.short_title, "user_prompt": TASK.user_prompt},
            "system_prompt": "",
            "user_prompt": "",
            "skill_input": "",
            "model": {"status": "resumed_existing_artifact"},
            "output_path": str(html_path),
            "score": score,
            "outcome": "success",
        }
    return status, trace, score


def _write_stage_artifacts(
    *,
    stage: StageSpec,
    result: dict[str, Any],
    trace: dict[str, Any],
    score: dict[str, Any],
    reports_dir: Path,
    traces_dir: Path,
) -> None:
    write_json(_stage_score_path(reports_dir, stage), score)
    write_json(_stage_status_path(reports_dir, stage), result)
    write_json(_stage_trace_path(traces_dir, stage), trace)


def _llm_call_record(stage: StageSpec, trace: dict[str, Any], *, resumed: bool = False) -> dict[str, Any] | None:
    if resumed:
        return None
    score = trace.get("score") if isinstance(trace.get("score"), dict) else {}
    model = trace.get("model") if isinstance(trace.get("model"), dict) else {}
    cache = _usage_cache_stats(model.get("usage", {}))
    return {
        "created_at": trace.get("created_at") or _now_iso(),
        "stage": stage.stage_id,
        "system_prompt": trace.get("system_prompt", ""),
        "user_prompt": trace.get("user_prompt", ""),
        "skill_excerpt": trace.get("skill_input", ""),
        "response_summary": model.get("response_summary", ""),
        "html_extraction_status": "ok" if trace.get("outcome") == "success" else score.get("status") or "failed",
        "model": model,
        "prompt_cache": cache,
        "prompt_strategy": trace.get("prompt_strategy", DEFAULT_PROMPT_STRATEGY),
    }


def _usage_cache_stats(usage: Any) -> dict[str, Any]:
    if not isinstance(usage, dict):
        usage = {}
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    prompt_tokens = int(usage.get("prompt_tokens") or hit + miss or 0)
    denominator = hit + miss
    return {
        "hit_tokens": hit,
        "miss_tokens": miss,
        "prompt_tokens": prompt_tokens,
        "hit_ratio": round(hit / denominator, 6) if denominator else 0.0,
        "reported": bool(denominator),
    }


def _cache_summary_from_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    by_stage: dict[str, Any] = {}
    total_hit = 0
    total_miss = 0
    total_prompt = 0
    reported_calls = 0
    for call in calls:
        model = call.get("model") if isinstance(call.get("model"), dict) else {}
        cache = call.get("prompt_cache") if isinstance(call.get("prompt_cache"), dict) else _usage_cache_stats(model.get("usage", {}))
        hit = int(cache.get("hit_tokens") or 0)
        miss = int(cache.get("miss_tokens") or 0)
        prompt_tokens = int(cache.get("prompt_tokens") or hit + miss or 0)
        if hit or miss:
            reported_calls += 1
        total_hit += hit
        total_miss += miss
        total_prompt += prompt_tokens
        stage = str(call.get("stage") or "unknown")
        by_stage[stage] = {
            "hit_tokens": hit,
            "miss_tokens": miss,
            "prompt_tokens": prompt_tokens,
            "hit_ratio": round(hit / (hit + miss), 6) if hit + miss else 0.0,
            "reported": bool(hit + miss),
        }
    denominator = total_hit + total_miss
    return {
        "schema": "diaevo.deepseek_prompt_cache_summary.v1",
        "call_count": len(calls),
        "reported_call_count": reported_calls,
        "hit_tokens": total_hit,
        "miss_tokens": total_miss,
        "prompt_tokens": total_prompt,
        "hit_ratio": round(total_hit / denominator, 6) if denominator else 0.0,
        "by_stage": by_stage,
    }


def _call_stage_llm(
    *,
    client: LLMClient,
    stage: StageSpec,
    skill_text: str,
    feedback: list[dict[str, Any]],
    frontend_dir: Path,
    reports_dir: Path,
    traces_dir: Path,
    image_assets: dict[str, Any] | None = None,
    prompt_strategy: str = DEFAULT_PROMPT_STRATEGY,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    system_prompt = _system_prompt(stage)
    user_prompt = _stage_user_prompt(stage, skill_text=skill_text, feedback=feedback, image_assets=image_assets)
    messages = _stage_messages(
        stage,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        skill_text=skill_text,
        feedback=feedback,
        image_assets=image_assets,
        prompt_strategy=prompt_strategy,
    )
    if prompt_strategy == "legacy":
        llm_result = client.generate(stage=stage, system_prompt=system_prompt, user_prompt=user_prompt)
    else:
        if hasattr(client, "generate_with_messages"):
            llm_result = client.generate_with_messages(  # type: ignore[attr-defined]
                stage=stage,
                messages=messages,
                trace_system_prompt=_trace_prompt_from_messages(messages, role="system"),
                trace_user_prompt=_trace_prompt_from_messages(messages, role="user"),
            )
        else:
            llm_result = client.generate(
                stage=stage,
                system_prompt=_trace_prompt_from_messages(messages, role="system"),
                user_prompt=_trace_prompt_from_messages(messages, role="user"),
            )
    html_text = extract_full_html(str(llm_result.get("response_text") or ""))
    if html_text is None:
        if prompt_strategy == "legacy":
            retry_system_prompt = system_prompt + "上一次输出未包含完整 </html>，现在必须只输出一个更短、更静态的完整 HTML；不要解释，不要代码围栏，不要长 JS。"
            retry_user_prompt = user_prompt + f"\n\n{_retry_user_instruction()}"
            retry_result = client.generate(stage=stage, system_prompt=retry_system_prompt, user_prompt=retry_user_prompt)
        else:
            retry_messages = _stage_messages(
                stage,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                skill_text=skill_text,
                feedback=feedback,
                image_assets=image_assets,
                prompt_strategy=prompt_strategy,
                retry=True,
            )
            retry_system_prompt = _trace_prompt_from_messages(retry_messages, role="system")
            retry_user_prompt = _trace_prompt_from_messages(retry_messages, role="user")
            if hasattr(client, "generate_with_messages"):
                retry_result = client.generate_with_messages(  # type: ignore[attr-defined]
                    stage=stage,
                    messages=retry_messages,
                    trace_system_prompt=retry_system_prompt,
                    trace_user_prompt=retry_user_prompt,
                )
            else:
                retry_result = client.generate(stage=stage, system_prompt=retry_system_prompt, user_prompt=retry_user_prompt)
        retry_html = extract_full_html(str(retry_result.get("response_text") or ""))
        if retry_html is not None:
            llm_result = {
                **retry_result,
                "status": retry_result.get("status", "ok"),
                "retry_after_failed_extract": True,
                "first_response_summary": llm_result.get("response_summary", ""),
                "first_usage": llm_result.get("usage", {}),
            }
            system_prompt = retry_system_prompt
            user_prompt = retry_user_prompt
            html_text = retry_html
    if html_text is None:
        score = {
            "status": "failed_extract_html",
            "aggregate": 0,
            "bad_cases": [{"label": "html_extract_failed", "note": "模型回答中未找到完整 HTML。"}],
            "vision_status": "skipped",
        }
        trace_system_prompt = str(llm_result.get("trace_system_prompt") or system_prompt)
        trace_user_prompt = str(llm_result.get("trace_user_prompt") or user_prompt)
        trace = _stage_trace(
            stage=stage,
            system_prompt=trace_system_prompt,
            user_prompt=trace_user_prompt,
            skill_input=skill_text,
            llm_result={**llm_result, "status": "failed_extract_html"},
            output_path=None,
            score=score,
        )
        trace["prompt_strategy"] = prompt_strategy
        _write_stage_artifacts(stage=stage, result={"status": "failed_extract_html", "stage": stage.stage_id}, trace=trace, score=score, reports_dir=reports_dir, traces_dir=traces_dir)
        return {"status": "failed_extract_html", "stage": stage.stage_id}, trace, score
    html_text = _sanitize_stage_html(html_text, stage)
    output_path = frontend_dir / stage.filename
    _write_text(output_path, html_text)
    score = _score_html(stage, html_text, skill_text=skill_text)
    trace_system_prompt = str(llm_result.get("trace_system_prompt") or system_prompt)
    trace_user_prompt = str(llm_result.get("trace_user_prompt") or user_prompt)
    trace = _stage_trace(
        stage=stage,
        system_prompt=trace_system_prompt,
        user_prompt=trace_user_prompt,
        skill_input=skill_text,
        llm_result=llm_result,
        output_path=output_path,
        score=score,
    )
    trace["prompt_strategy"] = prompt_strategy
    result = {"status": "ok", "stage": stage.stage_id, "output_path": str(output_path)}
    _write_stage_artifacts(stage=stage, result=result, trace=trace, score=score, reports_dir=reports_dir, traces_dir=traces_dir)
    return result, trace, score


def _run_or_resume_stage(
    *,
    client: LLMClient,
    stage: StageSpec,
    skill_text: str,
    feedback: list[dict[str, Any]],
    frontend_dir: Path,
    reports_dir: Path,
    traces_dir: Path,
    image_assets: dict[str, Any] | None = None,
    prompt_strategy: str = DEFAULT_PROMPT_STRATEGY,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
    resumed = _read_resume_stage(stage, frontend_dir=frontend_dir, reports_dir=reports_dir, traces_dir=traces_dir)
    if resumed is not None:
        result, trace, score = resumed
        trace.setdefault("prompt_strategy", prompt_strategy)
        return result, trace, score, True
    result, trace, score = _call_stage_llm(
        client=client,
        stage=stage,
        skill_text=skill_text,
        feedback=feedback,
        frontend_dir=frontend_dir,
        reports_dir=reports_dir,
        traces_dir=traces_dir,
        image_assets=image_assets,
        prompt_strategy=prompt_strategy,
    )
    return result, trace, score, False


def _materialize_final_adopted_stage(
    *,
    source_stage: StageSpec,
    target_stage: StageSpec,
    skill_text: str,
    frontend_dir: Path,
    reports_dir: Path,
    traces_dir: Path,
    prompt_strategy: str = DEFAULT_PROMPT_STRATEGY,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source_path = frontend_dir / source_stage.filename
    if not source_path.exists():
        raise FileNotFoundError(f"adopted frontend source does not exist: {source_path}")
    html_text = _sanitize_stage_html(_read_text(source_path), target_stage)
    output_path = frontend_dir / target_stage.filename
    _write_text(output_path, html_text)
    score = _score_html(target_stage, html_text, skill_text=skill_text)
    trace = _stage_trace(
        stage=target_stage,
        system_prompt="",
        user_prompt="",
        skill_input=skill_text,
        llm_result={
            "provider": "local",
            "model": "adopted-stage-copy",
            "base_url": "",
            "status": "adopted_existing_stage",
            "response_summary": f"Copied adopted frontend from {source_stage.stage_id}.",
            "usage": {},
        },
        output_path=output_path,
        score=score,
    )
    trace["adopted_from_stage"] = source_stage.stage_id
    trace["prompt_strategy"] = prompt_strategy
    result = {
        "status": "ok",
        "stage": target_stage.stage_id,
        "output_path": str(output_path),
        "adopted_from_stage": source_stage.stage_id,
    }
    _write_stage_artifacts(stage=target_stage, result=result, trace=trace, score=score, reports_dir=reports_dir, traces_dir=traces_dir)
    return result, trace, score


def _write_compare_page(frontend_dir: Path) -> Path:
    frames = "\n".join(
        f"""<section><h2>{html.escape(stage.short_title)}</h2><iframe src="{html.escape(stage.filename)}" title="{html.escape(stage.short_title)}"></iframe><a href="{html.escape(stage.filename)}">打开单页</a></section>"""
        for stage in STAGES
    )
    compare = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>阶段对比</title>
  <style>
    body {{ margin: 0; font-family: Arial, "Microsoft YaHei", sans-serif; background: #f5f5f4; color: #1f2937; }}
    main {{ padding: 18px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    section {{ min-width: 0; }}
    h1 {{ margin: 18px 18px 0; font-size: 22px; }}
    h2 {{ margin: 0 0 8px; font-size: 15px; }}
    iframe {{ width: 100%; height: 520px; border: 1px solid #d4d4d8; background: white; }}
    a {{ display: inline-block; margin-top: 6px; color: #0f766e; }}
    @media (max-width: 900px) {{ main {{ grid-template-columns: 1fr; }} iframe {{ height: 460px; }} }}
  </style>
</head>
<body>
  <h1>Garden Skill 迁移进化阶段对比</h1>
  <main>{frames}</main>
</body>
</html>
"""
    path = frontend_dir / "compare.html"
    _write_text(path, compare)
    return path


def _normalize_prompt_strategy(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in PROMPT_STRATEGIES:
        raise ValueError(f"prompt_strategy must be one of {', '.join(PROMPT_STRATEGIES)}, got {value!r}")
    return normalized


def _strategy_root(root: str | Path | None, strategy: str) -> Path | None:
    if root is None:
        return None
    return Path(root) / strategy


def _cache_comparison_report(reports: dict[str, dict[str, Any]], root: Path) -> dict[str, Any]:
    strategies: dict[str, Any] = {}
    for name, report in reports.items():
        cache = report.get("prompt_cache") if isinstance(report.get("prompt_cache"), dict) else {}
        adoption = report.get("adoption_decision") if isinstance(report.get("adoption_decision"), dict) else {}
        strategies[name] = {
            "status": report.get("status"),
            "experiment_root": report.get("experiment_root"),
            "runtime": report.get("runtime", {}),
            "prompt_cache": cache,
            "stage2_aggregate": adoption.get("stage2_aggregate"),
            "final_source": adoption.get("final_source"),
        }
    legacy = strategies.get("legacy", {}).get("prompt_cache", {})
    cache_first = strategies.get("cache_first", {}).get("prompt_cache", {})
    hit_ratio_delta = float(cache_first.get("hit_ratio") or 0.0) - float(legacy.get("hit_ratio") or 0.0)
    miss_delta = int(cache_first.get("miss_tokens") or 0) - int(legacy.get("miss_tokens") or 0)
    return {
        "schema": "diaevo.reasonix_cache_first_comparison.v1",
        "status": "completed",
        "experiment_root": str(root),
        "strategies": strategies,
        "cache_first_vs_legacy": {
            "hit_ratio_delta": round(hit_ratio_delta, 6),
            "miss_tokens_delta": miss_delta,
            "reported": bool(legacy.get("reported_call_count") or cache_first.get("reported_call_count")),
        },
    }


def run_cache_first_comparison(
    *,
    root: str | Path | None = None,
    refresh_reference_repo: bool = False,
    with_gepa: bool = False,
    with_llm: bool = False,
    dry_run: bool = False,
    env_path: str | None = None,
    llm_client: LLMClient | None = None,
    task_limit: int | None = None,
    reference_repo_dir: str | Path = DEFAULT_REFERENCE_REPO_DIR,
    reference_url: str = REFERENCE_URL,
    reference_commit: str = REFERENCE_COMMIT,
    reference_subdir: str = REFERENCE_SUBDIR,
    task: FrontendTask | None = None,
    stage1_feedback_summary: bool = False,
) -> dict[str, Any]:
    started_at = _now_iso()
    started_perf = time.perf_counter()
    comparison_root = Path(root) if root else ROOT / "experiments" / f"reasonix_cache_first_comparison_{_now_stamp()}"
    reports: dict[str, dict[str, Any]] = {}
    for strategy in PROMPT_STRATEGIES:
        reports[strategy] = run_experiment(
            root=comparison_root / strategy,
            refresh_reference_repo=refresh_reference_repo,
            with_gepa=with_gepa,
            with_llm=with_llm,
            dry_run=dry_run,
            env_path=env_path,
            llm_client=llm_client,
            task_limit=task_limit,
            reference_repo_dir=reference_repo_dir,
            reference_url=reference_url,
            reference_commit=reference_commit,
            reference_subdir=reference_subdir,
            task=task,
            prompt_strategy=strategy,
            stage1_feedback_summary=stage1_feedback_summary,
        )
    report = _cache_comparison_report(reports, comparison_root)
    report["runtime"] = _runtime_summary(started_at, started_perf)
    reports_dir = comparison_root / "reports"
    write_json(reports_dir / "cache_first_comparison.json", report)
    _write_text(reports_dir / "cache_first_comparison.md", _cache_comparison_markdown(report))
    return report


def _cache_comparison_markdown(report: dict[str, Any]) -> str:
    strategies = report.get("strategies", {}) if isinstance(report.get("strategies"), dict) else {}
    lines = [
        "# Reasonix Cache-First Prompt 对比",
        "",
        f"- 状态：`{report.get('status')}`",
        f"- 实验目录：`{report.get('experiment_root')}`",
        f"- 总运行时秒数：`{report.get('runtime', {}).get('duration_seconds', '')}`",
        "",
        "| 策略 | 状态 | Cache Hit Ratio | Hit Tokens | Miss Tokens | LLM Calls | Runtime s | Stage2 分数 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in PROMPT_STRATEGIES:
        item = strategies.get(name, {}) if isinstance(strategies.get(name), dict) else {}
        cache = item.get("prompt_cache", {}) if isinstance(item.get("prompt_cache"), dict) else {}
        runtime = item.get("runtime", {}) if isinstance(item.get("runtime"), dict) else {}
        lines.append(
            f"| {name} | {item.get('status', '')} | {cache.get('hit_ratio', 0)} | {cache.get('hit_tokens', 0)} | {cache.get('miss_tokens', 0)} | {cache.get('call_count', 0)} | {runtime.get('duration_seconds', '')} | {item.get('stage2_aggregate', '')} |"
        )
    delta = report.get("cache_first_vs_legacy", {}) if isinstance(report.get("cache_first_vs_legacy"), dict) else {}
    lines.extend(
        [
            "",
            "## Delta",
            "",
            f"- hit_ratio_delta：`{delta.get('hit_ratio_delta', 0)}`",
            f"- miss_tokens_delta：`{delta.get('miss_tokens_delta', 0)}`",
        ]
    )
    return "\n".join(lines)


def _markdown_report(report: dict[str, Any]) -> str:
    migration = report.get("migration", {})
    prompt_cache = report.get("prompt_cache", {}) if isinstance(report.get("prompt_cache"), dict) else {}
    lines = [
        "# Garden Skill 迁移进化实验",
        "",
        f"- 状态：`{report['status']}`",
        f"- 实验目录：`{report['experiment_root']}`",
        f"- 固定任务：{TASK.user_prompt}",
        f"- Prompt 策略：`{report.get('prompt_strategy', DEFAULT_PROMPT_STRATEGY)}`",
        f"- DeepSeek cache hit ratio：`{prompt_cache.get('hit_ratio', 0)}`；hit/miss tokens：`{prompt_cache.get('hit_tokens', 0)}` / `{prompt_cache.get('miss_tokens', 0)}`",
        f"- 视觉模型状态：`skipped`",
        f"- 参考仓库 URL：`{migration.get('reference_url', REFERENCE_URL)}`",
        f"- 本地参考仓库：`{migration.get('local_repo_path', '')}`",
        f"- Git HEAD：`{migration.get('git_head', '')}`",
        f"- 源子目录：`{migration.get('source_subdir', '')}`",
        "- 历史说明：`website/web-design-website` 不再作为本实验迁移源。",
        "",
        "## 阶段评分",
        "",
        "| 阶段 | 综合 | 业务可用 | 信息架构 | 视觉克制 | 反模板 | 响应式 | 验收准备 | Skill 遵循 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for stage in STAGES:
        score = report["stage_scores"].get(stage.stage_id, {})
        lines.append(
            f"| {stage.stage_id} | {score.get('aggregate', 0)} | {score.get('business_usability', 0)} | {score.get('information_architecture', 0)} | {score.get('visual_restraint', 0)} | {score.get('anti_template', 0)} | {score.get('responsive_risk', 0)} | {score.get('verification_readiness', 0)} | {score.get('skill_compliance', 0)} |"
        )
    decision = report["adoption_decision"]
    lines.extend(
        [
            "",
            "## 迁移证据",
            "",
            f"- 源 commit：`{report['migration']['source_commit']}`",
            f"- 源子目录：`{report['migration']['source_subdir']}`",
            f"- 迁移 skill 哈希：`{report['migration']['migrated_skill_sha256']}`",
            f"- 已记录文件哈希数量：`{len(report['migration']['file_hashes'])}`",
            "",
            "## 进化证据",
            "",
            f"- Stage1 反馈条目：`{report['evolution']['stage1_bad_case_count']}`",
            "- 本地进化 skill 聚焦中文非专业用户、去 AI 味、看板信息密度、响应式溢出和截图/构建验收。",
            "",
            "## 采纳决策",
            "",
            f"- 决策：`{decision['status']}`",
            f"- 最终来源：`{decision['final_source']}`",
            f"- 规则：{decision['rule']}",
            f"- 原因：`{'; '.join(decision['reasons']) or '无'}`",
            "",
            "## 产物",
            "",
            f"- 对比页：`{report['artifacts'].get('compare_html', '')}`",
            f"- 实验 trace：`{report['artifacts'].get('experiment_traces', '')}`",
            f"- LLM 调用记录：`{report['artifacts'].get('llm_calls', '')}`",
            f"- 阶段反馈：`{report['artifacts'].get('stage_feedback', '')}`",
            "",
        ]
    )
    return "\n".join(lines)


def _blocked_report(
    root: Path,
    llm_status: dict[str, Any],
    *,
    dry_run: bool = False,
    reference_repo_dir: Path = DEFAULT_REFERENCE_REPO_DIR,
    reference_url: str = REFERENCE_URL,
    reference_commit: str = REFERENCE_COMMIT,
    reference_subdir: str = REFERENCE_SUBDIR,
) -> dict[str, Any]:
    reference_check = _reference_repo_status(
        reference_repo_dir,
        reference_url=reference_url,
        commit=reference_commit,
        subdir=reference_subdir,
    )
    return {
        "schema": "diaevo.garden_skill_migration_evolution_experiment.v2",
        "status": "dry_run" if dry_run else "blocked_missing_llm_config",
        "experiment_root": str(root),
        "llm": llm_status,
        "reference_repo_check": reference_check,
        "cleanup_precheck": {
            "validation_runs_related_old_copies": _validation_run_leftovers(),
            "cleanup_policy": "这里只做只读扫描；清理动作必须单独执行，不混入实验脚本默认流程",
        },
        "output_plan": {"top_level_dirs": list(TOP_LEVEL_DIRS), "stage_files": [stage.filename for stage in STAGES]},
        "task": TASK.__dict__,
        "artifacts": {},
    }


def _write_run_trace_artifacts(
    *,
    traces_dir: Path,
    reports_dir: Path,
    traces: list[dict[str, Any]],
    llm_calls: list[dict[str, Any]],
    feedback_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    write_jsonl(traces_dir / "experiment_traces.jsonl", traces)
    existing_llm_calls = read_jsonl(traces_dir / "llm_calls.jsonl") if (traces_dir / "llm_calls.jsonl").exists() else []
    all_llm_calls = [*existing_llm_calls, *llm_calls]
    write_jsonl(traces_dir / "llm_calls.jsonl", all_llm_calls)
    write_jsonl(traces_dir / "stage_feedback.jsonl", feedback_rows)
    prompt_cache = _cache_summary_from_calls(all_llm_calls)
    write_json(reports_dir / "prompt_cache_summary.json", prompt_cache)
    return all_llm_calls, prompt_cache


def _run_experiment(
    *,
    root: str | Path | None = None,
    refresh_reference_repo: bool = False,
    with_gepa: bool = False,
    with_llm: bool = False,
    dry_run: bool = False,
    env_path: str | None = None,
    llm_client: LLMClient | None = None,
    task_limit: int | None = None,
    reference_repo_dir: str | Path = DEFAULT_REFERENCE_REPO_DIR,
    reference_url: str = REFERENCE_URL,
    reference_commit: str = REFERENCE_COMMIT,
    reference_subdir: str = REFERENCE_SUBDIR,
    prompt_strategy: str = DEFAULT_PROMPT_STRATEGY,
    stage1_feedback_summary: bool = False,
) -> dict[str, Any]:
    del task_limit  # compatibility with the previous smoke-test interface
    prompt_strategy = _normalize_prompt_strategy(prompt_strategy)
    ensure_project_dirs()
    experiment_root = Path(root) if root else ROOT / "experiments" / f"garden_web_design_engineer_migration_full_{_now_stamp()}"
    reference_repo = Path(reference_repo_dir)
    config, llm_status = _llm_config_status(env_path=env_path or str(ROOT / ".env"))
    if dry_run:
        return _blocked_report(
            experiment_root,
            llm_status,
            dry_run=True,
            reference_repo_dir=reference_repo,
            reference_url=reference_url,
            reference_commit=reference_commit,
            reference_subdir=reference_subdir,
        )
    if not with_llm and llm_client is None:
        report = _blocked_report(
            experiment_root,
            {**llm_status, "reason": "--with-llm is required for real stage generation"},
            reference_repo_dir=reference_repo,
            reference_url=reference_url,
            reference_commit=reference_commit,
            reference_subdir=reference_subdir,
        )
        (experiment_root / "reports").mkdir(parents=True, exist_ok=True)
        write_json(experiment_root / "reports" / "final_status.json", report)
        return report
    if llm_client is None and config is None:
        report = _blocked_report(
            experiment_root,
            llm_status,
            reference_repo_dir=reference_repo,
            reference_url=reference_url,
            reference_commit=reference_commit,
            reference_subdir=reference_subdir,
        )
        (experiment_root / "reports").mkdir(parents=True, exist_ok=True)
        write_json(experiment_root / "reports" / "final_status.json", report)
        return report
    client = llm_client or DeepSeekLLMClient(config)  # type: ignore[arg-type]

    for name in TOP_LEVEL_DIRS:
        (experiment_root / name).mkdir(parents=True, exist_ok=True)
    frontend_dir = experiment_root / "frontend_html"
    traces_dir = experiment_root / "traces"
    reports_dir = experiment_root / "reports"
    skills_dir = experiment_root / "skills"

    migrated_dir = skills_dir / "migrated"
    try:
        repo_info = _prepare_reference_repo(
            repo_dir=reference_repo,
            reference_url=reference_url,
            commit=reference_commit,
            subdir=reference_subdir,
            refresh=refresh_reference_repo,
        )
    except Exception as exc:
        report = _blocked_report(
            experiment_root,
            llm_status,
            reference_repo_dir=reference_repo,
            reference_url=reference_url,
            reference_commit=reference_commit,
            reference_subdir=reference_subdir,
        )
        report["status"] = "blocked_reference_repo_unavailable"
        report["reference_repo_error"] = _reference_repo_error(exc)
        write_json(reports_dir / "final_status.json", report)
        return report
    source_dir = Path(repo_info["local_repo_path"]) / reference_subdir
    if not source_dir.exists():
        report = _blocked_report(
            experiment_root,
            llm_status,
            reference_repo_dir=reference_repo,
            reference_url=reference_url,
            reference_commit=reference_commit,
            reference_subdir=reference_subdir,
        )
        report["status"] = "blocked_reference_source_missing"
        report["reference_repo_error"] = {
            "type": "FileNotFoundError",
            "message": f"参考源子目录不存在：{source_dir}",
            "source_dir": str(source_dir),
        }
        write_json(reports_dir / "final_status.json", report)
        return report
    existing_verify_migrated = verify_skill(migrated_dir) if migrated_dir.exists() else {"passed": False}
    if existing_verify_migrated.get("passed"):
        adaptation = read_json(migrated_dir / "adaptation_report.json", default={}) or {}
        verify_migrated = existing_verify_migrated
    else:
        adaptation = adapt_external_skill(
            source=source_dir,
            source_commit=reference_commit,
            output_dir=migrated_dir,
            with_gepa=with_gepa,
        )
        verify_migrated = verify_skill(migrated_dir)
    migration = _migration_summary(
        adaptation,
        migrated_dir,
        reference_url=reference_url,
        local_repo_path=Path(repo_info["local_repo_path"]),
        source_dir=source_dir,
        source_subdir=reference_subdir,
        source_commit=reference_commit,
        git_head=repo_info["head"],
    )
    write_json(reports_dir / "migration_report.json", migration)

    traces: list[dict[str, Any]] = []
    llm_calls: list[dict[str, Any]] = []
    feedback_rows: list[dict[str, Any]] = []
    stage_scores: dict[str, Any] = {}
    stage_outputs: dict[str, Any] = {}

    migrated_text = _read_text(migrated_dir / "SKILL.md")
    image_assets = _prepare_photography_assets(frontend_dir, reports_dir) if TASK.task_id == "photography_portfolio_exhibition" else {}
    stage0_result, stage0_trace, stage0_score, stage0_resumed = _run_or_resume_stage(
        client=client,
        stage=STAGES[0],
        skill_text="",
        feedback=[],
        frontend_dir=frontend_dir,
        reports_dir=reports_dir,
        traces_dir=traces_dir,
        image_assets=image_assets,
        prompt_strategy=prompt_strategy,
    )
    traces.append(stage0_trace)
    call = _llm_call_record(STAGES[0], stage0_trace, resumed=stage0_resumed)
    if call:
        llm_calls.append(call)
    stage_scores[STAGES[0].stage_id] = stage0_score
    stage_outputs[STAGES[0].stage_id] = stage0_result
    if stage0_result["status"] != "ok":
        _, prompt_cache = _write_run_trace_artifacts(traces_dir=traces_dir, reports_dir=reports_dir, traces=traces, llm_calls=llm_calls, feedback_rows=feedback_rows)
        return {
            "status": "failed_extract_html",
            "experiment_root": str(experiment_root),
            "stage": STAGES[0].stage_id,
            "prompt_strategy": prompt_strategy,
            "prompt_cache": prompt_cache,
            "stage_scores": stage_scores,
            "stage_outputs": stage_outputs,
        }

    stage1_result, stage1_trace, stage1_score, stage1_resumed = _run_or_resume_stage(
        client=client,
        stage=STAGES[1],
        skill_text=migrated_text,
        feedback=[],
        frontend_dir=frontend_dir,
        reports_dir=reports_dir,
        traces_dir=traces_dir,
        image_assets=image_assets,
        prompt_strategy=prompt_strategy,
    )
    traces.append(stage1_trace)
    call = _llm_call_record(STAGES[1], stage1_trace, resumed=stage1_resumed)
    if call:
        llm_calls.append(call)
    stage_scores[STAGES[1].stage_id] = stage1_score
    stage_outputs[STAGES[1].stage_id] = stage1_result
    feedback_rows.extend({"stage": STAGES[1].stage_id, **case} for case in stage1_score.get("bad_cases", []))
    if stage1_feedback_summary:
        feedback_rows.extend(_stage1_feedback_summary(stage1_score))
    if stage1_result["status"] != "ok":
        _, prompt_cache = _write_run_trace_artifacts(traces_dir=traces_dir, reports_dir=reports_dir, traces=traces, llm_calls=llm_calls, feedback_rows=feedback_rows)
        return {
            "status": "failed_extract_html",
            "experiment_root": str(experiment_root),
            "stage": STAGES[1].stage_id,
            "prompt_strategy": prompt_strategy,
            "prompt_cache": prompt_cache,
            "stage_scores": stage_scores,
            "stage_outputs": stage_outputs,
        }

    local_evolved_dir = skills_dir / "local_evolved"
    verify_local_evolved = verify_skill(local_evolved_dir) if local_evolved_dir.exists() else {"passed": False}
    if not verify_local_evolved.get("passed"):
        verify_local_evolved = _write_local_evolved_skill(migrated_dir, local_evolved_dir, feedback_rows)
    local_evolved_text = _read_text(local_evolved_dir / "SKILL.md")
    evolution = {
        "schema": "diaevo.local_skill_evolution_report.v2",
        "status": "ok",
        "source_skill": str(migrated_dir / "SKILL.md"),
        "local_evolved_skill": str(local_evolved_dir / "SKILL.md"),
        "stage1_bad_case_count": len(stage1_score.get("bad_cases", [])),
        "stage1_feedback_row_count": len(feedback_rows),
        "stage1_feedback_summary_enabled": stage1_feedback_summary,
        "bad_case_to_local_evolution": _evolution_mappings(feedback_rows),
        "focus": ["中文非专业用户场景", "去 AI 味约束", "dashboard 信息密度", "响应式与文本溢出", "截图/构建验收建议"],
        "skill_structure": _skill_structure_score(local_evolved_text),
        "verify_local_evolved": verify_local_evolved,
    }
    write_json(reports_dir / "evolution_report.json", evolution)

    stage2_result, stage2_trace, stage2_score, stage2_resumed = _run_or_resume_stage(
        client=client,
        stage=STAGES[2],
        skill_text=local_evolved_text,
        feedback=feedback_rows,
        frontend_dir=frontend_dir,
        reports_dir=reports_dir,
        traces_dir=traces_dir,
        image_assets=image_assets,
        prompt_strategy=prompt_strategy,
    )
    traces.append(stage2_trace)
    call = _llm_call_record(STAGES[2], stage2_trace, resumed=stage2_resumed)
    if call:
        llm_calls.append(call)
    stage_scores[STAGES[2].stage_id] = stage2_score
    stage_outputs[STAGES[2].stage_id] = stage2_result
    feedback_rows.extend({"stage": STAGES[2].stage_id, **case} for case in stage2_score.get("bad_cases", []))
    if stage2_result["status"] != "ok":
        _, prompt_cache = _write_run_trace_artifacts(traces_dir=traces_dir, reports_dir=reports_dir, traces=traces, llm_calls=llm_calls, feedback_rows=feedback_rows)
        return {
            "status": "failed_extract_html",
            "experiment_root": str(experiment_root),
            "stage": STAGES[2].stage_id,
            "prompt_strategy": prompt_strategy,
            "prompt_cache": prompt_cache,
            "stage_scores": stage_scores,
            "stage_outputs": stage_outputs,
        }

    decision_before_final = _adoption_decision(
        stage_scores=stage_scores,
        verify_migrated=verify_migrated,
        verify_local_evolved=verify_local_evolved,
    )
    final_adopted_dir = skills_dir / "final_adopted"
    if decision_before_final["final_source"] == "local_evolved":
        _copy_skill(local_evolved_dir, final_adopted_dir)
        final_skill_text = local_evolved_text
    else:
        _copy_skill(migrated_dir, final_adopted_dir)
        final_skill_text = migrated_text
    verify_final_adopted = verify_skill(final_adopted_dir)

    gepa = {
        "requested": with_gepa,
        "status": "skipped",
        "reason": "本脚本不调用 GEPA skill 进化 runner；若本地进化分数提升且 verifier 通过，则采用本地进化。",
    }
    source_stage_by_id = {stage.stage_id: stage for stage in STAGES}
    final_frontend_source = source_stage_by_id.get(decision_before_final["final_stage"], STAGES[1])
    stage3_result, stage3_trace, stage3_score = _materialize_final_adopted_stage(
        source_stage=final_frontend_source,
        target_stage=STAGES[3],
        skill_text=final_skill_text,
        frontend_dir=frontend_dir,
        reports_dir=reports_dir,
        traces_dir=traces_dir,
        prompt_strategy=prompt_strategy,
    )
    traces.append(stage3_trace)
    stage_scores[STAGES[3].stage_id] = stage3_score
    stage_outputs[STAGES[3].stage_id] = stage3_result
    feedback_rows.extend({"stage": STAGES[3].stage_id, **case} for case in stage3_score.get("bad_cases", []))
    if stage3_result["status"] != "ok":
        _, prompt_cache = _write_run_trace_artifacts(traces_dir=traces_dir, reports_dir=reports_dir, traces=traces, llm_calls=llm_calls, feedback_rows=feedback_rows)
        return {
            "status": "failed_extract_html",
            "experiment_root": str(experiment_root),
            "stage": STAGES[3].stage_id,
            "prompt_strategy": prompt_strategy,
            "prompt_cache": prompt_cache,
            "stage_scores": stage_scores,
            "stage_outputs": stage_outputs,
        }

    final_decision = _adoption_decision(
        stage_scores=stage_scores,
        verify_migrated=verify_migrated,
        verify_local_evolved=verify_local_evolved,
    )
    compare_path = _write_compare_page(frontend_dir)
    _, prompt_cache = _write_run_trace_artifacts(traces_dir=traces_dir, reports_dir=reports_dir, traces=traces, llm_calls=llm_calls, feedback_rows=feedback_rows)
    write_json(reports_dir / "stage_scores.json", stage_scores)
    write_json(reports_dir / "adoption_decision.json", final_decision)

    report = {
        "schema": "diaevo.garden_skill_migration_evolution_experiment.v2",
        "status": "migration_evolution_passed" if final_decision["status"].startswith("adopted") and stage3_result["status"] == "ok" else "completed_but_not_adopted",
        "experiment_root": str(experiment_root),
        "prompt_strategy": prompt_strategy,
        "prompt_cache": prompt_cache,
        "task": TASK.__dict__,
        "llm": llm_status,
        "reference_repo": repo_info,
        "migration": migration,
        "evolution": evolution,
        "quality_experiment": {
            "stage1_feedback_summary": stage1_feedback_summary,
        },
        "gepa": gepa,
        "photography_assets": image_assets,
        "verify": {"migrated": verify_migrated, "local_evolved": verify_local_evolved, "final_adopted": verify_final_adopted},
        "stage_scores": stage_scores,
        "stage_outputs": stage_outputs,
        "adoption_decision": final_decision,
        "artifacts": {
            "frontend_html": str(frontend_dir),
            "compare_html": str(compare_path),
            "experiment_traces": str(traces_dir / "experiment_traces.jsonl"),
            "llm_calls": str(traces_dir / "llm_calls.jsonl"),
            "stage_feedback": str(traces_dir / "stage_feedback.jsonl"),
            "stage_scores": str(reports_dir / "stage_scores.json"),
            "migration_report": str(reports_dir / "migration_report.json"),
            "evolution_report": str(reports_dir / "evolution_report.json"),
            "adoption_decision": str(reports_dir / "adoption_decision.json"),
            "prompt_cache_summary": str(reports_dir / "prompt_cache_summary.json"),
            "photography_assets": str(reports_dir / "photography_assets.json") if image_assets else "",
            "final_report": str(reports_dir / "final_experiment_report.md"),
            "migrated_skill": str(migrated_dir / "SKILL.md"),
            "local_evolved_skill": str(local_evolved_dir / "SKILL.md"),
            "final_adopted_skill": str(final_adopted_dir / "SKILL.md"),
        },
    }
    write_json(reports_dir / "final_experiment_report.json", report)
    _write_text(reports_dir / "final_experiment_report.md", _markdown_report(report))
    write_json(REPORTS_DIR / "garden_skill_migration_evolution_latest.json", report)
    return report


def run_experiment(
    *,
    root: str | Path | None = None,
    refresh_reference_repo: bool = False,
    with_gepa: bool = False,
    with_llm: bool = False,
    dry_run: bool = False,
    env_path: str | None = None,
    llm_client: LLMClient | None = None,
    task_limit: int | None = None,
    reference_repo_dir: str | Path = DEFAULT_REFERENCE_REPO_DIR,
    reference_url: str = REFERENCE_URL,
    reference_commit: str = REFERENCE_COMMIT,
    reference_subdir: str = REFERENCE_SUBDIR,
    task: FrontendTask | None = None,
    prompt_strategy: str = DEFAULT_PROMPT_STRATEGY,
    stage1_feedback_summary: bool = False,
) -> dict[str, Any]:
    global TASK
    started_at = _now_iso()
    started_perf = time.perf_counter()
    previous_task = TASK
    if task is not None:
        TASK = task
    try:
        report = _run_experiment(
            root=root,
            refresh_reference_repo=refresh_reference_repo,
            with_gepa=with_gepa,
            with_llm=with_llm,
            dry_run=dry_run,
            env_path=env_path,
            llm_client=llm_client,
            task_limit=task_limit,
            reference_repo_dir=reference_repo_dir,
            reference_url=reference_url,
            reference_commit=reference_commit,
            reference_subdir=reference_subdir,
            prompt_strategy=prompt_strategy,
            stage1_feedback_summary=stage1_feedback_summary,
        )
        report["runtime"] = _runtime_summary(started_at, started_perf)
        _persist_report_runtime(report)
        return report
    finally:
        TASK = previous_task


def _parse_required_signals(value: str | None, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return fallback
    signals = tuple(item.strip() for item in re.split(r"[,，]", value) if item.strip())
    return signals or fallback


def _retry_user_instruction() -> str:
    if TASK.task_id == "photography_portfolio_exhibition":
        return (
            "重试要求：请将页面压缩为一个摄影作品展单页：首屏代表性影像、4 个作品系列、"
            "1 个作品清单、1 个关于摄影师区、1 个联系/预约区和图片来源/许可证脚注；"
            "不要写长篇单张作品解析、设计说明或 HTML 外部文字；"
            "预算紧张时先删 JS、动画和长文案，必须从 <!doctype html> 开始并以 </html> 结束。"
        )
    return (
        "重试要求：请将页面压缩为 6 个指标卡、1 个 6 行工单表格、1 个负责人负载区和 1 个验收备注区；"
        "必须从 <!doctype html> 开始并以 </html> 结束。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Garden web-design-engineer skill 迁移与本地进化实验。")
    parser.add_argument("--root", default=None, help="实验根目录；默认写入带时间戳的 experiments/ 子目录。")
    parser.add_argument("--resume-root", default=None, help="断点续传目录；兼容旧参数语义，等价于 --root。")
    parser.add_argument("--reference-repo-dir", default=str(DEFAULT_REFERENCE_REPO_DIR), help="ConardLi/garden-skills 的本地 git checkout 路径。")
    parser.add_argument("--reference-url", default=REFERENCE_URL, help="Garden skills 参考仓库的 Git URL。")
    parser.add_argument("--reference-commit", default=REFERENCE_COMMIT, help="固定使用的 Garden skills commit。")
    parser.add_argument("--reference-subdir", default=REFERENCE_SUBDIR, help="参考仓库内的源 skill 子目录。")
    parser.add_argument("--refresh-reference-repo", action="store_true", help="重新 fetch 并 checkout 到固定参考 commit。")
    parser.add_argument("--with-gepa", action="store_true", help="记录 GEPA 请求；当前脚本仍使用本地进化采纳路径。")
    parser.add_argument("--with-llm", action="store_true", help="实际调用已配置的 DeepSeek/OpenAI-compatible LLM。")
    parser.add_argument("--dry-run", action="store_true", help="只检查参考仓库、LLM 配置和输出规划，不写阶段 HTML。")
    parser.add_argument("--env-path", default=str(ROOT / ".env"), help="包含 DeepSeek/OpenAI-compatible 配置的 .env 路径。")
    parser.add_argument("--task-preset", choices=sorted(TASK_PRESETS), default="logistics", help="内置实验任务。")
    parser.add_argument("--task-id", default=None, help="自定义任务 ID。")
    parser.add_argument("--task-title", default=None, help="自定义短标题。")
    parser.add_argument("--task-prompt", default=None, help="自定义用户提示词。")
    parser.add_argument("--task-audience", default=None, help="自定义目标用户。")
    parser.add_argument("--task-required-signals", default=None, help="自定义必需信号，使用逗号分隔。")
    parser.add_argument(
        "--prompt-strategy",
        choices=PROMPT_STRATEGY_CLI_CHOICES,
        default="legacy",
        help="DeepSeek prompt 组织策略；both 会分别运行 legacy 和 cache-first 并生成对比报告。",
    )
    parser.add_argument(
        "--stage1-feedback-summary",
        action="store_true",
        help="将 stage1 的低分 rubric 摘要作为额外 feedback 传给 local_evolved，用于最小质量增强实验。",
    )
    parser.add_argument("reference_repo_dir_positional", nargs="?", help=argparse.SUPPRESS)
    args = parser.parse_args()
    reference_repo_dir = args.reference_repo_dir
    if args.reference_repo_dir_positional:
        if args.reference_repo_dir != str(DEFAULT_REFERENCE_REPO_DIR):
            parser.error("不要同时使用裸路径参数和 --reference-repo-dir；二选一即可。")
        reference_repo_dir = args.reference_repo_dir_positional
    preset_task = TASK_PRESETS[args.task_preset]
    task = FrontendTask(
        task_id=args.task_id or preset_task.task_id,
        short_title=args.task_title or preset_task.short_title,
        user_prompt=args.task_prompt or preset_task.user_prompt,
        audience=args.task_audience or preset_task.audience,
        required_signals=_parse_required_signals(args.task_required_signals, preset_task.required_signals),
    )
    if args.prompt_strategy == "both":
        report = run_cache_first_comparison(
            root=args.resume_root or args.root,
            refresh_reference_repo=args.refresh_reference_repo,
            with_gepa=args.with_gepa,
            with_llm=args.with_llm,
            dry_run=args.dry_run,
            env_path=args.env_path,
            reference_repo_dir=reference_repo_dir,
            reference_url=args.reference_url,
            reference_commit=args.reference_commit,
            reference_subdir=args.reference_subdir,
            task=task,
            stage1_feedback_summary=args.stage1_feedback_summary,
        )
    else:
        report = run_experiment(
            root=args.resume_root or args.root,
            refresh_reference_repo=args.refresh_reference_repo,
            with_gepa=args.with_gepa,
            with_llm=args.with_llm,
            dry_run=args.dry_run,
            env_path=args.env_path,
            reference_repo_dir=reference_repo_dir,
            reference_url=args.reference_url,
            reference_commit=args.reference_commit,
            reference_subdir=args.reference_subdir,
            task=task,
            prompt_strategy=args.prompt_strategy,
            stage1_feedback_summary=args.stage1_feedback_summary,
        )
    _print_json(
        {
            "status": report["status"],
            "experiment_root": report["experiment_root"],
            "artifacts": report.get("artifacts", {}),
            "adoption_decision": report.get("adoption_decision", {}),
            "llm": report.get("llm", {}),
            "reference_repo_error": report.get("reference_repo_error", {}),
            "stage": report.get("stage", ""),
            "prompt_strategy": report.get("prompt_strategy", args.prompt_strategy),
            "quality_experiment": report.get("quality_experiment", {}),
            "prompt_cache": report.get("prompt_cache", {}),
            "cache_first_vs_legacy": report.get("cache_first_vs_legacy", {}),
        }
    )
    return 0 if report["status"] in {"migration_evolution_passed", "dry_run", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
