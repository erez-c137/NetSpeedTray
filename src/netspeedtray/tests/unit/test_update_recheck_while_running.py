"""
Automatic update checks while the app keeps running (2.2.1, #508).

Before this, the only automatic check ran once, 5 s after launch - a PC that sleeps instead of
rebooting could run one instance for weeks and never hear of a new release. Now a periodic tick
and a wake from sleep both ask UpdateChecker.should_check() (24 h interval, user toggle, IT policy)
and only then check_now(). What they find is offered with a non-modal flyout (never a dialog the
user did not ask for); the 5 s startup check and the manual menu check keep the dialog.

Driven against the real NetworkSpeedWidget methods with a lightweight fake ``self`` (the pattern of
test_update_manual_check_wiring.py / test_native_event_resume.py). No network: the checker is a mock,
or a real UpdateChecker whose _on_result / _on_failed are fed directly.
"""
import ctypes
import logging
import time
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QObject

from netspeedtray import constants
from netspeedtray.views.widget.main import (
    NetworkSpeedWidget as W, _NativeMSG, _WM_POWERBROADCAST, _PBT_APMRESUMEAUTOMATIC,
)

SNOOZE = constants.app.UPDATE_NOT_NOW_SNOOZE_S


def _bind(fake, *names):
    for n in names:
        setattr(fake, n, types.MethodType(getattr(W, n), fake))


def _fake(should_check=True, **overrides):
    checker = MagicMock()
    checker.should_check.return_value = should_check
    fake = types.SimpleNamespace(
        logger=MagicMock(),
        update_checker=checker,
        _will_quit_app=False,
        _update_dialog_open=False,
        _manual_update_check=False,
        _update_auto_source=None,
        _update_dismissed_at={},
        _pending_update_offer=None,
        _show_update_dialog=MagicMock(),
        _show_pending_update_offer=MagicMock(),
        _on_up_to_date_manual=MagicMock(),
        _on_check_failed_manual=MagicMock(),
    )
    _bind(fake, "_app_is_quitting", "_take_update_check_context", "_update_recently_dismissed")
    for k, v in overrides.items():
        setattr(fake, k, v)
    return fake


def _tick(fake, reason="periodic"):
    W._run_automatic_update_check(fake, reason)


def _available(fake, version="v2.2.1"):
    W._on_update_available(fake, version, "https://example.invalid/r", "", "", "")


# --- the periodic tick ---------------------------------------------------------------------------

def test_tick_checks_when_due_and_marks_itself_automatic():
    fake = _fake(should_check=True)
    _tick(fake)
    fake.update_checker.should_check.assert_called_once()
    fake.update_checker.check_now.assert_called_once()
    assert fake._update_auto_source == "periodic"


def test_tick_does_not_check_when_not_due_or_opted_out():
    # should_check() is False both inside the 24 h interval and with the user's toggle off.
    fake = _fake(should_check=False)
    _tick(fake)
    fake.update_checker.should_check.assert_called_once()
    fake.update_checker.check_now.assert_not_called()
    assert fake._update_auto_source is None


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


# --- where a found update goes -------------------------------------------------------------------

def test_automatic_result_is_offered_by_flyout_not_dialog():
    fake = _fake(_update_auto_source="periodic")
    _available(fake)
    fake._show_update_dialog.assert_not_called()
    fake._show_pending_update_offer.assert_called_once()
    assert fake._pending_update_offer[0] == "v2.2.1"
    assert fake._update_auto_source is None      # consumed


def test_startup_result_keeps_the_dialog():
    fake = _fake(_update_auto_source=None)       # the 5 s startup check sets no source
    _available(fake)
    fake._show_update_dialog.assert_called_once()
    fake._show_pending_update_offer.assert_not_called()


def test_manual_result_shows_the_dialog():
    fake = _fake(_manual_update_check=True)
    _available(fake)
    fake._show_update_dialog.assert_called_once()
    assert fake._manual_update_check is False


def test_result_while_a_prompt_is_open_does_not_stack_a_second():
    fake = _fake(_update_dialog_open=True, _update_auto_source="resume")
    _available(fake)
    fake._show_update_dialog.assert_not_called()
    fake._show_pending_update_offer.assert_not_called()


# --- "Not now" snoozes for 3 days ------------------------------------------------------------------

def test_not_now_snoozes_automatic_offers_for_three_days():
    fake = _fake(_update_dismissed_at={"2.2.1": time.time() - 3600}, _update_auto_source="periodic")
    _available(fake, "v2.2.1")
    fake._show_pending_update_offer.assert_not_called()
    fake._update_auto_source = "periodic"
    _available(fake, "v2.2.2")                   # a newer release is offered
    fake._show_pending_update_offer.assert_called_once()


def test_not_now_expires_after_three_days():
    fake = _fake(_update_dismissed_at={"2.2.1": time.time() - SNOOZE - 60}, _update_auto_source="resume")
    _available(fake, "v2.2.1")
    fake._show_pending_update_offer.assert_called_once()


def test_not_now_also_snoozes_the_startup_dialog_path():
    fake = _fake(_update_dismissed_at={"2.2.1": time.time() - 60})
    _available(fake, "v2.2.1")
    fake._show_update_dialog.assert_not_called()


def test_manual_check_still_shows_a_snoozed_version():
    fake = _fake(_update_dismissed_at={"2.2.1": time.time()}, _manual_update_check=True)
    _available(fake, "v2.2.1")
    fake._show_update_dialog.assert_called_once()


def test_show_dialog_marks_open_and_records_not_now_time(monkeypatch):
    """The open flag is up for exactly the dialog's lifetime; 'Not now' stores its time."""
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
    before = time.time()
    W._show_update_dialog(fake, "v2.2.1", "u")
    assert seen["open_during_exec"] is True
    assert fake._update_dialog_open is False
    assert before <= fake._update_dismissed_at["2.2.1"] <= time.time()


# --- HIGH 1: no leftover manual boxes ----------------------------------------------------------------

class _Host(QObject):
    """A real QObject host so the real UpdateChecker's real signals reach the real handlers."""

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger("test_update_recheck")
        self.boxes = []
        self.dialogs = []
        self._will_quit_app = False
        self._update_dialog_open = False
        self._manual_update_check = False
        self._update_auto_source = None
        self._update_dismissed_at = {}
        self._pending_update_offer = None
        self._show_pending_update_offer = MagicMock()
        _bind(self, "check_for_updates", "_on_update_available", "_on_update_check_up_to_date",
              "_on_update_check_failed", "_take_update_check_context", "_app_is_quitting",
              "_update_recently_dismissed", "_run_automatic_update_check")
        from netspeedtray.core.update_checker import UpdateChecker
        self.update_checker = UpdateChecker({"check_for_updates": True}, self)
        self.update_checker.check_now = MagicMock()          # no network, ever
        self.update_checker.should_check = MagicMock(return_value=True)
        self.update_checker.update_available.connect(self._on_update_available)
        self.update_checker.up_to_date.connect(self._on_update_check_up_to_date)
        self.update_checker.check_failed.connect(self._on_update_check_failed)

    def _show_update_dialog(self, latest, *_a):            # the user answers "Not now"
        self.dialogs.append(latest)
        self._update_dismissed_at[latest.lstrip("vV")] = time.time()

    def _on_up_to_date_manual(self):
        self.boxes.append("up to date")

    def _on_check_failed_manual(self, _e):
        self.boxes.append("failed")


@pytest.fixture
def host(q_app, monkeypatch):
    from netspeedtray.core import update_checker as uc
    monkeypatch.setattr(uc, "update_check_disabled_by_policy", lambda: False)
    monkeypatch.setattr(constants.app, "VERSION", "2.2.0", raising=False)
    h = _Host()
    yield h
    h.deleteLater()


def test_manual_check_finding_an_update_leaves_no_box_for_the_next_automatic_check(host):
    """The #508 review repro: manual -> update -> Not now; later automatic checks fail / are up to
    date -> NO box (the old per-click SingleShot connections were still armed)."""
    host.check_for_updates()
    host.update_checker._on_result("v2.2.1", "u")
    assert host.dialogs == ["v2.2.1"] and host.boxes == []
    host._run_automatic_update_check("resume")
    host.update_checker._on_failed("DNS not ready")
    host._run_automatic_update_check("periodic")
    host.update_checker._on_result("v2.2.0", "u")           # up to date
    assert host.boxes == []


def test_manual_check_still_shows_its_own_boxes(host):
    host.check_for_updates()
    host.update_checker._on_failed("offline")
    host.check_for_updates()
    host.update_checker._on_result("v2.2.0", "u")
    assert host.boxes == ["failed", "up to date"]


def test_older_latest_stays_quiet(host):
    """rc build vs an older final Latest (2.2.0-rc.7 vs v2.1.7): up_to_date, never a prompt."""
    constants.app.VERSION = "2.2.0-rc.7"
    host._run_automatic_update_check("periodic")
    host.update_checker._on_result("v2.1.7", "https://example.invalid/r", "", "", "")
    assert host.dialogs == [] and host.boxes == []
    host._show_pending_update_offer.assert_not_called()


# --- the flyout offer ------------------------------------------------------------------------------

def _offer_fake(blocked=False):
    fake = _fake(i18n=MagicMock(), _update_offer_retry_timer=MagicMock(), _update_flyout=None,
                 _pending_update_offer=("v2.2.1", "u", "", "", ""))
    fake._update_offer_blocked = MagicMock(return_value=blocked)
    fake.frameGeometry = MagicMock()
    fake._open_update_dialog_from_offer = MagicMock()
    return fake


def test_offer_waits_while_blocked_then_shows_a_flyout(q_app):
    fake = _offer_fake(blocked=True)
    with patch("netspeedtray.views.flyout.Flyout") as flyout_cls:
        W._show_pending_update_offer(fake)
        flyout_cls.assert_not_called()
        fake._update_offer_retry_timer.start.assert_called_once()
        assert fake._pending_update_offer is not None        # kept for the retry
        fake._update_offer_blocked.return_value = False
        W._show_pending_update_offer(fake)                    # the retry
        flyout_cls.assert_called_once()
        flyout_cls.return_value.show_at.assert_called_once()
    assert fake._pending_update_offer is None
    fake._show_update_dialog.assert_not_called()             # only on the user's click


def test_flyout_click_opens_the_dialog_deferred(q_app, monkeypatch):
    fake = _offer_fake()
    deferred = []
    monkeypatch.setattr("netspeedtray.views.widget.main.QTimer.singleShot",
                        lambda _ms, fn: deferred.append(fn))
    with patch("netspeedtray.views.flyout.Flyout") as flyout_cls:
        W._show_pending_update_offer(fake)
        click = flyout_cls.return_value.action_clicked.connect.call_args[0][0]
    click()
    fake._open_update_dialog_from_offer.assert_not_called()  # not inside the button's handler
    deferred[0]()
    fake._open_update_dialog_from_offer.assert_called_once_with(("v2.2.1", "u", "", "", ""))


@pytest.mark.parametrize("fullscreen, modal, loop_level, blocked", [
    (False, None, 1, False),
    (True, None, 1, True),
    (False, object(), 1, True),
    (False, None, 2, True),
])
def test_offer_blocked_by_fullscreen_modal_or_nested_loop(monkeypatch, fullscreen, modal, loop_level, blocked):
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QApplication
    handler = MagicMock()
    handler.is_fullscreen_active.return_value = fullscreen
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: modal))
    thread = MagicMock()
    thread.loopLevel.return_value = loop_level
    monkeypatch.setattr(QThread, "currentThread", staticmethod(lambda: thread))
    fake = types.SimpleNamespace(system_event_handler=handler)
    assert W._update_offer_blocked(fake) is blocked


def test_open_from_offer_skips_while_quitting_or_dialog_open():
    fake = _fake(_will_quit_app=True)
    W._open_update_dialog_from_offer(fake, ("v2.2.1", "u", "", "", ""))
    fake._will_quit_app, fake._update_dialog_open = False, True
    W._open_update_dialog_from_offer(fake, ("v2.2.1", "u", "", "", ""))
    fake._show_update_dialog.assert_not_called()


# --- resume ---------------------------------------------------------------------------------------

def test_resume_message_schedules_the_update_check(monkeypatch):
    monkeypatch.setattr("netspeedtray.views.widget.main.QTimer.singleShot", lambda _ms, fn: None)
    fake = _fake()
    fake._on_environment_changed = MagicMock()
    fake._schedule_resume_update_check = MagicMock()
    m = _NativeMSG()
    m.message = _WM_POWERBROADCAST
    m.wParam = _PBT_APMRESUMEAUTOMATIC
    assert W.nativeEvent(fake, b"windows_generic_MSG", ctypes.addressof(m)) == (False, 0)
    fake._schedule_resume_update_check.assert_called_once()


def test_resume_timer_fires_a_check_when_due():
    timer = MagicMock()
    fake = _fake(should_check=True, _update_resume_check_timer=timer)
    W._schedule_resume_update_check(fake)
    timer.start.assert_called_once()
    _tick(fake, "resume")                       # what the timer's timeout runs
    fake.update_checker.check_now.assert_called_once()
    assert fake._update_auto_source == "resume"


# --- wiring + shutdown (real QTimers) -----------------------------------------------------------

def test_constants():
    assert constants.app.UPDATE_RECHECK_INTERVAL_MS == 3 * 60 * 60 * 1000
    assert constants.app.UPDATE_NOT_NOW_SNOOZE_S == 3 * 24 * 60 * 60
    assert constants.app.UPDATE_RESUME_CHECK_DELAY_MS > 0
    assert constants.app.UPDATE_OFFER_RETRY_MS > 0 and constants.app.UPDATE_FLYOUT_DISMISS_MS > 0


def test_setup_timers_on_a_real_qobject_starts_the_recheck_timer(q_app, monkeypatch):
    from netspeedtray.core import update_checker as uc
    monkeypatch.setattr(uc, "update_check_disabled_by_policy", lambda: False)

    class _Real(QObject):
        pass

    host = _Real()
    for k, v in dict(config={"check_for_updates": False}, i18n=MagicMock(), logger=MagicMock(),
                     position_manager=MagicMock(), _state_watcher_timer=MagicMock(),
                     _cycle_timer=MagicMock(), widget_state=MagicMock(), update_config=MagicMock(),
                     _show_usage_alert=MagicMock(), _execute_refresh=MagicMock()).items():
        setattr(host, k, v)
    _bind(host, "_on_update_available", "_on_update_check_up_to_date", "_on_update_check_failed",
          "_run_automatic_update_check", "_show_pending_update_offer")
    try:
        W._setup_timers(host)
        assert host._update_recheck_timer.isActive()
        assert host._update_recheck_timer.interval() == constants.app.UPDATE_RECHECK_INTERVAL_MS
        assert host._update_resume_check_timer.isSingleShot()
        assert not host._update_resume_check_timer.isActive()   # only a resume starts it
        host.logger.error.assert_not_called()                   # the guard did not swallow anything
    finally:
        for name in ("_update_recheck_timer", "_update_resume_check_timer",
                     "_update_offer_retry_timer", "_usage_alert_timer"):
            t = getattr(host, name, None)
            if t is not None:
                t.stop()
        host.deleteLater()


def test_cleanup_stops_the_update_timers(q_app, monkeypatch):
    from netspeedtray.utils import font_release
    from netspeedtray.core.db import history_backup
    monkeypatch.setattr(font_release, "cancel_pending_release", lambda **_k: None)
    monkeypatch.setattr(history_backup, "cancel_all", lambda *_a: None)
    from PyQt6.QtCore import QTimer
    periodic, resume, retry = QTimer(), QTimer(), QTimer()
    periodic.start(constants.app.UPDATE_RECHECK_INTERVAL_MS)
    resume.setSingleShot(True)
    resume.start(constants.app.UPDATE_RESUME_CHECK_DELAY_MS)
    retry.setSingleShot(True)
    retry.start(constants.app.UPDATE_OFFER_RETRY_MS)

    fake = types.SimpleNamespace(
        logger=MagicMock(), config_manager=MagicMock(), config={},
        update_checker=MagicMock(), _pending_update_offer=("v2.2.1",),
        _update_recheck_timer=periodic, _update_resume_check_timer=resume,
        _update_offer_retry_timer=retry,
        _hover_card_timer=MagicMock(), _hide_usage_hover_card=MagicMock(),
        _capture_position_config=MagicMock(),
    )
    W.cleanup(fake, save_config=False)
    assert not periodic.isActive() and not resume.isActive() and not retry.isActive()
    assert fake._pending_update_offer is None
    fake.update_checker.stop.assert_called_once()
