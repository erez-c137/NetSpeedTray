"""
Lock position (#616) and the optional Usage history menu item (#619), both 2.2.2.

* Lock position: a user with a wrist brace kept moving the widget by accident. `position_locked`
  (default False) stops a left-button drag from ever starting - in every mode, Free Move included -
  while a click, a double-click, the hover card and the right-click menu keep working. It is a
  switch in Settings > Widget > Placement and an opt-in checkable right-click item
  (`lock_position_in_menu`, default False - owner: the default menu stays exactly as in 2.2.1).
* Usage history: an opt-in right-click item (`usage_history_in_menu`, default False) that opens the
  same per-day / month / year sheet as the Monitor's usage card - one shared opener, not a copy.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QInputDevice, QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.input_handler import InputHandler
from netspeedtray.core.tray_manager import TrayIconManager
from netspeedtray.utils.config import ConfigManager

D = constants.config.defaults


@pytest.fixture
def i18n():
    return I18nStrings("en_US")


# --------------------------------------------------------------------------------------------- config

def _cm():
    return ConfigManager(config_path=Path(".") / "NetSpeedTray_Config.json")


def test_lock_defaults_off():
    assert D.DEFAULT_POSITION_LOCKED is False
    assert D.DEFAULT_CONFIG["position_locked"] is False
    assert _cm()._validate_config({})["position_locked"] is False


def test_usage_item_defaults_off():
    assert D.DEFAULT_USAGE_HISTORY_IN_MENU is False
    assert D.DEFAULT_CONFIG["usage_history_in_menu"] is False
    assert _cm()._validate_config({})["usage_history_in_menu"] is False


@pytest.mark.parametrize("key", ["position_locked", "usage_history_in_menu", "lock_position_in_menu"])
def test_schema_keeps_true_and_resets_a_non_bool(key):
    assert _cm()._validate_config({key: True})[key] is True
    assert _cm()._validate_config({key: "yes"})[key] is False


def test_both_menu_items_are_opt_in_with_their_own_keys():
    assert D.DEFAULT_LOCK_POSITION_IN_MENU is False
    assert D.DEFAULT_CONFIG["lock_position_in_menu"] is False
    assert _cm()._validate_config({})["lock_position_in_menu"] is False
    # Own opt-in keys, like Pause - never hidden-items ids (an id shows to every existing user, whose
    # saved hidden list is already []).
    assert D.DEFAULT_CONTEXT_MENU_HIDDEN_ITEMS == []
    assert not {"lock_position", "usage_history"} & set(D.CONTEXT_MENU_HIDEABLE_ITEMS)


# -------------------------------------------------------------------------------------------- input

class _Widget(QWidget):
    def __init__(self):
        super().__init__()
        self._dragging = False
        self.config = {}
        self.is_paused = False


@pytest.fixture
def handler(qt_owned):
    w = qt_owned(_Widget())
    w.setGeometry(100, 100, 200, 50)
    w.move = MagicMock()
    w.update_config = MagicMock()
    w.open_monitor_window = MagicMock()
    pm = MagicMock()
    pm.constrain_drag.side_effect = lambda p: p
    pm.is_floating.side_effect = lambda: bool(w.config.get("free_move", False))
    h = InputHandler(widget=w, position_manager=pm, tray_manager=MagicMock())
    return h, w


def _ev(x, y, button=Qt.MouseButton.LeftButton, kind=QEvent.Type.MouseButtonPress):
    event = MagicMock(spec=QMouseEvent)
    event.button.return_value = button
    event.buttons.return_value = button
    event.globalPosition.return_value = QPointF(float(x), float(y))
    event.type.return_value = kind
    event.pointingDevice.return_value.type.return_value = QInputDevice.DeviceType.Mouse
    return event


def _drag(h, dx=60):
    h.handle_mouse_press(_ev(150, 150))
    h.handle_mouse_move(_ev(150 + dx, 150, kind=QEvent.Type.MouseMove))
    h.handle_mouse_move(_ev(150 + 2 * dx, 150, kind=QEvent.Type.MouseMove))
    h.handle_mouse_release(_ev(150 + 2 * dx, 150, kind=QEvent.Type.MouseButtonRelease))


def test_unlocked_still_drags(handler):
    h, w = handler
    _drag(h)
    assert w.move.called and w.update_config.called


@pytest.mark.parametrize("free_move", [False, True])
def test_locked_never_drags(handler, free_move):
    h, w = handler
    w.config = {"position_locked": True, "free_move": free_move}
    h._on_single_click = MagicMock()
    _drag(h)
    w.move.assert_not_called()
    w.update_config.assert_not_called()          # no dragged position saved
    assert not h._is_dragging and not w._dragging
    assert not h._drag_watchdog.isActive()
    h._on_single_click.assert_not_called()       # an attempted drag is not a click either


def test_locked_click_still_clicks(handler):
    h, w = handler
    w.config = {"position_locked": True}
    h._on_single_click = MagicMock()
    h.handle_mouse_press(_ev(150, 150))
    h.handle_mouse_move(_ev(151, 150, kind=QEvent.Type.MouseMove))    # a wobble under the threshold
    h.handle_mouse_release(_ev(151, 150, kind=QEvent.Type.MouseButtonRelease))
    h._on_single_click.assert_called_once()
    w.move.assert_not_called()


def test_locked_double_click_still_opens_monitor(handler):
    h, w = handler
    w.config = {"position_locked": True}
    h.handle_double_click(_ev(150, 150, kind=QEvent.Type.MouseButtonDblClick))
    w.open_monitor_window.assert_called_once()


def test_a_click_after_a_blocked_drag_still_clicks(handler):
    h, w = handler
    w.config = {"position_locked": True}
    _drag(h)
    h._on_single_click = MagicMock()
    h.handle_mouse_press(_ev(150, 150))
    h.handle_mouse_release(_ev(150, 150, kind=QEvent.Type.MouseButtonRelease))
    h._on_single_click.assert_called_once()


# --------------------------------------------------------------------------------------------- menu

def _menu(qt_owned, i18n, config, moved=False):
    widget = qt_owned(QWidget())
    widget.config = config
    widget.has_custom_position = lambda: moved
    widget.fully_exit_application = lambda: None
    widget.update_config = MagicMock(side_effect=lambda u, *a, **k: config.update(u))
    widget.set_position_locked = MagicMock(side_effect=lambda on: config.update(position_locked=on))
    widget.open_usage_history = MagicMock()
    with patch("os.path.exists", return_value=False):
        manager = TrayIconManager(widget, i18n)
        manager.initialize()
    _refresh(manager)
    return manager


def _refresh(manager):
    with patch("netspeedtray.core.update_checker.update_check_disabled_by_policy", return_value=False):
        manager._refresh_dynamic_items()


def _shown(manager):
    return ["-" if a.isSeparator() else a.text() for a in manager.context_menu.actions() if a.isVisible()]


def test_lock_item_hidden_by_default(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {})
    assert not m.lock_position_action.isVisible()
    assert i18n.LOCK_POSITION_MENU_ITEM not in _shown(m)


def test_lock_item_shown_when_enabled_unchecked(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {"lock_position_in_menu": True}, moved=True)
    a = m.lock_position_action
    assert a.text() == i18n.LOCK_POSITION_MENU_ITEM
    assert a.isCheckable() and a.isVisible() and not a.isChecked()
    shown = _shown(m)
    assert shown.index(i18n.LOCK_POSITION_MENU_ITEM) + 1 == shown.index(i18n.RESET_POSITION_MENU_ITEM)


def test_lock_item_toggles_and_persists(qt_owned, i18n):
    config = {"lock_position_in_menu": True}
    m = _menu(qt_owned, i18n, config)
    m.lock_position_action.trigger()
    m.widget.set_position_locked.assert_called_with(True)
    assert config["position_locked"] is True
    _refresh(m)
    assert m.lock_position_action.isChecked()
    m.lock_position_action.trigger()
    m.widget.set_position_locked.assert_called_with(False)


def test_lock_item_reflects_config_on_open(qt_owned, i18n):
    """A change made in Settings shows on the next open (the menu is built once)."""
    config = {}
    m = _menu(qt_owned, i18n, config)
    config["position_locked"] = True
    _refresh(m)
    assert m.lock_position_action.isChecked()


def test_usage_item_hidden_by_default(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {})
    assert not m.usage_history_action.isVisible()
    assert i18n.USAGE_HISTORY_MENU_ITEM not in _shown(m)


def test_usage_item_shown_when_enabled_and_opens_the_sheet(qtbot, qt_owned, i18n):
    m = _menu(qt_owned, i18n, {"usage_history_in_menu": True})
    shown = _shown(m)
    assert shown.index(i18n.USAGE_HISTORY_MENU_ITEM) == shown.index(i18n.SHOW_MONITOR_MENU_ITEM) + 1
    m.usage_history_action.trigger()
    qtbot.waitUntil(lambda: m.widget.open_usage_history.called, timeout=1000)   # after the menu loop
    m.widget.open_usage_history.assert_called_once()


@pytest.mark.parametrize("config", [{}, {"context_menu_hidden_items": []}], ids=["fresh", "existing"])
@pytest.mark.parametrize("moved", [False, True])
def test_default_menu_is_unchanged(qt_owned, i18n, config, moved):
    """Owner, 2026-11-25: neither new item is in the default menu - a fresh config, or an existing one
    with its saved (empty) hidden list."""
    m = _menu(qt_owned, i18n, dict(config), moved=moved)
    assert _shown(m) == [i18n.SETTINGS_MENU_ITEM, i18n.SHOW_MONITOR_MENU_ITEM, i18n.CHECK_CONN_MENU_ITEM, "-",
                         *([i18n.RESET_POSITION_MENU_ITEM, "-"] if moved else []),
                         i18n.CHECK_FOR_UPDATES_MENU_ITEM, i18n.SUPPORT_MENU_ITEM, "-",
                         i18n.EXIT_MENU_ITEM]


# ----------------------------------------------------------------------------- the shared opener

def test_widget_opens_usage_history_through_the_shared_opener():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake = MagicMock()
    with patch("netspeedtray.views.monitor.usage_history_sheet.open_usage_history") as opener:
        NetworkSpeedWidget.open_usage_history(fake)
    opener.assert_called_once()
    assert opener.call_args[0][0] is fake


def test_overview_card_uses_the_same_opener():
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    tab = MagicMock()
    with patch("netspeedtray.views.monitor.usage_history_sheet.open_usage_history") as opener:
        OverviewTab._open_usage_history(tab)
    opener.assert_called_once()
    assert opener.call_args[0][0] is tab._main_widget


def test_shared_opener_builds_the_sheet_from_the_widget_state(q_app):
    from netspeedtray.views.monitor import usage_history_sheet as mod
    mw = MagicMock()
    mw._usage_cap_set.return_value = False
    with patch.object(mod, "UsageHistorySheet") as sheet_cls,             patch.object(mod, "sip", MagicMock(isdeleted=lambda _s: False)):
        mod.open_usage_history(mw, {"a": 1}, "i18n")
    sheet_cls.assert_called_once()
    args, kwargs = sheet_cls.call_args
    assert args[0] is mw.widget_state and args[1] == {"a": 1} and args[2] == "i18n"
    assert kwargs["period_ceiling"]() is None
    sheet_cls.return_value.exec.assert_called_once()
    sheet_cls.return_value.deleteLater.assert_called_once()


def test_shared_opener_without_widget_state_is_a_noop():
    from netspeedtray.views.monitor import usage_history_sheet as mod
    mw = MagicMock()
    mw.widget_state = None
    with patch.object(mod, "UsageHistorySheet") as sheet_cls:
        mod.open_usage_history(mw, {}, "i18n")
    sheet_cls.assert_not_called()


# ----------------------------------------------------------------------------------------- settings

@pytest.fixture
def general(qt_owned, i18n):
    from netspeedtray.views.settings.pages.general import GeneralPage
    return qt_owned(GeneralPage(i18n, MagicMock()))


def test_settings_menu_list_has_both_rows_in_menu_order(general, i18n):
    from netspeedtray.utils.components import SettingCard
    rows = [c._title_label.text() for c in general.context_menu_items.findChildren(SettingCard)]
    assert rows == ["Show Monitor", "Usage history", "Check connection", i18n.CLICK_ACTION_PAUSE_LABEL,
                    "Lock position", "Reset position", "Check for updates", "Support this project"]
    assert "lock_position" not in general.context_menu_toggles   # own opt-in keys, not hidden-items ids
    assert general.context_menu_items.isAncestorOf(general.usage_history_in_menu)
    assert general.context_menu_items.isAncestorOf(general.lock_position_in_menu)


def test_settings_usage_row_defaults_off_and_round_trips(general):
    general.load_settings({}, is_startup_enabled=False)
    assert general.usage_history_in_menu.isChecked() is False
    out = general.get_settings()
    assert out["usage_history_in_menu"] is False and out["context_menu_hidden_items"] == []
    general.load_settings({"usage_history_in_menu": True}, is_startup_enabled=False)
    assert general.get_settings()["usage_history_in_menu"] is True


def test_settings_lock_row_defaults_off_and_round_trips(general):
    general.load_settings({}, is_startup_enabled=False)
    assert general.lock_position_in_menu.isChecked() is False
    assert general.get_settings()["lock_position_in_menu"] is False
    general.load_settings({"lock_position_in_menu": True}, is_startup_enabled=False)
    assert general.get_settings()["lock_position_in_menu"] is True
    assert general.get_settings()["context_menu_hidden_items"] == []


@pytest.fixture
def widget_page(qt_owned, i18n):
    from netspeedtray.views.settings.pages.widget import WidgetPage
    return qt_owned(WidgetPage(i18n, lambda: None))


def test_widget_page_lock_switch_sits_after_free_move_and_round_trips(widget_page, i18n):
    from netspeedtray.utils.components import SettingCard
    titles = [c._title_label.text() for c in widget_page.findChildren(SettingCard)]
    assert titles.index("Lock position") == titles.index(i18n.FREE_MOVE_LABEL) + 1
    widget_page.load_settings({})
    assert widget_page.position_locked.isChecked() is False
    assert widget_page.get_settings()["position_locked"] is False
    widget_page.load_settings({"position_locked": True})
    assert widget_page.get_settings()["position_locked"] is True


def test_lock_item_shows_its_state_in_the_check_column(qt_owned, i18n):
    """An icon takes the check column and hid the state (seen on a render): the item has no glyph of its
    own - a checkmark glyph when locked, nothing when not."""
    config = {"lock_position_in_menu": True}
    m = _menu(qt_owned, i18n, config)
    assert m.lock_position_action.icon().isNull()
    assert all(a is not m.lock_position_action for a, _cp in m._menu_icon_actions)
    config["position_locked"] = True
    _refresh(m)
    assert not m.lock_position_action.icon().isNull()


# ------------------------------------------- review F1: the menu toggle while Settings is open

def _lock_main(cfg):
    from netspeedtray.tests.unit.test_integration_222 import _Main
    from netspeedtray.views.widget.main import NetworkSpeedWidget

    class Main(_Main):
        set_position_locked = NetworkSpeedWidget.set_position_locked
    return Main(cfg)


def _toggle_from_menu(main, on):
    """TrayIconManager's slot, unbound, over the harness widget (not a QObject)."""
    fake = MagicMock()
    fake.widget = main
    TrayIconManager._toggle_position_lock(fake, on)


@pytest.fixture
def _no_boxes(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    for name in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)


@pytest.mark.parametrize("widget_page_built", [False, True])
@pytest.mark.parametrize("close", ["preview_then_save", "cancel"])
def test_menu_lock_with_settings_open_never_saves_the_preview_and_survives_the_dialog(
        qtbot, _no_boxes, widget_page_built, close):
    from netspeedtray.tests.unit.test_integration_222 import _open_settings, _preview_font_edit
    main = _lock_main(dict(D.DEFAULT_CONFIG, position_locked=False, font_size=9))
    dlg = _open_settings(qtbot, main, build_network_page=False)
    if widget_page_built:
        dlg._ensure_page("widget")
    _preview_font_edit(dlg, main)
    _toggle_from_menu(main, True)
    written = main.saved[-1]
    assert written["position_locked"] is True and written["font_size"] == 9   # (a) never the preview
    assert main.config["position_locked"] is True                              # applies at once
    if widget_page_built:
        assert dlg._built_pages["widget"].position_locked.isChecked()        # the open page shows it
    if close == "preview_then_save":
        dlg._emit_settings_changed_throttled()                                  # (b) a later preview...
        assert main.config["position_locked"] is True
        dlg._save_and_close()                                                   # ...and Save
        assert main.saved[-1]["position_locked"] is True
    else:
        dlg.reject()                                                            # (c) Cancel
        assert main.saved[-1]["position_locked"] is True
        assert main.config["font_size"] == 9
    assert main.config["position_locked"] is True


def test_menu_lock_without_settings_saves_through_save_config_keys():
    main = _lock_main(dict(D.DEFAULT_CONFIG, position_locked=False))
    _toggle_from_menu(main, True)
    assert main.config["position_locked"] is True and main.saved[-1]["position_locked"] is True
    _toggle_from_menu(main, False)
    assert main.config["position_locked"] is False and main.saved[-1]["position_locked"] is False


# ------------------------------------------------- review F2: sheets open after the menu loop ends

@pytest.mark.parametrize("action_attr, opener", [("usage_history_action", "open_usage_history"),
                                                 ("check_connection_action", "open_connection_check")])
def test_sheet_items_open_after_the_menu_closes(qtbot, qt_owned, i18n, action_attr, opener):
    """Action slots run inside QMenu.exec, while _is_context_menu_visible is True - the refresh, the
    burial recovery, the hover card and the mirrors all stand down for as long as a modal sheet opened
    there stays up. The sheet must open once the menu loop has ended."""
    from PyQt6.QtCore import QPoint
    widget = qt_owned(QWidget())
    widget.config = {"usage_history_in_menu": True}
    widget.has_custom_position = lambda: False
    widget.fully_exit_application = lambda: None
    widget._is_context_menu_visible = False
    seen = []
    setattr(widget, opener, lambda: seen.append(widget._is_context_menu_visible))
    with patch("os.path.exists", return_value=False):
        m = TrayIconManager(widget, i18n)
        m.initialize()
    m._calculate_menu_position = lambda anchor=None: QPoint(0, 0)
    m.context_menu.exec = lambda *_a: getattr(m, action_attr).trigger()
    with patch("netspeedtray.core.update_checker.update_check_disabled_by_policy", return_value=False):
        m.show_context_menu()
    assert seen == []                     # not inside the menu loop
    qtbot.waitUntil(lambda: seen != [], timeout=1000)
    assert seen == [False]


def test_a_blocked_drag_dismisses_the_hover_card(handler):
    h, w = handler
    w.config = {"position_locked": True}
    w._hover_card_timer = MagicMock()
    w._hide_usage_hover_card = MagicMock()
    _drag(h)
    w._hover_card_timer.stop.assert_called()
    w._hide_usage_hover_card.assert_called()
