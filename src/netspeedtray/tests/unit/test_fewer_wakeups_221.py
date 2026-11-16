"""2.2.1: background threads wait ONCE per period instead of waking in short slices.

The stats monitor thread used to sleep its period in 0.1 s slices (10 wake-ups a second at any update
rate) and the database worker woke from a 0.5 s get() timeout twice a second while idle. Both now
block on one interruptible wait, so these tests pin the wait count AND that stop() still ends the
wait at once.
"""
import threading
import time
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QThread

from netspeedtray import constants
from netspeedtray.core import monitor_thread as module
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.monitor_thread import StatsMonitorThread


# --- the stats monitor thread ---------------------------------------------------------------------

class _CountingStop:
    """Stands in for the stop event: records every wait and stops after ``polls`` of them."""

    def __init__(self, polls):
        self.polls = polls
        self.waits = []
        self._set = False

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if len(self.waits) >= self.polls:
            self._set = True
        return self._set

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def clear(self):
        self._set = False


def _cheap_thread(monkeypatch, interval=1.0):
    """A real StatsMonitorThread whose poll does nothing expensive (no PDH/WMI/COM)."""
    thread = StatsMonitorThread(interval=interval, config={})
    thread._network_identity_monitor = Mock()
    thread._init_com = Mock()
    thread._update_lhm_notice = Mock()
    thread._collect_power_stats = Mock()
    for name in ("_cleanup_gpu_query", "_cleanup_thermal_query", "_cleanup_power_query",
                 "_cleanup_ohm_wmi", "_cleanup_com"):
        monkeypatch.setattr(thread, name, Mock())
    monkeypatch.setattr(module, "is_rdp_session", lambda: False)
    monkeypatch.setattr(module, "hardware_collection_gates", lambda _config, _force: (False, False))
    monkeypatch.setattr(module, "memory_collection_gates", lambda _config, _force: (False, False))
    monkeypatch.setattr(module.psutil, "net_io_counters", lambda **_kwargs: {})
    monkeypatch.setattr(module.psutil, "cpu_percent", lambda **_kwargs: 0)
    return thread


def _record_slices(monkeypatch, thread):
    """time.sleep in the monitor module, recorded - and ending the loop, so the pre-2.2.1 slice loop
    fails these tests instead of spinning forever."""
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        thread._is_running = False

    monkeypatch.setattr(module.time, "sleep", sleep)
    return sleeps


def test_the_monitor_loop_waits_once_per_period_and_never_slices(q_app, monkeypatch):
    thread = _cheap_thread(monkeypatch, interval=1.0)
    sleeps = _record_slices(monkeypatch, thread)
    stop = _CountingStop(polls=3)
    thread._stop_event = stop
    polls = []
    thread.stats_ready.connect(lambda s: polls.append(s))
    thread.run()
    assert sleeps == []                              # the 0.1 s slice loop is gone
    assert stop.waits == [1.0, 1.0, 1.0]             # exactly one full-period wait per poll
    assert len(polls) == 3                           # ... and one poll per period: cadence unchanged


def test_the_error_backoff_is_still_one_wait(q_app, monkeypatch):
    thread = _cheap_thread(monkeypatch, interval=1.0)
    sleeps = _record_slices(monkeypatch, thread)
    thread._collect_power_stats = Mock(side_effect=RuntimeError("boom"))
    stop = _CountingStop(polls=2)
    thread._stop_event = stop
    thread.run()
    # 1 s * 2**errors, capped - the backoff period is unchanged, it is just waited in one go.
    assert sleeps == []
    assert stop.waits == [2.0, 4.0]


def test_a_rate_change_applies_from_the_next_period(q_app, monkeypatch):
    """set_interval does not cut the wait in progress short (as before): the NEXT wait uses it."""
    thread = _cheap_thread(monkeypatch, interval=1.0)
    sleeps = _record_slices(monkeypatch, thread)
    stop = _CountingStop(polls=3)
    real_wait = stop.wait

    def wait(timeout=None):
        if not stop.waits:
            thread.set_interval(0.5)                 # Settings applies 0.5 s mid-wait
        return real_wait(timeout)

    stop.wait = wait
    thread._stop_event = stop
    thread.run()
    assert sleeps == []
    assert stop.waits == [1.0, 0.5, 0.5]


def test_stop_ends_a_long_wait_at_once(q_app, monkeypatch):
    """At a 10 s rate a quit must not sit out the period (the slices used to bound it at 0.1 s)."""
    thread = _cheap_thread(monkeypatch, interval=10.0)
    polled = threading.Event()
    thread.stats_ready.connect(lambda _s: polled.set(),
                               type=__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ConnectionType.DirectConnection)
    thread.start()
    try:
        assert polled.wait(5.0)
        time.sleep(0.05)                             # into the 10 s wait
        t0 = time.monotonic()
        thread.stop()
        elapsed = time.monotonic() - t0
        assert thread.isFinished()
        assert elapsed < 0.5, f"stop() took {elapsed:.3f}s"
    finally:
        if thread.isRunning():
            thread.stop()


def test_clearing_is_running_from_another_thread_ends_the_wait(q_app, monkeypatch):
    """Tests (and any caller) that flip _is_running directly still stop the loop promptly."""
    thread = _cheap_thread(monkeypatch, interval=10.0)
    polled = threading.Event()
    thread.stats_ready.connect(lambda _s: polled.set(),
                               type=__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ConnectionType.DirectConnection)
    thread.start()
    try:
        assert polled.wait(5.0)
        t0 = time.monotonic()
        thread._is_running = False
        assert thread.wait(2000)
        assert time.monotonic() - t0 < 0.5
    finally:
        if thread.isRunning():
            thread.stop()


def test_is_running_works_on_a_thread_built_without_init():
    thread = StatsMonitorThread.__new__(StatsMonitorThread)
    QThread.__init__(thread)
    assert thread._is_running is True
    thread._is_running = False
    assert thread._is_running is False
    assert thread._wait_for_stop(5.0) is True       # already stopping: returns at once
    thread._is_running = True
    assert thread._is_running is True


# --- the database worker --------------------------------------------------------------------------

def test_db_worker_idle_timeout_blocks_unless_something_is_owed(tmp_path):
    w = DatabaseWorker(tmp_path / "speed_history.db")
    assert w._idle_timeout() is None                 # idle: block until a task / the stop sentinel
    w._compact_pending = True
    assert w._idle_timeout() == constants.timeouts.DB_IDLE_TICK_SEC   # the retry still gets its tick
    w._compact_pending = False
    w._stop_event.set()
    assert w._idle_timeout() == constants.timeouts.DB_IDLE_TICK_SEC   # draining: exit once empty


def test_db_worker_does_not_wake_while_idle_and_stops_promptly(tmp_path, q_app):
    w = DatabaseWorker(tmp_path / "speed_history.db")
    gets = []
    real_get = w._queue.get

    def counting_get(*args, **kwargs):
        gets.append(kwargs.get("timeout", args[1] if len(args) > 1 else None))
        return real_get(*args, **kwargs)

    w._queue.get = counting_get
    w.start()
    try:
        assert w.ready.wait(30)
        time.sleep(0.3)                              # let it reach the idle get()
        before = len(gets)
        time.sleep(1.3)                              # the old 0.5 s timeout woke it 2-3 times here
        assert len(gets) == before                   # still the same blocking call: no wake-ups
        assert gets[-1] is None
        t0 = time.monotonic()
        w.stop()
        assert w.wait(5000)
        assert time.monotonic() - t0 < 1.0           # the sentinel ends the block at once
    finally:
        if w.isRunning():
            w.stop()
            w.wait(5000)


def test_db_worker_drains_queued_tasks_before_exiting(tmp_path, q_app):
    w = DatabaseWorker(tmp_path / "speed_history.db")
    w.start()
    try:
        assert w.ready.wait(30)
        flags = [threading.Event() for _ in range(3)]
        for f in flags:
            w.enqueue_task("__signal__", f)
        w.stop()
        assert w.wait(5000)
        assert all(f.is_set() for f in flags)
    finally:
        if w.isRunning():
            w.stop()
            w.wait(5000)
