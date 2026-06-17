from __future__ import annotations

import pytest

from diaevo import sessions
from diaevo.sessions import load_session, render_session_list, render_session_transcript, save_session


def test_save_load_and_list_sessions(tmp_path, monkeypatch) -> None:
    root = tmp_path / ".diaevo" / "sessions"
    monkeypatch.setattr(sessions, "SESSIONS_DIR", root)

    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "实现 resume 功能"},
        {"role": "assistant", "content": "可以保存历史。"},
    ]

    saved = save_session(messages, session_id="test-session")
    loaded = load_session("test-session")
    listed = sessions.list_sessions()

    assert saved.id == "test-session"
    assert saved.title == "实现 resume 功能"
    assert loaded["messages"] == messages
    assert [item.id for item in listed] == ["test-session"]
    assert listed[0].preview == "可以保存历史。"


def test_render_session_list_and_transcript(tmp_path, monkeypatch) -> None:
    root = tmp_path / ".diaevo" / "sessions"
    monkeypatch.setattr(sessions, "SESSIONS_DIR", root)
    save_session(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "第一轮"},
            {"role": "assistant", "content": "第一轮回答"},
        ],
        session_id="s1",
    )

    rendered_list = render_session_list(sessions.list_sessions())
    rendered_transcript = render_session_transcript(load_session("s1")["messages"])

    assert "s1" in rendered_list
    assert "diaevo resume <session-id>" in rendered_list
    assert "user>" in rendered_transcript
    assert "assistant>" in rendered_transcript
    assert "第一轮回答" in rendered_transcript


def test_invalid_session_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        sessions.session_path("../outside")
