"""Simple interval-based scheduler for metadata collection tasks."""

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)


class CollectorScheduler:
    def __init__(self):
        self._tasks: list = []
        self._running = False
        self._threads: list = []

    def add(self, fn: Callable, interval_secs: int, name: str):
        self._tasks.append((fn, interval_secs, name))

    def _run_task(self, fn: Callable, interval_secs: int, name: str):
        while self._running:
            start = time.monotonic()
            try:
                fn()
            except Exception as e:
                logger.error("Task %s failed: %s", name, e)
            elapsed = time.monotonic() - start
            sleep_for = max(0, interval_secs - elapsed)
            time.sleep(sleep_for)

    def run_forever(self):
        self._running = True
        for fn, interval, name in self._tasks:
            t = threading.Thread(
                target=self._run_task,
                args=(fn, interval, name),
                name=f"collector-{name}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
        # Block main thread
        while self._running:
            time.sleep(1)

    def stop(self):
        self._running = False
