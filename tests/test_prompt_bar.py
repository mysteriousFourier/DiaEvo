from ui import prompt_bar


def test_slash_menu_scrolls_past_first_page() -> None:
    menu = prompt_bar.render_command_menu("/", selected_index=9)
    lines = menu.splitlines()

    assert len(lines) == prompt_bar.COMMAND_MENU_PAGE_SIZE
    assert "/ingest" not in menu
    assert "/baseurl" in lines[-1]


def test_submit_can_select_command_after_first_page() -> None:
    assert prompt_bar._submit_value("/", selected_index=13) == "/exit"


def test_menu_window_returns_to_top_after_last_selection() -> None:
    matches = prompt_bar._matching_commands("/")
    selected_index = prompt_bar._move_menu_selection(len(matches) - 1, len(matches), 1)
    menu = prompt_bar.render_command_menu("/", selected_index=selected_index)

    assert selected_index == 0
    assert "/ingest" in menu
    assert "/exit" not in menu
