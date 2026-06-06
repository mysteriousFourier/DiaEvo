import queue

from ui.flow_input import FlowInputController


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


def test_escape_still_queues_hard_interrupt(capsys):
    controller = FlowInputController()

    controller._handle_character("\x1b")

    event = controller.queue.get_nowait()
    assert event.interrupt is True
    assert event.hard_interrupt is True
    assert controller.interrupt_event.is_set() is True
    assert controller.force_terminate_event.is_set() is True
    capsys.readouterr()
