"""
2.2.2 D3: the "NetSpeedTray is running" panel a Start-menu launch opens (pytest-qt, offscreen).

There is no tray icon, so this small, normal, focusable window is what "opening" the app shows: name +
version, one live status line, and the app's doors - Settings, Monitor, Check connection, Check for
updates - plus a quiet Quit. Each door is the widget's existing action (never a copy of it). Keyboard:
the first door has focus, Tab walks them in order, Enter/Space press the focused one, Esc closes.
It is the first version of 2.3's About window (C3/C7), so it is built to grow.
"""
import types
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
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


def _main(i18n, **over):
    mw = types.SimpleNamespace(
        config={"unit_type": "bits_decimal", "decimal_places": 1, "speed_display_mode": "auto",
                "short_unit_labels": False, "latency_public_enabled": False},
        i18n=i18n, upload_speed=1.2, download_speed=88.6, is_paused=False,
        latency_headline_hop=None, latency_headline_ms=None, latency_loss=None, latency_link_lost=False,
        show_settings=MagicMock(), open_monitor_window=MagicMock(), open_connection_check=MagicMock(),
        check_for_updates=MagicMock(), fully_exit_application=MagicMock())
    for k, v in over.items():
        setattr(mw, k, v)
    return mw


@pytest.fixture
def panel(qtbot, i18n):
    def make(**over):
        mw = _main(i18n, **over)
        p = RunningPanel(mw, i18n, version="2.2.2")
        p.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)   # the test inspects it after close
        qtbot.addWidget(p)
        return p, mw
    return make


def test_shows_name_version_and_running_line(panel, i18n):
    p, _ = panel()
    assert p.windowTitle() == "NetSpeedTray"
    assert p.title_label.text() == "NetSpeedTray"
    assert p.version_label.text() == i18n.RUNNING_PANEL_VERSION.format(version="2.2.2")
    assert p.running_label.text() == i18n.RUNNING_PANEL_RUNNING
    assert not p.isModal()


def test_it_is_a_normal_focusable_window_not_a_tool_window(panel):
    p, _ = panel()
    flags = p.windowFlags()
    assert (flags & Qt.WindowType.Tool) != Qt.WindowType.Tool     # a tool window can't take focus well
    assert not flags & Qt.WindowType.WindowStaysOnTopHint
    assert not p.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert p.settings_button.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_status_line_shows_live_speeds(panel):
    p, mw = panel()
    text = p.status_label.text()
    assert "88.6" in text and "1.2" in text and "Mbps" in text
    assert text.index("88.6") < text.index("1.2")    # download first, like the hover card
    mw.download_speed = 5.0
    p.refresh_status()
    assert "5.0" in p.status_label.text()
    assert p.status_label.accessibleName() == p.status_label.text()


def test_status_line_says_paused(panel, i18n):
    p, _ = panel(is_paused=True)
    assert i18n.MONITOR_PAUSED in p.status_label.text()


def test_status_line_adds_the_connection_in_the_overviews_words(panel, i18n):
    p, _ = panel(latency_headline_hop="gateway", latency_headline_ms=3.0, latency_loss=0.0)
    text = p.status_label.text()
    assert f"{i18n.CHECK_CONN_ROUTER}: {i18n.LATENCY_GOOD}" in text and "3 ms" in text
    p, _ = panel(latency_headline_hop="anchor", latency_headline_ms=20.0, latency_loss=0.0)
    assert f"{i18n.LATENCY_LABEL}: {i18n.LATENCY_GOOD}" in p.status_label.text()
    p, _ = panel(latency_headline_hop="gateway", latency_link_lost=True)
    assert f"{i18n.CHECK_CONN_ROUTER}: {i18n.LATENCY_NO_CONNECTION}" in p.status_label.text()


def test_status_refreshes_on_a_timer_only_while_shown(panel, qtbot):
    p, mw = panel()
    assert not p._status_timer.isActive()
    p.show()
    assert p._status_timer.isActive()
    mw.download_speed = 42.0
    qtbot.waitUntil(lambda: "42.0" in p.status_label.text(), timeout=3000)
    p.close()
    assert not p._status_timer.isActive()


@pytest.mark.parametrize("button,action", [
    ("settings_button", "show_settings"),
    ("monitor_button", "open_monitor_window"),
    ("check_connection_button", "open_connection_check"),
    ("updates_button", "check_for_updates"),
    ("quit_button", "fully_exit_application"),
])
def test_each_door_runs_the_widgets_own_action_and_closes(panel, qtbot, button, action):
    p, mw = panel()
    p.show()
    getattr(p, button).click()
    assert not p.isVisible()
    qtbot.waitUntil(lambda: getattr(mw, action).called, timeout=2000)
    getattr(mw, action).assert_called_once_with()
    others = {"show_settings", "open_monitor_window", "open_connection_check", "check_for_updates",
              "fully_exit_application"} - {action}
    assert not any(getattr(mw, o).called for o in others)


def test_check_for_updates_hidden_under_the_it_policy(panel, monkeypatch):
    monkeypatch.setattr(rp, "_update_check_disabled", lambda: True)
    p, _ = panel()
    p.show()
    assert not p.updates_button.isVisible()
    assert p.settings_button.isVisible()


def test_first_door_has_focus_and_tab_walks_in_order(panel, qtbot):
    p, _ = panel()
    p.show()
    p.activateWindow()
    qtbot.waitUntil(lambda: p.focusWidget() is p.settings_button, timeout=2000)
    order = [p.settings_button, p.monitor_button, p.check_connection_button, p.updates_button,
             p.quit_button]
    seen = [p.focusWidget()]
    for _ in range(len(order) - 1):
        p.focusNextChild()
        seen.append(p.focusWidget())
    assert seen == order


def test_enter_presses_the_focused_door(panel, qtbot):
    p, mw = panel()
    p.show()
    p.monitor_button.setFocus()
    qtbot.keyClick(p.monitor_button, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: mw.open_monitor_window.called, timeout=2000)
    mw.show_settings.assert_not_called()


def test_escape_closes(panel, qtbot):
    p, mw = panel()
    p.show()
    qtbot.keyClick(p, Qt.Key.Key_Escape)
    assert not p.isVisible()
    mw.fully_exit_application.assert_not_called()


def test_doors_have_accessible_names(panel, i18n):
    p, _ = panel()
    assert p.settings_button.accessibleName() == i18n.SETTINGS_WINDOW_TITLE
    assert p.monitor_button.accessibleName() == i18n.MONITOR_WINDOW_TITLE
    assert p.check_connection_button.accessibleName() == i18n.CHECK_CONN_ACTION
    assert p.updates_button.accessibleName() == i18n.RUNNING_PANEL_CHECK_UPDATES
    assert p.quit_button.accessibleName() == i18n.RUNNING_PANEL_QUIT
    assert p.accessibleName() == "NetSpeedTray"
    assert i18n.RUNNING_PANEL_RUNNING in p.accessibleDescription()


def test_high_contrast_uses_the_system_colours(panel, monkeypatch):
    monkeypatch.setattr(rp, "read_high_contrast", lambda: (True, "#FFFF00", "#000000"))
    monkeypatch.setattr(rp, "_sys_color_hex", lambda index: {
        rp._COLOR_BTNFACE: "#000000", rp._COLOR_BTNTEXT: "#FFFFFF",
        rp._COLOR_HIGHLIGHT: "#1AEBFF", rp._COLOR_HIGHLIGHTTEXT: "#000000"}[index])
    p, _ = panel()
    sheet = p.styleSheet()
    assert "#FFFF00" in sheet and "#000000" in sheet and "#1AEBFF" in sheet
    from netspeedtray.constants.styles import styles as tokens
    assert tokens.CARD_BG_DARK not in sheet and tokens.CARD_BG_LIGHT not in sheet


def test_rtl_follows_the_application(panel, qtbot):
    app = QApplication.instance()
    before = app.layoutDirection()
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    try:
        p, _ = panel()
        assert p.layoutDirection() == Qt.LayoutDirection.RightToLeft
    finally:
        app.setLayoutDirection(before)


# --- the widget side: one panel at a time ---------------------------------------------------------

def _widget_fake(i18n):
    mw = _main(i18n)
    mw.logger = MagicMock()
    mw._running_panel = None
    return mw


def test_show_running_panel_is_single_instance(qtbot, i18n, monkeypatch):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fg = MagicMock()
    monkeypatch.setattr(rp, "_force_foreground", fg)
    mw = _widget_fake(i18n)
    NetworkSpeedWidget.show_running_panel(mw)
    first = mw._running_panel
    assert isinstance(first, RunningPanel) and first.isVisible()
    NetworkSpeedWidget.show_running_panel(mw)
    assert mw._running_panel is first                    # re-raised, not a second window
    assert len([w for w in QApplication.topLevelWidgets()
                if isinstance(w, RunningPanel) and w.isVisible()]) == 1
    assert fg.call_count == 2                            # brought to the front both times
    first.close()
    qtbot.waitUntil(lambda: mw._running_panel is None, timeout=2000)   # released when closed
    NetworkSpeedWidget.show_running_panel(mw)
    assert mw._running_panel is not None and mw._running_panel is not first
    mw._running_panel.close()


def test_the_panel_opens_on_the_screen_under_the_pointer(qtbot, i18n, monkeypatch):
    monkeypatch.setattr(rp, "_force_foreground", MagicMock())
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    mw = _widget_fake(i18n)
    NetworkSpeedWidget.show_running_panel(mw)
    p = mw._running_panel
    avail = p.screen().availableGeometry()
    assert avail.contains(p.frameGeometry().center())
    p.close()
