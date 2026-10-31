"""
DatabaseWorker queue mechanics (H2) + flush barrier (H3).

H2: the worker uses a blocking queue.Queue (no 100ms busy-poll) and drains pending tasks
before honoring stop. H3: a "__signal__" task is a FIFO flush barrier - once it fires, every
persist enqueued before it is on disk, so a reader sees freshly-flushed samples.
"""
import threading

import pytest

from netspeedtray.core.database import DatabaseWorker

# Generous deadlines for the running-thread tests. Nothing here asserts on SPEED - only that the
# worker opens the file, serves tasks in FIFO order and stops. The worker's start-up (create the v8
# schema, switch to WAL, and up to _INIT_ATTEMPTS retries with backoff, each waiting up to
# DB_INIT_LOCK_TIMEOUT_MS for a lock) legitimately takes seconds on a loaded CI runner; the old
# 2-5 s waits on a just-started QThread failed there (#448's CI run) and passed on rerun. A wait
# that succeeds returns at once, so these only cost time when the test is failing anyway.
_START_DEADLINE_S = 60.0
_TASK_DEADLINE_S = 30.0
_STOP_DEADLINE_MS = 30_000


def _start_writable(worker: DatabaseWorker) -> None:
    """Start the worker and wait on the condition itself - its ``ready`` event, set once storage
    initialization has finished (whatever the outcome) - then require a WRITABLE session, so a
    start-up that fell back to read-only fails here with a clear message instead of as a barrier
    timeout or a missing row."""
    worker.start()
    assert worker.ready.wait(_START_DEADLINE_S), "worker never finished initializing its storage"
    assert worker._writer is not None, (
        f"worker started read-only (migration_deferred={worker._migration_deferred!r}, "
        f"schema_incompatible={worker._schema_incompatible})")


def _stop(worker: DatabaseWorker) -> None:
    worker.stop()
    assert worker.wait(_STOP_DEADLINE_MS), "worker did not stop after draining its queue"


def test_signal_task_sets_the_event_immediately(tmp_path):
    worker = DatabaseWorker(tmp_path / "x.db")
    ev = threading.Event()
    worker._execute_task("__signal__", ev)
    assert ev.is_set(), "the __signal__ barrier task must set its event"


def test_running_worker_drains_queue_and_fires_barrier(tmp_path):
    worker = DatabaseWorker(tmp_path / "hist.db")
    try:
        _start_writable(worker)
        # The first barrier proves the running thread serves the queue at all; the second, enqueued
        # after it, that it keeps doing so in order.
        ready = threading.Event()
        barrier = threading.Event()
        worker.enqueue_task("__signal__", ready)
        assert ready.wait(_TASK_DEADLINE_S), "worker did not start / process tasks"

        worker.enqueue_task("__signal__", barrier)
        assert barrier.wait(_TASK_DEADLINE_S), "FIFO barrier signal never fired"
    finally:
        _stop(worker)
    assert not worker.isRunning(), "worker must stop cleanly after draining"


def test_flush_and_wait_short_circuits_when_worker_not_running(tmp_path):
    """If the worker thread isn't running, flush_and_wait must NOT burn its timeout."""
    import time
    from unittest.mock import MagicMock

    # Minimal stand-in: a WidgetState-like object exercising the real method.
    from netspeedtray.core.widget_state import WidgetState

    import threading
    ws = WidgetState.__new__(WidgetState)
    ws.logger = MagicMock()
    ws._read_only = False
    ws._batch_lock = threading.Lock()
    ws._net_batch = []
    ws._hw_batch = []
    ws.db_worker = MagicMock()
    ws.db_worker.isRunning.return_value = False

    start = time.monotonic()
    ws.flush_and_wait(timeout=2.0)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, "must return immediately when the worker isn't running"
    # The flush itself is handed over (v8: always, it carries the heartbeat); the barrier is not.
    ws.db_worker.enqueue_task.assert_called_once_with("persist_v8", ([], []))


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
    try:
        _start_writable(worker)
        done = threading.Event()
        worker.enqueue_task("__signal__", done)
        assert done.wait(_TASK_DEADLINE_S), "worker failed to initialize"
        worker.enqueue_task("maintenance", {"keep_data": 36500})    # would crash the thread pre-fix
        worker.enqueue_task("persist_v8", ([], [(int(time.time()), "ram", 47.5)]))
        flushed = threading.Event()
        worker.enqueue_task("__signal__", flushed)
        assert flushed.wait(_TASK_DEADLINE_S), "worker died during maintenance (the OSError-22 crash)"
    finally:
        _stop(worker)
    con = sqlite3.connect(tmp_path / "keep.db")
    n = con.execute(f"SELECT COUNT(*) FROM {constants.data.HARDWARE_STATS_TABLE_RAW} WHERE stat_type='ram'").fetchone()[0]
    con.close()
    assert n == 1, "the RAM sample must have persisted after maintenance"
