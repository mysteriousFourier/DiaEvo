from ui import prompt_bar
from ui import cli_style


def test_slash_menu_scrolls_past_first_page() -> None:
    menu = prompt_bar.render_command_menu("/", selected_index=10)
    lines = menu.splitlines()

    assert len(lines) == prompt_bar.COMMAND_MENU_PAGE_SIZE
    assert "/kg" in menu
    assert "/exit" not in menu


def test_submit_can_select_command_after_first_page() -> None:
    assert prompt_bar._submit_value("/", selected_index=len(prompt_bar.COMMANDS) - 1) == "/exit"


def test_kg_is_single_user_facing_command() -> None:
    names = [name for name, _ in prompt_bar.COMMANDS]

    assert "/kg" in names
    assert not any(name.startswith("/kg-") for name in names)


def test_menu_window_returns_to_top_after_last_selection() -> None:
    matches = prompt_bar._matching_commands("/")
    selected_index = prompt_bar._move_menu_selection(len(matches) - 1, len(matches), 1)
    menu = prompt_bar.render_command_menu("/", selected_index=selected_index)

    assert selected_index == 0
    assert "/ingest" in menu
    assert "/exit" not in menu


def test_prompt_line_has_no_horizontal_rules() -> None:
    rendered = prompt_bar.render_prompt_line("/mine")

    assert cli_style.GLYPHS["h"] not in rendered
    assert rendered.startswith(cli_style.GLYPHS["prompt"])


def test_home_card_has_no_outer_border() -> None:
    rendered = cli_style.render_logo_card()

    assert cli_style.GLYPHS["tl"] not in rendered
    assert cli_style.GLYPHS["tr"] not in rendered
    assert cli_style.GLYPHS["bl"] not in rendered
    assert cli_style.GLYPHS["br"] not in rendered


def test_home_workspace_and_title_stay_inside_card_content() -> None:
    rendered = cli_style.render_logo_card()
    lines = rendered.splitlines()
    workspace_index = next(index for index, line in enumerate(lines) if str(cli_style.WORKSPACE_ROOT) in line)
    title_index = next(index for index, line in enumerate(lines) if "DiaEvo" in line and "v0.1.0" in line)

    assert workspace_index > 0
    assert title_index == workspace_index + 1
    assert not lines[workspace_index].lstrip().startswith(str(cli_style.WORKSPACE_ROOT))
    assert not lines[title_index].lstrip().startswith("DiaEvo")
