from __future__ import annotations

import pytest

from diaevo import cli, sessions
from diaevo.sessions import save_session


def test_resume_without_id_lists_sessions(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / ".diaevo" / "sessions")
    monkeypatch.setattr(cli, "bootstrap_workspace", lambda: None)
    save_session(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "历史任务"},
            {"role": "assistant", "content": "历史回答"},
        ],
        session_id="resume-me",
    )

    code = cli.main(["resume"])

    output = capsys.readouterr().out
    assert code == 0
    assert "resume-me" in output
    assert "diaevo resume <session-id>" in output


def test_chat_test_resume_requires_interactive(monkeypatch) -> None:
    monkeypatch.setattr(cli, "bootstrap_workspace", lambda: None)
    with pytest.raises(SystemExit):
        cli.main(["chat-test", "--resume", "resume-me"])
