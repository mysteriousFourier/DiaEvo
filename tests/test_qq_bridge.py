import json
from pathlib import Path

import diaevo.qq_bridge as qq_bridge
from diaevo.qq_bridge import (
    QQBridgeConfig,
    QQRemoteSession,
    RemoteMessage,
    config_from_env_vars,
    discover_napcat_command,
    _shell_command_for_path,
    parse_onebot_private_message,
    prepare_onebot_service,
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
    monkeypatch.setenv("DIAEVO_QQ_NAPCAT_AUTOSTART", "true")
    monkeypatch.setenv("DIAEVO_QQ_NAPCAT_COMMAND", 'start "" "D:\\NapCat\\NapCatQQ.exe"')
    monkeypatch.setenv("DIAEVO_QQ_NAPCAT_STARTUP_WAIT_SECONDS", "12.5")

    config = config_from_env_vars()

    assert config.enabled is True
    assert config.allowed_users == {"10001", "10002"}
    assert config.onebot_ws_url == "ws://localhost:3001"
    assert config.onebot_http_url == "http://localhost:3000"
    assert config.napcat_autostart is True
    assert config.napcat_command == 'start "" "D:\\NapCat\\NapCatQQ.exe"'
    assert config.napcat_startup_wait_seconds == 12.5


def test_config_defaults_to_napcat_autostart(monkeypatch) -> None:
    monkeypatch.setattr("diaevo.qq_bridge.load_env", lambda *args, **kwargs: {})
    monkeypatch.setenv("DIAEVO_QQ_ENABLED", "true")
    monkeypatch.setenv("DIAEVO_QQ_ALLOWED_USERS", "10001")
    monkeypatch.setenv("DIAEVO_QQ_ONEBOT_WS_URL", "ws://localhost:3001")
    monkeypatch.setenv("DIAEVO_QQ_ONEBOT_HTTP_URL", "http://localhost:3000")
    monkeypatch.delenv("DIAEVO_QQ_NAPCAT_AUTOSTART", raising=False)
    monkeypatch.delenv("DIAEVO_QQ_NAPCAT_COMMAND", raising=False)

    config = config_from_env_vars()

    assert config.napcat_autostart is True
    assert config.napcat_command == ""


def test_prepare_onebot_service_reports_when_napcat_not_found(monkeypatch, tmp_path) -> None:
    config = QQBridgeConfig(
        enabled=True,
        allowed_users={"10001"},
        onebot_ws_url="ws://127.0.0.1:3001",
        onebot_http_url="http://127.0.0.1:3000",
        event_log_path=tmp_path / "qq_remote_events.jsonl",
        napcat_autostart=True,
        napcat_command="",
    )
    monkeypatch.setattr("diaevo.qq_bridge.onebot_service_available", lambda config: False)
    monkeypatch.setattr("diaevo.qq_bridge.discover_napcat_command", lambda: "")

    result = prepare_onebot_service(config)

    assert result["status"] == "missing_command"
    assert "没有" in result["message"]


def test_prepare_onebot_service_starts_napcat_until_port_ready(monkeypatch, tmp_path) -> None:
    class FakeProcess:
        pid = 42
        returncode = None

        def poll(self):
            return None

    checks = {"count": 0}
    started = []

    def fake_available(config):
        checks["count"] += 1
        return checks["count"] >= 2

    def fake_start(command):
        started.append(command)
        return FakeProcess()

    monkeypatch.setattr("diaevo.qq_bridge.onebot_service_available", fake_available)
    monkeypatch.setattr("diaevo.qq_bridge._start_napcat_process", fake_start)
    monkeypatch.setattr("diaevo.qq_bridge.time.sleep", lambda seconds: None)
    config = QQBridgeConfig(
        enabled=True,
        allowed_users={"10001"},
        onebot_ws_url="ws://127.0.0.1:3001",
        onebot_http_url="http://127.0.0.1:3000",
        event_log_path=tmp_path / "qq_remote_events.jsonl",
        napcat_autostart=True,
        napcat_command="napcat-start",
        napcat_startup_wait_seconds=5,
    )

    result = prepare_onebot_service(config)

    assert result["status"] == "started"
    assert started == ["napcat-start"]
    assert result["pid"] == 42


def test_prepare_onebot_service_uses_discovered_napcat_command(monkeypatch, tmp_path) -> None:
    class FakeProcess:
        pid = 43
        returncode = None

        def poll(self):
            return None

    checks = {"count": 0}
    started = []

    def fake_available(config):
        checks["count"] += 1
        return checks["count"] >= 2

    monkeypatch.setattr("diaevo.qq_bridge.onebot_service_available", fake_available)
    monkeypatch.setattr("diaevo.qq_bridge.discover_napcat_command", lambda: "discovered-napcat")
    monkeypatch.setattr("diaevo.qq_bridge._start_napcat_process", lambda command: started.append(command) or FakeProcess())
    monkeypatch.setattr("diaevo.qq_bridge.time.sleep", lambda seconds: None)
    config = QQBridgeConfig(
        enabled=True,
        allowed_users={"10001"},
        onebot_ws_url="ws://127.0.0.1:3001",
        onebot_http_url="http://127.0.0.1:3000",
        event_log_path=tmp_path / "qq_remote_events.jsonl",
        napcat_autostart=True,
        napcat_command="",
        napcat_startup_wait_seconds=5,
    )

    result = prepare_onebot_service(config)

    assert result["status"] == "started"
    assert result["command"] == "discovered-napcat"
    assert started == ["discovered-napcat"]


def test_discover_napcat_command_uses_path(monkeypatch, tmp_path) -> None:
    fake = tmp_path / "napcat.cmd"
    fake.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr("diaevo.qq_bridge._npm_global_bin", lambda: None)

    command = discover_napcat_command()

    assert str(fake).lower() in command.lower()


def test_discover_napcat_command_finds_workspace_tmp_onekey(monkeypatch, tmp_path) -> None:
    root = tmp_path / ".tmp" / "napcat" / "onekey" / "bootmain"
    root.mkdir(parents=True)
    script = root / "napcat.bat"
    script.write_text("@echo off\n.\\NapCatWinBootMain.exe\n", encoding="utf-8")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("diaevo.qq_bridge.WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr("diaevo.qq_bridge.INSTALL_ROOT", tmp_path / "install")
    monkeypatch.setattr("diaevo.qq_bridge._npm_global_bin", lambda: None)

    command = discover_napcat_command()

    assert str(script).lower() in command.lower()


def test_windows_shell_command_sets_working_directory(monkeypatch, tmp_path) -> None:
    script = tmp_path / "napcat.bat"
    script.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(qq_bridge.sys, "platform", "win32")

    command = _shell_command_for_path(script)

    assert "/D" in command
    assert str(tmp_path) in command


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
