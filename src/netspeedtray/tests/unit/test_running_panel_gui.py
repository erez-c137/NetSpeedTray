"""
2.2.2 D3: the "NetSpeedTray is running" panel a Start-menu launch opens (pytest-qt, offscreen).

There is no tray icon, so this small, normal, focusable window is what "opening" the app shows: name +
version with an inline update check, a status card that says what is true right now (running / hidden /
paused), and navigation rows - Monitor, Settings, Check connection - plus a quiet Quit. Each row is the
widget's existing action (run first, then the panel closes). Keyboard: the first row has focus, Tab and
Up/Down move, Enter presses, Esc closes; the focus ring shows only after keyboard use. It is the first
version of 2.3's About window (C3/C7).
"""
import types
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication, QDialog

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views import running_panel as rp
from netspeedtray.views.running_panel import RunningPanel


@pytest.fixture
def i18n():
    return I18nStrings("en_US")


@pytest.fixture(autouse=True)
def _no_policy_no_hc(monkeypatch):
    monkeypatch.setattr(rp, "_update_check_disabled", lambda: False)
    monkeypatch.setattr(rp, "read_high_contrast", lambda: (False, None, None))
    monkeypatch.setattr(rp, "_force_foreground", MagicMock())


def _main(i18n, **over):
    mw = types.SimpleNamespace(
        config={"unit_type": "bits_decimal", "decimal_places": 1, "speed_display_mode": "auto",
                "short_unit_labels": False, "latency_public_enabled": False},
        i18n=i18n, upload_speed=1.2, download_speed=88.6, is_paused=False,
        latency_headline_hop=None, latency_headline_ms=None, latency_loss=None, latency_link_lost=False,
        isVisible=lambda: True, _app_is_quitting=lambda: False, is_startup_enabled=lambda: False,
        show_settings=MagicMock(), open_monitor_window=MagicMock(), open_connection_check=MagicMock(),
        check_for_updates=MagicMock(), fully_exit_application=MagicMock(), resume=MagicMock(),
        _open_update_dialog_from_offer=MagicMock())
    for k, v in over.items():
        setattr(mw, k, v)
    return mw


@pytest.fixture
def panel(qtbot, i18n):
    made = []

    def make(version="2.2.2", keep=True, **over):
        mw = _main(i18n, **over)
        p = RunningPanel(mw, i18n, version=version)
        if keep:   # most tests inspect the panel after it closes
            p.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            qtbot.addWidget(p)
        made.append(p)
        return p, mw
    return make


ACTIONS = ("show_settings", "open_monitor_window", "open_connection_check", "fully_exit_application",
           "_open_update_dialog_from_offer")


# --- content ----------------------------------------------------------------------------------------

def test_header_shows_name_version_and_the_update_link(panel, i18n):
    p, _ = panel()
    assert p.windowTitle() == "NetSpeedTray"
    assert p.title_label.text() == "NetSpeedTray"
    assert p.version_label.text() == i18n.RUNNING_PANEL_VERSION.format(version="2.2.2")
    assert p.version_label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert p.update_link.text() == i18n.RUNNING_PANEL_CHECK_UPDATES
    assert p.beta_badge.isHidden()
    assert not p.isModal()


def test_a_prerelease_shows_the_beta_badge(panel, i18n):
    p, _ = panel(version="2.2.2-beta.1")
    assert not p.beta_badge.isHidden() and p.beta_badge.text() == i18n.RUNNING_PANEL_BETA


def test_it_is_a_normal_focusable_window_with_a_fixed_size(panel):
    p, mw = panel()
    flags = p.windowFlags()
    assert (flags & Qt.WindowType.Tool) != Qt.WindowType.Tool
    assert not flags & Qt.WindowType.WindowStaysOnTopHint
    assert not p.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    p.show()
    assert p.minimumSize() == p.maximumSize()          # can't be dragged into odd shapes
    width = p.width()
    mw.download_speed, mw.upload_speed = 987.6, 876.5   # the live values never resize it
    p.refresh_status()
    assert p.width() == width


def test_speeds_and_connection_are_two_lines(panel, i18n):
    p, _ = panel(latency_headline_hop="gateway", latency_headline_ms=3.0, latency_loss=0.0)
    assert p.speeds_label.text() == "↓ 88.6 Mbps   ↑ 1.2 Mbps"
    assert p.connection_label.text() == f"{i18n.CHECK_CONN_ROUTER}: {i18n.LATENCY_GOOD} · 3 ms"
    assert p.speeds_label.minimumWidth() >= p.speeds_label.fontMetrics().horizontalAdvance(
        "↓ 888.8 Mbps   ↑ 888.8 Mbps")     # sized from stable reference strings


def test_connection_wording_follows_the_overview(panel, i18n):
    p, _ = panel(latency_headline_hop="anchor", latency_headline_ms=20.0, latency_loss=0.0)
    assert p.connection_label.text().startswith(f"{i18n.LATENCY_LABEL}: {i18n.LATENCY_GOOD}")
    p, _ = panel(latency_headline_hop="gateway", latency_link_lost=True)
    assert p.connection_label.text() == f"{i18n.CHECK_CONN_ROUTER}: {i18n.LATENCY_NO_CONNECTION}"
    p, _ = panel()                                       # no probe data: no connection line
    assert p.connection_label.isHidden()


def test_paused_says_so_and_offers_resume(panel, i18n):
    p, mw = panel(is_paused=True)
    assert p.running_label.text() == i18n.MONITOR_PAUSED
    assert not p.paused_label.isHidden() and p.speeds_label.isHidden()
    assert not p.resume_link.isHidden()
    p.show()
    p.resume_link.click()
    mw.resume.assert_called_once_with()
    assert p.isVisible()                                 # Resume keeps the panel open
    mw.is_paused = False
    p.refresh_status()
    assert p.running_label.text() == i18n.RUNNING_PANEL_RUNNING and p.paused_label.isHidden()


@pytest.mark.parametrize("state,key", [
    ("fullscreen", "RUNNING_PANEL_HIDDEN_FULLSCREEN"),
    ("yielded", "RUNNING_PANEL_HIDDEN_YIELD"),
    ("hidden", "RUNNING_PANEL_HIDDEN"),
])
def test_the_title_stays_true_when_the_widget_is_hidden(panel, i18n, state, key):
    handler = MagicMock()
    handler.is_fullscreen_active.return_value = state == "fullscreen"
    pm = MagicMock()
    pm.is_yielding_to_apps.return_value = state == "yielded"
    p, _ = panel(isVisible=lambda: False, system_event_handler=handler, position_manager=pm)
    assert p.running_label.text() == getattr(i18n, key)


def test_status_refreshes_on_a_timer_only_while_shown(panel, qtbot):
    p, mw = panel()
    assert not p._status_timer.isActive()
    p.show()
    assert p._status_timer.isActive()
    mw.download_speed = 42.0
    qtbot.waitUntil(lambda: "42.0" in p.speeds_label.text(), timeout=3000)
    p.close()
    assert not p._status_timer.isActive()


def test_quit_tooltip_only_when_it_starts_with_windows(panel, i18n):
    p, _ = panel()
    assert p.quit_button.toolTip() == ""
    p, _ = panel(is_startup_enabled=lambda: True)
    assert p.quit_button.toolTip() == i18n.RUNNING_PANEL_QUIT_TIP


# --- rows ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("button,action", [
    ("monitor_button", "open_monitor_window"),
    ("settings_button", "show_settings"),
    ("check_connection_button", "open_connection_check"),
    ("quit_button", "fully_exit_application"),
])
def test_each_row_runs_the_widgets_action_first_then_closes(panel, qtbot, button, action):
    p, mw = panel()
    p.show()
    seen = []
    getattr(mw, action).side_effect = lambda: seen.append(p.isVisible())
    getattr(p, button).click()
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)
    getattr(mw, action).assert_called_once_with()
    assert seen == [True]                              # the action ran while the panel was still up
    assert not any(getattr(mw, o).called for o in ACTIONS if o != action)


def test_nothing_runs_once_the_app_is_quitting(panel, qtbot):
    p, mw = panel(_app_is_quitting=lambda: True)
    p.show()
    p.settings_button.click()
    qtbot.wait(50)
    mw.show_settings.assert_not_called()


def test_row_order_and_glyph_chevron_rows(panel):
    p, _ = panel()
    assert p.rows == [p.monitor_button, p.settings_button, p.check_connection_button]
    assert all(r.objectName() == "Row" for r in p.rows)
    assert all(r.cursor().shape() != Qt.CursorShape.PointingHandCursor for r in p.rows)


# --- inline update check -----------------------------------------------------------------------------

def test_update_check_is_inline_and_keeps_the_panel_open(panel, i18n):
    p, mw = panel()
    p.show()
    p.update_link.click()
    reply = mw.check_for_updates.call_args.kwargs["reply_to"]
    assert p.isVisible() and p.update_state_label.text() == i18n.RUNNING_PANEL_CHECKING
    assert p.update_link.isHidden()
    assert reply("up_to_date") is True
    assert p.update_state_label.text() == i18n.RUNNING_PANEL_UP_TO_DATE


def test_an_available_update_offers_update_which_opens_the_dialog(panel, qtbot, i18n):
    p, mw = panel()
    p.show()
    p.update_link.click()
    offer = ("2.2.3", "url", "", "", "")
    assert mw.check_for_updates.call_args.kwargs["reply_to"]("available", "2.2.3", offer) is True
    assert p.update_state_label.text() == i18n.RUNNING_PANEL_UPDATE_AVAILABLE.format(version="2.2.3")
    assert not p.update_now_link.isHidden()
    p.update_now_link.click()
    qtbot.waitUntil(lambda: mw._open_update_dialog_from_offer.called, timeout=2000)
    mw._open_update_dialog_from_offer.assert_called_once_with(offer)


def test_a_failed_check_says_so_and_can_retry(panel, i18n):
    p, mw = panel()
    p.show()
    p.update_link.click()
    mw.check_for_updates.call_args.kwargs["reply_to"]("failed", "boom")
    assert p.update_state_label.text() == i18n.RUNNING_PANEL_CHECK_FAILED
    assert not p.update_link.isHidden()


def test_a_closed_panel_hands_the_reply_back(panel):
    p, mw = panel()
    p.show()
    p.update_link.click()
    reply = mw.check_for_updates.call_args.kwargs["reply_to"]
    p.close()
    assert reply("up_to_date") is False                # the widget then shows its usual reply


def test_update_check_hidden_under_the_it_policy(panel, monkeypatch):
    monkeypatch.setattr(rp, "_update_check_disabled", lambda: True)
    p, _ = panel()
    p.show()
    assert p.update_link.isHidden() and p.update_sep.isHidden()


def test_the_widget_routes_a_manual_reply_to_the_panel_or_its_boxes():
    """main.py side of the inline check: the reply goes to the asker; if it can't show it, the usual
    manual message box / dialog appears instead."""
    from netspeedtray.views.widget import main as main_mod
    W = main_mod.NetworkSpeedWidget
    w = types.SimpleNamespace(logger=MagicMock(), _update_reply_sink=None)
    sink = MagicMock(return_value=True)
    w._update_reply_sink = sink
    assert main_mod._deliver_update_reply(w, "up_to_date") is True and w._update_reply_sink is None
    sink.assert_called_once_with("up_to_date")
    w._update_reply_sink = MagicMock(side_effect=RuntimeError("deleted"))
    assert main_mod._deliver_update_reply(w, "failed", "x") is False
    assert main_mod._deliver_update_reply(w, "up_to_date") is False   # nobody asked
    # Through the real handlers: a panel that took it -> no box; one that couldn't -> the box.
    w = types.SimpleNamespace(_manual_update_check=True, _update_auto_source=None,
                              _update_reply_sink=MagicMock(return_value=True), _on_up_to_date_manual=MagicMock())
    w._take_update_check_context = W._take_update_check_context.__get__(w)
    W._on_update_check_up_to_date(w)
    w._on_up_to_date_manual.assert_not_called()
    w._manual_update_check, w._update_reply_sink = True, MagicMock(return_value=False)
    W._on_update_check_up_to_date(w)
    w._on_up_to_date_manual.assert_called_once_with()


# --- keyboard -------------------------------------------------------------------------------------------

def test_first_row_has_focus_and_tab_walks_in_order(panel, qtbot):
    p, _ = panel()
    p.show()
    p.activateWindow()
    qtbot.waitUntil(lambda: p.focusWidget() is p.monitor_button, timeout=2000)
    order = [p.monitor_button, p.settings_button, p.check_connection_button, p.quit_button]
    seen = [p.focusWidget()]
    for _ in range(len(order) - 1):
        p.focusNextChild()
        seen.append(p.focusWidget())
    assert seen == order


def test_the_focus_ring_waits_for_the_keyboard(panel, qtbot):
    p, _ = panel()
    p.show()
    assert p.keyboard_mode is False                    # a mouse user never sees a ring
    p.focusNextChild()
    assert p.keyboard_mode is True


def test_up_down_arrows_move_between_rows(panel, qtbot):
    p, _ = panel()
    p.show()
    p.monitor_button.setFocus()
    qtbot.keyClick(p.monitor_button, Qt.Key.Key_Down)
    assert p.focusWidget() is p.settings_button and p.keyboard_mode
    qtbot.keyClick(p.settings_button, Qt.Key.Key_Down)
    assert p.focusWidget() is p.check_connection_button
    qtbot.keyClick(p.check_connection_button, Qt.Key.Key_Down)   # no wrap
    assert p.focusWidget() is p.check_connection_button
    qtbot.keyClick(p.check_connection_button, Qt.Key.Key_Up)
    assert p.focusWidget() is p.settings_button


def test_enter_presses_the_focused_row(panel, qtbot):
    p, mw = panel()
    p.show()
    p.settings_button.setFocus()
    qtbot.keyClick(p.settings_button, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: mw.show_settings.called, timeout=2000)
    mw.open_monitor_window.assert_not_called()


def test_escape_closes_and_frees_the_panel(qtbot, i18n):
    """WA_DeleteOnClose left on: Esc must end in the panel's destruction and the owner forgetting it."""
    owner = types.SimpleNamespace(_running_panel=None)
    mw = _main(i18n)
    p = RunningPanel(mw, i18n, version="2.2.2")
    rp.keep_until_destroyed(owner, "_running_panel", p)
    destroyed = []
    p.destroyed.connect(lambda *_: destroyed.append(True))
    p.show()
    qtbot.keyClick(p, Qt.Key.Key_Escape)
    assert owner._running_panel is None                 # forgotten at once (review minor 7)
    qtbot.waitUntil(lambda: destroyed == [True], timeout=3000)
    mw.fully_exit_application.assert_not_called()


def test_rows_have_accessible_names(panel, i18n):
    p, _ = panel()
    assert p.settings_button.accessibleName() == i18n.SETTINGS_WINDOW_TITLE
    assert p.monitor_button.accessibleName() == i18n.MONITOR_WINDOW_TITLE
    assert p.check_connection_button.accessibleName() == i18n.CHECK_CONN_ACTION
    assert p.update_link.accessibleName() == i18n.RUNNING_PANEL_CHECK_UPDATES
    assert p.quit_button.accessibleName() == i18n.RUNNING_PANEL_QUIT
    assert p.accessibleName() == "NetSpeedTray"
    assert i18n.RUNNING_PANEL_RUNNING in p.accessibleDescription()


# --- theme / direction ----------------------------------------------------------------------------------

def _hc(monkeypatch):
    monkeypatch.setattr(rp, "read_high_contrast", lambda: (True, "#FFFF00", "#000000"))
    monkeypatch.setattr(rp, "_sys_color_hex", lambda index: {
        rp._COLOR_BTNFACE: "#000000", rp._COLOR_BTNTEXT: "#FFFFFF", rp._COLOR_HOTLIGHT: "#8080FF",
        rp._COLOR_HIGHLIGHT: "#1AEBFF", rp._COLOR_HIGHLIGHTTEXT: "#000001"}[index])


def test_high_contrast_uses_the_system_colours(panel, monkeypatch):
    _hc(monkeypatch)
    p, _ = panel()
    sheet = p.styleSheet()
    assert "#FFFF00" in sheet and "#1AEBFF" in sheet and "#8080FF" in sheet
    from netspeedtray.constants.styles import styles as tokens
    assert tokens.CARD_BG_DARK not in sheet and tokens.CARD_BG_LIGHT not in sheet
    assert p.palette_tokens["focus"] == "#1AEBFF"
    row = p.monitor_button
    assert row._icon_hover is not row._icon_normal      # icons re-tint to HighlightText on hover


def test_rtl_follows_the_application(panel):
    app = QApplication.instance()
    before = app.layoutDirection()
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    try:
        p, _ = panel()
        assert p.layoutDirection() == Qt.LayoutDirection.RightToLeft
        assert p.speeds_label.text().startswith("‏")      # an RTL paragraph
    finally:
        app.setLayoutDirection(before)


# --- the widget side: one panel, modal dialogs, quit ---------------------------------------------------

def _widget_fake(i18n):
    mw = _main(i18n)
    mw.logger = MagicMock()
    mw._running_panel = None
    return mw


def test_show_running_panel_is_single_instance(qtbot, i18n):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    mw = _widget_fake(i18n)
    NetworkSpeedWidget.show_running_panel(mw)
    first = mw._running_panel
    assert isinstance(first, RunningPanel) and first.isVisible()
    NetworkSpeedWidget.show_running_panel(mw)
    assert mw._running_panel is first                    # re-raised, not a second window
    assert len([w for w in QApplication.topLevelWidgets()
                if isinstance(w, RunningPanel) and w.isVisible()]) == 1
    assert rp._force_foreground.call_count >= 2          # brought to the front both times
    first.close()
    assert mw._running_panel is None                     # forgotten as it closes
    NetworkSpeedWidget.show_running_panel(mw)
    assert mw._running_panel is not None and mw._running_panel is not first
    mw._running_panel.close()


def test_an_open_modal_dialog_is_brought_forward_instead(qtbot, i18n):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    mw = _widget_fake(i18n)
    dlg = QDialog()
    dlg.setModal(True)
    qtbot.addWidget(dlg)
    dlg.show()
    try:
        assert QApplication.activeModalWidget() is dlg
        NetworkSpeedWidget.show_running_panel(mw)
        assert mw._running_panel is None                 # no panel beside a dialog that owns input
        rp._force_foreground.assert_called_with(dlg)
    finally:
        dlg.close()


def test_quit_closes_the_panel_first(qtbot, i18n):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    import inspect
    src = inspect.getsource(NetworkSpeedWidget.cleanup)
    assert src.index('("running panel", _running_panel)') < src.index('("mirrors", _mirrors)')
    mw = _widget_fake(i18n)
    NetworkSpeedWidget.show_running_panel(mw)
    panel = mw._running_panel
    NetworkSpeedWidget.close_running_panel(mw)
    assert mw._running_panel is None and not panel.isVisible()
    NetworkSpeedWidget.close_running_panel(mw)           # nothing open: a no-op


def test_the_panel_opens_on_the_launch_monitor_else_the_widgets(qtbot, i18n, monkeypatch):
    from netspeedtray.core import shutdown_channel as sc
    primary = QGuiApplication.primaryScreen()
    monkeypatch.setattr(sc, "monitor_device_at", lambda x, y: primary.name())
    assert rp.screen_for_new_window(sc.pack_point(100, 100)) is primary
    monkeypatch.setattr(sc, "monitor_device_at", lambda x, y: None)
    widget = MagicMock()
    widget.screen.return_value = primary
    assert rp.screen_for_new_window(sc.pack_point(100, 100), widget) is primary
    assert rp.screen_for_new_window(0, None) is primary
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    mw = _widget_fake(i18n)
    NetworkSpeedWidget.show_running_panel(mw)
    p = mw._running_panel
    assert p.screen().availableGeometry().contains(p.frameGeometry().center())
    p.close()
