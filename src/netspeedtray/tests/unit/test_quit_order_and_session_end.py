"""
Quit ordering and Windows session end (2.2.0-beta.1 lifecycle review, findings 1 and 4).

* The Monitor window (and its graph worker) must be closed BEFORE WidgetState.cleanup() flushes and
  stops the database - otherwise the graph thread can be mid-query while read connections go away.
* The final config save must come BEFORE the database step, which can wait up to
  DB_SHUTDOWN_WAIT_MS (15 s): Windows ends a logging-off app after ~5 s, so a save queued behind that
  wait was lost.
* WM_QUERYENDSESSION / WM_ENDSESSION: the essential saves (config + odometer) happen right there,
  inside Windows' budget, since the normal quit path never gets to run at logoff.
"""
import ctypes
import types
from unittest.mock import MagicMock

import pytest

from netspeedtray.views.widget import main as M
from netspeedtray.views.widget.main import NetworkSpeedWidget

_WM_QUERYENDSESSION = 0x0011
_WM_ENDSESSION = 0x0016


def _cleanup_fake(order):
    def rec(name, rv=None):
        def _f(*_a, **_k):
            order.append(name)
            return rv
        return _f

    mw = MagicMock()
    mw.close.side_effect = rec("monitor window")
    ws = MagicMock()
    ws.cleanup.side_effect = rec("history flush")
    cm = MagicMock()
    cm.save.side_effect = rec("config save")
    fake = types.SimpleNamespace(
        logger=MagicMock(), _hover_card_timer=MagicMock(), _hide_usage_hover_card=MagicMock(),
        system_event_handler=MagicMock(), position_manager=MagicMock(), _state_watcher_timer=MagicMock(),
        monitor_thread=MagicMock(), latency_probe=MagicMock(), metrics_endpoint=MagicMock(),
        controller=MagicMock(), widget_state=ws, monitor_window=mw,
        config={"free_move": False, "free_float": True}, config_manager=cm,
        update_config=MagicMock(), pos=MagicMock())
    fake.position_manager.is_floating.return_value = False
    fake._capture_position_config = lambda: NetworkSpeedWidget._capture_position_config(fake)
    return fake


def test_monitor_closes_and_config_saves_before_the_database_step():
    order = []
    fake = _cleanup_fake(order)
    NetworkSpeedWidget.cleanup(fake)
    assert order.index("monitor window") < order.index("history flush"), order
    assert order.index("config save") < order.index("history flush"), order
    assert fake.monitor_window is None


def _msg(message, wparam=0, lparam=0):
    m = M._NativeMSG()
    m.message, m.wParam, m.lParam = message, wparam, lparam
    return m


def _session_fake():
    fake = types.SimpleNamespace(logger=MagicMock(), _save_session_essentials=MagicMock())
    return fake


def test_query_end_session_saves_essentials_and_lets_windows_proceed():
    fake = _session_fake()
    m = _msg(_WM_QUERYENDSESSION, 0, 0)
    rv = NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", ctypes.addressof(m))
    fake._save_session_essentials.assert_called_once()
    assert rv == (False, 0)            # never veto the logoff; Qt answers TRUE


def test_end_session_true_saves_essentials():
    fake = _session_fake()
    m = _msg(_WM_ENDSESSION, 1, 0)
    NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", ctypes.addressof(m))
    fake._save_session_essentials.assert_called_once()


def test_cancelled_end_session_saves_nothing():
    fake = _session_fake()
    m = _msg(_WM_ENDSESSION, 0, 0)
    NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", ctypes.addressof(m))
    fake._save_session_essentials.assert_not_called()


def test_session_essentials_save_config_and_odometer_within_budget():
    order = []
    fake = _cleanup_fake(order)
    NetworkSpeedWidget._save_session_essentials(fake, "test")
    fake.config_manager.save.assert_called_once_with(fake.config)
    fake.widget_state.persist_essentials.assert_called_once()
    timeout = fake.widget_state.persist_essentials.call_args.kwargs.get("timeout")
    assert timeout is not None and timeout <= 3.0     # Windows allows ~5 s in total
    fake.widget_state.cleanup.assert_not_called()     # the 15 s DB wait is NOT on this path


def test_session_essentials_save_config_even_if_the_odometer_fails():
    order = []
    fake = _cleanup_fake(order)
    fake.widget_state.persist_essentials.side_effect = RuntimeError("db gone")
    NetworkSpeedWidget._save_session_essentials(fake, "test")
    fake.config_manager.save.assert_called_once_with(fake.config)


def test_database_thread_exits_promptly_on_quit(tmp_path):
    """The stop() sentinel used to be followed by one more idle 0.5 s get() - on every quit."""
    import time
    from pathlib import Path
    from unittest.mock import patch
    from PyQt6.QtWidgets import QApplication
    from netspeedtray import constants
    from netspeedtray.core.widget_state import WidgetState
    QApplication.instance() or QApplication([])
    with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=Path(tmp_path)):
        ws = WidgetState(dict(constants.config.defaults.DEFAULT_CONFIG))
    assert ws.db_worker.ready.wait(10)
    # Make every idle get() wait 5 s instead of 0.5 s, so the regression (one more idle get after
    # the stop sentinel) costs >= 5 s while the fix exits at once - a margin no slow CI runner can
    # blur. (A fixed 0.4 s bound failed at 0.68 s on a shared runner.)
    queue = ws.db_worker._queue
    real_get = queue.get
    queue.get = lambda *a, **k: real_get(*a, **({**k, "timeout": 5.0} if "timeout" in k else k))
    time.sleep(0.7)                                   # let the in-flight 0.5 s get expire
    t0 = time.perf_counter()
    ws.cleanup()
    took = time.perf_counter() - t0
    assert not ws.db_worker.isRunning()
    assert took < 2.5, f"quit waited {took:.2f} s for an idle database thread"


def test_widget_state_persist_essentials_is_bounded(tmp_path):
    from netspeedtray.core.widget_state import WidgetState
    ws = MagicMock()
    WidgetState.persist_essentials(ws, timeout=1.5)
    ws.flush_batch.assert_called_once_with()
    ws.persist_usage_final.assert_called_once_with()   # handles a usage reset still in flight too
    ws.flush_and_wait.assert_called_once_with(timeout=1.5)
