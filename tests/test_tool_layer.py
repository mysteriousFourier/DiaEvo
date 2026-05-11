from pathlib import Path

from skillminer.tool_layer import execute_tool, parse_tool_arg_pairs, parse_tool_args, resolve_workspace_path, tool_schemas


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
    assert "SkillMiner" in result["content"]
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
