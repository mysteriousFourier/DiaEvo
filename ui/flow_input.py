from __future__ import annotations

import queue
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .prompt_bar import (
    _cursor_to_bottom,
    _cursor_to_input,
    _erase_lines,
    _matching_commands,
    _menu_match_count,
    _menu_completion_value,
    _move_menu_selection,
    _should_complete_menu_selection,
    _submit_value,
    render_prompt_state,
)
from .cli_style import DIM, RESET

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows is the primary target.
    msvcrt = None


@dataclass(frozen=True)
class FlowInputEvent:
    text: str
    interrupt: bool = False
    talk: bool = False
    hard_interrupt: bool = False
    source: str = field(default="", compare=False)
    reply_to_user_id: str = field(default="", compare=False)


class FlowInputController:
    """Keeps the bottom draft prompt alive while model/tool work is running."""

    def __init__(self) -> None:
        self.queue: "queue.Queue[FlowInputEvent]" = queue.Queue()
        self.active = threading.Event()
        self.paused = threading.Event()
        self.interrupt_event = threading.Event()
        self.force_terminate_event = threading.Event()
        self.prompt_visible = threading.Event()
        self.status_visible = threading.Event()
        self.draft = ""
        self.cursor_index = 0
        self.selected_index = 0
        self.queued_preview: list[str] = []
        self._rendered_lines = 0
        self._rendered_lines_above_input = 0
        self._rendered_value = ""
        self._rendered_cursor_index = 0
        self._lock = threading.Lock()
        self._render_lock = threading.RLock()
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return sys.stdin.isatty()

    @property
    def listener_available(self) -> bool:
        return msvcrt is not None and sys.stdin.isatty()

    def start(self, *, listen: bool = True) -> bool:
        if not self.available:
            return False
        if listen and self.listener_available:
            with self._lock:
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._worker, daemon=True)
                    self._thread.start()
        self.active.set()
        return True

    def stop(self, enabled: bool) -> None:
        if not enabled:
            return
        with self._render_lock:
            self.active.clear()
            self.begin_output()
            self.interrupt_event.clear()
            self.force_terminate_event.clear()
            self.prompt_visible.clear()
            self.status_visible.clear()
            self.draft = ""
            self.cursor_index = 0
            self.selected_index = 0
            self.queued_preview.clear()
            self._rendered_lines = 0
            self._rendered_lines_above_input = 0
            self._rendered_value = ""
            self._rendered_cursor_index = 0

    @contextmanager
    def session(self, *, listen: bool = True) -> Iterator[None]:
        enabled = self.start(listen=listen)
        try:
            yield
        finally:
            self.stop(enabled)

    @contextmanager
    def pause(self) -> Iterator[None]:
        self.paused.set()
        try:
            yield
        finally:
            self.paused.clear()

    def begin_output(self) -> None:
        with self._render_lock:
            if self.prompt_visible.is_set() and self._rendered_lines:
                sys.stdout.write(
                    _cursor_to_bottom(
                        self._rendered_lines,
                        self._rendered_value,
                        self._rendered_lines_above_input,
                        self._rendered_cursor_index,
                    )
                )
                _erase_lines(self._rendered_lines)
            if self.status_visible.is_set():
                sys.stdout.write("\033[1A\r\033[2K")
                self.status_visible.clear()
            sys.stdout.flush()
            self.prompt_visible.clear()
            self._rendered_lines = 0
            self._rendered_lines_above_input = 0
            self._rendered_value = ""
            self._rendered_cursor_index = 0

    def show_prompt(self, label: str = "", *, force: bool = False) -> None:
        if not self.available or self.paused.is_set():
            return
        with self._render_lock:
            if self.prompt_visible.is_set() and not force:
                return
            self._render_prompt_line(label=label)

    def update_status_line(self, text: str) -> None:
        if not self.available or self.paused.is_set():
            return
        clean = str(text).replace("\n", " ").strip()
        with self._render_lock:
            if not self.prompt_visible.is_set():
                return
            if not self.status_visible.is_set():
                self._erase_rendered_prompt()
                sys.stdout.write(clean)
                sys.stdout.write("\n")
                self.status_visible.set()
                self._render_prompt_line()
                return
            self._erase_rendered_prompt()
            sys.stdout.write("\033[1A\r\033[2K")
            sys.stdout.write(clean)
            sys.stdout.write("\n")
            self._render_prompt_line()

    def clear_status_line(self) -> None:
        if not self.available or self.paused.is_set():
            return
        with self._render_lock:
            if not self.prompt_visible.is_set() or not self.status_visible.is_set():
                return
            self._erase_rendered_prompt()
            sys.stdout.write("\033[1A\r\033[2K")
            self.status_visible.clear()
            self._render_prompt_line()

    def _render_prompt_line(self, label: str = "") -> None:
        if self._rendered_lines:
            self._erase_rendered_prompt()
        rendered = render_prompt_state(self.draft, self.selected_index)
        preview = self._render_queued_preview()
        lines_above_input = 0
        if preview:
            rendered = f"{preview}\n{rendered}"
            lines_above_input = preview.count("\n") + 1
        self._rendered_lines = rendered.count("\n") + 1
        self._rendered_lines_above_input = lines_above_input
        self._rendered_value = self.draft
        self._rendered_cursor_index = self.cursor_index
        if label:
            sys.stdout.write(f"{label} ")
        sys.stdout.write(rendered)
        sys.stdout.write(
            _cursor_to_input(
                self._rendered_lines,
                self.draft,
                self._rendered_lines_above_input,
                self.cursor_index,
            )
        )
        sys.stdout.flush()
        self.prompt_visible.set()

    def _erase_rendered_prompt(self) -> None:
        if self._rendered_lines:
            sys.stdout.write(
                _cursor_to_bottom(
                    self._rendered_lines,
                    self._rendered_value,
                    self._rendered_lines_above_input,
                    self._rendered_cursor_index,
                )
            )
            _erase_lines(self._rendered_lines)
            self._rendered_lines = 0
            self._rendered_lines_above_input = 0
            self._rendered_value = ""
            self._rendered_cursor_index = 0
            return
        sys.stdout.write("\r\033[2K")

    def drain(self) -> list[FlowInputEvent]:
        events: list[FlowInputEvent] = []
        while True:
            try:
                events.append(self.queue.get_nowait())
            except queue.Empty:
                if events:
                    with self._render_lock:
                        self.queued_preview = self.queued_preview[len(events) :]
                return events

    def handle_queued(
        self,
        messages: list[dict[str, object]],
        talk_handler: Callable[[FlowInputEvent], None],
    ) -> bool:
        interrupted = False
        for event in self.drain():
            if event.hard_interrupt and not event.text:
                interrupted = True
                continue
            if event.talk:
                talk_handler(event)
                continue
            if event.text:
                messages.append({"role": "user", "content": event.text})
            if event.interrupt:
                interrupted = True
        return interrupted

    def handle_talk_queued(self, talk_handler: Callable[[FlowInputEvent], None]) -> int:
        talk_events: list[FlowInputEvent] = []
        with self.queue.mutex:
            kept = self.queue.queue.__class__()
            while self.queue.queue:
                event = self.queue.queue.popleft()
                if event.talk:
                    talk_events.append(event)
                else:
                    kept.append(event)
            self.queue.queue.extend(kept)
        for event in talk_events:
            if event.text:
                talk_handler(event)
        if talk_events:
            with self._render_lock:
                for event in talk_events:
                    self._remove_queued_preview_for_event(event)
        return len(talk_events)

    def queue_external_text(self, text: str, *, source: str = "", reply_to_user_id: str = "") -> FlowInputEvent:
        event = _event_from_text(
            text.strip(),
            interrupt=True,
            source=source,
            reply_to_user_id=reply_to_user_id,
        )
        self.queue.put(event)
        if event.interrupt or event.hard_interrupt:
            self.force_terminate_event.set()
            self.interrupt_event.set()
        preview = _preview_text(event.text if event.talk else text.strip())
        if source:
            preview = f"{source} {preview}".strip()
        with self._render_lock:
            self.queued_preview.append(preview)
            self.show_prompt(force=True)
        return event

    def _worker(self) -> None:
        while True:
            if not self.active.is_set() or msvcrt is None:
                time.sleep(0.05)
                continue
            if self.paused.is_set() or not msvcrt.kbhit():
                time.sleep(0.05)
                continue

            char = msvcrt.getwch()
            if char in {"\x00", "\xe0"}:
                key = msvcrt.getwch()
                self._handle_extended_key(key)
                continue
            self._handle_character(char)

    def _handle_character(self, char: str) -> None:
        if char == "\003":
            return
        if char == "\x1b":
            self._queue_escape_interrupt()
            return
        if char in {"\r", "\n"}:
            self._queue_enter()
            return
        if char == "\b":
            self._backspace()
            return
        if char == "\t":
            self._complete_selected_command()
            return
        if char.isprintable():
            self._append_printable(char)

    def _queue_enter(self) -> None:
        if _should_complete_menu_selection(self.draft, self.selected_index):
            with self._render_lock:
                self.draft = _menu_completion_value(self.draft, self.selected_index)
                self.cursor_index = len(self.draft)
                self.selected_index = 0
                self._render_prompt_line()
            return
        text = _submit_value(self.draft, self.selected_index).strip()
        if not text:
            return
        event = _event_from_text(text, interrupt=True)
        self.queue.put(event)
        with self._render_lock:
            self.queued_preview.append(_preview_text(event.text if event.talk else text))
            self.draft = ""
            self.cursor_index = 0
            self.selected_index = 0
            self.begin_output()
            if event.talk:
                self.show_prompt(force=True)

    def _queue_interrupt(self, *, hard: bool) -> None:
        payload = self.draft.strip()
        self.queue.put(FlowInputEvent(payload, interrupt=True, hard_interrupt=hard))
        self.force_terminate_event.set()
        self.interrupt_event.set()

    def _queue_escape_interrupt(self) -> None:
        if _matching_commands(self.draft):
            with self._render_lock:
                self.draft = ""
                self.cursor_index = 0
                self.selected_index = 0
                self._render_prompt_line()
            return

        text = self.draft.strip()
        if text:
            self.queue.put(_event_from_text(text, interrupt=True, hard_interrupt=True))
            self.force_terminate_event.set()
            self.interrupt_event.set()
            with self._render_lock:
                self.draft = ""
                self.cursor_index = 0
                self.selected_index = 0
                self.begin_output()
                sys.stdout.write("已终止当前模型请求；新输入已排队。\n")
                self.show_prompt(force=True)
            return

        self.queue.put(FlowInputEvent("", interrupt=True, hard_interrupt=True))
        self.force_terminate_event.set()
        self.interrupt_event.set()
        with self._render_lock:
            self.begin_output()
            sys.stdout.write("已终止当前模型请求；可直接输入下一条。\n")
            self.show_prompt(force=True)

    def _append_printable(self, char: str) -> None:
        with self._render_lock:
            self.cursor_index = max(0, min(self.cursor_index, len(self.draft)))
            self.draft = self.draft[: self.cursor_index] + char + self.draft[self.cursor_index :]
            self.cursor_index += len(char)
            self.selected_index = 0
            self._render_prompt_line()

    def _handle_extended_key(self, key: str) -> None:
        matches = _matching_commands(self.draft)
        match_count = _menu_match_count(self.draft)
        with self._render_lock:
            if match_count and key == "H":
                self.selected_index = _move_menu_selection(self.selected_index, match_count, -1)
                self._render_prompt_line()
            elif match_count and key == "P":
                self.selected_index = _move_menu_selection(self.selected_index, match_count, 1)
                self._render_prompt_line()
            elif key == "K":
                self.cursor_index = max(0, self.cursor_index - 1)
                self.selected_index = 0
                self._render_prompt_line()
            elif key == "M":
                self.cursor_index = min(len(self.draft), self.cursor_index + 1)
                self.selected_index = 0
                self._render_prompt_line()
            elif key == "G":
                self.cursor_index = 0
                self.selected_index = 0
                self._render_prompt_line()
            elif key == "O":
                self.cursor_index = len(self.draft)
                self.selected_index = 0
                self._render_prompt_line()
            elif key == "S" and self.cursor_index < len(self.draft):
                self.draft = self.draft[: self.cursor_index] + self.draft[self.cursor_index + 1 :]
                self.selected_index = 0
                self._render_prompt_line()

    def _backspace(self) -> None:
        with self._render_lock:
            if self.cursor_index <= 0:
                return
            self.cursor_index = max(0, min(self.cursor_index, len(self.draft)))
            self.draft = self.draft[: self.cursor_index - 1] + self.draft[self.cursor_index :]
            self.cursor_index -= 1
            self.selected_index = 0
            self._render_prompt_line()

    def _complete_selected_command(self) -> None:
        match_count = _menu_match_count(self.draft)
        if not match_count:
            return
        with self._render_lock:
            self.selected_index = max(0, min(self.selected_index, match_count - 1))
            self.draft = _menu_completion_value(self.draft, self.selected_index)
            self.cursor_index = len(self.draft)
            self.selected_index = 0
            self._render_prompt_line()

    def _render_queued_preview(self) -> str:
        if not self.queued_preview:
            return ""
        count = len(self.queued_preview)
        latest = self.queued_preview[-1]
        return f"{DIM}排队 {count}  {latest}{RESET}"

    def _remove_queued_preview_for_event(self, event: FlowInputEvent) -> None:
        shown = _preview_text(event.text if event.talk else event.text)
        for index, preview in enumerate(self.queued_preview):
            if preview == shown or preview.endswith(f" {shown}"):
                del self.queued_preview[index]
                return


def _event_from_text(
    text: str,
    *,
    interrupt: bool,
    hard_interrupt: bool = False,
    source: str = "",
    reply_to_user_id: str = "",
) -> FlowInputEvent:
    talk = text.lower().startswith("/talk ")
    payload = text[6:].strip() if talk else text
    return FlowInputEvent(
        payload,
        interrupt=False if talk else interrupt,
        talk=talk,
        hard_interrupt=hard_interrupt,
        source=source,
        reply_to_user_id=reply_to_user_id,
    )


def _preview_text(text: str, limit: int = 72) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."
