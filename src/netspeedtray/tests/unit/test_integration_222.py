"""
2.2.2 integration: where two of D1-D6 meet (integration/2.2.2; pytest-qt, offscreen).

Each PR tests its own feature. These pin the seams the merge created:

* D5 x D1/D2 (#597 x #592) - the Monitor's "Turn on" / "Not now" save the internet-check keys while
  Settings is open. #592 changed Settings' save path (it no longer writes back the app state its pages
  don't own), so the keys must survive Settings' Save AND Cancel, the preview must never reach the
  file, and app state written while Settings is open (a first-run tip shown) must not be rolled back
  by the Monitor's save either.
* D3 x D1 (#591 x #592) - a first run: the first-run tip and a Start-menu launch's "running" panel.
  The tip steps aside for the panel (never lost) and comes back after it closes.
* D4 x D6 / D5 (#590 x #593 / #597) - the NoticeLinks inside D6's Settings > Hardware notice and
  D5's offer: real buttons, keyboard-activated, doing their own action (never a silent opt-in).
"""
import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import startup_notices as logic
from netspeedtray.utils.components import NoticeLink
from netspeedtray.views import running_panel as rp
from netspeedtray.views.monitor import internet_offer as IO
from netspeedtray.views.widget import startup_notices as ui
from netspeedtray.views.widget.main import NetworkSpeedWidget

EN = I18nStrings("en_US")
DEFAULTS = constants.config.defaults.DEFAULT_CONFIG


@pytest.fixture(autouse=True)
def _quiet_world(monkeypatch):
    """No policy, signed in long ago, no High Contrast, no real foreground juggling or message boxes."""
    monkeypatch.setattr(logic, "tips_disabled_by_policy", lambda: False)
    monkeypatch.setattr(ui, "_seconds_since_sign_in", lambda: 3600.0)
    monkeypatch.setattr(rp, "_update_check_disabled", lambda: False)
    monkeypatch.setattr(rp, "read_high_contrast", lambda: (False, None, None))
    monkeypatch.setattr(rp, "_force_foreground", MagicMock())
    for name in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)


# --- D5 x #592: the Monitor's saves while Settings is open ---------------------------------------------

class _Main:
    """The parts of NetworkSpeedWidget that SettingsDialog and the D5 offer touch, with the REAL
    save_config_keys / turn_on_internet_check bound. ``saved`` is every dict written to the file."""

    save_config_keys = NetworkSpeedWidget.save_config_keys
    turn_on_internet_check = NetworkSpeedWidget.turn_on_internet_check

    def __init__(self, cfg):
        self.config = dict(cfg)
        self.logger = MagicMock()
        self.metrics_endpoint = None
        self.settings_dialog = None
        self.apply_latency_probe = MagicMock()
        self.saved = []
        manager = MagicMock()
        manager.save.side_effect = lambda c: self.saved.append(dict(c))
        self.config_controller = types.SimpleNamespace(
            config_manager=manager,
            update_config=lambda u, **k: (self.config.update(u), self.saved.append(dict(self.config))))

    def handle_settings_changed(self, updated, save_to_disk=True):
        self.config.update(updated)                          # ConfigController's merge semantics
        if save_to_disk:
            self.saved.append(dict(self.config))

    def toggle_startup(self, on):
        pass

    def update(self):
        pass

    def width(self):
        return 200


def _open_settings(qtbot, main, build_network_page):
    from netspeedtray.views.settings.dialog import SettingsDialog
    dlg = SettingsDialog(main_widget=main, config=dict(main.config), version="2.2.2", i18n=EN,
                         available_interfaces=["Ethernet"], is_startup_enabled=False)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.settings_changed.connect(lambda c: main.handle_settings_changed(c, save_to_disk=False))
    qtbot.addWidget(dlg)
    dlg.show()
    if build_network_page:
        dlg.open_page("network")
        dlg._ensure_page("interfaces")
    main.settings_dialog = dlg
    return dlg


def _preview_font_edit(dlg, main):
    """An unsaved Settings edit, previewed live (the font size lands in the live config)."""
    main.handle_settings_changed({"font_size": 20}, save_to_disk=False)


@pytest.mark.parametrize("build_network_page", [True, False])
@pytest.mark.parametrize("close", ["save", "cancel"])
def test_turn_on_with_settings_open_survives_save_and_cancel(qtbot, close, build_network_page):
    main = _Main(dict(DEFAULTS, latency_public_enabled=False, font_size=9))
    dlg = _open_settings(qtbot, main, build_network_page)
    _preview_font_edit(dlg, main)
    assert IO.InternetOffer(main, main.config).turn_on() is True
    written = main.saved[-1]
    assert written["latency_public_enabled"] is True
    assert written["font_size"] == 9                         # the preview never reaches the file
    if close == "save":
        dlg._save_and_close()
    else:
        dlg.reject()
    assert main.config["latency_public_enabled"] is True     # neither Save nor Cancel turns it off
    if close == "save":
        assert main.saved[-1]["latency_public_enabled"] is True
    else:
        assert main.config["font_size"] == 9                 # Cancel still reverts the preview


@pytest.mark.parametrize("close", ["save", "cancel"])
def test_not_now_with_settings_open_survives_save_and_cancel(qtbot, close):
    main = _Main(dict(DEFAULTS, internet_offer_declines=0))
    dlg = _open_settings(qtbot, main, build_network_page=True)
    IO.InternetOffer(main, main.config).decline()
    assert main.saved[-1]["internet_offer_declines"] == 1
    if close == "save":
        dlg._save_and_close()
        assert main.saved[-1]["internet_offer_declines"] == 1
    else:
        dlg.reject()
    assert main.config["internet_offer_declines"] == 1
    # #592's filter: Settings never sends the app-state key back at all.
    assert "internet_offer_declines" in dlg.unowned_app_state_keys()


def test_the_monitors_save_keeps_app_state_written_while_settings_is_open(qtbot):
    """Integration fix: save_config_keys used to write the dialog's opened-at snapshot for every key
    but the new one, so a first-run tip shown (or the widget dragged) while Settings was open was rolled
    back on disk. Since #592 the LIVE config is the truth for app state Settings doesn't own."""
    main = _Main(dict(DEFAULTS, first_run_tip_shown=False, position_x=None))
    dlg = _open_settings(qtbot, main, build_network_page=False)
    _preview_font_edit(dlg, main)
    main.config.update(first_run_tip_shown=True, last_seen_version="2.2.2", position_x=640)
    main.turn_on_internet_check()
    written = main.saved[-1]
    assert written["latency_public_enabled"] is True
    assert written["first_run_tip_shown"] is True and written["last_seen_version"] == "2.2.2"
    assert written["position_x"] == 640
    assert written["font_size"] == DEFAULTS["font_size"]     # still never the preview
    dlg.reject()
    assert main.config["first_run_tip_shown"] is True and main.config["position_x"] == 640


def test_a_page_owned_app_state_key_still_comes_from_the_baseline(qtbot):
    """start_with_windows is in RESET_ALL_KEEPS but the General page shows it: an unsaved flip of it is
    the user's edit, so the Monitor's save writes the dialog's baseline value, not the preview."""
    main = _Main(dict(DEFAULTS, start_with_windows=False))
    dlg = _open_settings(qtbot, main, build_network_page=False)
    dlg._ensure_page("general")
    assert "start_with_windows" not in dlg.unowned_app_state_keys()
    main.config["start_with_windows"] = True                 # previewed, not saved
    main.save_config_keys({"internet_offer_declines": 1})
    assert main.saved[-1]["start_with_windows"] is False
    dlg.reject()


# --- D3 x D1: the first-run tip and the "running" panel -----------------------------------------------

def _first_run_widget():
    w = types.SimpleNamespace()
    w.config = dict(DEFAULTS)                                # a brand-new install
    w.saved = []
    w.update_config = lambda updates, save_to_disk=True: w.saved.append(dict(updates))
    w.isVisible = lambda: True
    w._update_offer_blocked = lambda: False
    w._app_is_quitting = lambda: False
    w.position_manager = types.SimpleNamespace(is_yielding_to_apps=lambda: False)
    from PyQt6.QtCore import QRect
    w.frameGeometry = lambda: QRect(1500, 1000, 120, 40)
    w.i18n = EN
    w.logger = MagicMock()
    w.settings_dialog = None
    w._running_panel = None
    # What the panel reads / calls.
    w.config.update(unit_type="bits_decimal", speed_display_mode="auto")
    w.upload_speed, w.download_speed, w.is_paused = 1.2, 88.6, False
    w.latency_headline_hop = w.latency_headline_ms = w.latency_loss = None
    w.latency_link_lost = False
    w.is_startup_enabled = lambda: False
    for name in ("show_settings", "open_monitor_window", "open_connection_check", "check_for_updates",
                 "fully_exit_application", "resume", "_open_update_dialog_from_offer"):
        setattr(w, name, MagicMock())
    w._startup_notices = ui.StartupNotices(w, version="2.2.2")
    return w


def _tip_shown(n):
    card = n._live_card()
    return card is not None and card.isVisible()


def _close_panel(w):
    panel = w._running_panel
    panel.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)   # WA_DeleteOnClose
    QApplication.processEvents()
    return panel


def test_a_visible_tip_steps_aside_for_the_panel_and_comes_back(qtbot):
    w = _first_run_widget()
    n = w._startup_notices
    assert n.start() == logic.NOTICE_TIP
    n._timer.stop()
    n.try_show()
    assert _tip_shown(n) and w.config["first_run_tip_shown"] is True
    NetworkSpeedWidget.show_running_panel(w)                 # Start -> NetworkSpeedTray, first run
    assert w._running_panel is not None and w._running_panel.isVisible()
    assert not _tip_shown(n) and n._live_card() is not None  # stepped aside, not answered
    assert n.blocked_reason() == "the running panel is open"
    n.try_show()
    assert not _tip_shown(n)                                 # still waits while the panel is up
    _close_panel(w)
    assert w._running_panel is None
    assert n._timer.isActive() and n._timer.interval() == constants.app.STARTUP_NOTICE_DELAY_MS
    n._timer.stop()
    n.try_show()
    assert _tip_shown(n)                                     # back beside the widget
    n._live_card()._dismiss()


def test_a_tip_still_waiting_does_not_show_beside_the_panel_and_is_not_used_up(qtbot):
    w = _first_run_widget()
    n = w._startup_notices
    n.start()
    NetworkSpeedWidget.show_running_panel(w)                 # inside the tip's 5 s delay
    n._timer.stop()
    n.try_show()
    assert not _tip_shown(n)
    assert w.config.get("first_run_tip_shown", False) is False   # still owed
    _close_panel(w)
    n._timer.stop()
    n.try_show()
    assert _tip_shown(n) and w.config["first_run_tip_shown"] is True
    n._live_card()._dismiss()


def test_the_panels_own_hint_and_the_tip_teach_the_same_gestures():
    """Both ship in 2.2.2 and a first run can show both (one after the other): keep them in step."""
    assert "Point to the speeds" in EN.RUNNING_PANEL_HINT
    assert EN.FIRST_RUN_TIP_BODY.startswith("Point to the speeds")


# --- D4 inside D6 and D5 ------------------------------------------------------------------------------

def test_d6_server_off_notice_link_in_real_settings_is_a_keyboard_button(qtbot, monkeypatch):
    from netspeedtray.views import temp_onboarding_dialog as tod
    from netspeedtray.utils import taskbar_appearance as ta
    monkeypatch.setattr(ta, "read_high_contrast", lambda: (False, None, None))
    opened = []

    class _FakeSetup:
        def __init__(self, *a, focus_step=1, **k):
            opened.append(focus_step)

        def exec(self):
            return 0
    monkeypatch.setattr(tod, "TempOnboardingDialog", _FakeSetup)
    main = _Main(dict(DEFAULTS, show_hardware_temps=True))
    main.monitor_thread = types.SimpleNamespace(lhm_status=constants.lhm.STATUS_SERVER_OFF,
                                                lhm_port=constants.lhm.HTTP_PORT)
    dlg = _open_settings(qtbot, main, build_network_page=False)
    before = dict(main.config)
    assert dlg.open_page("hardware", "monitor_cpu_enabled") is True    # D4's router onto D6's page
    page = dlg.hardware_page
    page.refresh_lhm_status()
    bar = page.lhm_server_off_notice
    assert bar.isVisible()
    assert isinstance(bar.link, NoticeLink) and isinstance(page.temps_setup_link, NoticeLink)
    button = bar.link.button()
    assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert button.accessibleName() == EN.LHM_SERVER_OFF_ACTION
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert opened == [2]                                     # "How to turn it on" -> step 2
    assert main.config == before                             # a link never changes a setting
    dlg.reject()


def test_d5_offer_link_is_a_d4_noticelink_that_asks_before_turning_anything_on(qtbot):
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    cfg = dict(DEFAULTS, latency_public_enabled=False, latency_enabled=True)
    mw = MagicMock()
    mw.config = cfg
    mw.system_power = None
    mw.latency_gw, mw.latency_anchor, mw.latency_link_lost = 4.0, None, False
    mw.internet_offer_snoozed = False
    mw.save_config_keys.side_effect = lambda u: cfg.update(u)
    mw.turn_on_internet_check.side_effect = lambda: mw.save_config_keys({"latency_public_enabled": True})
    ov = OverviewTab(mw, cfg, EN)
    try:
        ov.resize(900, 600)
        ov.show()
        now = datetime.now()
        ov._latency_events = {"count": 0, "last_start": None, "total_down_seconds": 0.0, "runs": [],
                               "hop": "gateway", "rows": 100, "first": now - timedelta(hours=24),
                               "window_start": now - timedelta(hours=24)}   # a router line, no drops
        ov._render_health_strip(mw)
        link = ov._internet_link
        assert isinstance(link, NoticeLink) and not link.isHidden()
        link.button().setFocus()
        QTest.keyClick(link.button(), Qt.Key.Key_Space)
        tip = ov._internet_tip
        assert tip is not None and tip.isVisible()           # an inline confirm, not Settings
        mw.open_settings.assert_not_called()
        mw.turn_on_internet_check.assert_not_called()
        assert cfg["latency_public_enabled"] is False
        tip.confirm.turn_on_button().click()
        mw.turn_on_internet_check.assert_called_once()
        assert cfg["latency_public_enabled"] is True
    finally:
        if ov._internet_tip is not None:
            ov._internet_tip.close_for_action()
        ov.teardown()
        ov.deleteLater()
