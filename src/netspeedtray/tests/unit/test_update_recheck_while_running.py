"""
Automatic update checks while the app keeps running (rc.8 follow-up).

Before this, the only automatic check ran once, 5 s after launch - a PC that sleeps instead of
rebooting could run one instance for weeks and never hear of a new release. Now a periodic tick
and a wake from sleep both ask UpdateChecker.should_check() (24 h interval, user toggle, IT policy)
and only then check_now().

Driven against the real NetworkSpeedWidget methods with a lightweight fake ``self`` (the pattern of
test_update_manual_check_wiring.py / test_native_event_resume.py). No network: the checker is a mock,
or a real UpdateChecker whose _on_result is fed a tag directly.
"""
import ctypes
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.views.widget.main import (
    NetworkSpeedWidget, _NativeMSG, _WM_POWERBROADCAST, _PBT_APMRESUMEAUTOMATIC,
)


def _fake(should_check=True, **overrides):
    checker = MagicMock()
    checker.should_check.return_value = should_check
    fake = types.SimpleNamespace(
        logger=MagicMock(),
        update_checker=checker,
        _will_quit_app=False,
        _update_dialog_open=False,
        _manual_update_check=False,
        _update_dismissed_versions=set(),
        _show_update_dialog=MagicMock(),
    )
    for k, v in overrides.items():
        setattr(fake, k, v)
    return fake


def _tick(fake, reason="periodic"):
    NetworkSpeedWidget._run_automatic_update_check(fake, reason)


# --- the periodic tick ---------------------------------------------------------------------------

def test_tick_checks_when_due():
    fake = _fake(should_check=True)
    _tick(fake)
    fake.update_checker.should_check.assert_called_once()
    fake.update_checker.check_now.assert_called_once()


def test_tick_does_not_check_when_not_due_or_opted_out():
    # should_check() is False both inside the 24 h interval and with the user's toggle off.
    fake = _fake(should_check=False)
    _tick(fake)
    fake.update_checker.should_check.assert_called_once()
    fake.update_checker.check_now.assert_not_called()


def test_tick_respects_user_opt_out_through_real_should_check():
    """End to end with the real UpdateChecker.should_check: toggle off -> no check, ever."""
    from netspeedtray.core.update_checker import UpdateChecker
    real = UpdateChecker({"check_for_updates": False}, None)
    real.check_now = MagicMock()
    fake = _fake()
    fake.update_checker = real
    _tick(fake)
    real.check_now.assert_not_called()


def test_tick_due_after_24h_through_real_should_check(monkeypatch):
    from netspeedtray.core import update_checker as uc
    monkeypatch.setattr(uc, "update_check_disabled_by_policy", lambda: False)
    recent = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    real = uc.UpdateChecker({"check_for_updates": True, "last_update_check": recent}, None)
    real.check_now = MagicMock()
    fake = _fake()
    fake.update_checker = real
    _tick(fake)
    real.check_now.assert_not_called()          # 3 h since the last check: not due
    real.config["last_update_check"] = old
    _tick(fake)
    real.check_now.assert_called_once()         # 25 h: due


@pytest.mark.parametrize("state", [
    {"_update_dialog_open": True},
    {"_will_quit_app": True},
    {"_cleanup_running": True},
    {"_cleanup_done": True},
])
def test_tick_never_checks_during_prompt_or_shutdown(state):
    fake = _fake(should_check=True, **state)
    _tick(fake)
    fake.update_checker.check_now.assert_not_called()


def test_tick_never_checks_while_an_update_is_being_applied():
    updater = MagicMock()
    updater.is_running.return_value = True
    fake = _fake(should_check=True, _secure_updater=updater)
    _tick(fake)
    fake.update_checker.check_now.assert_not_called()


def test_tick_tolerates_a_deleted_updater():
    updater = MagicMock()
    updater.is_running.side_effect = RuntimeError("wrapped C/C++ object has been deleted")
    fake = _fake(should_check=True, _secure_updater=updater)
    _tick(fake)
    fake.update_checker.check_now.assert_called_once()


def test_tick_without_checker_is_a_noop():
    fake = _fake()
    fake.update_checker = None
    _tick(fake)   # must not raise


# --- one prompt at a time ------------------------------------------------------------------------

def _available(fake, version="v2.2.0"):
    NetworkSpeedWidget._on_update_available(fake, version, "https://example.invalid/r", "", "", "")


def test_result_while_a_prompt_is_open_does_not_stack_a_second():
    fake = _fake(_update_dialog_open=True)
    _available(fake)
    fake._show_update_dialog.assert_not_called()


def test_automatic_result_for_a_not_now_version_stays_quiet():
    fake = _fake(_update_dismissed_versions={"2.2.0"})
    _available(fake, "v2.2.0")
    fake._show_update_dialog.assert_not_called()
    _available(fake, "v2.2.1")                  # a newer release is offered again
    fake._show_update_dialog.assert_called_once()


def test_manual_check_still_shows_a_not_now_version():
    fake = _fake(_update_dismissed_versions={"2.2.0"}, _manual_update_check=True)
    _available(fake, "v2.2.0")
    fake._show_update_dialog.assert_called_once()
    assert fake._manual_update_check is False   # consumed


def test_manual_flag_cleared_by_up_to_date_and_failure():
    fake = _fake(_manual_update_check=True)
    NetworkSpeedWidget._clear_manual_update_check(fake)
    assert fake._manual_update_check is False
    fake._manual_update_check = True
    NetworkSpeedWidget._clear_manual_update_check(fake, "timed out")   # check_failed(str)
    assert fake._manual_update_check is False


def test_show_dialog_marks_open_and_records_not_now(monkeypatch):
    """The open flag is up for exactly the dialog's lifetime; 'Not now' is remembered."""
    from netspeedtray.views import update_dialog
    seen = {}

    class _FakeDialog:
        ACTION_DISMISS, ACTION_DOWNLOAD, ACTION_SKIP = "dismiss", "download", "skip"

        def __init__(self, *a, **k):
            self.action = self.ACTION_DISMISS

        def exec(self):
            seen["open_during_exec"] = fake._update_dialog_open

    monkeypatch.setattr(update_dialog, "UpdateDialog", _FakeDialog)
    fake = _fake(i18n=MagicMock())
    NetworkSpeedWidget._show_update_dialog(fake, "v2.2.0", "u")
    assert seen["open_during_exec"] is True
    assert fake._update_dialog_open is False
    assert "2.2.0" in fake._update_dismissed_versions


def test_older_latest_stays_quiet(monkeypatch):
    """rc build vs an older final Latest (2.2.0-rc.7 vs v2.1.7): up_to_date, never a prompt."""
    from netspeedtray.core import update_checker as uc
    monkeypatch.setattr(constants.app, "VERSION", "2.2.0-rc.7", raising=False)
    real = uc.UpdateChecker({"check_for_updates": True}, None)
    got = {"available": 0, "up_to_date": 0}
    real.update_available.connect(lambda *a: got.__setitem__("available", got["available"] + 1))
    real.up_to_date.connect(lambda: got.__setitem__("up_to_date", got["up_to_date"] + 1))
    real._on_result("v2.1.7", "https://example.invalid/r", "", "", "")
    assert got == {"available": 0, "up_to_date": 1}


# --- resume ---------------------------------------------------------------------------------------

def test_resume_message_schedules_the_update_check(monkeypatch):
    monkeypatch.setattr("netspeedtray.views.widget.main.QTimer.singleShot", lambda _ms, fn: None)
    fake = _fake()
    fake._on_environment_changed = MagicMock()
    fake._schedule_resume_update_check = MagicMock()
    m = _NativeMSG()
    m.message = _WM_POWERBROADCAST
    m.wParam = _PBT_APMRESUMEAUTOMATIC
    assert NetworkSpeedWidget.nativeEvent(fake, b"windows_generic_MSG", ctypes.addressof(m)) == (False, 0)
    fake._schedule_resume_update_check.assert_called_once()


def test_resume_timer_fires_a_check_when_due():
    timer = MagicMock()
    fake = _fake(should_check=True, _update_resume_check_timer=timer)
    NetworkSpeedWidget._schedule_resume_update_check(fake)
    timer.start.assert_called_once()
    # What the timer's timeout runs:
    _tick(fake, "resume")
    fake.update_checker.check_now.assert_called_once()


# --- wiring + shutdown (real QTimers) -----------------------------------------------------------

def test_recheck_interval_is_a_named_constant():
    assert constants.app.UPDATE_RECHECK_INTERVAL_MS == 3 * 60 * 60 * 1000
    assert constants.app.UPDATE_RESUME_CHECK_DELAY_MS > 0


def test_cleanup_stops_the_update_timers(monkeypatch):
    from netspeedtray.utils import font_release
    from netspeedtray.core.db import history_backup
    monkeypatch.setattr(font_release, "cancel_pending_release", lambda **_k: None)
    monkeypatch.setattr(history_backup, "cancel_all", lambda *_a: None)
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    periodic, resume = QTimer(), QTimer()
    periodic.start(constants.app.UPDATE_RECHECK_INTERVAL_MS)
    resume.setSingleShot(True)
    resume.start(constants.app.UPDATE_RESUME_CHECK_DELAY_MS)

    fake = types.SimpleNamespace(
        logger=MagicMock(), config_manager=MagicMock(), config={},
        update_checker=MagicMock(),
        _update_recheck_timer=periodic, _update_resume_check_timer=resume,
        _hover_card_timer=MagicMock(), _hide_usage_hover_card=MagicMock(),
        _capture_position_config=MagicMock(),
    )
    NetworkSpeedWidget.cleanup(fake, save_config=False)
    assert not periodic.isActive()
    assert not resume.isActive()
    fake.update_checker.stop.assert_called_once()
