from __future__ import annotations

import itertools
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from .output_policy import sanitize_no_emoji


class TerminalStatus:
    def __init__(self, message: str, *, interval: float = 0.12) -> None:
        self.message = sanitize_no_emoji(message)
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = bool(sys.stderr.isatty())

    def __enter__(self) -> "TerminalStatus":
        if not self._enabled:
            return self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if not self._enabled:
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        sys.stderr.write("\r\033[2K")
        sys.stderr.flush()

    def update(self, message: str) -> None:
        self.message = sanitize_no_emoji(message)

    def _run(self) -> None:
        for frame in itertools.cycle("-\\|/"):
            if self._stop.is_set():
                return
            sys.stderr.write(f"\r\033[2K{frame} {self.message}")
            sys.stderr.flush()
            time.sleep(self.interval)


@contextmanager
def status(message: str) -> Iterator[TerminalStatus]:
    indicator = TerminalStatus(message)
    with indicator:
        yield indicator
