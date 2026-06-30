"""
DatabaseWorker queue mechanics (H2) + flush barrier (H3).

H2: the worker uses a blocking queue.Queue (no 100ms busy-poll) and drains pending tasks
before honoring stop. H3: a "__signal__" task is a FIFO flush barrier - once it fires, every
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


def test_retention_cutoff_never_crashes_on_keep_forever():
    """Regression: keep_data=36500 ('keep ~forever') made `now - timedelta(days=36500)` land in 1926,
    whose .timestamp() raises OSError(22) on Windows - which crashed the maintenance thread on startup
    and silently stopped ALL history writes. The arithmetic cutoff must floor at 0 without raising."""
    from datetime import datetime
    now = datetime.now()
    # Normal retention -> a sane, recent cutoff.
    assert DatabaseWorker._retention_cutoff(now, 365) > 0
    # The pre-1970 cases (keep-forever / absurd values) must clamp to 0, never raise.
    assert DatabaseWorker._retention_cutoff(now, 36500) == 0
    assert DatabaseWorker._retention_cutoff(now, 99_999_999) == 0
    # Cutoff is monotonic: a longer retention keeps more (smaller-or-equal cutoff).
    assert DatabaseWorker._retention_cutoff(now, 730) <= DatabaseWorker._retention_cutoff(now, 365)


def test_maintenance_survives_keep_forever_and_keeps_writing(tmp_path):
    """End-to-end: with keep_data=36500 a full maintenance pass must complete WITHOUT killing the
    worker, and a subsequent persist must still land on disk."""
    import sqlite3, threading, time
    from netspeedtray import constants
    worker = DatabaseWorker(tmp_path / "keep.db")
    worker.start()
    try:
        done = threading.Event()
        worker.enqueue_task("__signal__", done)
        assert done.wait(5.0), "worker failed to initialise"
        worker.enqueue_task("maintenance", {"keep_data": 36500})    # would crash the thread pre-fix
        worker.enqueue_task("persist_hardware", [(int(time.time()), "ram", 47.5)])
        flushed = threading.Event()
        worker.enqueue_task("__signal__", flushed)
        assert flushed.wait(5.0), "worker died during maintenance (the OSError-22 crash)"
    finally:
        worker.stop()
        worker.wait(3000)
    con = sqlite3.connect(tmp_path / "keep.db")
    n = con.execute(f"SELECT COUNT(*) FROM {constants.data.HARDWARE_STATS_TABLE_RAW} WHERE stat_type='ram'").fetchone()[0]
    con.close()
    assert n == 1, "the RAM sample must have persisted after maintenance"
