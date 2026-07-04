"""
Preferred-monitor taskbar selection tests (#72 / #166).

Verifies that ``_select_taskbar_for_screen`` matches the user's chosen monitor
robustly - by stored name, re-resolved name, OR geometry - so a Qt-vs-WinAPI
naming desync at mixed DPI (e.g. dual 4K at 150%) does not silently fall back to
primary, and that it returns ``None`` (a documented fallback the caller handles)
when the preferred monitor has no taskbar of its own.
"""
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

import netspeedtray.utils.taskbar_utils as tbu
from netspeedtray.utils.taskbar_utils import _select_taskbar_for_screen, _taskbar_window_is_ready


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    """A QApplication must exist for QScreen access."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class MockScreen:
    """Minimal QScreen stand-in with name() + geometry()."""
    def __init__(self, name, geometry_rect):
        self._name = name
        self._geo = QRect(*geometry_rect)

    def name(self):
        return self._name

    def geometry(self):
        return self._geo


def _mock_tb(hwnd, stored_name, resolved_screen, geometry, is_primary):
    tb = MagicMock()
    tb.hwnd = hwnd
    tb.screen_name = stored_name
    tb.screen_geometry = geometry
    tb.is_primary = is_primary
    tb.get_screen.return_value = resolved_screen
    return tb


# Dual 4K at 150% -> logical geometry is 2560x1440 per monitor, side by side.
PRIMARY = MockScreen(r"\\.\DISPLAY1", (0, 0, 2560, 1440))
SECOND = MockScreen(r"\\.\DISPLAY2", (2560, 0, 2560, 1440))


def _patch_screens(screens):
    return patch(
        "netspeedtray.utils.taskbar_utils.QApplication.screens",
        return_value=screens,
    )


class TestPreferredMonitorSelection:
    def test_matches_secondary_by_stored_name(self):
        tbs = [
            _mock_tb(1, r"\\.\DISPLAY1", PRIMARY, (0, 0, 2560, 1440), True),
            _mock_tb(2, r"\\.\DISPLAY2", SECOND, (2560, 0, 2560, 1440), False),
        ]
        with _patch_screens([PRIMARY, SECOND]):
            tb = _select_taskbar_for_screen(tbs, r"\\.\DISPLAY2")
        assert tb is not None and tb.hwnd == 2

    def test_matches_by_geometry_when_name_desyncs(self):
        """
        The regression that likely bit #72: the secondary taskbar mis-resolved
        its NAME (to DISPLAY1) under mixed DPI, but its geometry still equals the
        preferred DISPLAY2 screen. The geometry layer must still find it.
        """
        desynced = MockScreen(r"\\.\DISPLAY1", (2560, 0, 2560, 1440))
        tbs = [
            _mock_tb(1, r"\\.\DISPLAY1", PRIMARY, (0, 0, 2560, 1440), True),
            _mock_tb(2, r"\\.\DISPLAY1", desynced, (2560, 0, 2560, 1440), False),
        ]
        with _patch_screens([PRIMARY, SECOND]):
            tb = _select_taskbar_for_screen(tbs, r"\\.\DISPLAY2")
        assert tb is not None and tb.hwnd == 2, "geometry fallback should still find the DISPLAY2 taskbar"

    def test_returns_none_when_preferred_monitor_has_no_taskbar(self):
        """'Show taskbar on all displays' off: only the primary has a taskbar."""
        tbs = [_mock_tb(1, r"\\.\DISPLAY1", PRIMARY, (0, 0, 2560, 1440), True)]
        with _patch_screens([PRIMARY, SECOND]):
            tb = _select_taskbar_for_screen(tbs, r"\\.\DISPLAY2")
        assert tb is None, "no taskbar on the preferred monitor -> None (caller falls back to primary)"

    def test_matches_primary_when_primary_preferred(self):
        tbs = [
            _mock_tb(1, r"\\.\DISPLAY1", PRIMARY, (0, 0, 2560, 1440), True),
            _mock_tb(2, r"\\.\DISPLAY2", SECOND, (2560, 0, 2560, 1440), False),
        ]
        with _patch_screens([PRIMARY, SECOND]):
            tb = _select_taskbar_for_screen(tbs, r"\\.\DISPLAY1")
        assert tb is not None and tb.hwnd == 1


class _FakeWin32Error(Exception):
    pass


def _fake_win32gui(*, rect=(0, 0, 2560, 1440), tray_hwnd=0, is_window=True, rect_raises=False):
    w = MagicMock()
    w.error = _FakeWin32Error
    if rect_raises:
        w.GetWindowRect.side_effect = _FakeWin32Error("not queryable")
    else:
        w.GetWindowRect.return_value = rect
    w.FindWindowEx.return_value = tray_hwnd
    w.IsWindow.return_value = is_window
    return w


class TestTaskbarReadinessGate:
    """#72: process_taskbar dropped every secondary-monitor taskbar because it required a TrayNotifyWnd
    child that only the primary taskbar has, so only one taskbar ever enumerated and Preferred Monitor
    always fell back to primary. _taskbar_window_is_ready gates that check to the primary only."""

    def test_secondary_survives_without_tray_child(self):
        with patch.object(tbu, "win32gui", _fake_win32gui(tray_hwnd=0)):
            assert _taskbar_window_is_ready(2, "Shell_SecondaryTrayWnd") is True

    def test_primary_ready_with_valid_tray_child(self):
        with patch.object(tbu, "win32gui", _fake_win32gui(tray_hwnd=99, is_window=True)):
            assert _taskbar_window_is_ready(1, "Shell_TrayWnd") is True

    def test_primary_not_ready_without_tray_child(self):
        with patch.object(tbu, "win32gui", _fake_win32gui(tray_hwnd=0)):
            assert _taskbar_window_is_ready(1, "Shell_TrayWnd") is False

    def test_not_ready_when_taskbar_window_unqueryable(self):
        with patch.object(tbu, "win32gui", _fake_win32gui(rect_raises=True)):
            assert _taskbar_window_is_ready(2, "Shell_SecondaryTrayWnd") is False


def test_preferred_monitor_fallback_logs_once_per_state(caplog):
    """The no-taskbar fallback logs once per distinct state, not once per (~1s) reposition tick (#188)."""
    import logging

    tbu._last_fallback_log_key = None
    tb = _mock_tb(1, r"\.\DISPLAY1", PRIMARY, (0, 0, 2560, 1440), is_primary=True)

    def count():
        return sum(1 for r in caplog.records if "did not match" in r.getMessage())

    try:
        with caplog.at_level(logging.INFO, logger="NetSpeedTray.TaskbarUtils"):
            for _ in range(3):  # three per-second ticks, same state
                tbu._log_preferred_monitor_fallback("XENEON EDGE", [tb])
            assert count() == 1, "identical fallback state must log only once, not per tick"

            tbu._log_preferred_monitor_fallback("OTHER MON", [tb])  # preference changed -> logs again
            assert count() == 2, "a genuine state change should re-log once"
    finally:
        tbu._last_fallback_log_key = None  # don't leak module state to other tests
