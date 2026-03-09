"""
Unit tests for CollectorScheduler.

Verifies that registered tasks are called at the expected cadence
and that stop() terminates execution cleanly.
"""

import sys
import os
import time
import threading
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scheduler import CollectorScheduler


# ─── Task is called ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_task_is_called_at_least_once():
    called = threading.Event()

    def task():
        called.set()

    sched = CollectorScheduler()
    sched.add(task, interval_secs=1, name="test-task")

    t = threading.Thread(target=sched.run_forever, daemon=True)
    t.start()

    assert called.wait(timeout=5), "Task was not called within 5s"
    sched.stop()


@pytest.mark.unit
def test_task_is_called_multiple_times():
    counter = {"n": 0}
    lock = threading.Lock()

    def task():
        with lock:
            counter["n"] += 1

    sched = CollectorScheduler()
    sched.add(task, interval_secs=1, name="counter-task")

    t = threading.Thread(target=sched.run_forever, daemon=True)
    t.start()

    time.sleep(3.5)
    sched.stop()

    with lock:
        assert counter["n"] >= 2, f"Expected ≥2 calls, got {counter['n']}"


# ─── Multiple tasks run independently ────────────────────────────────────────

@pytest.mark.unit
def test_multiple_tasks_run_independently():
    events = {"a": threading.Event(), "b": threading.Event()}

    def task_a():
        events["a"].set()

    def task_b():
        events["b"].set()

    sched = CollectorScheduler()
    sched.add(task_a, interval_secs=1, name="task-a")
    sched.add(task_b, interval_secs=1, name="task-b")

    t = threading.Thread(target=sched.run_forever, daemon=True)
    t.start()

    assert events["a"].wait(timeout=5), "task_a not called"
    assert events["b"].wait(timeout=5), "task_b not called"
    sched.stop()


# ─── Exception in task does not kill scheduler ────────────────────────────────

@pytest.mark.unit
def test_exception_in_task_does_not_stop_scheduler():
    call_count = {"n": 0}
    lock = threading.Lock()

    def flaky_task():
        with lock:
            call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("intentional failure")

    sched = CollectorScheduler()
    sched.add(flaky_task, interval_secs=1, name="flaky")

    t = threading.Thread(target=sched.run_forever, daemon=True)
    t.start()

    time.sleep(3)
    sched.stop()

    with lock:
        assert call_count["n"] >= 2, "Scheduler stopped after first exception"


# ─── stop() terminates cleanly ───────────────────────────────────────────────

@pytest.mark.unit
def test_stop_terminates_scheduler():
    sched = CollectorScheduler()
    sched.add(lambda: None, interval_secs=1, name="noop")

    t = threading.Thread(target=sched.run_forever)
    t.start()

    time.sleep(0.5)
    sched.stop()
    t.join(timeout=5)

    assert not t.is_alive(), "Scheduler thread did not terminate after stop()"
