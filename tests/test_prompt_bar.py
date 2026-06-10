from ui import prompt_bar
from ui import cli_style
from diaevo import __version__


def test_slash_menu_scrolls_past_first_page() -> None:
    menu = prompt_bar.render_command_menu("/", selected_index=len(prompt_bar.COMMANDS) - 1)
    lines = menu.splitlines()

    assert len(lines) == prompt_bar.COMMAND_MENU_PAGE_SIZE
    assert "/qq" in menu
    assert "/learn" not in menu
    assert "/exit" in menu


def test_submit_can_select_command_after_first_page() -> None:
    assert prompt_bar._submit_value("/", selected_index=len(prompt_bar.COMMANDS) - 1) == "/exit"


def test_skill_menu_selection_requires_arguments_before_submit() -> None:
    selected_index = next(index for index, command in enumerate(prompt_bar.COMMANDS) if command[0] == "/skill")

    assert prompt_bar._should_complete_menu_selection("/", selected_index) is True
    assert prompt_bar._menu_completion_value("/", selected_index) == "/skill "


def test_skill_menu_lists_name_and_summary(monkeypatch) -> None:
    prompt_bar._set_skill_menu_cache_for_tests(
        [
            ("alpha-skill", "Alpha model summary."),
            ("beta-skill", "Beta model summary."),
        ]
    )
    monkeypatch.setattr(prompt_bar, "_term_width", lambda: 80)

    menu = prompt_bar.render_command_menu("/skill")

    assert "alpha-skill" in menu
    assert "beta-skill" in menu
    assert "Alpha model summary." in menu
    assert prompt_bar._menu_completion_value("/skill", 1) == "/skill beta-skill"

    rendered = prompt_bar.render_prompt_state("/skill beta-skill")
    assert "说明" not in rendered

    prompt_bar._set_skill_menu_cache_for_tests(None)


def test_exit_menu_selection_can_submit_directly() -> None:
    selected_index = next(index for index, command in enumerate(prompt_bar.COMMANDS) if command[0] == "/exit")

    assert prompt_bar._should_complete_menu_selection("/", selected_index) is False
    assert prompt_bar._submit_value("/", selected_index) == "/exit"


def test_kg_is_single_user_facing_command() -> None:
    names = [name for name, _ in prompt_bar.COMMANDS]

    assert "/kg" in names
    assert not any(name.startswith("/kg-") for name in names)


def test_default_slash_menu_hides_internal_pipeline_commands() -> None:
    names = [name for name, _ in prompt_bar.COMMANDS]

    assert "/learn" in names
    assert "/debug" in names
    for hidden in {"/ingest", "/mine", "/recommend", "/generate", "/verify", "/self-evolve", "/feedback"}:
        assert hidden not in names
        assert hidden in prompt_bar.COMMAND_NAMES


def test_menu_window_returns_to_top_after_last_selection() -> None:
    matches = prompt_bar._matching_commands("/")
    selected_index = prompt_bar._move_menu_selection(len(matches) - 1, len(matches), 1)
    menu = prompt_bar.render_command_menu("/", selected_index=selected_index)

    assert selected_index == 0
    assert "/learn" in menu
    assert "/exit" not in menu


def test_prompt_line_has_no_horizontal_rules() -> None:
    rendered = prompt_bar.render_prompt_line("/mine")

    assert cli_style.GLYPHS["h"] not in rendered
    assert rendered.startswith(cli_style.GLYPHS["prompt"])


def test_prompt_footer_is_short_and_composer_like() -> None:
    rendered = prompt_bar.render_footer()

    assert "Enter 发送" in rendered
    assert "Tab 补全" in rendered
    assert "Enter 运行命令或当前菜单项" not in rendered


def test_prompt_line_wraps_long_input_without_truncating(monkeypatch) -> None:
    monkeypatch.setattr(prompt_bar, "_term_width", lambda: 80)
    value = "x" * 160

    rendered = prompt_bar.render_prompt_line(value)

    assert "..." not in rendered
    assert rendered.count("x") == len(value)
    assert len(rendered.splitlines()) > 1


def test_prompt_cursor_uses_wrapped_input_line_count(monkeypatch) -> None:
    monkeypatch.setattr(prompt_bar, "_term_width", lambda: 80)
    value = "x" * 160
    prompt_line_count = len(prompt_bar.render_prompt_line(value).splitlines())
    rendered_lines = prompt_line_count + 2

    cursor = prompt_bar._cursor_to_input(rendered_lines, value)

    assert cursor.startswith("\033[2A")


def test_cursor_to_bottom_accounts_for_lines_above_input() -> None:
    cursor = prompt_bar._cursor_to_bottom(rendered_lines=4, value="/", lines_above_input=1)

    assert cursor == "\033[2B\r"


def test_cursor_sequences_omit_zero_distance_moves() -> None:
    to_input = prompt_bar._cursor_to_input(rendered_lines=3, value="")
    to_bottom = prompt_bar._cursor_to_bottom(rendered_lines=1, value="")

    assert "\033[0A" not in to_input
    assert "\033[0B" not in to_bottom
    assert "\033[0C" not in to_input
    assert to_bottom == "\r"


def test_fit_preserves_ansi_accent_when_truncated() -> None:
    rendered = cli_style._fit(f"{cli_style.PURPLE}重点词汇{cli_style.RESET} 后面很长", 8)

    assert cli_style.PURPLE in rendered
    assert cli_style.RESET in rendered
    assert cli_style.ANSI_RE.sub("", rendered).endswith("...")


def test_read_prompt_erases_menu_and_footer_on_submit(monkeypatch) -> None:
    class FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, value: str) -> int:
            self.writes.append(value)
            return len(value)

        def flush(self) -> None:
            return None

    class FakeStdin:
        def isatty(self) -> bool:
            return True

    class FakeMsvcrt:
        def __init__(self) -> None:
            self.chars = iter(["/", "\r"])

        def getwch(self) -> str:
            return next(self.chars)

    fake_stdout = FakeStdout()
    monkeypatch.setattr(prompt_bar.sys, "stdout", fake_stdout)
    monkeypatch.setattr(prompt_bar.sys, "stdin", FakeStdin())
    monkeypatch.setattr(prompt_bar, "msvcrt", FakeMsvcrt())

    value = prompt_bar._read_prompt_raw()

    assert value == "/learn"
    writes = "".join(fake_stdout.writes)
    assert "Enter 发送" in writes
    assert writes.rstrip().endswith("\033[2K")


def test_read_prompt_uses_plain_input_by_default(monkeypatch) -> None:
    monkeypatch.delenv("DIAEVO_RAW_PROMPT", raising=False)
    monkeypatch.setenv("DIAEVO_PROMPT_TOOLKIT", "0")
    monkeypatch.setattr("builtins.input", lambda prompt="": f"{prompt}hello")

    assert prompt_bar.read_prompt() == f"{cli_style.GLYPHS['prompt']} hello"


def test_read_prompt_uses_prompt_toolkit_session_when_available(monkeypatch) -> None:
    class FakeSession:
        def prompt(self) -> str:
            return "/"

    monkeypatch.delenv("DIAEVO_RAW_PROMPT", raising=False)
    monkeypatch.setenv("DIAEVO_PROMPT_TOOLKIT", "1")
    monkeypatch.setattr(prompt_bar, "_prompt_session", lambda: FakeSession())

    assert prompt_bar.read_prompt() == "/learn"


def test_prompt_toolkit_completion_items_include_commands_and_skills(monkeypatch) -> None:
    prompt_bar._set_skill_menu_cache_for_tests([("alpha-skill", "Alpha summary")])

    assert ("/help", "显示本地命令") in prompt_bar._completion_items("/h")
    assert ("/skill alpha-skill", "Alpha summary") in prompt_bar._completion_items("/skill a")

    prompt_bar._set_skill_menu_cache_for_tests(None)


def test_prompt_toolkit_style_builds_without_default_background() -> None:
    prompt_bar._prompt_style()


def test_read_prompt_ignores_ctrl_c_until_exit_command(monkeypatch) -> None:
    class FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, value: str) -> int:
            self.writes.append(value)
            return len(value)

        def flush(self) -> None:
            return None

    class FakeStdin:
        def isatty(self) -> bool:
            return True

    class FakeMsvcrt:
        def __init__(self) -> None:
            self.chars = iter(["\003", "/", "e", "x", "i", "t", "\r"])

        def getwch(self) -> str:
            return next(self.chars)

    monkeypatch.setattr(prompt_bar.sys, "stdout", FakeStdout())
    monkeypatch.setattr(prompt_bar.sys, "stdin", FakeStdin())
    monkeypatch.setattr(prompt_bar, "msvcrt", FakeMsvcrt())

    assert prompt_bar._read_prompt_raw() == "/exit"


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
    title_index = next(index for index, line in enumerate(lines) if "DiaEvo" in line and f"v{__version__}" in line)

    assert workspace_index > 0
    assert title_index == workspace_index + 1
    assert not lines[workspace_index].lstrip().startswith(str(cli_style.WORKSPACE_ROOT))
    assert not lines[title_index].lstrip().startswith("DiaEvo")


def test_trust_dialog_choice_can_use_arrow_keys(monkeypatch) -> None:
    class FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, value: str) -> int:
            self.writes.append(value)
            return len(value)

        def flush(self) -> None:
            return None

    class FakeStdin:
        def isatty(self) -> bool:
            return True

    class FakeMsvcrt:
        def __init__(self) -> None:
            self.chars = iter(["\xe0", "P", "\r"])

        def getwch(self) -> str:
            return next(self.chars)

    fake_stdout = FakeStdout()
    monkeypatch.setattr(cli_style.sys, "stdout", fake_stdout)
    monkeypatch.setattr(cli_style.sys, "stdin", FakeStdin())
    monkeypatch.setattr(cli_style, "msvcrt", FakeMsvcrt())

    assert cli_style._read_trust_dialog_choice() == "2"

    writes = "".join(fake_stdout.writes)
    assert "❯ 2. 否，退出" in cli_style.ANSI_RE.sub("", writes)
    assert "上下键选择" in writes
