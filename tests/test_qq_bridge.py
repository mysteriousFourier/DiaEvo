import json
import sys
import zipfile
from pathlib import Path

import diaevo.qq_bridge as qq_bridge
from diaevo.qq_bridge import (
    QQBridgeConfig,
    QQRemoteSession,
    RemoteMessage,
    config_from_env_vars,
    discover_napcat_command,
    install_managed_napcat,
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
    monkeypatch.setenv("DIAEVO_QQ_NAPCAT_AUTO_INSTALL", "false")
    monkeypatch.setenv("DIAEVO_QQ_NAPCAT_COMMAND", 'start "" "D:\\NapCat\\NapCatQQ.exe"')
    monkeypatch.setenv("DIAEVO_QQ_NAPCAT_STARTUP_WAIT_SECONDS", "12.5")
    monkeypatch.setenv("DIAEVO_QQ_NAPCAT_DOWNLOAD_URL", "https://example.test/napcat.zip")
    monkeypatch.setenv("DIAEVO_QQ_NAPCAT_INSTALL_DIR", "D:\\DiaEvo\\NapCat")

    config = config_from_env_vars()

    assert config.enabled is True
    assert config.allowed_users == {"10001", "10002"}
    assert config.onebot_ws_url == "ws://localhost:3001"
    assert config.onebot_http_url == "http://localhost:3000"
    assert config.napcat_autostart is True
    assert config.napcat_auto_install is False
    assert config.napcat_command == 'start "" "D:\\NapCat\\NapCatQQ.exe"'
    assert config.napcat_startup_wait_seconds == 12.5
    assert config.napcat_download_url == "https://example.test/napcat.zip"
    assert config.napcat_install_dir == Path("D:\\DiaEvo\\NapCat")


def test_config_defaults_to_napcat_autostart(monkeypatch) -> None:
    monkeypatch.setattr("diaevo.qq_bridge.load_env", lambda *args, **kwargs: {})
    monkeypatch.setenv("DIAEVO_QQ_ENABLED", "true")
    monkeypatch.setenv("DIAEVO_QQ_ALLOWED_USERS", "10001")
    monkeypatch.setenv("DIAEVO_QQ_ONEBOT_WS_URL", "ws://localhost:3001")
    monkeypatch.setenv("DIAEVO_QQ_ONEBOT_HTTP_URL", "http://localhost:3000")
    monkeypatch.delenv("DIAEVO_QQ_NAPCAT_AUTOSTART", raising=False)
    monkeypatch.delenv("DIAEVO_QQ_NAPCAT_AUTO_INSTALL", raising=False)
    monkeypatch.delenv("DIAEVO_QQ_NAPCAT_COMMAND", raising=False)

    config = config_from_env_vars()

    assert config.napcat_autostart is True
    assert config.napcat_auto_install is True
    assert config.napcat_command == ""


def test_config_defaults_napcat_install_dir_to_install_root(monkeypatch, tmp_path) -> None:
    install_root = tmp_path / "diaevo-install"
    workspace_root = tmp_path / "some-workspace"
    monkeypatch.setattr("diaevo.qq_bridge.load_env", lambda *args, **kwargs: {})
    monkeypatch.setattr("diaevo.qq_bridge.INSTALL_ROOT", install_root)
    monkeypatch.setattr("diaevo.qq_bridge.WORKSPACE_ROOT", workspace_root)
    monkeypatch.setenv("DIAEVO_QQ_ENABLED", "true")
    monkeypatch.setenv("DIAEVO_QQ_ALLOWED_USERS", "10001")
    monkeypatch.setenv("DIAEVO_QQ_ONEBOT_WS_URL", "ws://localhost:3001")
    monkeypatch.setenv("DIAEVO_QQ_ONEBOT_HTTP_URL", "http://localhost:3000")
    monkeypatch.delenv("DIAEVO_QQ_NAPCAT_INSTALL_DIR", raising=False)

    config = config_from_env_vars()

    assert config.napcat_install_dir == install_root / ".tmp" / "napcat"
    assert config.napcat_install_dir != workspace_root / ".tmp" / "napcat"


def test_config_resolves_relative_napcat_install_dir_from_install_root(monkeypatch, tmp_path) -> None:
    install_root = tmp_path / "diaevo-install"
    workspace_root = tmp_path / "selected-workspace"
    monkeypatch.setattr("diaevo.qq_bridge.load_env", lambda *args, **kwargs: {})
    monkeypatch.setattr("diaevo.qq_bridge.INSTALL_ROOT", install_root)
    monkeypatch.setattr("diaevo.qq_bridge.WORKSPACE_ROOT", workspace_root)
    monkeypatch.setenv("DIAEVO_QQ_ENABLED", "true")
    monkeypatch.setenv("DIAEVO_QQ_ALLOWED_USERS", "10001")
    monkeypatch.setenv("DIAEVO_QQ_ONEBOT_WS_URL", "ws://localhost:3001")
    monkeypatch.setenv("DIAEVO_QQ_ONEBOT_HTTP_URL", "http://localhost:3000")
    monkeypatch.setenv("DIAEVO_QQ_NAPCAT_INSTALL_DIR", ".tmp/napcat-custom")

    config = config_from_env_vars()

    assert config.napcat_install_dir == install_root / ".tmp" / "napcat-custom"


def test_ensure_napcat_onebot_config_writes_http_and_ws_servers(tmp_path) -> None:
    config_dir = (
        tmp_path
        / ".tmp"
        / "napcat"
        / "onekey"
        / "NapCat.44498.Shell"
        / "versions"
        / "9.9.26-44498"
        / "resources"
        / "app"
        / "napcat"
        / "config"
    )
    config_dir.mkdir(parents=True)
    (config_dir / "napcat.json").write_text("{}", encoding="utf-8")
    (config_dir / "webui.json").write_text("{}", encoding="utf-8")
    account_config = config_dir / "onebot11_10001.json"
    account_config.write_text(
        json.dumps({"network": {"httpServers": [], "websocketServers": []}}),
        encoding="utf-8",
    )
    config = QQBridgeConfig(
        enabled=True,
        allowed_users={"10001"},
        onebot_ws_url="ws://localhost:3001",
        onebot_http_url="http://localhost:3000",
        access_token="secret-token",
        event_log_path=tmp_path / "qq_remote_events.jsonl",
        napcat_install_dir=tmp_path / ".tmp" / "napcat",
    )

    changed = qq_bridge._ensure_napcat_onebot_config(config)

    assert account_config in changed
    assert config_dir / "onebot11.json" in changed
    data = json.loads(account_config.read_text(encoding="utf-8"))
    assert data["network"]["httpServers"] == [
        {
            "enable": True,
            "name": "diaevo-http",
            "host": "127.0.0.1",
            "port": 3000,
            "enableCors": True,
            "enableWebsocket": False,
            "messagePostFormat": "array",
            "token": "secret-token",
            "debug": False,
        }
    ]
    assert data["network"]["websocketServers"] == [
        {
            "enable": True,
            "name": "diaevo-ws",
            "host": "127.0.0.1",
            "port": 3001,
            "messagePostFormat": "array",
            "reportSelfMessage": False,
            "enableForcePushEvent": True,
            "token": "secret-token",
            "debug": False,
            "heartInterval": 30000,
        }
    ]


def test_prepare_onebot_service_reports_when_napcat_not_found(monkeypatch, tmp_path) -> None:
    config = QQBridgeConfig(
        enabled=True,
        allowed_users={"10001"},
        onebot_ws_url="ws://127.0.0.1:3001",
        onebot_http_url="http://127.0.0.1:3000",
        event_log_path=tmp_path / "qq_remote_events.jsonl",
        napcat_autostart=True,
        napcat_command="",
        napcat_auto_install=False,
    )
    monkeypatch.setattr("diaevo.qq_bridge.onebot_service_available", lambda config: False)
    monkeypatch.setattr("diaevo.qq_bridge.discover_napcat_command", lambda: "")

    result = prepare_onebot_service(config)

    assert result["status"] == "missing_command"
    assert "没有" in result["message"]


def test_prepare_onebot_service_auto_installs_when_napcat_not_found(monkeypatch, tmp_path) -> None:
    class FakeProcess:
        pid = 44
        returncode = None

        def poll(self):
            return None

    checks = {"count": 0}
    started = []

    def fake_available(config):
        checks["count"] += 1
        return checks["count"] >= 2

    monkeypatch.setattr("diaevo.qq_bridge.onebot_service_available", fake_available)
    monkeypatch.setattr("diaevo.qq_bridge.discover_napcat_command", lambda: "")
    monkeypatch.setattr("diaevo.qq_bridge.install_managed_napcat", lambda config: "installed-napcat")
    monkeypatch.setattr("diaevo.qq_bridge._ensure_napcat_onebot_config", lambda config: [])
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
        napcat_auto_install=True,
        napcat_startup_wait_seconds=5,
    )

    result = prepare_onebot_service(config)

    assert result["status"] == "started"
    assert result["command"] == "installed-napcat"
    assert started == ["installed-napcat"]


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
    monkeypatch.setattr("diaevo.qq_bridge._ensure_napcat_onebot_config", lambda config: [])
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


def test_prepare_onebot_service_reports_non_detached_process_exit(monkeypatch, tmp_path) -> None:
    class FakeProcess:
        pid = 45
        returncode = 2

        def poll(self):
            return self.returncode

    monkeypatch.setattr("diaevo.qq_bridge.onebot_service_available", lambda config: False)
    monkeypatch.setattr("diaevo.qq_bridge._ensure_napcat_onebot_config", lambda config: [])
    monkeypatch.setattr("diaevo.qq_bridge._start_napcat_process", lambda command: FakeProcess())
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

    assert result["status"] == "exited"
    assert result["returncode"] == 2


def test_prepare_onebot_service_waits_after_windows_detached_launcher_exit(monkeypatch, tmp_path) -> None:
    class FakeProcess:
        pid = 46
        returncode = 0

        def poll(self):
            return self.returncode

    times = iter([0.0, 0.0, 0.5, 1.1])
    monkeypatch.setattr(qq_bridge.sys, "platform", "win32")
    monkeypatch.setattr("diaevo.qq_bridge.onebot_service_available", lambda config: False)
    monkeypatch.setattr("diaevo.qq_bridge._ensure_napcat_onebot_config", lambda config: [])
    monkeypatch.setattr("diaevo.qq_bridge._start_napcat_process", lambda command: FakeProcess())
    monkeypatch.setattr("diaevo.qq_bridge.time.monotonic", lambda: next(times))
    monkeypatch.setattr("diaevo.qq_bridge.time.sleep", lambda seconds: None)
    config = QQBridgeConfig(
        enabled=True,
        allowed_users={"10001"},
        onebot_ws_url="ws://127.0.0.1:3001",
        onebot_http_url="http://127.0.0.1:3000",
        event_log_path=tmp_path / "qq_remote_events.jsonl",
        napcat_autostart=True,
        napcat_command='start "" /D "D:\\NapCat" "D:\\NapCat\\napcat.bat"',
        napcat_startup_wait_seconds=1,
    )

    result = prepare_onebot_service(config)

    assert result["status"] == "timeout"
    assert "等待 OneBot" in result["message"]


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
    monkeypatch.setattr("diaevo.qq_bridge._ensure_napcat_onebot_config", lambda config: [])
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


def test_discover_napcat_command_finds_install_root_tmp_when_workspace_differs(monkeypatch, tmp_path) -> None:
    install_root = tmp_path / "diaevo-install"
    workspace_root = tmp_path / "selected-workspace"
    root = install_root / ".tmp" / "napcat" / "onekey" / "bootmain"
    root.mkdir(parents=True)
    script = root / "napcat.bat"
    script.write_text("@echo off\n.\\NapCatWinBootMain.exe\n", encoding="utf-8")
    workspace_root.mkdir()
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("diaevo.qq_bridge.WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr("diaevo.qq_bridge.INSTALL_ROOT", install_root)
    monkeypatch.setattr("diaevo.qq_bridge._npm_global_bin", lambda: None)

    command = discover_napcat_command()

    assert str(script).lower() in command.lower()


def test_discover_napcat_command_prefers_script_with_local_qq_exe(monkeypatch, tmp_path) -> None:
    install_root = tmp_path / "diaevo-install"
    bootmain = install_root / ".tmp" / "napcat" / "onekey" / "bootmain"
    bootmain.mkdir(parents=True)
    bad_script = bootmain / "napcat.bat"
    bad_script.write_text("@echo off\n.\\NapCatWinBootMain.exe\n", encoding="utf-8")

    shell = install_root / ".tmp" / "napcat" / "onekey" / "NapCat.44498.Shell"
    shell.mkdir(parents=True)
    good_script = shell / "napcat.bat"
    good_script.write_text("@echo off\n.\\NapCatWinBootMain.exe\n", encoding="utf-8")
    (shell / "QQ.exe").write_bytes(b"fake qq")

    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("diaevo.qq_bridge.WORKSPACE_ROOT", tmp_path / "workspace")
    monkeypatch.setattr("diaevo.qq_bridge.INSTALL_ROOT", install_root)
    monkeypatch.setattr("diaevo.qq_bridge._npm_global_bin", lambda: None)

    command = discover_napcat_command()

    assert str(good_script).lower() in command.lower()
    assert str(bad_script).lower() not in command.lower()


def test_install_managed_napcat_extracts_zip_and_returns_command(monkeypatch, tmp_path) -> None:
    source_zip = tmp_path / "source.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("onekey/bootmain/napcat.bat", "@echo off\n.\\NapCatWinBootMain.exe\n")
        archive.writestr("onekey/NapCat.44498.Shell/napcat.bat", "@echo off\n.\\NapCatWinBootMain.exe\n")
        archive.writestr("onekey/NapCat.44498.Shell/QQ.exe", b"fake qq")

    def fake_download(url, target):
        target.write_bytes(source_zip.read_bytes())

    monkeypatch.setattr(qq_bridge.sys, "platform", "win32")
    monkeypatch.setattr("diaevo.qq_bridge._download_url", fake_download)
    install_dir = tmp_path / ".tmp" / "napcat"
    config = QQBridgeConfig(
        enabled=True,
        allowed_users={"10001"},
        onebot_ws_url="ws://127.0.0.1:3001",
        onebot_http_url="http://127.0.0.1:3000",
        event_log_path=tmp_path / "qq_remote_events.jsonl",
        napcat_auto_install=True,
        napcat_install_dir=install_dir,
    )

    command = install_managed_napcat(config)

    assert str(install_dir / "onekey" / "NapCat.44498.Shell" / "napcat.bat").lower() in command.lower()


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

    assert sent == []
    log_text = (tmp_path / "qq_remote_events.jsonl").read_text(encoding="utf-8")
    assert "forbidden_command" in log_text
    assert "sk-test" not in log_text


def test_remote_tool_error_is_logged_without_qq_message(monkeypatch, tmp_path) -> None:
    sent: list[tuple[str, str]] = []

    def fake_execute_tool(name, args, *, approve=False, event_log_path=None):
        return {"status": "error", "tool": name, "error": "boom"}

    monkeypatch.setattr("diaevo.qq_bridge.execute_tool", fake_execute_tool)
    session = QQRemoteSession(_config(tmp_path), send_message=lambda user, text: sent.append((user, text)))

    session.handle_message(RemoteMessage(user_id="10001", text="/tool run_shell command=pytest"))

    assert sent == []


def test_remote_chat_error_is_logged_without_qq_message(monkeypatch, tmp_path) -> None:
    sent: list[tuple[str, str]] = []

    def fake_chat_once(*args, **kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr("diaevo.qq_bridge.chat_once", fake_chat_once)
    session = QQRemoteSession(_config(tmp_path), send_message=lambda user, text: sent.append((user, text)))

    session.handle_message(RemoteMessage(user_id="10001", text="你好"))

    assert sent == []
    log_text = (tmp_path / "qq_remote_events.jsonl").read_text(encoding="utf-8")
    assert "chat_failed" in log_text


def test_remote_cli_error_is_logged_without_qq_message(monkeypatch, tmp_path) -> None:
    sent: list[tuple[str, str]] = []

    def fake_cli_main(argv):
        print("bad output")
        print("bad error", file=sys.stderr)
        return 2

    monkeypatch.setattr("diaevo.qq_bridge.cli_main", fake_cli_main)
    session = QQRemoteSession(_config(tmp_path), send_message=lambda user, text: sent.append((user, text)))

    session.handle_message(RemoteMessage(user_id="10001", text="/tools"))

    assert sent == []
    log_text = (tmp_path / "qq_remote_events.jsonl").read_text(encoding="utf-8")
    assert "command_failed" in log_text
    assert "bad output" in log_text


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
