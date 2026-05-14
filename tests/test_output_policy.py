from __future__ import annotations

from ui.output_policy import render_assistant_text, sanitize_no_emoji, strip_markdown
from ui.progress import status
from ui.tool_render import render_tool_result


def test_sanitize_no_emoji_preserves_terminal_glyphs_and_chinese() -> None:
    text = "完成 ✅ ❯ 中文 ─ ok"

    assert sanitize_no_emoji(text) == "完成  ❯ 中文 ─ ok"


def test_strip_markdown_keeps_terminal_readable_content() -> None:
    text = """# 标题

- **步骤一**
- `python -m pytest`

```python
print("ok")
```
"""

    rendered = strip_markdown(text)

    assert "标题" in rendered
    assert "步骤一" in rendered
    assert "python -m pytest" in rendered
    assert 'print("ok")' in rendered
    assert "```" not in rendered
    assert "**" not in rendered


def test_render_assistant_text_plain_removes_markdown_and_emoji() -> None:
    rendered = render_assistant_text("## Done ✅\n- **Run tests**", mode="plain")

    assert rendered == "Done\nRun tests"


def test_tool_result_rendering_removes_emoji() -> None:
    rendered = render_tool_result({"status": "ok ✅", "tool": "read_file", "content": "hello 🚀"})

    assert "✅" not in rendered
    assert "🚀" not in rendered
    assert "hello" in rendered


def test_status_is_silent_when_stderr_is_not_tty(capsys) -> None:
    with status("正在请求模型 🚀"):
        pass

    captured = capsys.readouterr()
    assert captured.err == ""
