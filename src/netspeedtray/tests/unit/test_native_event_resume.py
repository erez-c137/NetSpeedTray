"""
Regression tests for NetworkSpeedWidget.nativeEvent (WM_POWERBROADCAST observer).

These lock in the contract that fixed the v2.0 crash-on-launch: the override must
*observe* native messages and return ``(False, 0)`` ("not handled, let Qt continue"),
and must NEVER re-dispatch via ``super().nativeEvent(...)``. Calling super from a Python
nativeEvent override access-violates inside Qt during the widget's first show() (a hard
0xC0000005 in QtCore, no Python traceback) — see the method docstring.

nativeEvent is exercised as a plain function with a lightweight fake ``self`` (the
established pattern in test_widget_cycle.py), so no live QWidget is needed. A real MSG is
synthesised in memory via the module's own _NativeMSG struct and passed by address.
"""
import ctypes
import types
from unittest.mock import MagicMock

from netspeedtray.views.widget.main import (
    NetworkSpeedWidget, _NativeMSG,
    _WM_POWERBROADCAST, _PBT_APMRESUMESUSPEND, _PBT_APMRESUMEAUTOMATIC,
)


def _fake_widget():
    fake = types.SimpleNamespace()
    fake.logger = MagicMock()
    fake._on_environment_changed = MagicMock()
    return fake


def _msg_addr(message: int, wparam: int = 0) -> int:
    m = _NativeMSG()
    m.message = message
    m.wParam = wparam
    # Keep a reference alive on the function's frame via the returned tuple.
    return m, ctypes.addressof(m)


def test_normal_message_returns_unhandled_tuple():
    """A non-power message must return (False, 0) — the safe contract, not super()."""
    fake = _fake_widget()
    _ref, addr = _msg_addr(0x0010)  # arbitrary non-power message
    result = NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr)
    assert result == (False, 0)
    fake._on_environment_changed.assert_not_called()


def test_resume_message_schedules_reassert(monkeypatch):
    """WM_POWERBROADCAST + PBT_APMRESUMEAUTOMATIC schedules an environment re-assert."""
    scheduled = []
    monkeypatch.setattr(
        "netspeedtray.views.widget.main.QTimer.singleShot",
        lambda _ms, fn: scheduled.append(fn),
    )
    fake = _fake_widget()
    _ref, addr = _msg_addr(_WM_POWERBROADCAST, _PBT_APMRESUMEAUTOMATIC)
    result = NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr)

    assert result == (False, 0)            # still returns the unhandled contract
    assert len(scheduled) == 1             # exactly one deferred re-assert queued
    scheduled[0]()                         # fire it
    fake._on_environment_changed.assert_called_once_with("resume")


def test_resume_suspend_flag_also_reasserts(monkeypatch):
    """The other resume flag (PBT_APMRESUMESUSPEND) is honoured too."""
    scheduled = []
    monkeypatch.setattr(
        "netspeedtray.views.widget.main.QTimer.singleShot",
        lambda _ms, fn: scheduled.append(fn),
    )
    fake = _fake_widget()
    _ref, addr = _msg_addr(_WM_POWERBROADCAST, _PBT_APMRESUMESUSPEND)
    result = NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", addr)
    assert result == (False, 0)
    assert len(scheduled) == 1


def test_non_windows_eventtype_is_ignored():
    """A foreign eventType must be a quiet, safe no-op returning (False, 0)."""
    fake = _fake_widget()
    result = NetworkSpeedWidget.nativeEvent(fake, b"xcb_generic_event_t", 0)
    assert result == (False, 0)
    fake._on_environment_changed.assert_not_called()
