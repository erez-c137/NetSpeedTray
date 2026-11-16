"""R8 (2.2.1): run()'s end-of-thread cleanup runs even when something escapes the poll loop.

Drives the REAL run() (no private loop helper patched): the collectors are stubbed so one poll is
cheap, and the post-poll wait raises - the one call in the loop body outside its try/except.
"""
from unittest.mock import Mock

import pytest

from netspeedtray.core import monitor_thread as module
from netspeedtray.core.monitor_thread import StatsMonitorThread

_CLEANUPS = ("_cleanup_gpu_query", "_cleanup_thermal_query", "_cleanup_power_query",
             "_cleanup_ohm_wmi", "_cleanup_com")


def _thread_whose_wait_raises(monkeypatch, exc):
    thread = StatsMonitorThread(interval=1.0, config={})
    calls = []
    loop_done = []

    def failing_close():
        if not loop_done:
            return                                   # the poll closes it too (identity is off)
        calls.append("identity")
        raise OSError("close failed")                # one failing cleanup step must not skip the rest

    thread._network_identity_monitor = Mock(close=failing_close)
    thread._init_com = Mock()
    thread._update_lhm_notice = Mock()
    thread._collect_power_stats = Mock()
    for name in _CLEANUPS:
        monkeypatch.setattr(thread, name, lambda n=name: calls.append(n))
    monkeypatch.setattr(module, "is_rdp_session", lambda: False)
    monkeypatch.setattr(module, "hardware_collection_gates", lambda _config, _force: (False, False))
    monkeypatch.setattr(module, "memory_collection_gates", lambda _config, _force: (False, False))
    monkeypatch.setattr(module.psutil, "net_io_counters", lambda **_kwargs: {})
    monkeypatch.setattr(module.psutil, "cpu_percent", lambda **_kwargs: 0)

    def wait_raises():
        loop_done.append(True)
        thread._is_running = False                   # never loop again, even if the raise is swallowed
        raise exc

    monkeypatch.setattr(thread, "_wait_period", wait_raises)
    thread._is_running = True
    return thread, calls


@pytest.mark.parametrize("exc", [RuntimeError("escaped the loop"), SystemExit(3)],
                         ids=["exception", "base-exception"])
def test_run_releases_everything_when_the_wait_raises(monkeypatch, exc):
    thread, calls = _thread_whose_wait_raises(monkeypatch, exc)
    with pytest.raises(type(exc)):
        thread.run()                                 # the escape still propagates after the cleanup
    assert calls == ["identity", *_CLEANUPS]         # every step, in order, COM last
