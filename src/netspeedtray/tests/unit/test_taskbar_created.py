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
    NetworkSpeedWidget, _NativeMSG, _WM_TASKBARCREATED, _MSGFLT_ALLOW,
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


# --- UIPI filter (ChangeWindowMessageFilterEx) --------------------------------


def _fake_uipi():
    """A fake widget with only the attributes _allow_taskbar_created touches."""
    return types.SimpleNamespace(
        logger=MagicMock(), winId=MagicMock(return_value=0x101), _taskbar_created_allowed=0,
    )


def test_change_message_filter_applies_once_per_hwnd(monkeypatch):
    calls = []
    monkeypatch.setattr("netspeedtray.views.widget.main._WM_TASKBARCREATED", 0x4A5B)
    monkeypatch.setattr(
        "netspeedtray.views.widget.main._ChangeWindowMessageFilterEx",
        lambda *args: calls.append(args) or 1,
    )
    fake = _fake_uipi()
    NetworkSpeedWidget._allow_taskbar_created(fake)
    NetworkSpeedWidget._allow_taskbar_created(fake)
    # 4-arg call (NULL FILTER_STATUS pointer) and idempotent: remembered HWND skips.
    assert calls == [(0x101, 0x4A5B, _MSGFLT_ALLOW, None)]


def test_change_message_filter_false_not_cached_and_retried(monkeypatch):
    """A FALSE return must not cache the HWND, so a later call retries (#327 review)."""
    results = iter([0, 1])
    calls = []
    monkeypatch.setattr("netspeedtray.views.widget.main._WM_TASKBARCREATED", 0x4A5B)
    monkeypatch.setattr(
        "netspeedtray.views.widget.main._ChangeWindowMessageFilterEx",
        lambda *args: calls.append(args) or next(results),
    )
    fake = _fake_uipi()
    NetworkSpeedWidget._allow_taskbar_created(fake)   # FALSE: not cached
    assert fake._taskbar_created_allowed == 0
    NetworkSpeedWidget._allow_taskbar_created(fake)   # retry succeeds
    assert fake._taskbar_created_allowed == 0x101
    assert calls == [
        (0x101, 0x4A5B, _MSGFLT_ALLOW, None),
        (0x101, 0x4A5B, _MSGFLT_ALLOW, None),
    ]


def test_change_message_filter_reapplied_on_fresh_hwnd(monkeypatch):
    """Qt can recreate the native window; the filter must follow."""
    calls = []
    monkeypatch.setattr("netspeedtray.views.widget.main._WM_TASKBARCREATED", 0x4A5B)
    monkeypatch.setattr(
        "netspeedtray.views.widget.main._ChangeWindowMessageFilterEx",
        lambda *args: calls.append(args) or 1,
    )
    fake = _fake_uipi()
    fake._taskbar_created_allowed = 0x101   # as if already applied to that HWND
    NetworkSpeedWidget._allow_taskbar_created(fake)
    assert calls == []                       # same HWND -> no-op
    fake.winId = MagicMock(return_value=0x202)
    NetworkSpeedWidget._allow_taskbar_created(fake)
    assert [c[0] for c in calls] == [0x202]


def test_change_message_filter_skipped_when_not_registered(monkeypatch):
    calls = []
    monkeypatch.setattr("netspeedtray.views.widget.main._WM_TASKBARCREATED", 0)
    monkeypatch.setattr(
        "netspeedtray.views.widget.main._ChangeWindowMessageFilterEx",
        lambda *args: calls.append(args) or 1,
    )
    fake = _fake_uipi()
    NetworkSpeedWidget._allow_taskbar_created(fake)   # no raise, no call
    assert calls == []


def test_change_message_filter_survives_winid_failure(monkeypatch):
    monkeypatch.setattr("netspeedtray.views.widget.main._WM_TASKBARCREATED", 0x4A5B)
    fake = _fake_uipi()
    fake.winId = MagicMock(side_effect=RuntimeError("not realized yet"))
    NetworkSpeedWidget._allow_taskbar_created(fake)   # must not raise
