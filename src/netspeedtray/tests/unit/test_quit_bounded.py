"""
Quit must be bounded and must not crash: workers wait at most a short, fixed time, a helper process
we started (nvidia-smi) is killed rather than waited out or orphaned, and a thread stuck in an
uncancellable call (DNS, the update check's urlopen, WMI) is handed to the thread reaper - so the
process exits with os._exit instead of Qt aborting on "QThread destroyed while still running".
"""
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, QThread

from netspeedtray.core import thread_reaper


_RELEASE = threading.Event()
# The REAL interpreter, not the venv's python.exe: that one is a launcher stub that re-spawns the
# base interpreter as a child, so killing it (what stop() does) leaves the real sleeper running as
# an orphan - found alive hours later on the maintainer's PC, and a join here that could time out.
_PY = getattr(sys, "_base_executable", None) or sys.executable


class _Sleeper(QThread):
    """Runs until the test releases it (``block``), or returns at once - no wall-clock sleeps, so
    a loaded machine cannot finish it before the assertion that needs it running."""
    def __init__(self, block=True, parent=None):
        super().__init__(parent)
        self.block = block

    def run(self):
        if self.block:
            _RELEASE.wait(30)


@pytest.fixture(autouse=True)
def _clean_reaper():
    _RELEASE.clear()
    yield
    _RELEASE.set()
    for t in list(thread_reaper._OUTLIVED):
        if hasattr(t, "wait"):
            t.wait(5000)
    thread_reaper._OUTLIVED.clear()


def test_a_thread_still_running_is_kept_and_unparented(q_app):
    parent = QObject()
    t = _Sleeper(True, parent)
    t.start()
    assert thread_reaper.outlived(t, "test") is True
    assert t.parent() is None, "a Qt parent would delete the running thread"
    assert thread_reaper.any_running() is True
    _RELEASE.set()
    assert t.wait(10000)
    assert thread_reaper.any_running() is False


def test_a_finished_thread_is_not_kept(q_app):
    t = _Sleeper(False)
    t.start()
    t.wait(5000)
    assert thread_reaper.outlived(t, "test") is False
    assert thread_reaper.any_running() is False


def test_finish_exits_hard_while_a_reaped_thread_runs(q_app, monkeypatch):
    import monitor
    t = _Sleeper(True)
    t.start()
    assert thread_reaper.outlived(t, "test")
    exits = []
    monkeypatch.setattr(monitor.os, "_exit", lambda code: exits.append(code))
    monitor._finish(0)
    assert exits == [0]


def test_stop_kills_an_in_flight_helper_process(q_app):
    """nvidia-smi can take 1.5 s (longer cold); the 1 s stop wait used to expire around it."""
    from netspeedtray.core.monitor_thread import StatsMonitorThread
    mt = StatsMonitorThread(interval=0.1)
    result = {}

    def call():
        start = time.monotonic()
        try:
            mt._run_child([_PY, "-c", "import time; time.sleep(30)"], 60)
        except Exception as e:
            result["error"] = e
        result["took"] = time.monotonic() - start

    th = threading.Thread(target=call)
    th.start()
    deadline = time.monotonic() + 10
    while mt._child is None and time.monotonic() < deadline:
        time.sleep(0.02)
    child = mt._child
    assert child is not None
    mt.stop()
    th.join(10)
    assert not th.is_alive(), "the helper read outlived the kill"
    assert result["took"] < 10 and "error" in result
    assert child.poll() is not None, "the helper process was left running"
    with pytest.raises(RuntimeError):
        mt._run_child([_PY, "-c", "pass"], 5)    # nothing new starts once stopping


def test_run_child_returns_output_and_raises_on_failure(q_app):
    from netspeedtray.core.monitor_thread import StatsMonitorThread
    mt = StatsMonitorThread(interval=0.1)
    assert mt._run_child([_PY, "-c", "print('52, 8192, 75')"], 30).strip() == "52, 8192, 75"
    with pytest.raises(subprocess.CalledProcessError):
        mt._run_child([_PY, "-c", "import sys; sys.exit(3)"], 30)
    with pytest.raises(subprocess.TimeoutExpired):
        mt._run_child([_PY, "-c", "import time; time.sleep(30)"], 0.5)


def test_the_update_check_is_not_waited_out_at_quit(q_app):
    from netspeedtray.core.update_checker import UpdateChecker
    uc = UpdateChecker({}, None)
    worker = _Sleeper(True, uc)
    worker.finished = MagicMock()        # the real _CheckWorker's result signals
    worker.failed = MagicMock()
    QThread.start(worker)
    uc._worker = worker
    start = time.monotonic()
    uc.stop()
    assert time.monotonic() - start < 5, "a check in flight must not be waited out"
    worker.finished.disconnect.assert_called_once()
    assert worker in thread_reaper._OUTLIVED and worker.parent() is None


def test_widget_cleanup_stops_the_update_checker_and_reaps_a_stuck_probe():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    stub = MagicMock()
    stub._cleanup_done = False
    stub._cleanup_running = False
    stub._retired_latency_probes = []
    probe = MagicMock()
    probe.isRunning.return_value = True
    stub.latency_probe = probe
    NetworkSpeedWidget.cleanup(stub)
    stub.update_checker.stop.assert_called_once()
    probe.wait.assert_called_once_with(1500)
    assert probe in thread_reaper._OUTLIVED
    thread_reaper._OUTLIVED.clear()
