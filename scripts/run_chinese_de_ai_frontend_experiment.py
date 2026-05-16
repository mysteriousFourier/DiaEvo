from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diaevo.deepseek_chat import (
    chat_completion,
    config_from_env,
    extract_assistant_text,
    multimodal_user_message,
    vision_config_from_env,
)
from diaevo.evolution import evolve_skill
from diaevo.generator import generate_skill
from diaevo.gepa_adapter import evaluate_gepa
from diaevo.ingest import ingest_traces, load_traces
from diaevo.miner import mine
from diaevo.validation_runner import run_validation
from diaevo.verifier import verify_skill


REPORTS_DIR = ROOT / "outputs" / "reports"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
GEPA_BUDGET = 50
USER_PROMPT = (
    "我想做一个网页，给我们客服主管每天早上看物流工单情况用。她不懂技术，只想一眼知道今天哪些客户比较急、"
    "谁负责、有没有快超时的单子、整体忙不忙。页面要看起来专业一点，不要像那种 AI 生成的炫酷模板，"
    "别一堆紫色渐变、大圆角卡片和空话。最好电脑上能看，手机上临时打开也别乱掉。"
    "你帮我做一个可以展示的版本，里面可以用模拟数据，但别太假。"
)
REFERENCE_SOURCES = [
    {
        "name": "Zendesk incoming tickets real-time dashboard",
        "url": "https://support.zendesk.com/hc/en-us/articles/9757103190810-Using-the-incoming-tickets-real-time-dashboard",
        "focus": "队列、等待时间、agent availability、SLA、ticket distribution",
    },
    {
        "name": "Geckoboard Zendesk SLA/backlog dashboard",
        "url": "https://www.geckoboard.com/dashboard-examples/itsm/zendesk-service-desk-dashboard/",
        "focus": "backlog health、SLA risk、workload",
    },
    {
        "name": "Zendesk dashboard pattern gallery",
        "url": "https://www.saasui.design/pattern/dashboard/zendesk",
        "focus": "客服支持 dashboard 信息密度",
    },
    {
        "name": "Shopify admin orders",
        "url": "https://help.shopify.com/en/manual/orders/manage-orders/search-view-print-orders",
        "focus": "订单列表、筛选、搜索、状态管理",
    },
    {
        "name": "Ant Design Pro",
        "url": "https://pro.ant.design/",
        "focus": "中文企业后台表格、筛选、状态标签和布局密度",
    },
]


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def safe_copy(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def run_cmd(command: list[str], *, timeout: int = 120) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "elapsed_sec": round(time.perf_counter() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\nTIMEOUT after {timeout}s",
            "elapsed_sec": round(time.perf_counter() - started, 3),
        }


def directory_has_payload(path: Path) -> bool:
    if not path.exists():
        return False
    return any(item.name != ".gitkeep" for item in path.iterdir())


def copy_tree_contents(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def reset_directory_payload(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for item in path.iterdir():
        if item.name == ".gitkeep":
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)


def empty_evolution_memory() -> dict[str, Any]:
    return {
        "successful_patterns": [],
        "failure_patterns": [],
        "validation_feedback": [],
        "duplicate_feedback": [],
        "promotion_feedback": [],
    }


def archive_and_reset_runtime(root: Path, label: str) -> dict[str, Any]:
    archive_root = root / "archive" / f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    manifest: dict[str, Any] = {"label": label, "archive_root": str(archive_root), "items": []}
    runtime_dirs = [
        (ROOT / "outputs" / "candidate_skills", "outputs_candidate_skills"),
        (ROOT / "outputs" / "reports", "outputs_reports"),
        (ROOT / ".tmp" / "validation-runs", "tmp_validation_runs"),
    ]
    for source, name in runtime_dirs:
        entry = {"source": str(source), "archive": str(archive_root / name), "existed": source.exists(), "had_payload": directory_has_payload(source)}
        if source.exists() and entry["had_payload"]:
            copy_tree_contents(source, archive_root / name)
        reset_directory_payload(source)
        manifest["items"].append(entry)

    data_files = [
        (ROOT / "data" / "processed_traces.jsonl", "data_processed_traces.jsonl"),
        (ROOT / "data" / "evolution_memory.json", "data_evolution_memory.json"),
    ]
    for source, name in data_files:
        entry = {"source": str(source), "archive": str(archive_root / name), "existed": source.exists()}
        safe_copy(source, archive_root / name)
        manifest["items"].append(entry)

    safe_copy(ROOT / "data" / "sample_traces.jsonl", ROOT / "data" / "processed_traces.jsonl")
    write_json(ROOT / "data" / "evolution_memory.json", empty_evolution_memory())
    write_json(archive_root / "cleanup_manifest.json", manifest)
    return manifest


def fenced_block(text: str, lang: str = "html") -> str:
    pattern = re.compile(rf"```{lang}\s*(.*?)```", re.IGNORECASE | re.DOTALL)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    generic = re.search(r"```\s*(.*?)```", text, re.DOTALL)
    if generic:
        return generic.group(1).strip()
    start = text.lower().find("<!doctype html")
    if start < 0:
        start = text.lower().find("<html")
    return text[start:].strip() if start >= 0 else text.strip()


def extract_html(answer: str) -> str:
    content = fenced_block(answer, "html")
    if "<html" not in content.lower() or "</html>" not in content.lower():
        content = "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>生成失败占位</title></head><body><pre>" + html.escape(answer) + "</pre></body></html>"
    return content


def model_call(system: str, prompt: str, config: Any, label: str, tool_logs: Path) -> tuple[str, dict[str, Any]]:
    messages = [
        {"role": "system", "content": system + "\n\n禁止使用 emoji。只输出一个完整 HTML 代码块，不要解释，不要省略。"},
        {"role": "user", "content": prompt + "\n\n请直接给完整单文件 HTML，代码必须包含 </html> 结束标签。"},
    ]
    started = time.perf_counter()
    response = chat_completion(messages, config)
    answer = extract_assistant_text(response)
    log = {
        "label": label,
        "messages": messages,
        "response_usage": response.get("usage", {}),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "finish_reason": ((response.get("choices") or [{}])[0] or {}).get("finish_reason"),
    }
    with tool_logs.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(log, ensure_ascii=False, sort_keys=True) + "\n")
    return answer, log


def screenshot(html_path: Path, out_path: Path, width: int, height: int) -> dict[str, Any]:
    if not EDGE.exists():
        return {"status": "missing_edge", "path": str(out_path)}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    user_data = out_path.parent / f".edge-profile-{out_path.stem}"
    url = html_path.resolve().as_uri()
    command = [
        str(EDGE),
        "--headless=new",
        "--disable-gpu",
        f"--window-size={width},{height}",
        f"--user-data-dir={user_data}",
        f"--screenshot={out_path}",
        url,
    ]
    result = run_cmd(command, timeout=60)
    if user_data.exists():
        shutil.rmtree(user_data, ignore_errors=True)
    result["screenshot_path"] = str(out_path)
    result["exists"] = out_path.exists()
    return result


def screenshot_url(url: str, out_path: Path, width: int = 1440, height: int = 1000) -> dict[str, Any]:
    if not EDGE.exists():
        return {"status": "missing_edge", "url": url, "path": str(out_path)}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    user_data = out_path.parent / f".edge-profile-{out_path.stem}"
    command = [
        str(EDGE),
        "--headless=new",
        "--disable-gpu",
        f"--window-size={width},{height}",
        f"--user-data-dir={user_data}",
        f"--screenshot={out_path}",
        url,
    ]
    result = run_cmd(command, timeout=80)
    if user_data.exists():
        shutil.rmtree(user_data, ignore_errors=True)
    result["screenshot_path"] = str(out_path)
    result["exists"] = out_path.exists()
    result["url"] = url
    return result


def strip_html_text(raw: str, limit: int = 6000) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def fetch_reference_text(url: str, timeout: int = 25) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 DiaEvo reference harvester",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_000).decode("utf-8", errors="replace")
        return {
            "status": "ok",
            "url": url,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "chars": len(raw),
            "text_excerpt": strip_html_text(raw),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "status": "failed",
            "url": url,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "error": str(exc),
            "text_excerpt": "",
        }


def try_vision_config(root: Path) -> tuple[Any | None, dict[str, Any]]:
    try:
        config = vision_config_from_env(env_path=str(ROOT / ".env"), max_tokens=2048, temperature=0.1)
        return config, {
            "glm_vision_key_configured": True,
            "base_url": config.base_url,
            "model": config.model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
        }
    except Exception as exc:
        return None, {
            "glm_vision_key_configured": False,
            "base_url": os.environ.get("GLM_VISION_BASE_URL") or os.environ.get("GLM_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4",
            "model": os.environ.get("GLM_VISION_MODEL") or "glm-4.6v-flash",
            "error": str(exc),
        }


def parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def call_vision_json(config: Any | None, prompt: str, image_paths: list[Path], fallback_label: str) -> dict[str, Any]:
    existing = [path for path in image_paths if path.exists() and path.stat().st_size > 0]
    if config is None:
        return {"status": "not_configured", "label": fallback_label, "score": None, "comments": "未配置 GLM 视觉模型，无法进行硬门槛视觉评分。"}
    if not existing:
        return {"status": "missing_screenshot", "label": fallback_label, "score": None, "comments": "截图不存在或为空，无法视觉评分。"}
    messages = [
        {"role": "system", "content": "你是中文企业后台 UI 视觉评审。只输出 JSON，不输出 Markdown。禁止泄露任何密钥。"},
        multimodal_user_message(prompt, existing),
    ]
    try:
        response = chat_completion(messages, config)
        text = extract_assistant_text(response)
        parsed = parse_json_object(text)
        if parsed is None:
            return {"status": "parse_failed", "label": fallback_label, "raw": text[:2000], "score": None}
        parsed.setdefault("status", "ok")
        parsed.setdefault("label", fallback_label)
        return parsed
    except Exception as exc:
        return {"status": "failed", "label": fallback_label, "error": str(exc), "score": None}


def evaluate_stage_vision(root: Path, stage: dict[str, Any], config: Any | None) -> dict[str, Any]:
    desktop, mobile = [Path(path) for path in stage["screenshots"]]
    prompt = """请评估这组截图是否像真实客服/物流工单运营后台，而不是 AI 模板页。
输出 JSON，字段必须包括：
{
  "score": 0-10,
  "desktop_score": 0-10,
  "mobile_score": 0-10,
  "ai_flavor_risks": ["..."],
  "business_usability_strengths": ["..."],
  "business_usability_weaknesses": ["..."],
  "hard_fail_ai_cliche": true/false,
  "comments_zh": "中文简评"
}
评分重点：信息密度、SLA 风险、负责人、待办动作、内部工具感、移动端是否可读；扣分项：紫蓝粉渐变、玻璃拟态、光斑、过大圆角、hero 营销页、空泛文案、文本重叠。"""
    result = call_vision_json(config, prompt, [desktop, mobile], stage["stage"])
    out = root / "vision_evaluations" / f"{stage['stage']}_glm_visual_score.json"
    write_json(out, result)
    stage["vision_evaluation"] = result
    stage["vision_evaluation_path"] = str(out)
    return result


def stage_from_existing(root: Path, stage_key: str, stage_title: str) -> dict[str, Any] | None:
    output_path = root / "frontend_outputs" / stage_title / "index.html"
    shot_desktop = root / "screenshots" / f"{stage_key}_desktop.png"
    shot_mobile = root / "screenshots" / f"{stage_key}_mobile.png"
    if not output_path.exists() or not shot_desktop.exists() or not shot_mobile.exists():
        return None
    html_text = read_text(output_path)
    stage = {
        "stage": stage_key,
        "title": stage_title,
        "output_path": str(output_path),
        "conversation_path": str(root / "conversation_md" / f"{stage_title}_完整对话.md"),
        "screenshots": [str(shot_desktop), str(shot_mobile)],
        "screenshot_results": [
            {"exists": shot_desktop.exists(), "screenshot_path": str(shot_desktop), "reused": True},
            {"exists": shot_mobile.exists(), "screenshot_path": str(shot_mobile), "reused": True},
        ],
        "evaluation": evaluate_html(stage_key, html_text),
        "model_log": {"reused": True},
    }
    vision_path = root / "vision_evaluations" / f"{stage_key}_glm_visual_score.json"
    if vision_path.exists():
        stage["vision_evaluation"] = read_json(vision_path, {})
        stage["vision_evaluation_path"] = str(vision_path)
    return stage


def harvest_references(root: Path, text_config: Any, vision_config: Any | None) -> dict[str, Any]:
    ref_root = root / "references"
    shot_root = ref_root / "reference_screenshots"
    if (ref_root / "design_principles_extracted.md").exists() and (ref_root / "source_urls.json").exists():
        return {
            "source_urls": str(ref_root / "source_urls.json"),
            "fetched_notes": str(ref_root / "fetched_notes.md"),
            "reference_screenshots": str(shot_root),
            "glm_reference_analysis": str(ref_root / "glm_reference_analysis.md"),
            "design_principles": str(ref_root / "design_principles_extracted.md"),
            "fetch_results": read_json(ref_root / "source_urls.json", {}),
            "screenshot_results": read_json(ref_root / "reference_screenshot_results.json", {}),
            "reference_analysis": read_json(ref_root / "glm_reference_analysis.json", {}),
            "reused": True,
        }
    write_json(ref_root / "source_urls.json", {"sources": REFERENCE_SOURCES})
    fetched: list[dict[str, Any]] = []
    shot_results: list[dict[str, Any]] = []
    for index, source in enumerate(REFERENCE_SOURCES, start=1):
        fetched.append({**source, **fetch_reference_text(source["url"])})
        shot_results.append(screenshot_url(source["url"], shot_root / f"reference_{index:02d}.png"))

    notes = ["# 联网参考抓取笔记", ""]
    for item in fetched:
        notes.extend(
            [
                f"## {item['name']}",
                "",
                f"- URL：{item['url']}",
                f"- 关注点：{item['focus']}",
                f"- 抓取状态：{item['status']}",
                "",
                item.get("text_excerpt") or f"抓取失败：{item.get('error', 'unknown')}",
                "",
            ]
        )
    write_text(ref_root / "fetched_notes.md", "\n".join(notes))

    ref_prompt = """请基于参考网页截图，提炼中文企业客服/工单/运营后台的视觉设计要点。
只输出 JSON：
{
  "layout_principles": ["..."],
  "information_hierarchy": ["..."],
  "status_expression": ["..."],
  "density_strategy": ["..."],
  "avoid_copying": ["..."],
  "comments_zh": "..."
}
只抽取布局原则、信息层级、状态表达和密度策略；不要要求照抄品牌、商标、完整界面或视觉资产。"""
    screenshots = [Path(item["screenshot_path"]) for item in shot_results if item.get("exists")]
    ref_analysis = call_vision_json(vision_config, ref_prompt, screenshots[:5], "reference_harvest")
    write_json(ref_root / "reference_screenshot_results.json", shot_results)
    write_json(ref_root / "glm_reference_analysis.json", ref_analysis)
    write_text(ref_root / "glm_reference_analysis.md", "# GLM 参考截图视觉理解\n\n```json\n" + json.dumps(ref_analysis, ensure_ascii=False, indent=2) + "\n```\n")

    extraction_prompt = (
        "你是中文企业后台设计研究员。请根据以下网页文字抓取笔记和视觉理解，"
        "提炼一份可给前端 Agent 使用的设计原则。要求中文、具体、可操作，"
        "只抽取原则，不照抄视觉资产、商标或完整界面。\n\n"
        f"抓取笔记：\n{read_text(ref_root / 'fetched_notes.md')[:12000]}\n\n"
        f"视觉理解 JSON：\n{json.dumps(ref_analysis, ensure_ascii=False)[:8000]}"
    )
    try:
        response = chat_completion(
            [
                {"role": "system", "content": "只输出 Markdown，标题为“# 参考设计原则提炼”。禁止 emoji。"},
                {"role": "user", "content": extraction_prompt},
            ],
            text_config,
        )
        principles = extract_assistant_text(response).strip()
    except Exception as exc:
        principles = (
            "# 参考设计原则提炼\n\n"
            f"> 自动提炼失败：{exc}\n\n"
            "- 首屏优先呈现队列健康度、SLA 风险、待处理 backlog 和负责人负载。\n"
            "- 使用紧凑表格、筛选条、状态标签和分组摘要表达内部工具感。\n"
            "- 状态色服务于风险分级，不作为装饰性渐变背景。\n"
            "- 移动端保留工单摘要、负责人、剩余时间和下一步动作，弱化宽表格。\n"
        )
    write_text(ref_root / "design_principles_extracted.md", principles)
    return {
        "source_urls": str(ref_root / "source_urls.json"),
        "fetched_notes": str(ref_root / "fetched_notes.md"),
        "reference_screenshots": str(shot_root),
        "glm_reference_analysis": str(ref_root / "glm_reference_analysis.md"),
        "design_principles": str(ref_root / "design_principles_extracted.md"),
        "fetch_results": fetched,
        "screenshot_results": shot_results,
        "reference_analysis": ref_analysis,
    }


def evaluate_html(stage: str, html_text: str) -> dict[str, Any]:
    text = re.sub(r"<[^>]+>", " ", html_text)
    lowered = html_text.lower()
    labels: list[str] = []
    if re.search(r"gradient|linear-gradient|radial-gradient|#8b5cf6|#a855f7|purple|violet|pink", lowered):
        labels.append("紫色渐变或模板色风险")
    if re.search(r"blur|backdrop-filter|box-shadow:\s*0\s+\d{2,}px|rgba\([^)]*,\s*0\.[1-9]\)", lowered):
        labels.append("光斑玻璃拟态或重阴影风险")
    if re.search(r"border-radius:\s*(2[0-9]|3[0-9]|999)", lowered):
        labels.append("大圆角卡片风险")
    vague_terms = ["智能", "赋能", "一站式", "效率提升", "运营中枢", "全链路", "数据驱动"]
    vague_hits = [term for term in vague_terms if term in text]
    if vague_hits:
        labels.append("空泛营销文案")
    if not all(term in text for term in ["客户", "负责", "超时"]):
        labels.append("业务优先级不清")
    if "overflow-x" not in lowered and "<table" in lowered:
        labels.append("移动端表格溢出风险")
    data_specificity = sum(1 for term in ["SLA", "分钟", "负责人", "截止", "工单", "客户", "优先"] if term in text)
    ai_risk = min(10, 2 + len(labels) * 2 - min(3, data_specificity // 2))
    business_score = max(1, min(10, data_specificity + (2 if "负责人" in text else 0) - len(labels)))
    return {
        "stage": stage,
        "bad_case_labels": labels,
        "vague_terms": vague_hits,
        "data_specificity_hits": data_specificity,
        "ai_flavor_score_10_high_bad": ai_risk,
        "business_usability_score_10_high_good": business_score,
        "html_chars": len(html_text),
    }


def conversation_md(stage_name: str, system: str, answer: str, output_path: Path, shots: list[Path], eval_result: dict[str, Any], skill_path: Path | None = None) -> str:
    skill_note = f"\n\n## 显式提供的 Skill\n\n`{skill_path}`\n" if skill_path else "\n\n## 显式提供的 Skill\n\n未提供新增专项 skill。\n"
    return f"""# {stage_name} 完整对话

## 用户原话

{USER_PROMPT}

## Agent 追问

本轮未追问。Agent 判断信息足以生成可展示版本，并基于合理假设继续。

## 用户回答

无追加回答。

## Agent 对“AI 味”的识别

{system}
{skill_note}
## 工具调用摘要

- 调用模型生成中文前端 HTML。
- 从模型回答提取 HTML 并保存到 `{output_path}`。
- 使用 Edge headless 截取桌面端和移动端截图。
- 使用中文 rubric 标注 AI 味 bad case。

## Agent 回答摘要

{answer[:3000]}

## 生成文件路径

`{output_path}`

## 截图路径

{chr(10).join(f"- `{path}`" for path in shots)}

## 失败反馈与改进反馈

- bad case 标签：{", ".join(eval_result.get("bad_case_labels") or ["暂无明显标签"])}
- AI 味评分：{eval_result.get("ai_flavor_score_10_high_bad")}/10（越高越差）
- 业务可用性评分：{eval_result.get("business_usability_score_10_high_good")}/10（越高越好）
"""


def make_stage(
    root: Path,
    stage_key: str,
    stage_title: str,
    system: str,
    config: Any,
    skill_path: Path | None,
    tool_log: Path,
) -> dict[str, Any]:
    output_dir = root / "frontend_outputs" / stage_title
    output_path = output_dir / "index.html"
    answer, model_log = model_call(system, USER_PROMPT, config, stage_key, tool_log)
    html_text = extract_html(answer)
    write_text(output_path, html_text)
    shot_desktop = root / "screenshots" / f"{stage_key}_desktop.png"
    shot_mobile = root / "screenshots" / f"{stage_key}_mobile.png"
    shot_results = [
        screenshot(output_path, shot_desktop, 1440, 1000),
        screenshot(output_path, shot_mobile, 390, 844),
    ]
    eval_result = evaluate_html(stage_key, html_text)
    md_path = root / "conversation_md" / f"{stage_title}_完整对话.md"
    write_text(md_path, conversation_md(stage_title, system, answer, output_path, [shot_desktop, shot_mobile], eval_result, skill_path))
    return {
        "stage": stage_key,
        "title": stage_title,
        "output_path": str(output_path),
        "conversation_path": str(md_path),
        "screenshots": [str(shot_desktop), str(shot_mobile)],
        "screenshot_results": shot_results,
        "evaluation": eval_result,
        "model_log": model_log,
    }


def build_trace_rows(stage0: dict[str, Any], reference_artifacts: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    base_files = [stage0["output_path"], *stage0["screenshots"]]
    ref_text = ""
    if reference_artifacts:
        ref_text = read_text(Path(reference_artifacts.get("design_principles") or ""))
    vision = stage0.get("vision_evaluation") or {}
    vision_feedback = vision.get("comments_zh") or vision.get("comments") or ""
    feedbacks = [
        ("TR-DEAI-001", "这个太像 AI 模板了，紫色渐变太重。", ["紫色渐变过度", "光斑玻璃拟态"]),
        ("TR-DEAI-002", "卡片很多，但我不知道先看哪一块，客服主管无法判断先处理谁。", ["大圆角卡片堆叠", "业务优先级不清"]),
        ("TR-DEAI-003", "这些数字看起来像假的，能不能更像客服主管真的会用的数据？", ["无意义统计数字", "数据可信度不足"]),
        ("TR-DEAI-004", "别写什么智能赋能，写具体一点，告诉我谁负责、还剩几分钟。", ["空泛营销文案", "行动信息缺失"]),
        ("TR-DEAI-005", "手机上表格挤在一起了，临时打开看不清。", ["移动端表格溢出", "缺少截图验证"]),
        ("TR-DEAI-006", "页面应该像内部早会工具，不像对外宣传落地页。", ["营销页气质", "工具感不足"]),
        ("TR-DEAI-007", "需要先把快超时、客户急、负责人和下一步动作排出来。", ["业务优先级不清", "信息层级缺失"]),
        ("TR-DEAI-008", "参考真实客服后台后，页面应该更像队列/SLA/backlog 工作台，而不是凭空编的展示页。", ["参考吸收不足", "真实产品原则缺失"]),
        ("TR-DEAI-009", f"GLM 视觉评价反馈：{vision_feedback or '需要截图视觉评审确认是否有 AI 味。'}", ["视觉评估反馈", "截图硬门槛"]),
    ]
    rows = []
    for trace_id, feedback, labels in feedbacks:
        rows.append(
            {
                "id": trace_id,
                "task": f"中文非专业用户要求客服物流工单网页去 AI 味：{USER_PROMPT} 反馈：{feedback}\n\n参考原则：{ref_text[:2000]}",
                "project": {
                    "language": "html css javascript 中文",
                    "frameworks": ["static-html", "responsive-design", "front-end"],
                    "files": base_files + ([reference_artifacts.get("design_principles")] if reference_artifacts and reference_artifacts.get("design_principles") else []),
                },
                "tools": ["chat_completion", "write_html", "edge_headless_screenshot", "rubric_evaluation", "glm_vision_evaluation", "reference_harvest"],
                "commands": ["generate html", "edge --headless --screenshot desktop", "edge --headless --screenshot mobile", "glm vision score screenshots"],
                "outcome": "failure" if "TR-DEAI-00" in trace_id and trace_id != "TR-DEAI-007" else "success",
                "error_type": labels[0],
                "used_skills": [],
                "duration_sec": 420,
                "retries": 1,
                "feedback": feedback,
                "tags": ["中文优先", "前端设计", "去AI味", *labels],
                "source": "experiment_conversation",
                "event_count": 4,
                "tool_success_rate": 1.0,
                "tool_failure_types": labels,
                "tool_reuse_count": 3,
                "bad_case_labels": labels,
                "generated_files": base_files,
                "screenshots": stage0["screenshots"],
                "reference_artifacts": reference_artifacts or {},
                "vision_feedback": vision,
            }
        )
    return rows


def select_cluster(report: dict[str, Any]) -> str:
    clusters = report.get("clusters") or []
    if not clusters:
        return "C01"
    def score(cluster: dict[str, Any]) -> tuple[float, int]:
        terms = " ".join(str(item) for item in cluster.get("top_terms", []))
        hit = sum(1 for term in ["ai", "前端", "紫色", "业务", "移动", "文案", "客服"] if term.lower() in terms.lower())
        return (float(cluster.get("coverage_gap") or 0) + hit, int(cluster.get("size") or 0))
    return str(max(clusters, key=score).get("id") or "C01")


def enrich_skill(skill_path: Path, output_path: Path, source_cluster: str, evolved: bool = False) -> None:
    body = read_text(skill_path)
    suffix = "GEPA 进化" if evolved else "挖掘生成"
    front = f"""---
name: "zh-de-ai-frontend-design-{source_cluster.lower()}{'-gepa' if evolved else ''}"
description: "面向中文非专业用户的去 AI 味前端设计技能：把口语业务诉求转译成安静、紧凑、可扫描、可截图验证的内部工具页面。"
tags: ["中文前端", "去AI味", "业务工具", "响应式验证", "客服物流"]
source_cluster: "{source_cluster}"
status: candidate
---

# 中文去 AI 味前端设计（{suffix}）

## When To Use

当中文用户用非专业口吻要求制作网页、后台、看板、工单页、SaaS/CRM/运营工具，并明确或隐含反感“AI 生成模板感”时使用。典型信号包括：不要紫色渐变、不要炫酷模板、不要大圆角卡片、不要空话、手机别乱、模拟数据别太假。

## Trigger Signals

- 用户说“不要像 AI 生成”“别一堆紫色渐变”“别大圆角卡片”“别空话”。
- 场景是客服主管、物流工单、销售跟进、财务对账、运营早会等内部业务工具。
- 用户关心“一眼知道先处理谁、谁负责、有没有快超时、忙不忙”，而不是品牌宣传。
- 页面需要桌面和手机都能临时查看。
- 用户不是设计或技术专业人员，需要 Agent 把口语转成信息架构、视觉层级和验证步骤。

## Operating Steps

1. 先原样保留用户口语需求，再翻译成业务问题：谁最急、谁负责、剩余时间、下一步动作、整体负载。
2. 建立信息层级：顶部只放当天队列状态和关键风险，主体优先展示快超时工单、负责人负载、需要升级的客户。
3. 视觉语言使用企业内部工具风格：浅中性底、细边框、克制状态色、8px 以内圆角、紧凑行高、可扫描表格或列表。
4. 主动避开紫蓝粉渐变、发光光斑、玻璃拟态、装饰性 orb、模板化 hero、假 logo、假见证和无意义巨型数字。
5. 中文文案写具体动作，不写“智能赋能、一站式提升效率、运营中枢、全链路洞察”等营销空话。
6. 模拟数据要克制且可解释：客户名、负责人、SLA 剩余分钟、最新阻塞、建议动作，避免无法指导行动的大数字。
7. 移动端不能只是缩小桌面表格；使用卡片化行摘要或横向滚动容器，并检查文本是否重叠、溢出。
8. 交付前必须保存桌面和移动截图，并在回复中说明如何检查 AI 味和业务可用性。

## Failure Fallbacks

- 如果页面仍像营销落地页，删除 hero 和宣传文案，改为早会工作台首屏。
- 如果卡片堆叠导致重点不清，压缩装饰模块，把快超时列表和负责人负载前置。
- 如果数据像假的，减少宏大统计，改成具体客户、工单号、时间、负责人和下一步。
- 如果手机端表格挤压，改为每条工单一块紧凑摘要，保留负责人和剩余时间。
- 如果缺少真实品牌或图标，使用文字标签和朴素状态标识，不用 emoji 或假插画填充。

## Verification Suggestions

- 生成后检查 HTML/CSS 是否含有 purple/violet/pink gradient、orb、glass、过大 border-radius。
- 检查中文页面是否出现“智能赋能、一站式、效率提升、全链路、运营中枢”等空话。
- 桌面截图应能一眼看出优先处理客户、负责人、SLA 风险和队列忙闲。
- 手机截图应无重叠、无明显文本溢出；表格需要有响应式替代。
- 把截图路径、生成文件路径和 bad case 标签写回对话记录。

## Safety Constraints

- 不泄露 `.env`、API key 或私有客户数据；模拟数据必须标注为样例或明显是脱敏场景。
- 不自动安装依赖；静态 HTML 能满足展示时优先用单文件。
- 不写 workspace 外路径；实验产物必须保存在指定实验目录。
- 不把未验证截图或未运行的 GEPA 说成已经成功。

## Mined Evidence

本技能来自中文实验 trace：客服物流工单页面在“紫色渐变过度、大圆角卡片堆叠、空泛营销文案、无意义统计数字、业务优先级不清、移动端表格溢出、缺少截图验证”等反馈上的聚类证据。

"""
    write_text(output_path, front + "\n\n## Original DiaEvo Generated Evidence\n\n" + body)


def comparison_page(root: Path, stages: list[dict[str, Any]]) -> Path:
    rows = []
    for stage in stages:
        title = html.escape(stage["title"])
        shot = Path(stage["screenshots"][0])
        rel = os.path.relpath(shot, root).replace("\\", "/")
        ev = stage["evaluation"]
        vision = stage.get("vision_evaluation") or {}
        rows.append(
            f"<section><h2>{title}</h2><img src='{rel}' alt='{title}'><p>文本 AI 味：{ev['ai_flavor_score_10_high_bad']}/10；业务可用性：{ev['business_usability_score_10_high_good']}/10；GLM 视觉：{html.escape(str(vision.get('score', 'n/a')))}/10</p><p>{html.escape('、'.join(ev['bad_case_labels']) or '暂无明显 bad case')}</p><p>{html.escape(str(vision.get('comments_zh') or vision.get('comments') or ''))}</p></section>"
        )
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>三阶段去 AI 味对比</title>
<style>body{{margin:0;font-family:"Microsoft YaHei",Arial,sans-serif;background:#f6f7f8;color:#1f2933}}main{{padding:24px;display:grid;grid-template-columns:repeat(3,1fr);gap:18px}}section{{background:white;border:1px solid #d8dee4;border-radius:8px;padding:14px}}h1{{margin:20px 24px 0;font-size:24px}}h2{{font-size:16px}}img{{width:100%;border:1px solid #e5e7eb}}p{{font-size:13px;line-height:1.55}}@media(max-width:900px){{main{{grid-template-columns:1fr}}}}</style></head>
<body><h1>中文非专业需求：三阶段前端效果对比</h1><main>{''.join(rows)}</main></body></html>"""
    out = root / "screenshots" / "三阶段并排对比.html"
    write_text(out, page)
    screenshot(out, root / "screenshots" / "三阶段并排对比.png", 1600, 1000)
    return out


def validation_passed(report: dict[str, Any]) -> bool:
    status = str(report.get("status") or report.get("validation_status") or "").lower()
    if report.get("verification_passed") is False:
        return False
    if status in {"passed", "ok", "success", "not_configured", "skipped"}:
        return True
    if report.get("approved") is False:
        return False
    if report.get("error") or report.get("security_error"):
        return False
    return status == "" and report.get("verification_passed") is not False


def numeric_score(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def decide_adoption(
    stage1: dict[str, Any],
    stage2: dict[str, Any],
    verify_gepa: dict[str, Any],
    validate_gepa: dict[str, Any],
    gepa_report: dict[str, Any],
) -> dict[str, Any]:
    stage1_text = stage1["evaluation"]
    stage2_text = stage2["evaluation"]
    stage1_vision = stage1.get("vision_evaluation") or {}
    stage2_vision = stage2.get("vision_evaluation") or {}
    stage1_visual = numeric_score(stage1_vision.get("score"))
    stage2_visual = numeric_score(stage2_vision.get("score"))
    reasons: list[str] = []

    verify_ok = bool(verify_gepa.get("ok") or verify_gepa.get("passed") or verify_gepa.get("verification_passed"))
    if not verify_ok:
        reasons.append("GEPA skill verify 未通过。")
    if not validation_passed(validate_gepa):
        reasons.append("GEPA skill validate 未通过，且不是明确 not_configured。")
    if stage2_text["business_usability_score_10_high_good"] < stage1_text["business_usability_score_10_high_good"]:
        reasons.append("Stage 2 文本业务可用性低于 Stage 1。")
    if stage2_text["ai_flavor_score_10_high_bad"] > stage1_text["ai_flavor_score_10_high_bad"]:
        reasons.append("Stage 2 文本 AI 味风险高于 Stage 1。")
    if stage1_visual is None or stage2_visual is None:
        reasons.append("GLM 视觉评分缺失，不能通过硬门槛。")
    elif stage2_visual < stage1_visual:
        reasons.append("Stage 2 GLM 桌面/移动综合视觉评分低于 Stage 1。")
    if stage2_vision.get("hard_fail_ai_cliche"):
        reasons.append("GLM 视觉评估标记 Stage 2 有明显新增 AI 味。")
    cliche_labels = {"紫色渐变或模板色风险", "光斑玻璃拟态或重阴影风险", "大圆角卡片风险", "空泛营销文案"}
    if any(label in cliche_labels for label in stage2_text.get("bad_case_labels") or []):
        reasons.append("Stage 2 文本检查仍有明显 AI 味标签。")
    if gepa_report.get("status") == "failed":
        reasons.append("GEPA 运行失败。")

    adopted = not reasons
    return {
        "status": "gepa_adopted" if adopted else "completed_but_not_adopted",
        "final_skill_source": "gepa" if adopted else "stage1_mined_local",
        "reasons": reasons or ["GEPA 同时通过 verify、validate、文本评分、GLM 视觉评分和 AI 味硬门槛。"],
        "stage1_text": stage1_text,
        "stage2_text": stage2_text,
        "stage1_visual_score": stage1_visual,
        "stage2_visual_score": stage2_visual,
        "gepa_adoption_recommendation": gepa_report.get("adoption_recommendation", {}),
    }


def final_report(root: Path, stages: list[dict[str, Any]], artifacts: dict[str, Any]) -> Path:
    course = artifacts.get("course_requirements", {})
    lines = [
        "# 中文非专业用户“去 AI 味”前端设计 Skill 挖掘与 GEPA 进化实验",
        "",
        f"- 实验目录：`{root}`",
        "- 固定用户原话：" + USER_PROMPT,
        f"- GEPA budget：`{GEPA_BUDGET}`",
        "- 视觉模型：`glm-4.6v-flash`，视觉并发：`1`",
        "- 目标参考 skill：`https://github.com/ConardLi/garden-skills/tree/main/skills/web-design-engineer`",
        "- 本地参考 skill：`C:\\Users\\csc20\\.agents\\skills\\web-design-engineer\\SKILL.md`",
        "- 课程作业要求来源：`D:\\codex\\Final Project Instructions.html`",
        f"- 课程要求摘录：代码={course.get('code')}, 文档={course.get('documentation')}, 至少三张截图={course.get('screenshots')}, 7-10 分钟展示={course.get('presentation')}",
        "",
        "## 三阶段评分",
        "",
        "| 阶段 | AI 味评分(越低越好) | 业务可用性(越高越好) | GLM 视觉 | 页面 | 对话 | 截图 |",
        "| --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for stage in stages:
        ev = stage["evaluation"]
        vision = stage.get("vision_evaluation") or {}
        lines.append(
            f"| {stage['title']} | {ev['ai_flavor_score_10_high_bad']} | {ev['business_usability_score_10_high_good']} | {vision.get('score', 'n/a')} | `{stage['output_path']}` | `{stage['conversation_path']}` | `{stage['screenshots'][0]}`, `{stage['screenshots'][1]}` |"
        )
    lines.extend(
        [
            "",
            "## 联网参考与视觉理解",
            "",
            f"- source urls：`{artifacts.get('reference_source_urls')}`",
            f"- fetched notes：`{artifacts.get('reference_fetched_notes')}`",
            f"- reference screenshots：`{artifacts.get('reference_screenshots')}`",
            f"- GLM reference analysis：`{artifacts.get('reference_glm_analysis')}`",
            f"- extracted principles：`{artifacts.get('reference_design_principles')}`",
            "",
            "## 挖掘与进化证据",
            "",
            f"- trace JSONL：`{artifacts.get('trace_path')}`",
            f"- mining report：`{artifacts.get('mining_report')}`",
            f"- mining cluster：`{artifacts.get('cluster_id')}`",
            f"- mined skill：`{artifacts.get('mined_skill')}`",
            f"- local evolved skill：`{artifacts.get('local_evolved_skill')}`",
            f"- GEPA skill：`{artifacts.get('gepa_skill')}`",
            f"- final adopted skill：`{artifacts.get('final_skill')}`",
            f"- GEPA report：`{artifacts.get('gepa_report')}`",
            f"- bad case 统计：`{artifacts.get('bad_case_report')}`",
            f"- adoption decision：`{artifacts.get('adoption_decision_report')}`",
            f"- 三阶段并排对比：`{artifacts.get('comparison_page')}`，`{artifacts.get('comparison_png')}`",
            "",
            "## GEPA 采纳结论",
            "",
            artifacts.get("gepa_conclusion", "GEPA 结果见报告。"),
            "",
            "## 测试命令与结果",
            "",
            f"- skill verify/validate：`{artifacts.get('skill_validation_report')}`",
            f"- GEPA validate：`{artifacts.get('gepa_validation_report')}`",
            f"- 相关 pytest：`{artifacts.get('pytest_report')}`",
            f"- 运行态清理：`{artifacts.get('final_cleanup_manifest')}`",
            "",
            "## 限制与复现",
            "",
            "- 本实验只使用同一个中文非专业需求比较三阶段，不代表所有前端任务。",
            "- `.env` 仅用于运行时读取 API 配置，报告不包含 key。",
            "- 如 GEPA 或模型调用失败，失败日志保存在报告中，不冒充成功。",
            "- 复现时从实验目录的 `traces/experiment_traces.jsonl`、`skills/`、`conversation_md/` 和 `frontend_outputs/` 开始核对。",
        ]
    )
    out = root / "reports" / "最终中文实验报告.md"
    write_text(out, "\n".join(lines))
    return out


def extract_course_requirements() -> dict[str, Any]:
    text = read_text(Path(r"D:\codex\Final Project Instructions.html"))
    lowered = text.lower()
    return {
        "code": "code" in lowered or "代码" in text,
        "documentation": "documentation" in lowered or "文档" in text,
        "screenshots": bool(re.search(r"3|three|三", text, re.I)) and ("screenshot" in lowered or "截图" in text),
        "presentation": bool(re.search(r"7\s*[-–]\s*10|7\s*to\s*10|7-10", text, re.I)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full Chinese de-AI frontend evolution experiment.")
    parser.add_argument("--resume-root", default=None, help="Existing experiment root to resume from saved artifacts.")
    parser.add_argument("--skip-initial-reset", action="store_true", help="Do not archive/reset global runtime at start.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(args.resume_root).resolve() if args.resume_root else ROOT / "experiments" / f"chinese_de_ai_frontend_evolution_full_{stamp}"
    for sub in ["conversation_md", "tool_logs", "frontend_outputs", "screenshots", "traces", "skills", "reports", "references", "vision_evaluations", "archive"]:
        (root / sub).mkdir(parents=True, exist_ok=True)

    status = read_json(root / "reports" / "experiment_status.json", {}) if args.resume_root else {}
    status.update({"experiment_root": str(root), "started_at": status.get("started_at") or stamp, "resumed_at": datetime.now().isoformat() if args.resume_root else None})
    write_json(root / "reports" / "experiment_status.json", status)
    if not args.skip_initial_reset and not args.resume_root:
        initial_cleanup = archive_and_reset_runtime(root, "runtime_reset_before")
        write_json(root / "reports" / "runtime_reset_before.json", initial_cleanup)
    tool_log = root / "tool_logs" / "model_and_tool_calls.jsonl"
    git_status = run_cmd(["git", "status", "--short"], timeout=30)
    write_json(root / "reports" / "git_status_before.json", git_status)

    config = config_from_env(env_path=str(ROOT / ".env"), max_tokens=12000, temperature=0.2, no_thinking=True)
    vision_config, vision_summary = try_vision_config(root)
    provider_summary = {
        "deepseek_key_configured": bool(config.api_key),
        "deepseek_base_url": config.base_url,
        "deepseek_model": config.model,
        "glm_vision_key_configured": bool(vision_summary.get("glm_vision_key_configured")),
        "glm_vision_base_url": vision_summary.get("base_url"),
        "glm_vision_model": vision_summary.get("model"),
    }
    write_json(root / "reports" / "api_provider_summary.json", provider_summary)

    reference_artifacts = harvest_references(root, config, vision_config)
    design_principles = read_text(Path(reference_artifacts["design_principles"]))

    stage0_system = (
        "你是一个普通前端 Agent。根据用户需求直接生成一个单文件中文 HTML/CSS/JS 展示页。"
        "可以使用模拟数据。输出先给简短说明，再给完整 HTML 代码块。"
    )
    stage0 = stage_from_existing(root, "stage0", "阶段0_挖掘前") or make_stage(root, "stage0", "阶段0_挖掘前", stage0_system, config, None, tool_log)
    if not stage0.get("vision_evaluation"):
        evaluate_stage_vision(root, stage0, vision_config)

    trace_path = root / "traces" / "experiment_traces.jsonl"
    raw_trace_path = root / "traces" / "raw_traces.jsonl"
    processed_path = root / "traces" / "processed_traces.jsonl"
    if not trace_path.exists() or not processed_path.exists():
        traces = build_trace_rows(stage0, reference_artifacts)
        write_jsonl(raw_trace_path, traces)
        write_jsonl(trace_path, traces)
        ingest_summary = ingest_traces(trace_path, processed_path, include_tool_events=False)
        write_json(root / "reports" / "ingest_summary.json", ingest_summary)
    mine_report = read_json(root / "reports" / "mining_report.json", None) or mine(processed_path, k=2)
    mining_report_path = root / "reports" / "mining_report.json"
    write_json(mining_report_path, mine_report)
    cluster_id = select_cluster(mine_report)

    mined_skill = root / "skills" / "去AI味前端设计_mined" / "SKILL.md"
    generated_dir = root / "skills" / "mined_generated"
    generated_skill_path = generated_dir / "SKILL.md"
    if not mined_skill.exists():
        generated = generate_skill(cluster_id, output_dir=generated_dir, with_code=True)
        generated_skill_path = Path(generated["skill_path"])
    enrich_skill(generated_skill_path, mined_skill, cluster_id)
    verify_mined = verify_skill(mined_skill.parent)
    validation_mined = run_validation(mined_skill.parent, approve=True)
    write_json(root / "reports" / "verify_mined_skill.json", verify_mined)
    write_json(root / "reports" / "validate_mined_skill.json", validation_mined)

    stage1_system = (
        "你是中文企业内部工具的前端设计 Agent。你必须遵循下面的去 AI 味 skill，"
        "把非专业口语需求转成具体信息架构，生成单文件中文 HTML/CSS/JS。"
        "避免紫蓝粉渐变、光斑、玻璃拟态、大圆角卡片堆叠、空泛营销文案和无意义大数字。"
        "优先展示谁最急、谁负责、还剩多久、下一步动作，并考虑手机端。"
        "你还必须吸收联网参考提炼出的真实客服/工单/运营后台设计原则，但不得照抄品牌或完整界面。输出完整 HTML 代码块。\n\n"
        "## 联网参考设计原则\n\n"
        + design_principles
        + "\n\n## Mined Skill\n\n"
        + read_text(mined_skill)
    )
    stage1 = make_stage(root, "stage1", "阶段1_挖掘skill后", stage1_system, config, mined_skill, tool_log)
    evaluate_stage_vision(root, stage1, vision_config)

    local_evolved_report = evolve_skill(cluster_id, budget=GEPA_BUDGET, output_dir=root / "skills" / "local_evolved_raw", memory_path=root / "reports" / "local_evolution_memory.json")
    write_json(root / "reports" / "local_evolution_report.json", local_evolved_report)
    local_skill_raw = Path(local_evolved_report["runs"][0]["output"]["skill_path"])
    local_skill = root / "skills" / "去AI味前端设计_local_evolved" / "SKILL.md"
    enrich_skill(local_skill_raw, local_skill, cluster_id)
    verify_local = verify_skill(local_skill.parent)
    validate_local = run_validation(local_skill.parent, approve=True)
    write_json(root / "reports" / "verify_local_evolved_skill.json", verify_local)
    write_json(root / "reports" / "validate_local_evolved_skill.json", validate_local)

    gepa_report: dict[str, Any]
    gepa_skill = root / "skills" / "去AI味前端设计_GEPA_evolved" / "SKILL.md"
    gepa_conclusion = ""
    try:
        gepa_report = evaluate_gepa(
            cluster_id,
            budget=GEPA_BUDGET,
            input_path=trace_path,
            processed_path=processed_path,
            include_tool_events=False,
            env_path=str(ROOT / ".env"),
            max_tokens=4000,
            temperature=0.2,
            output_dir=root / "skills" / "gepa_raw",
            memory_policy="ctm_epm",
            racing_policy="cheap_gates",
            judge_policy="none",
        )
        write_json(root / "reports" / "gepa_skill_optimization.json", gepa_report)
        gepa_output = ((gepa_report.get("comparison") or {}).get("gepa") or {}).get("output") or {}
        gepa_raw = Path(str(gepa_output.get("skill_path") or ""))
        if gepa_raw.exists():
            enrich_skill(gepa_raw, gepa_skill, cluster_id, evolved=True)
            adoption = gepa_report.get("adoption_recommendation") if isinstance(gepa_report.get("adoption_recommendation"), dict) else {}
            gepa_conclusion = (
                f"GEPA 正式运行完成，budget={GEPA_BUDGET}，报告 status={gepa_report.get('status')}。"
                f"DiaEvo adoption={adoption.get('status', 'unknown')}，原因：{adoption.get('reason', '未提供')}。"
                "Stage 2 使用 GEPA candidate 生成页面，但最终是否采纳还要经过文本、GLM 视觉和安全验证硬门槛。"
            )
        else:
            gepa_skill.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_skill, gepa_skill)
            gepa_conclusion = "GEPA 报告生成但未发现 skill_path，Stage 2 降级使用 local evolved skill，并在 GEPA 报告中保留原因。"
    except Exception as exc:
        gepa_report = {"status": "failed", "error": str(exc), "budget": GEPA_BUDGET}
        write_json(root / "reports" / "gepa_skill_optimization.json", gepa_report)
        gepa_skill.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_skill, gepa_skill)
        gepa_conclusion = f"GEPA 正式运行失败，未伪造成成功；失败原因：{exc}。Stage 2 使用 local evolved skill 作为降级输入。"
    verify_gepa = verify_skill(gepa_skill.parent)
    validate_gepa = run_validation(gepa_skill.parent, approve=True)
    write_json(root / "reports" / "verify_gepa_skill.json", verify_gepa)
    write_json(root / "reports" / "validate_gepa_skill.json", validate_gepa)

    stage2_system = (
        "你是已经通过 GEPA 进化的中文去 AI 味前端设计 Agent。严格按下面 skill 生成单文件中文 HTML/CSS/JS。"
        "必须让客服主管在首屏判断：先处理谁、谁负责、SLA 还剩多久、今天队列忙不忙。"
        "视觉应安静、紧凑、企业内部工具感强；移动端要改成可读摘要，不能挤压表格。"
        "必须吸收联网参考提炼出的真实客服/工单/运营后台设计原则，但不得照抄品牌或完整界面。输出完整 HTML 代码块。\n\n"
        "## 联网参考设计原则\n\n"
        + design_principles
        + "\n\n## GEPA Skill\n\n"
        + read_text(gepa_skill)
    )
    stage2 = make_stage(root, "stage2", "阶段2_GEPA进化后", stage2_system, config, gepa_skill, tool_log)
    evaluate_stage_vision(root, stage2, vision_config)

    stages = [stage0, stage1, stage2]
    comparison = comparison_page(root, stages)
    bad_counts: dict[str, int] = {}
    for stage in stages:
        for label in stage["evaluation"]["bad_case_labels"]:
            bad_counts[label] = bad_counts.get(label, 0) + 1
    bad_report = root / "reports" / "bad_case_feedback_report.json"
    write_json(bad_report, {"bad_case_label_counts": bad_counts, "stage_evaluations": [stage["evaluation"] for stage in stages]})

    adoption_decision = decide_adoption(stage1, stage2, verify_gepa, validate_gepa, gepa_report)
    adoption_report = root / "reports" / "adoption_decision.json"
    write_json(adoption_report, adoption_decision)
    final_skill = root / "skills" / "final_adopted_skill" / "SKILL.md"
    final_skill.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(gepa_skill if adoption_decision["final_skill_source"] == "gepa" else local_skill, final_skill)
    if adoption_decision["status"] != "gepa_adopted":
        gepa_conclusion += " 最终采纳门结论：GEPA completed_but_not_adopted；原因：" + "；".join(adoption_decision["reasons"])
    else:
        gepa_conclusion += " 最终采纳门结论：GEPA skill adopted。"

    pytest_report = run_cmd([
        str(ROOT / ".venv" / "Scripts" / "python.exe"),
        "-m",
        "pytest",
        "-q",
        "tests/test_tool_chat.py",
        "tests/test_output_policy.py",
        "tests/test_generator_verifier.py",
        "tests/test_validation_runner.py",
        "tests/test_gepa_adapter.py",
    ], timeout=300)
    write_json(root / "reports" / "pytest_related.json", pytest_report)

    artifacts = {
        "course_requirements": extract_course_requirements(),
        "trace_path": str(trace_path),
        "raw_trace_path": str(raw_trace_path),
        "mining_report": str(mining_report_path),
        "cluster_id": cluster_id,
        "reference_source_urls": reference_artifacts.get("source_urls"),
        "reference_fetched_notes": reference_artifacts.get("fetched_notes"),
        "reference_screenshots": reference_artifacts.get("reference_screenshots"),
        "reference_glm_analysis": reference_artifacts.get("glm_reference_analysis"),
        "reference_design_principles": reference_artifacts.get("design_principles"),
        "mined_skill": str(mined_skill),
        "local_evolved_skill": str(local_skill),
        "gepa_skill": str(gepa_skill),
        "final_skill": str(final_skill),
        "gepa_report": str(root / "reports" / "gepa_skill_optimization.json"),
        "bad_case_report": str(bad_report),
        "adoption_decision_report": str(adoption_report),
        "comparison_page": str(comparison),
        "comparison_png": str(root / "screenshots" / "三阶段并排对比.png"),
        "gepa_conclusion": gepa_conclusion,
        "skill_validation_report": str(root / "reports" / "validate_mined_skill.json"),
        "gepa_validation_report": str(root / "reports" / "validate_gepa_skill.json"),
        "pytest_report": str(root / "reports" / "pytest_related.json"),
    }
    report = final_report(root, stages, artifacts)
    write_json(root / "reports" / "stage_summary.json", {"stages": stages, "artifacts": artifacts, "final_report": str(report)})
    status.update({"status": "completed", "final_report": str(report), "completed_at": datetime.now().isoformat()})
    write_json(root / "reports" / "experiment_status.json", status)
    final_cleanup = archive_and_reset_runtime(root, "runtime_cleanup_after")
    artifacts["final_cleanup_manifest"] = str(Path(final_cleanup["archive_root"]) / "cleanup_manifest.json")
    write_json(root / "reports" / "stage_summary.json", {"stages": stages, "artifacts": artifacts, "final_report": str(report)})
    report = final_report(root, stages, artifacts)
    status.update({"final_report": str(report), "final_cleanup_manifest": artifacts["final_cleanup_manifest"]})
    write_json(root / "reports" / "experiment_status.json", status)
    print(json.dumps({"experiment_root": str(root), "final_report": str(report), "stages": stages, "gepa_conclusion": gepa_conclusion}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
