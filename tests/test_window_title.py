from __future__ import annotations

from ui.window_title import APP_TITLE, WindowTitleManager, render_title


def test_render_title_focus_hides_all_state_markers() -> None:
    assert render_title("idle", focused=True) == APP_TITLE
    assert render_title("running", focused=True) == APP_TITLE
    assert render_title("confirmation", focused=True) == APP_TITLE
    assert render_title("completed", focused=True) == APP_TITLE


def test_render_title_unfocused_shows_workflow_state() -> None:
    assert render_title("running", focused=False, frame_index=0) == "DiaEvo -"
    assert render_title("running", focused=False, frame_index=1) == "DiaEvo \\"
    assert render_title("running", focused=False, frame_index=2) == "DiaEvo |"
    assert render_title("running", focused=False, frame_index=3) == "DiaEvo /"
    assert render_title("confirmation", focused=False) == "DiaEvo [!]"
    assert render_title("completed", focused=False) == "DiaEvo ☖"


def test_manager_writes_base_title_on_start_and_stop() -> None:
    titles: list[str] = []
    manager = WindowTitleManager(writer=titles.append, focus_provider=lambda: True, interval=10)

    manager.start()
    manager.set_state("running")
    manager.stop()

    assert titles == ["DiaEvo"]


def test_manager_shows_unfocused_confirmation_and_completed() -> None:
    titles: list[str] = []
    manager = WindowTitleManager(writer=titles.append, focus_provider=lambda: False, interval=10)

    manager.start()
    manager.set_state("confirmation")
    manager.set_state("completed")
    manager.stop()

    assert "DiaEvo [!]" in titles
    assert "DiaEvo ☖" in titles
    assert titles[-1] == "DiaEvo"


def test_nested_activity_restores_running_state() -> None:
    titles: list[str] = []
    manager = WindowTitleManager(writer=titles.append, focus_provider=lambda: False, interval=10)

    manager.start()
    manager.set_state("running")
    with manager.activity("running"):
        pass

    assert titles[-1] == "DiaEvo -"
