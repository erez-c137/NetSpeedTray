"""Worker identity updates stay change-driven and survive a paused view."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QThread

from netspeedtray.core.monitor_thread import StatsMonitorThread
from netspeedtray.utils.network_utils import NetworkIdentity


@pytest.fixture
def worker():
    thread = StatsMonitorThread.__new__(StatsMonitorThread)
    QThread.__init__(thread)
    thread.config = {"show_network_identity": True, "identity_mode": "both"}
    thread.logger = Mock()
    thread._network_identity_monitor = Mock()
    thread._network_identity_monitor.read.return_value = NetworkIdentity(name="home", connected=True)
    thread._network_identity_update_requested = threading.Event()
    thread._network_identity_update_lock = threading.Lock()
    thread._last_emitted_network_identity = None
    return thread


def test_unchanged_identity_is_only_emitted_once(worker):
    first, second = {}, {}
    worker._collect_network_identity(first)
    worker._collect_network_identity(second)
    assert first["network_identity"].name == "home"
    assert "network_identity" not in second
    worker._network_identity_monitor.read.return_value = NetworkIdentity(name="other", connected=True)
    worker._collect_network_identity(second)
    assert second["network_identity"].name == "other"


def test_resume_requests_one_update_even_when_identity_did_not_change(worker):
    worker._collect_network_identity({})
    worker.request_network_identity_update()
    resumed, following = {}, {}
    worker._collect_network_identity(resumed)
    worker._collect_network_identity(following)
    assert "network_identity" in resumed
    assert "network_identity" not in following


def test_resume_request_during_read_is_retained_for_next_tick(worker):
    worker._collect_network_identity({})
    identity = worker._network_identity_monitor.read.return_value

    def request_during_read(_mode):
        worker.request_network_identity_update()
        return identity

    worker._network_identity_monitor.read.side_effect = request_during_read
    worker._collect_network_identity({})
    worker._network_identity_monitor.read.side_effect = None
    next_tick = {}
    worker._collect_network_identity(next_tick)
    assert "network_identity" in next_tick


def test_disabling_then_reenabling_emits_unchanged_identity(worker):
    worker._collect_network_identity({})
    worker.config["show_network_identity"] = False
    disabled = {}
    worker._collect_network_identity(disabled)
    assert "network_identity" not in disabled
    worker._network_identity_monitor.close.assert_called_once()
    worker.config["show_network_identity"] = True
    enabled = {}
    worker._collect_network_identity(enabled)
    assert "network_identity" in enabled


def test_com_cleanup_failure_is_contained_and_not_retried(worker, monkeypatch):
    import pythoncom
    cleanup = Mock(side_effect=RuntimeError("cleanup failed"))
    monkeypatch.setattr(pythoncom, "CoUninitialize", cleanup)
    worker._com_initialized = True
    worker._cleanup_com()
    worker._cleanup_com()
    assert worker._com_initialized is False
    cleanup.assert_called_once()


def test_widget_resume_requests_identity_update(worker):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    widget = SimpleNamespace(is_paused=True, logger=Mock(), monitor_thread=worker,
                             _paused_histories=(), update=Mock())
    NetworkSpeedWidget.resume(widget)
    assert widget.is_paused is False
    assert worker._network_identity_update_requested.is_set()


def test_failed_stats_tick_retries_identity_on_the_next_successful_tick(monkeypatch):
    from netspeedtray.core import monitor_thread as module
    thread = StatsMonitorThread(interval=0.1, config={"show_network_identity": True})
    identity = NetworkIdentity(name="home", connected=True)
    thread._network_identity_monitor = Mock()
    thread._network_identity_monitor.read.return_value = identity
    thread._init_com = Mock()
    thread._update_lhm_notice = Mock()
    for name in ("_cleanup_gpu_query", "_cleanup_thermal_query", "_cleanup_power_query",
                 "_cleanup_ohm_wmi", "_cleanup_com"):
        monkeypatch.setattr(thread, name, Mock())
    monkeypatch.setattr(module, "is_rdp_session", lambda: False)
    monkeypatch.setattr(module, "hardware_collection_gates", lambda _config, _force: (False, False))
    monkeypatch.setattr(module.psutil, "net_io_counters", lambda **_kwargs: {})
    monkeypatch.setattr(module.psutil, "cpu_percent", lambda **_kwargs: 0)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    calls = []

    def poll_power(_stats, _force):
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("temporary power query failure")
        thread._is_running = False

    thread._collect_power_stats = poll_power
    emitted = []
    thread.stats_ready.connect(emitted.append)
    thread.run()
    assert len(calls) == 2
    assert len(emitted) == 1
    assert emitted[0]["network_identity"] == identity
