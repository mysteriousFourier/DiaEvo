from __future__ import annotations

import queue
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .prompt_bar import (
    _completion_items,
    _cursor_to_bottom,
    _cursor_to_input,
    _erase_lines,
    _matching_commands,
    _menu_match_count,
    _menu_completion_value,
    _move_menu_selection,
    _prompt_style,
    _prompt_toolkit_enabled,
    _prompt_stdout_patch,
    _should_complete_menu_selection,
    _submit_value,
    render_prompt_state,
)
from .cli_style import DIM, GLYPHS, RESET

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
    plan: bool = False
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
        self.plan_mode = False
        self.queued_preview: list[str] = []
        self._rendered_lines = 0
        self._rendered_lines_above_input = 0
        self._rendered_value = ""
        self._rendered_cursor_index = 0
        self._lock = threading.Lock()
        self._render_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._toolkit_thread: threading.Thread | None = None
        self._toolkit_session: Any | None = None
        self._toolkit_stop_requested = threading.Event()
        self._toolkit_preferred = False
        self._toolkit_mode = False
        self.status_line = ""
        self._status_renderer: Callable[[], str] | None = None

    @property
    def available(self) -> bool:
        return sys.stdin.isatty()

    @property
    def listener_available(self) -> bool:
        return msvcrt is not None and sys.stdin.isatty()

    def start(self, *, listen: bool = True, toolkit: bool = False) -> bool:
        if not self.available:
            return False
        self.active.set()
        self._toolkit_preferred = toolkit and _prompt_toolkit_enabled()
        if self._toolkit_preferred and self._start_toolkit_worker():
            return True
        if listen and self.listener_available:
            with self._lock:
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._worker, daemon=True)
                    self._thread.start()
        return True

    def stop(self, enabled: bool) -> None:
        if not enabled:
            return
        self.active.clear()
        self._stop_toolkit_worker()
        with self._render_lock:
            self._toolkit_preferred = False
            self.begin_output()
            self.interrupt_event.clear()
            self.force_terminate_event.clear()
            self.prompt_visible.clear()
            self.status_visible.clear()
            self.draft = ""
            self.cursor_index = 0
            self.selected_index = 0
            self.plan_mode = False
            self.queued_preview.clear()
            self.status_line = ""
            self._status_renderer = None
            self._rendered_lines = 0
            self._rendered_lines_above_input = 0
            self._rendered_value = ""
            self._rendered_cursor_index = 0

    @contextmanager
    def session(self, *, listen: bool = True, toolkit: bool = False) -> Iterator[None]:
        enabled = self.start(listen=listen, toolkit=toolkit)
        try:
            yield
        finally:
            self.stop(enabled)

    @contextmanager
    def pause(self) -> Iterator[None]:
        resume_toolkit = self._toolkit_mode and self.active.is_set()
        self.paused.set()
        if resume_toolkit:
            self._stop_toolkit_worker()
        try:
            yield
        finally:
            self.paused.clear()
            if resume_toolkit and self.active.is_set() and not self._toolkit_mode:
                self._start_toolkit_worker()

    def begin_output(self) -> None:
        if self._toolkit_mode:
            self._stop_toolkit_worker()
            self.prompt_visible.clear()
            return
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
        if self._toolkit_preferred and not self._toolkit_mode:
            if self._start_toolkit_worker():
                self.prompt_visible.set()
                return
        if self._toolkit_mode:
            self.prompt_visible.set()
            self._invalidate_toolkit()
            return
        with self._render_lock:
            if self.prompt_visible.is_set() and not force:
                return
            self._render_prompt_line(label=label)

    def update_status_line(self, text: str) -> None:
        if not self.available or self.paused.is_set():
            return
        clean = str(text).replace("\n", " ").strip()
        self.status_line = clean
        if self._toolkit_mode:
            self.status_visible.set()
            self._invalidate_toolkit()
            return
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
        self.status_line = ""
        self._status_renderer = None
        if not self.available or self.paused.is_set():
            return
        if self._toolkit_mode:
            self.status_visible.clear()
            self._invalidate_toolkit()
            return
        with self._render_lock:
            if not self.prompt_visible.is_set() or not self.status_visible.is_set():
                return
            self._erase_rendered_prompt()
            sys.stdout.write("\033[1A\r\033[2K")
            self.status_visible.clear()
            self._render_prompt_line()

    def set_status_line_renderer(self, renderer: Callable[[], str] | None) -> None:
        self._status_renderer = renderer
        if renderer is not None:
            self.status_line = self._current_status_line()
        if self._toolkit_mode:
            self.status_visible.set()
            self._invalidate_toolkit()

    def _current_status_line(self) -> str:
        renderer = self._status_renderer
        if renderer is None:
            return self.status_line
        try:
            return str(renderer()).replace("\n", " ").strip()
        except Exception:
            return self.status_line

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

    def _start_toolkit_worker(self) -> bool:
        with self._lock:
            if self._toolkit_thread is not None and self._toolkit_thread.is_alive():
                self._toolkit_mode = True
                return True
            try:
                self._toolkit_session = self._create_toolkit_session()
            except Exception:
                self._toolkit_session = None
                self._toolkit_mode = False
                return False
            self._toolkit_stop_requested.clear()
            self._toolkit_mode = True
            self._toolkit_thread = threading.Thread(target=self._toolkit_worker, daemon=True)
            self._toolkit_thread.start()
        return True

    def _stop_toolkit_worker(self) -> None:
        if not self._toolkit_mode:
            return
        self._toolkit_stop_requested.set()
        self._abort_toolkit_prompt()
        thread = self._toolkit_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        if thread is not None and thread.is_alive():
            return
        self._toolkit_mode = False
        self._toolkit_thread = None
        self._toolkit_session = None

    def _create_toolkit_session(self) -> Any:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.completion import CompleteEvent
        from prompt_toolkit.document import Document
        from prompt_toolkit.key_binding import KeyBindings

        controller = self

        class DiaEvoCompleter(Completer):
            def get_completions(self, document: Document, complete_event: CompleteEvent):
                text = document.text_before_cursor
                if not text.startswith("/") or "\n" in text:
                    return
                for value, description in _completion_items(text):
                    yield Completion(value, start_position=-len(text), display=value, display_meta=description)

        bindings = KeyBindings()

        @bindings.add("c-c")
        def _ignore_ctrl_c(event) -> None:
            event.app.invalidate()

        @bindings.add("escape")
        def _escape_interrupt(event) -> None:
            controller._queue_toolkit_escape(event.app.current_buffer.text)
            event.app.current_buffer.reset()
            event.app.invalidate()

        @bindings.add("s-tab")
        def _toggle_plan_mode(event) -> None:
            controller.toggle_plan_mode()
            event.app.invalidate()

        return PromptSession(
            message=f"{GLYPHS['prompt']} ",
            completer=DiaEvoCompleter(),
            complete_while_typing=True,
            bottom_toolbar=self._render_toolkit_toolbar,
            reserve_space_for_menu=8,
            style=_prompt_style(),
            refresh_interval=0.2,
            key_bindings=bindings,
            erase_when_done=True,
        )

    def _toolkit_worker(self) -> None:
        session = self._toolkit_session
        while session is not None and self.active.is_set() and not self._toolkit_stop_requested.is_set():
            try:
                with _prompt_stdout_patch():
                    text = session.prompt(pre_run=self._toolkit_prompt_pre_run)
            except (EOFError, KeyboardInterrupt):
                if self._toolkit_stop_requested.is_set() or not self.active.is_set():
                    break
                continue
            except Exception:
                break
            self._queue_toolkit_submission(text)
        self._toolkit_mode = False

    def _toolkit_prompt_pre_run(self) -> None:
        if self._toolkit_stop_requested.is_set() or not self.active.is_set():
            self._exit_toolkit_app()

    def _abort_toolkit_prompt(self) -> None:
        self._exit_toolkit_app()
        self._invalidate_toolkit()

    def _exit_toolkit_app(self) -> None:
        session = self._toolkit_session
        app = getattr(session, "app", None)
        if app is None:
            return
        loop = getattr(app, "loop", None)

        def exit_app() -> None:
            try:
                app.exit(exception=EOFError)
            except Exception:
                return

        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(exit_app)
                return
            except Exception:
                pass
        exit_app()

    def _invalidate_toolkit(self) -> None:
        session = self._toolkit_session
        app = getattr(session, "app", None)
        if app is None:
            return
        try:
            app.invalidate()
        except Exception:
            return

    def _queue_toolkit_submission(self, text: str) -> None:
        value = _submit_value(str(text), 0).strip()
        if not value:
            return
        event = _event_from_text(value, interrupt=True, plan=self.plan_mode)
        self.queue.put(event)
        with self._render_lock:
            self.queued_preview.append(_preview_text(event.text if event.talk else value))
        self._invalidate_toolkit()

    def _queue_toolkit_escape(self, text: str) -> None:
        value = str(text).strip()
        if value:
            event = _event_from_text(value, interrupt=True, hard_interrupt=True)
        else:
            event = FlowInputEvent("", interrupt=True, hard_interrupt=True)
        self.queue.put(event)
        self.force_terminate_event.set()
        self.interrupt_event.set()
        if value:
            with self._render_lock:
                self.queued_preview.append(_preview_text(event.text if event.talk else value))
        self._invalidate_toolkit()

    def _render_toolkit_toolbar(self) -> str:
        pieces = []
        status_line = self._current_status_line()
        if status_line:
            pieces.append(status_line)
        if self.queued_preview:
            pieces.append(self._render_queued_preview().replace(DIM, "").replace(RESET, ""))
        mode = "Plan" if self.plan_mode else "Act"
        pieces.append(f"Mode {mode} · Shift+Tab 切换 · Enter 发送 · Tab 补全 · Esc 中止 · /exit 退出")
        return "  ".join(pieces)

    def toggle_plan_mode(self) -> bool:
        with self._render_lock:
            self.plan_mode = not self.plan_mode
            if self._toolkit_mode:
                self._invalidate_toolkit()
            elif self.prompt_visible.is_set():
                self._render_prompt_line()
            return self.plan_mode

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
        event = _event_from_text(text, interrupt=True, plan=self.plan_mode)
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
            if key in {"\x0f", "Z"}:
                self.plan_mode = not self.plan_mode
                self._render_prompt_line()
            elif match_count and key == "H":
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
    plan: bool = False,
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
        plan=plan,
        source=source,
        reply_to_user_id=reply_to_user_id,
    )


def _preview_text(text: str, limit: int = 72) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."
