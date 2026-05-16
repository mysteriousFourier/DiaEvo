from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Literal

APP_TITLE = "DiaEvo"
RUNNING_FRAMES = ("-", "\\", "|", "/")
State = Literal["idle", "running", "confirmation", "completed"]


def render_title(state: State, *, focused: bool, frame_index: int = 0) -> str:
    if focused or state == "idle":
        return APP_TITLE
    if state == "confirmation":
        return f"{APP_TITLE} [!]"
    if state == "completed":
        return f"{APP_TITLE} ☖"
    frame = RUNNING_FRAMES[frame_index % len(RUNNING_FRAMES)]
    return f"{APP_TITLE} {frame}"


def _windows_title_writer() -> Callable[[str], None] | None:
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return None

    def write(title: str) -> None:
        try:
            kernel32.SetConsoleTitleW(str(title))
        except Exception:
            pass

    return write


def _windows_focus_provider() -> Callable[[], bool] | None:
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
    except Exception:
        return None

    def is_focused() -> bool:
        try:
            console_window = kernel32.GetConsoleWindow()
            foreground_window = user32.GetForegroundWindow()
            if console_window and foreground_window == console_window:
                return True

            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(foreground_window, ctypes.byref(process_id))
            if process_id.value == os.getpid():
                return True

            title_buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(foreground_window, title_buffer, len(title_buffer))
            return APP_TITLE in title_buffer.value
        except Exception:
            return True

    return is_focused


class WindowTitleManager:
    def __init__(
        self,
        *,
        writer: Callable[[str], None] | None = None,
        focus_provider: Callable[[], bool] | None = None,
        interval: float = 0.25,
    ) -> None:
        self._writer = writer if writer is not None else _windows_title_writer()
        self._focus_provider = focus_provider if focus_provider is not None else _windows_focus_provider()
        self._interval = interval
        self._state: State = "idle"
        self._enabled = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_title = ""

    def start(self) -> None:
        if self._writer is None:
            return
        with self._lock:
            self._enabled = True
        self._write(APP_TITLE)
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._enabled = False
            self._state = "idle"
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        self._write(APP_TITLE)

    def set_state(self, state: State) -> None:
        with self._lock:
            if not self._enabled:
                return
            self._state = state
        self._refresh()

    @contextmanager
    def activity(self, state: State = "running") -> Iterator[None]:
        with self._lock:
            previous_state = self._state
        self.set_state(state)
        try:
            yield
        finally:
            self.set_state("completed" if previous_state == "idle" else previous_state)

    def _run(self) -> None:
        frame_index = 0
        while not self._stop.is_set():
            self._refresh(frame_index)
            frame_index += 1
            time.sleep(self._interval)

    def _refresh(self, frame_index: int = 0) -> None:
        if self._writer is None:
            return
        with self._lock:
            if not self._enabled:
                return
            state = self._state
        focused = self._is_focused()
        if focused and state == "completed":
            with self._lock:
                self._state = "idle"
                state = "idle"
        self._write(render_title(state, focused=focused, frame_index=frame_index))

    def _is_focused(self) -> bool:
        if self._focus_provider is None:
            return True
        try:
            return bool(self._focus_provider())
        except Exception:
            return True

    def _write(self, title: str) -> None:
        if self._writer is None or title == self._last_title:
            return
        self._last_title = title
        self._writer(title)


TITLE_MANAGER = WindowTitleManager()


def start_title_monitor() -> None:
    TITLE_MANAGER.start()


def stop_title_monitor() -> None:
    TITLE_MANAGER.stop()


def set_title_state(state: State) -> None:
    TITLE_MANAGER.set_state(state)


def title_activity(state: State = "running") -> Iterator[None]:
    return TITLE_MANAGER.activity(state)
