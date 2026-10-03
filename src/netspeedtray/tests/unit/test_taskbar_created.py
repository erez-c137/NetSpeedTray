"""
Regression tests for the TaskbarCreated recovery path.

Explorer broadcasts "TaskbarCreated" the moment it (re)creates the taskbar window.
nativeEvent must observe that broadcast (without consuming it) and kick the same
staggered-refresh recovery the 3s validity poll uses, so the widget re-docks to the
new taskbar instantly instead of up to a poll interval later. Follows the plain
function + fake ``self`` pattern of test_native_event_resume.py.
"""
import ctypes
import types
from unittest.mock import MagicMock

from netspeedtray import constants
from netspeedtray.views.widget.main import (
    NetworkSpeedWidget, _NativeMSG, _WM_TASKBARCREATED,
)


def _fake_widget():
    fake = types.SimpleNamespace()
    fake.logger = MagicMock()
    fake._on_environment_changed = MagicMock()
    fake._recover_taskbar_restarted = MagicMock()
    return fake


def _msg_addr(message: int, wparam: int = 0):
    m = _NativeMSG()
    m.message = message
    m.wParam = wparam
    return m, ctypes.addressof(m)


def test_taskbar_created_message_triggers_recovery():
    """The broadcast must call _recover_taskbar_restarted and stay non-consuming."""
    fake = _fake_widget()
    _ref, addr = _msg_addr(_WM_TASKBARCREATED)
    result = NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr)
    assert result == (False, 0)
    fake._recover_taskbar_restarted.assert_called_once_with()


def test_zero_registration_value_never_matches(monkeypatch):
    """If RegisterWindowMessageW failed (atom 0), message 0 (WM_NULL) must not match."""
    monkeypatch.setattr("netspeedtray.views.widget.main._WM_TASKBARCREATED", 0)
    fake = _fake_widget()
    _ref, addr = _msg_addr(0)  # WM_NULL
    result = NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr)
    assert result == (False, 0)
    fake._recover_taskbar_restarted.assert_not_called()


def test_power_message_still_works_alongside(monkeypatch):
    """The TaskbarCreated branch must not break the WM_POWERBROADCAST resume path."""
    from netspeedtray.views.widget.main import _WM_POWERBROADCAST, _PBT_APMRESUMEAUTOMATIC
    scheduled = []
    monkeypatch.setattr(
        "netspeedtray.views.widget.main.QTimer.singleShot",
        lambda _ms, fn: scheduled.append(fn),
    )
    fake = _fake_widget()
    _ref, addr = _msg_addr(_WM_POWERBROADCAST, _PBT_APMRESUMEAUTOMATIC)
    result = NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr)
    assert result == (False, 0)
    assert len(scheduled) == 1                      # the resume re-assert is queued
    fake._recover_taskbar_restarted.assert_not_called()  # and not the taskbar recovery


def test_recover_schedules_staggered_refreshes(monkeypatch):
    """_recover_taskbar_restarted schedules RETRIES refreshes at 0..(RETRIES-1)*DELAY ms."""
    calls = []

    def fake_single_shot(ms, fn):
        calls.append((ms, fn))

    monkeypatch.setattr(
        "netspeedtray.views.widget.main.QTimer.singleShot", fake_single_shot
    )
    fake = types.SimpleNamespace(logger=MagicMock(), _execute_refresh=MagicMock())
    NetworkSpeedWidget._recover_taskbar_restarted(fake)

    retries = constants.timeouts.TASKBAR_RESTART_RETRIES
    delay = constants.timeouts.TASKBAR_RESTART_RECOVERY_DELAY_MS
    assert [ms for ms, _ in calls] == [i * delay for i in range(retries)]
    # Firing the first (immediate) pass runs a refresh
    calls[0][1]()
    fake._execute_refresh.assert_called_once_with()
