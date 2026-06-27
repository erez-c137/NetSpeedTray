"""
DatabaseWorker queue mechanics (H2) + flush barrier (H3).

H2: the worker uses a blocking queue.Queue (no 100ms busy-poll) and drains pending tasks
before honoring stop. H3: a "__signal__" task is a FIFO flush barrier — once it fires, every
persist enqueued before it is on disk, so a reader sees freshly-flushed samples.
"""
import threading

import pytest

from netspeedtray.core.database import DatabaseWorker


def test_signal_task_sets_the_event_immediately(tmp_path):
    worker = DatabaseWorker(tmp_path / "x.db")
    ev = threading.Event()
    worker._execute_task("__signal__", ev)
    assert ev.is_set(), "the __signal__ barrier task must set its event"


def test_running_worker_drains_queue_and_fires_barrier(tmp_path, qtbot=None):
    worker = DatabaseWorker(tmp_path / "hist.db")
    worker.start()
    try:
        # Give the worker a moment to initialise its connection/schema.
        ready = threading.Event()
        # The barrier proves the worker reached and processed an enqueued task in order.
        barrier = threading.Event()
        worker.enqueue_task("__signal__", ready)
        assert ready.wait(3.0), "worker did not start / process tasks"

        worker.enqueue_task("__signal__", barrier)
        assert barrier.wait(2.0), "FIFO barrier signal never fired"
    finally:
        worker.stop()
        worker.wait(3000)
        assert not worker.isRunning(), "worker must stop cleanly after draining"


def test_flush_and_wait_short_circuits_when_worker_not_running(tmp_path):
    """If the worker thread isn't running, flush_and_wait must NOT burn its timeout."""
    import time
    from unittest.mock import MagicMock

    # Minimal stand-in: a WidgetState-like object exercising the real method.
    from netspeedtray.core.widget_state import WidgetState

    ws = WidgetState.__new__(WidgetState)
    ws.logger = MagicMock()
    ws._db_batch = []
    ws._hw_batch = []
    ws.db_worker = MagicMock()
    ws.db_worker.isRunning.return_value = False

    start = time.monotonic()
    ws.flush_and_wait(timeout=2.0)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, "must return immediately when the worker isn't running"
    ws.db_worker.enqueue_task.assert_not_called()
