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

# run() reads the stop flag about once per loop. A regression that loops without ever waiting would
# spin an inline run() forever (an early draft of this file did, against the old code, and starved the
# PC overnight), so the fake stop event forces "stop" after this many reads and the test fails fast.
_MAX_CHECKS = 1000
_MAX_GETS = 10_000
_THREAD_STOP_MS = 5000


class _CountingStop:
    """Stands in for the stop event: records every wait and stops after ``polls`` of them."""

    def __init__(self, polls):
        self.polls = polls
        self.waits = []
        self._set = False
        self.checks = 0
        self.runaway = False

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if len(self.waits) >= self.polls:
            self._set = True
        return self._set

    def is_set(self):
        self.checks += 1
        if self.checks >= _MAX_CHECKS and not self._set:
            self.runaway = True                      # looping without waiting: stop it, fail below
            self._set = True
        return self._set

    def set(self):
        self._set = True

    def clear(self):
        self._set = False


class _CountingWake:
    """Stands in for the wake event the period wait blocks on; its waits are counted by ``stop``."""

    def __init__(self, stop):
        self.stop = stop
        self._set = False

    def wait(self, timeout=None):
        self.stop.wait(timeout)
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
    """time.sleep / QThread.msleep / sleep / usleep, recorded - and ending the loop, so the pre-2.2.1
    slice loop (or a regression to msleep(1000)) fails these tests at once instead of hanging."""
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        thread._is_running = False

    monkeypatch.setattr(module.time, "sleep", sleep)
    for name in ("msleep", "sleep", "usleep"):
        monkeypatch.setattr(thread, name, sleep, raising=False)
        try:
            monkeypatch.setattr(QThread, name, staticmethod(sleep))
        except (AttributeError, TypeError):
            pass                                     # a sip type that refuses it: the instance stub stands
    return sleeps


def _run_inline(thread, stop):
    thread._stop_event = stop
    thread._wake_event = _CountingWake(stop)
    thread.run()
    assert not stop.runaway, f"run() looped {stop.checks} times without waiting"


def _reap(thread, stop=None):
    """Never leave a QThread running (it aborts the interpreter at exit): stop, wait with a bound, and as
    a last resort terminate it - then fail loudly."""
    if not thread.isRunning():
        return
    (stop or thread.stop)()
    if thread.wait(_THREAD_STOP_MS):
        return
    thread.terminate()
    thread.wait(_THREAD_STOP_MS)
    pytest.fail(f"{type(thread).__name__} did not stop within {_THREAD_STOP_MS} ms (terminated)")


def test_the_monitor_loop_waits_once_per_period_and_never_slices(q_app, monkeypatch):
    thread = _cheap_thread(monkeypatch, interval=1.0)
    sleeps = _record_slices(monkeypatch, thread)
    stop = _CountingStop(polls=3)
    polls = []
    thread.stats_ready.connect(lambda s: polls.append(s))
    _run_inline(thread, stop)
    assert sleeps == []                              # the 0.1 s slice loop is gone
    assert stop.waits == [1.0, 1.0, 1.0]             # exactly one full-period wait per poll
    assert len(polls) == 3                           # ... and one poll per period: cadence unchanged


def test_the_error_backoff_is_still_one_wait(q_app, monkeypatch):
    thread = _cheap_thread(monkeypatch, interval=1.0)
    sleeps = _record_slices(monkeypatch, thread)
    thread._collect_power_stats = Mock(side_effect=RuntimeError("boom"))
    stop = _CountingStop(polls=2)
    _run_inline(thread, stop)
    # 1 s * 2**errors, capped - the backoff period is unchanged, it is just waited in one go.
    assert sleeps == []
    assert stop.waits == [2.0, 4.0]


def test_a_rate_change_applies_to_the_wait_in_progress(q_app, monkeypatch):
    """R3 (2.2.1 beta.1 readiness): set_interval wakes the wait in progress and the rest of the period
    is re-measured at the new rate - 10 s -> 0.5 s used to apply only after the 10 s wait."""
    thread = _cheap_thread(monkeypatch, interval=1.0)
    sleeps = _record_slices(monkeypatch, thread)
    stop = _CountingStop(polls=3)
    real_wait = stop.wait
    polls = []
    thread.stats_ready.connect(lambda s: polls.append(len(stop.waits)))

    def wait(timeout=None):
        if not stop.waits:
            thread.set_interval(0.5)                 # Settings applies 0.5 s mid-wait
        return real_wait(timeout)

    stop.wait = wait
    _run_inline(thread, stop)
    assert sleeps == []
    assert stop.waits[0] == 1.0
    assert 0 <= stop.waits[1] <= 0.5                 # the rest of the period at the NEW rate ...
    assert stop.waits[2] == 0.5                      # ... and full new periods after it
    assert polls == [0, 2]                           # the woken wait did not poll early (or stop)


def test_a_rate_change_never_reads_as_stop(q_app, monkeypatch):
    thread = _cheap_thread(monkeypatch, interval=1.0)
    _record_slices(monkeypatch, thread)
    stop = _CountingStop(polls=4)
    real_wait = stop.wait

    def wait(timeout=None):
        thread.set_interval(1.0 if thread.interval != 1.0 else 2.0)   # a change on every wait
        return real_wait(timeout)

    stop.wait = wait
    _run_inline(thread, stop)
    assert len(stop.waits) == 4 and thread._is_running is False   # stopped by stop only


def test_a_live_rate_change_from_10s_applies_within_a_second(q_app, monkeypatch):
    """The real thread: at a 10 s rate, choosing a short one polls again at once - and stop still ends
    the loop promptly afterwards."""
    thread = _cheap_thread(monkeypatch, interval=10.0)
    polled = []
    first = threading.Event()
    second = threading.Event()
    third = threading.Event()

    def on_poll(_s):
        polled.append(time.monotonic())
        (first if len(polled) == 1 else second if len(polled) == 2 else third).set()

    thread.stats_ready.connect(on_poll,
                               type=__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ConnectionType.DirectConnection)
    thread.start()
    try:
        assert first.wait(5.0)
        time.sleep(0.05)                             # into the 10 s wait
        t0 = time.monotonic()
        thread.set_interval(0.2)
        assert second.wait(2.0), "the new rate waited out the old 10 s period"
        assert third.wait(2.0)                       # and it keeps polling: not mistaken for stop
        assert thread.isRunning()
        t1 = time.monotonic()
        thread.stop()
        assert thread.isFinished()
        assert time.monotonic() - t1 < constants.timeouts.MONITOR_THREAD_STOP_WAIT_MS / 1000.0
        assert polled[1] - t0 < 2.0
    finally:
        _reap(thread)


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
        _reap(thread)


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
        _reap(thread)


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
        if len(gets) > _MAX_GETS:                    # a get() that never blocks: end the worker, fail below
            raise RuntimeError("DatabaseWorker get() spinning")
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
        _reap(w)


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
        _reap(w)
