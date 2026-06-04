from ui.widgets import table


def test_table_separator_uses_box_drawing_line() -> None:
    rendered = table(["A", "B"], [[1, 2]])

    assert "─┼─" in rendered
    assert "-+-" not in rendered
