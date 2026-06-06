import queue
import threading
from contextlib import nullcontext

from ui.flow_input import FlowInputController, FlowInputEvent


def test_escape_with_active_slash_menu_clears_draft_without_interrupt(capsys):
    controller = FlowInputController()
    controller.draft = "/"

    controller._queue_escape_interrupt()

    assert controller.draft == ""
    assert controller.selected_index == 0
    try:
        controller.queue.get_nowait()
    except queue.Empty:
        pass
    else:
        raise AssertionError("escape in slash menu should not queue an interrupt")
    capsys.readouterr()


def test_talk_preview_is_removed_when_talk_event_is_handled(capsys):
    controller = FlowInputController()
    controller.draft = "/talk 快速问题"

    controller._queue_enter()
    handled = []
    count = controller.handle_talk_queued(lambda event: handled.append(event.text))

    assert count == 1
    assert handled == ["快速问题"]
    assert controller.queued_preview == []
    capsys.readouterr()


def test_qq_talk_preview_is_removed_when_talk_event_is_handled(capsys):
    controller = FlowInputController()

    controller.queue_external_text("/talk 当前进度", source="QQ")
    handled = []
    count = controller.handle_talk_queued(lambda event: handled.append(event.text))

    assert count == 1
    assert handled == ["当前进度"]
    assert controller.queued_preview == []
    capsys.readouterr()


def test_external_text_queues_interrupt_event_with_source_preview(capsys):
    controller = FlowInputController()

    event = controller.queue_external_text("继续当前任务", source="QQ")

    queued = controller.queue.get_nowait()
    assert queued == event
    assert queued.text == "继续当前任务"
    assert queued.interrupt is True
    assert controller.interrupt_event.is_set()
    assert controller.force_terminate_event.is_set()
    assert controller.queued_preview == ["QQ 继续当前任务"]
    capsys.readouterr()


def test_flow_input_edits_at_cursor_position(capsys):
    controller = FlowInputController()
    controller.draft = "/talk 当前状态"
    controller.cursor_index = len("/talk 当前")

    controller._append_printable("的")

    assert controller.draft == "/talk 当前的状态"
    assert controller.cursor_index == len("/talk 当前的")
    capsys.readouterr()


def test_flow_input_arrow_delete_and_backspace_edit_cursor(capsys):
    controller = FlowInputController()
    controller.draft = "abcd"
    controller.cursor_index = 2

    controller._handle_extended_key("K")
    controller._backspace()
    controller._handle_extended_key("M")
    controller._handle_extended_key("S")

    assert controller.draft == "bd"
    assert controller.cursor_index == 1
    capsys.readouterr()


def test_ctrl_c_does_not_queue_flow_interrupt(capsys):
    controller = FlowInputController()
    controller.draft = "keep typing"

    controller._handle_character("\003")

    assert controller.draft == "keep typing"
    assert controller.queue.empty()
    assert controller.interrupt_event.is_set() is False
    assert controller.force_terminate_event.is_set() is False
    capsys.readouterr()


def test_start_can_render_without_raw_listener(monkeypatch):
    controller = FlowInputController()

    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)

    assert controller.start(listen=False) is True
    assert controller.active.is_set()
    assert controller._thread is None

    controller.stop(enabled=True)


def test_start_can_use_toolkit_without_raw_listener(monkeypatch):
    controller = FlowInputController()
    prompt_started = threading.Event()

    class FakeApp:
        def __init__(self):
            self.exited = threading.Event()

        def exit(self, **kwargs):
            self.exited.set()

        def invalidate(self):
            pass

    class FakeSession:
        def __init__(self):
            self.app = FakeApp()

        def prompt(self, **kwargs):
            pre_run = kwargs.get("pre_run")
            if pre_run is not None:
                pre_run()
            prompt_started.set()
            self.app.exited.wait(timeout=1)
            raise EOFError

    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(controller, "_create_toolkit_session", lambda: FakeSession())
    monkeypatch.setattr("ui.flow_input._prompt_stdout_patch", lambda: nullcontext())

    assert controller.start(listen=False, toolkit=True) is True
    assert prompt_started.wait(timeout=1)
    assert controller.active.is_set()
    assert controller._thread is None
    assert controller._toolkit_thread is not None

    controller.stop(enabled=True)

    assert not controller.active.is_set()
    assert controller._toolkit_thread is None


def test_pause_stops_toolkit_prompt_until_transient_input_finishes(monkeypatch):
    controller = FlowInputController()
    prompt_started = threading.Event()
    sessions = []

    class FakeApp:
        def __init__(self):
            self.exited = threading.Event()

        def exit(self, **kwargs):
            self.exited.set()

        def invalidate(self):
            pass

    class FakeSession:
        def __init__(self):
            self.app = FakeApp()
            self.started = threading.Event()
            sessions.append(self)

        def prompt(self, **kwargs):
            pre_run = kwargs.get("pre_run")
            if pre_run is not None:
                pre_run()
            self.started.set()
            prompt_started.set()
            self.app.exited.wait(timeout=1)
            raise EOFError

    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(controller, "_create_toolkit_session", FakeSession)
    monkeypatch.setattr("ui.flow_input._prompt_stdout_patch", lambda: nullcontext())

    assert controller.start(listen=False, toolkit=True) is True
    assert prompt_started.wait(timeout=1)
    first_session = sessions[0]

    with controller.pause():
        assert controller.paused.is_set()
        assert first_session.app.exited.is_set()
        assert controller._toolkit_thread is None
        assert not controller._toolkit_mode

    assert not controller.paused.is_set()
    assert controller._toolkit_thread is not None
    assert len(sessions) == 2

    controller.stop(enabled=True)


def test_begin_output_suspends_toolkit_prompt_until_prompt_is_shown(monkeypatch):
    controller = FlowInputController()
    prompt_started = threading.Event()
    sessions = []

    class FakeApp:
        def __init__(self):
            self.exited = threading.Event()

        def exit(self, **kwargs):
            self.exited.set()

        def invalidate(self):
            pass

    class FakeSession:
        def __init__(self):
            self.app = FakeApp()
            sessions.append(self)

        def prompt(self, **kwargs):
            pre_run = kwargs.get("pre_run")
            if pre_run is not None:
                pre_run()
            prompt_started.set()
            self.app.exited.wait(timeout=1)
            raise EOFError

    monkeypatch.setattr("ui.flow_input.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(controller, "_create_toolkit_session", FakeSession)
    monkeypatch.setattr("ui.flow_input._prompt_stdout_patch", lambda: nullcontext())

    assert controller.start(listen=False, toolkit=True) is True
    assert prompt_started.wait(timeout=1)
    first_session = sessions[0]

    controller.begin_output()

    assert first_session.app.exited.is_set()
    assert controller.active.is_set()
    assert controller._toolkit_thread is None
    assert not controller._toolkit_mode

    controller.show_prompt(force=True)

    assert controller._toolkit_thread is not None
    assert len(sessions) == 2

    controller.stop(enabled=True)


def test_toolkit_submission_queues_flow_event():
    controller = FlowInputController()

    controller._queue_toolkit_submission("继续检查输入栏")

    assert controller.queue.get_nowait() == FlowInputEvent("继续检查输入栏", interrupt=True)
    assert controller.queued_preview == ["继续检查输入栏"]


def test_toolkit_escape_queues_hard_interrupt():
    controller = FlowInputController()

    controller._queue_toolkit_escape("改成 prompt_toolkit")

    assert controller.queue.get_nowait() == FlowInputEvent(
        "改成 prompt_toolkit",
        interrupt=True,
        hard_interrupt=True,
    )
    assert controller.interrupt_event.is_set()
    assert controller.force_terminate_event.is_set()


def test_toolkit_exit_uses_prompt_loop_threadsafe_callback():
    controller = FlowInputController()
    scheduled = []
    exited = []

    class FakeLoop:
        def is_closed(self):
            return False

        def call_soon_threadsafe(self, callback):
            scheduled.append(callback)

    class FakeApp:
        loop = FakeLoop()

        def exit(self, **kwargs):
            exited.append(kwargs)

    class FakeSession:
        app = FakeApp()

    controller._toolkit_session = FakeSession()

    controller._exit_toolkit_app()

    assert exited == []
    assert len(scheduled) == 1
    scheduled[0]()
    assert exited == [{"exception": EOFError}]


def test_toolkit_toolbar_recomputes_dynamic_status_line():
    controller = FlowInputController()
    seconds = {"value": 3}

    controller.set_status_line_renderer(lambda: f"Working ({seconds['value']}s) · running")

    assert "Working (3s) · running" in controller._render_toolkit_toolbar()

    seconds["value"] = 27

    assert "Working (27s) · running" in controller._render_toolkit_toolbar()


def test_escape_still_queues_hard_interrupt(capsys):
    controller = FlowInputController()

    controller._handle_character("\x1b")

    event = controller.queue.get_nowait()
    assert event.interrupt is True
    assert event.hard_interrupt is True
    assert controller.interrupt_event.is_set() is True
    assert controller.force_terminate_event.is_set() is True
    capsys.readouterr()
