import contextlib
from pathlib import Path
import io
from urllib.error import HTTPError

from diaevo.tool_layer import (
    execute_tool,
    parse_tool_arg_pairs,
    parse_tool_args,
    resolve_workspace_path,
    tool_schemas,
)


def test_tool_schemas_mark_gated_tools():
    schemas = {item["name"]: item for item in tool_schemas()}
    assert schemas["read_file"]["read_only"] is True
    assert schemas["read_file"]["approval_required"] is False
    assert schemas["write_file"]["read_only"] is False
    assert schemas["write_file"]["approval_required"] is True
    assert schemas["delete_file"]["destructive"] is True


def test_resolve_workspace_path_rejects_parent_escape():
    try:
        resolve_workspace_path("..")
    except ValueError as exc:
        assert "outside workspace" in str(exc)
    else:
        raise AssertionError("parent path escape should fail")


def test_read_file_executes_and_logs(tmp_path):
    log_path = tmp_path / "events.jsonl"
    result = execute_tool(
        "read_file",
        {"path": "README.md", "limit": 2},
        event_log_path=log_path,
    )
    assert result["status"] == "ok"
    assert result["path"] == "README.md"
    assert "DiaEvo" in result["content"]
    assert log_path.exists()
    assert '"tool": "read_file"' in log_path.read_text(encoding="utf-8")


def test_write_file_requires_approval_before_write(tmp_path):
    target = Path(".tmp/tool-layer-test.txt")
    if target.exists():
        target.unlink()
    log_path = tmp_path / "events.jsonl"

    preview = execute_tool(
        "write_file",
        {"path": str(target), "content": "hello\n"},
        event_log_path=log_path,
    )
    assert preview["status"] == "requires_approval"
    assert not target.exists()
    assert "+hello" in preview["preview"]["diff"]

    written = execute_tool(
        "write_file",
        {"path": str(target), "content": "hello\n"},
        approve=True,
        event_log_path=log_path,
    )
    assert written["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "hello\n"
    target.unlink()


def test_write_file_rejects_empty_path_and_missing_content(tmp_path):
    log_path = tmp_path / "events.jsonl"

    empty_path = execute_tool("write_file", {"path": "", "content": "hello"}, event_log_path=log_path)
    assert empty_path["status"] == "error"
    assert empty_path["error"] == "write_file requires non-empty path"

    missing_content = execute_tool("write_file", {"path": ".tmp/missing-content.txt"}, event_log_path=log_path)
    assert missing_content["status"] == "error"
    assert missing_content["error"] == "write_file requires content"

    none_content = execute_tool(
        "write_file",
        {"path": ".tmp/missing-content.txt", "content": None},
        event_log_path=log_path,
    )
    assert none_content["status"] == "error"
    assert none_content["error"] == "write_file requires content"


def test_required_string_schemas_have_min_length():
    schemas = {item["name"]: item for item in tool_schemas()}

    assert schemas["read_file"]["input_schema"]["properties"]["path"]["minLength"] == 1
    assert schemas["write_file"]["input_schema"]["properties"]["path"]["minLength"] == 1
    assert schemas["edit_file"]["input_schema"]["properties"]["path"]["minLength"] == 1
    assert schemas["delete_file"]["input_schema"]["properties"]["path"]["minLength"] == 1
    assert schemas["run_shell"]["input_schema"]["properties"]["command"]["minLength"] == 1


def test_write_file_rejects_empty_content(tmp_path):
    result = execute_tool(
        "write_file",
        {"path": ".tmp/empty-content.txt", "content": ""},
        event_log_path=tmp_path / "events.jsonl",
    )

    assert result["status"] == "error"
    assert result["error"] == "write_file requires content"


def _write_local_web_design_skill(root: Path) -> Path:
    skill_dir = root / "web-design-engineer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: web-design-engineer",
                "description: Local DiaEvo workflow for React frontend pages and interface verification.",
                "tags: [web, frontend, react, ui]",
                "source_cluster: local:test",
                "status: candidate",
                "---",
                "",
                "# Web Design Engineer",
                "",
                "## When To Use",
                "",
                "Use this local project skill for React frontend page work, UI layout checks, and browser-based verification.",
                "",
                "## Trigger Signals",
                "",
                "- React, Vite, frontend, web page, UI, dashboard, or visual interface tasks.",
                "",
                "## Operating Steps",
                "",
                "1. Inspect the existing frontend structure before editing.",
                "2. Follow local component and style conventions.",
                "3. Verify responsive layout and text overflow.",
                "",
                "## Failure Fallbacks",
                "",
                "- If the app cannot run locally, report the command and error clearly.",
                "",
                "## Verification Suggestions",
                "",
                "- Run the local build or targeted UI tests when available.",
            ]
        ),
        encoding="utf-8",
    )
    return skill_dir


def test_skill_context_tools_recommend_and_load_web_design(monkeypatch, tmp_path):
    import diaevo.skill_context as skill_context

    skills_root = tmp_path / "skills"
    skill_dir = _write_local_web_design_skill(skills_root)
    monkeypatch.setenv("DIAEVO_SKILLS_DIR", str(skills_root))
    monkeypatch.setattr(skill_context, "load_skill_registry", lambda: [])
    monkeypatch.setattr(skill_context, "load_plugins", lambda: [])

    recommendations = execute_tool("recommend_skills", {"task": "做一个 React 前端页面", "top_k": 5})
    names = [item["name"] for item in recommendations["recommendations"]]

    assert "web-design-engineer" in names
    recommended = next(item for item in recommendations["recommendations"] if item["name"] == "web-design-engineer")
    assert Path(recommended["path"]) == skill_dir

    context = execute_tool(
        "load_skill_context",
        {"name": "web-design-engineer", "task": "做一个 React 前端页面"},
    )
    assert context["status"] == "ok"
    assert "SKILL.md" in context["skill_file"]
    assert context["skill_text"]
    assert "references_routing" in context


def test_web_fetch_extracts_readable_text_after_approval(monkeypatch, tmp_path):
    import diaevo.tool_layer as tool_layer

    def fake_fetch(url, max_bytes):
        return {
            "url": "https://example.com/final",
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "truncated": False,
            "content": "<html><script>ignore()</script><h1>Title</h1><p>Hello <b>world</b>.</p></html>",
        }

    monkeypatch.setattr(tool_layer, "_fetch_url", fake_fetch)

    result = execute_tool(
        "web_fetch",
        {"url": "https://example.com/page"},
        approve=True,
        event_log_path=tmp_path / "events.jsonl",
    )

    assert result["status"] == "ok"
    assert result["url"] == "https://example.com/page"
    assert result["final_url"] == "https://example.com/final"
    assert "Title" in result["content"]
    assert "Hello world." in result["content"]
    assert "ignore" not in result["content"]


def test_web_search_uses_searxng_and_fetches_top_results(monkeypatch, tmp_path):
    import diaevo.tool_layer as tool_layer

    calls = []

    def fake_fetch(url, max_bytes):
        calls.append(url)
        if "searx.local/search" in url:
            return {
                "url": url,
                "status_code": 200,
                "content_type": "application/json",
                "truncated": False,
                "content": (
                    '{"results": ['
                    '{"title": "One", "url": "https://one.example/page", "content": "Snippet one"},'
                    '{"title": "Two", "url": "https://two.example/page", "content": "Snippet two"}'
                    "]}"
                ),
            }
        return {
            "url": url,
            "status_code": 200,
            "content_type": "text/html",
            "truncated": False,
            "content": "<main><h1>Fetched page</h1><p>Evidence body.</p></main>",
        }

    monkeypatch.setenv("DIAEVO_SEARXNG_URL", "https://searx.local")
    monkeypatch.setattr(tool_layer, "_fetch_url", fake_fetch)

    result = execute_tool(
        "web_search",
        {"query": "DiaEvo", "max_results": 2, "fetch_top": 1, "domains": ["example.com"]},
        approve=True,
        event_log_path=tmp_path / "events.jsonl",
    )

    assert result["status"] == "ok"
    assert result["backend"] == "searxng"
    assert result["results"][0]["fetch_status"] == "ok"
    assert "Fetched page" in result["results"][0]["content_excerpt"]
    assert result["results"][1]["fetch_status"] == "not_fetched"
    assert "site%3Aexample.com" in calls[0]


def test_web_search_falls_back_to_duckduckgo_when_searxng_fails(monkeypatch, tmp_path):
    import diaevo.tool_layer as tool_layer

    ddg_html = """
    <div class="result">
      <h2><a class="result__a" href="https://example.com/a">Example A</a></h2>
      <a class="result__snippet">Duck snippet</a>
    </div></div>
    """

    def fake_fetch(url, max_bytes):
        if "searx.local/search" in url:
            raise tool_layer.ToolError("searx unavailable")
        return {
            "url": url,
            "status_code": 200,
            "content_type": "text/html",
            "truncated": False,
            "content": ddg_html,
        }

    monkeypatch.setenv("DIAEVO_SEARXNG_URL", "https://searx.local")
    monkeypatch.setattr(tool_layer, "_fetch_url", fake_fetch)

    result = execute_tool(
        "web_search",
        {"query": "DiaEvo", "fetch_top": 0},
        approve=True,
        event_log_path=tmp_path / "events.jsonl",
    )

    assert result["status"] == "ok"
    assert result["backend"] == "duckduckgo_html"
    assert result["fallback_reason"] == "searx unavailable"
    assert result["results"][0]["title"] == "Example A"
    assert result["results"][0]["snippet"] == "Duck snippet"
    assert result["results"][0]["content_excerpt"] == ""


def test_web_search_can_use_bing_html_backend(monkeypatch, tmp_path):
    import diaevo.tool_layer as tool_layer

    calls = []
    bing_html = """
    <ol id="b_results">
      <li class="b_algo">
        <h2><a href="https://example.com/a">Example A</a></h2>
        <div class="b_caption"><p>Bing snippet</p></div>
      </li>
    </ol>
    """

    def fake_fetch(url, max_bytes):
        calls.append(url)
        return {
            "url": url,
            "status_code": 200,
            "content_type": "text/html",
            "truncated": False,
            "content": bing_html,
        }

    monkeypatch.setattr(tool_layer, "_fetch_url", fake_fetch)

    result = execute_tool(
        "web_search",
        {"query": "DiaEvo", "backend": "bing_html", "fetch_top": 0, "domains": ["example.com"]},
        approve=True,
        event_log_path=tmp_path / "events.jsonl",
    )

    assert result["status"] == "ok"
    assert result["backend"] == "bing_html"
    assert result["attempted_backends"] == ["bing_html"]
    assert result["results"][0]["title"] == "Example A"
    assert result["results"][0]["snippet"] == "Bing snippet"
    assert "bing.com/search" in calls[0]
    assert "site%3Aexample.com" in calls[0]


def test_web_search_default_backend_can_be_forced_by_env(monkeypatch, tmp_path):
    import diaevo.tool_layer as tool_layer

    calls = []

    def fake_fetch(url, max_bytes):
        calls.append(url)
        if "searx.local/search" in url:
            raise tool_layer.ToolError("searx unavailable")
        raise AssertionError("duckduckgo should not be called when env forces searxng")

    monkeypatch.setenv("DIAEVO_WEB_SEARCH_BACKEND", "searxng")
    monkeypatch.setenv("DIAEVO_SEARXNG_URL", "https://searx.local")
    monkeypatch.setattr(tool_layer, "_fetch_url", fake_fetch)

    result = execute_tool(
        "web_search",
        {"query": "DiaEvo", "fetch_top": 0},
        approve=True,
        event_log_path=tmp_path / "events.jsonl",
    )

    assert result["status"] == "error"
    assert result["error"] == "searx unavailable"
    assert len(calls) == 1
    assert "searx.local/search" in calls[0]


def test_web_search_schema_exposes_new_search_options():
    schemas = {item["name"]: item for item in tool_schemas()}
    properties = schemas["web_search"]["input_schema"]["properties"]

    assert schemas["web_search"]["approval_required"] is True
    assert properties["query"]["minLength"] == 1
    assert properties["fetch_top"]["default"] == 3
    assert "searxng" in properties["backend"]["enum"]
    assert "bing_html" in properties["backend"]["enum"]
    assert "domains" in properties


def test_arxiv_search_parses_atom_feed(monkeypatch, tmp_path):
    import diaevo.tool_layer as tool_layer

    feed = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2401.01234v1</id>
    <updated>2024-01-03T00:00:00Z</updated>
    <published>2024-01-02T00:00:00Z</published>
    <title> Test Paper About Retrieval </title>
    <summary>
      This paper studies retrieval augmented generation.
    </summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <arxiv:primary_category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <link href="http://arxiv.org/abs/2401.01234v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2401.01234v1" rel="related" type="application/pdf"/>
  </entry>
</feed>"""

    captured = {}

    def fake_fetch(url, max_bytes):
        captured["url"] = url
        captured["max_bytes"] = max_bytes
        return {"url": url, "status_code": 200, "content_type": "application/atom+xml", "content": feed}

    monkeypatch.setattr(tool_layer, "ARXIV_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(tool_layer, "ARXIV_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(tool_layer, "ARXIV_LOCK_DIR", tmp_path / "lock")
    monkeypatch.setattr(tool_layer, "ARXIV_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(tool_layer, "_fetch_arxiv_api_url", fake_fetch)

    result = execute_tool(
        "arxiv_search",
        {"query": "retrieval augmented generation", "category": "cs.CL", "max_results": 3},
    )

    assert result["status"] == "ok"
    assert result["source"] == "arxiv_api"
    assert result["total_results"] == 1
    assert "search_query=all%3A%22retrieval+augmented+generation%22" in captured["url"]
    assert "cat%3Acs.CL" in captured["url"]
    assert result["results"][0]["arxiv_id"] == "2401.01234v1"
    assert result["results"][0]["title"] == "Test Paper About Retrieval"
    assert result["results"][0]["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert result["results"][0]["pdf_url"] == "http://arxiv.org/pdf/2401.01234v1"


def test_arxiv_search_schema_is_read_only_and_available_to_chat_tools():
    from diaevo.tool_chat import chat_tool_schemas

    schemas = {item["name"]: item for item in tool_schemas()}
    chat_names = {item["function"]["name"] for item in chat_tool_schemas()}

    assert schemas["arxiv_search"]["read_only"] is True
    assert schemas["arxiv_search"]["approval_required"] is False
    assert schemas["arxiv_search"]["input_schema"]["properties"]["query"]["minLength"] == 1
    assert "arxiv_search" in chat_names


def test_arxiv_search_enforces_request_spacing(monkeypatch):
    import diaevo.tool_layer as tool_layer

    feed = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>0</opensearch:totalResults>
</feed>"""

    calls = []
    times = iter([100.2, 103.4, 103.5])

    def fake_fetch(url, max_bytes):
        calls.append(url)
        return {"url": url, "status_code": 200, "content_type": "application/atom+xml", "content": feed}

    monkeypatch.setattr(tool_layer, "_fetch_arxiv_api_url", fake_fetch)
    monkeypatch.setattr(tool_layer, "_read_arxiv_cache", lambda url: None)
    monkeypatch.setattr(tool_layer, "_write_arxiv_cache", lambda url, fetched: None)
    monkeypatch.setattr(tool_layer, "_read_arxiv_state_last_request_at", lambda: 0.0)
    monkeypatch.setattr(tool_layer, "_write_arxiv_state_last_request_at", lambda value: None)
    monkeypatch.setattr(tool_layer, "_arxiv_api_file_lock", lambda: contextlib.nullcontext())
    monkeypatch.setattr(tool_layer, "ARXIV_LAST_REQUEST_AT", 100.0)
    monkeypatch.setattr(tool_layer.time, "time", lambda: next(times))
    slept = []
    monkeypatch.setattr(tool_layer.time, "sleep", lambda seconds: slept.append(round(seconds, 1)))

    result = execute_tool("arxiv_search", {"query": "retrieval"})

    assert result["status"] == "ok"
    assert calls
    assert slept == [2.8]


def test_arxiv_search_reuses_cached_url(monkeypatch, tmp_path):
    import diaevo.tool_layer as tool_layer

    feed = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>0</opensearch:totalResults>
</feed>"""

    calls = []

    def fake_fetch(url, max_bytes):
        calls.append(url)
        return {"url": url, "status_code": 200, "content_type": "application/atom+xml", "content": feed}

    monkeypatch.setattr(tool_layer, "ARXIV_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(tool_layer, "ARXIV_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(tool_layer, "ARXIV_LOCK_DIR", tmp_path / "lock")
    monkeypatch.setattr(tool_layer, "ARXIV_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(tool_layer, "_fetch_arxiv_api_url", fake_fetch)

    first = execute_tool("arxiv_search", {"query": "retrieval"})
    second = execute_tool("arxiv_search", {"query": "retrieval"})

    assert first["status"] == "ok"
    assert first["cache_hit"] is False
    assert second["status"] == "ok"
    assert second["cache_hit"] is True
    assert len(calls) == 1


def test_arxiv_search_uses_shared_state_for_request_spacing(monkeypatch, tmp_path):
    import diaevo.tool_layer as tool_layer

    feed = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>0</opensearch:totalResults>
</feed>"""

    calls = []

    def fake_fetch(url, max_bytes):
        calls.append(url)
        return {"url": url, "status_code": 200, "content_type": "application/atom+xml", "content": feed}

    monkeypatch.setattr(tool_layer, "ARXIV_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(tool_layer, "ARXIV_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(tool_layer, "ARXIV_LOCK_DIR", tmp_path / "lock")
    monkeypatch.setattr(tool_layer, "ARXIV_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(tool_layer, "_fetch_arxiv_api_url", fake_fetch)
    monkeypatch.setattr(tool_layer, "_read_arxiv_state_last_request_at", lambda: 100.0)
    monkeypatch.setattr(tool_layer, "_write_arxiv_state_last_request_at", lambda value: None)
    monkeypatch.setattr(tool_layer, "_read_arxiv_cache", lambda url: None)
    monkeypatch.setattr(tool_layer, "_write_arxiv_cache", lambda url, fetched: None)
    monkeypatch.setattr(tool_layer, "_arxiv_api_file_lock", lambda: contextlib.nullcontext())
    times = iter([101.0, 104.0, 104.1, 104.2])
    monkeypatch.setattr(tool_layer.time, "time", lambda: next(times))
    slept = []
    monkeypatch.setattr(tool_layer.time, "sleep", lambda seconds: slept.append(round(seconds, 1)))

    result = execute_tool("arxiv_search", {"query": "different retrieval"})

    assert result["status"] == "ok"
    assert calls
    assert slept == [2.0]


def test_arxiv_search_records_cooldown_after_429(monkeypatch):
    import diaevo.tool_layer as tool_layer

    writes = []
    times = iter([100.0, 100.1, 100.2])

    class Headers:
        def get(self, name, default=None):
            return "12" if name == "Retry-After" else default

    def fake_fetch(url, max_bytes):
        raise tool_layer.ToolError(
            f"HTTP 429 fetching {url}; Retry-After=12; body=Rate exceeded. Please slow down."
        )

    monkeypatch.setattr(tool_layer, "_fetch_arxiv_api_url", fake_fetch)
    monkeypatch.setattr(tool_layer, "_read_arxiv_cache", lambda url: None)
    monkeypatch.setattr(tool_layer, "_write_arxiv_cache", lambda url, fetched: None)
    monkeypatch.setattr(tool_layer, "_read_arxiv_state_last_request_at", lambda: 0.0)
    monkeypatch.setattr(tool_layer, "_write_arxiv_state_last_request_at", writes.append)
    monkeypatch.setattr(tool_layer, "_arxiv_api_file_lock", lambda: contextlib.nullcontext())
    monkeypatch.setattr(tool_layer, "ARXIV_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(tool_layer.time, "time", lambda: next(times))

    result = execute_tool("arxiv_search", {"query": "retrieval"})

    assert result["status"] == "error"
    assert "HTTP 429" in result["error"]
    assert "Retry-After=12" in result["error"]
    assert "Rate exceeded" in result["error"]
    assert writes[-1] == 112.2


def test_http_error_message_includes_retry_after_and_body():
    import diaevo.tool_layer as tool_layer

    class Headers:
        def get(self, name, default=None):
            return "12" if name == "Retry-After" else default

    exc = HTTPError(
        "https://export.arxiv.org/api/query",
        429,
        "Too Many Requests",
        Headers(),
        io.BytesIO(b"Rate exceeded. Please slow down."),
    )

    message = tool_layer._http_error_message(exc, "https://export.arxiv.org/api/query")

    assert "HTTP 429" in message
    assert "Retry-After=12" in message
    assert "Rate exceeded" in message


def test_parse_tool_args_requires_json_object():
    assert parse_tool_args('{"path": "README.md"}') == {"path": "README.md"}
    assert parse_tool_arg_pairs(["path=README.md", "limit=3", "recursive=false"]) == {
        "path": "README.md",
        "limit": 3,
        "recursive": False,
    }
    try:
        parse_tool_args("[]")
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("list args should fail")


def test_run_shell_repeated_failure_hint_is_added_by_tool_layer(monkeypatch, tmp_path):
    import diaevo.tool_layer as tool_layer

    class FakeProcess:
        returncode = 1

        def poll(self):
            return 1

        def communicate(self, timeout=None):
            return "", "boom"

    monkeypatch.setattr(tool_layer, "_LAST_FAILED_SHELL_COMMAND", "")
    monkeypatch.setattr(tool_layer.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    first = execute_tool(
        "run_shell",
        {"command": "pytest -q"},
        approve=True,
        event_log_path=tmp_path / "events.jsonl",
    )
    second = execute_tool(
        "run_shell",
        {"command": "pytest -q"},
        approve=True,
        event_log_path=tmp_path / "events.jsonl",
    )

    assert first["status"] == "error"
    assert "note" not in first
    assert second["status"] == "error"
    assert "连续失败" in second["note"]
