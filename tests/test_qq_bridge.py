import json
from pathlib import Path

from diaevo.qq_bridge import (
    QQBridgeConfig,
    QQRemoteSession,
    RemoteMessage,
    config_from_env_vars,
    parse_onebot_private_message,
)


def _config(tmp_path: Path, *, ttl: int = 300) -> QQBridgeConfig:
    return QQBridgeConfig(
        enabled=True,
        allowed_users={"10001"},
        onebot_ws_url="ws://127.0.0.1:3001",
        onebot_http_url="http://127.0.0.1:3000",
        approval_ttl_seconds=ttl,
        event_log_path=tmp_path / "qq_remote_events.jsonl",
    )


def test_config_from_env_vars_reads_whitelist(monkeypatch) -> None:
    monkeypatch.setattr("diaevo.qq_bridge.load_env", lambda *args, **kwargs: {})
    monkeypatch.setenv("DIAEVO_QQ_ENABLED", "true")
    monkeypatch.setenv("DIAEVO_QQ_ALLOWED_USERS", "10001, 10002")
    monkeypatch.setenv("DIAEVO_QQ_ONEBOT_WS_URL", "ws://localhost:3001")
    monkeypatch.setenv("DIAEVO_QQ_ONEBOT_HTTP_URL", "http://localhost:3000/")

    config = config_from_env_vars()

    assert config.enabled is True
    assert config.allowed_users == {"10001", "10002"}
    assert config.onebot_ws_url == "ws://localhost:3001"
    assert config.onebot_http_url == "http://localhost:3000"


def test_parse_onebot_private_text_message() -> None:
    message = parse_onebot_private_message(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 10001,
            "message_id": 42,
            "message": [{"type": "text", "data": {"text": " /status "}}],
        }
    )

    assert message is not None
    assert message.user_id == "10001"
    assert message.message_id == "42"
    assert message.text == "/status"


def test_unauthorized_private_message_is_ignored(tmp_path) -> None:
    sent: list[tuple[str, str]] = []
    session = QQRemoteSession(_config(tmp_path), send_message=lambda user, text: sent.append((user, text)))

    session.handle_message(RemoteMessage(user_id="99999", text="/status", message_id="m1"))

    assert sent == []
    log_text = (tmp_path / "qq_remote_events.jsonl").read_text(encoding="utf-8")
    assert "ignored_unauthorized" in log_text


def test_remote_key_command_is_forbidden(tmp_path) -> None:
    sent: list[tuple[str, str]] = []
    session = QQRemoteSession(_config(tmp_path), send_message=lambda user, text: sent.append((user, text)))

    session.handle_message(RemoteMessage(user_id="10001", text="/key sk-test"))

    assert "禁用 /key" in sent[-1][1]
    log_text = (tmp_path / "qq_remote_events.jsonl").read_text(encoding="utf-8")
    assert "forbidden_command" in log_text
    assert "sk-test" not in log_text


def test_tool_requires_approval_then_executes_after_code(monkeypatch, tmp_path) -> None:
    sent: list[tuple[str, str]] = []
    calls: list[tuple[str, dict, bool]] = []

    def fake_execute_tool(name, args, *, approve=False, event_log_path=None):
        calls.append((name, args, approve))
        if not approve:
            return {
                "status": "requires_approval",
                "tool": name,
                "event_id": "preview-1",
                "preview": {"operation": "run", "command": args["command"]},
            }
        return {"status": "ok", "tool": name, "event_id": "run-1", "stdout": "done"}

    monkeypatch.setattr("diaevo.qq_bridge.execute_tool", fake_execute_tool)
    session = QQRemoteSession(
        _config(tmp_path),
        send_message=lambda user, text: sent.append((user, text)),
        now=lambda: 1000.0,
    )

    session.handle_message(RemoteMessage(user_id="10001", text="/tool run_shell command=pytest"))

    assert calls == [("run_shell", {"command": "pytest"}, False)]
    assert "需要远程确认" in sent[-1][1]
    code = next(iter(session.pending))

    session.handle_message(RemoteMessage(user_id="10001", text=f"/approve {code}"))

    assert calls[-1] == ("run_shell", {"command": "pytest"}, True)
    assert "done" in sent[-1][1]
    assert session.pending == {}

    events = [
        json.loads(line)
        for line in (tmp_path / "qq_remote_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(item["action"] == "approval_requested" for item in events)
    assert any(item["action"] == "approval_accepted" for item in events)
    assert all("approval_code_hash" not in item or code not in json.dumps(item) for item in events)


def test_expired_approval_code_does_not_execute(monkeypatch, tmp_path) -> None:
    sent: list[tuple[str, str]] = []
    calls: list[bool] = []
    current_time = {"value": 1000.0}

    def fake_execute_tool(name, args, *, approve=False, event_log_path=None):
        calls.append(approve)
        if not approve:
            return {"status": "requires_approval", "tool": name, "event_id": "preview-1"}
        return {"status": "ok", "tool": name}

    monkeypatch.setattr("diaevo.qq_bridge.execute_tool", fake_execute_tool)
    session = QQRemoteSession(
        _config(tmp_path, ttl=5),
        send_message=lambda user, text: sent.append((user, text)),
        now=lambda: current_time["value"],
    )

    session.handle_message(RemoteMessage(user_id="10001", text="/tool run_shell command=pytest"))
    code = next(iter(session.pending))
    current_time["value"] = 1006.0
    session.handle_message(RemoteMessage(user_id="10001", text=f"/approve {code}"))

    assert calls == [False]
    assert "已过期" in sent[-1][1]
