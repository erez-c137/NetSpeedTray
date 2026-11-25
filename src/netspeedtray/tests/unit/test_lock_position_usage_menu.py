"""
Lock position (#616) and the optional Usage history menu item (#619), both 2.2.2.

* Lock position: a user with a wrist brace kept moving the widget by accident. `position_locked`
  (default False) stops a left-button drag from ever starting - in every mode, Free Move included -
  while a click, a double-click, the hover card and the right-click menu keep working. It is a
  checkable right-click item (shown by default, hideable like the others) and a switch in
  Settings > Widget > Placement.
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


@pytest.mark.parametrize("key", ["position_locked", "usage_history_in_menu"])
def test_schema_keeps_true_and_resets_a_non_bool(key):
    assert _cm()._validate_config({key: True})[key] is True
    assert _cm()._validate_config({key: "yes"})[key] is False


def test_lock_item_is_hideable_and_nothing_new_hidden_by_default():
    assert D.CONTEXT_MENU_ITEM_LOCK_POSITION == "lock_position"
    assert D.CONTEXT_MENU_ITEM_LOCK_POSITION in D.CONTEXT_MENU_HIDEABLE_ITEMS
    assert D.DEFAULT_CONTEXT_MENU_HIDDEN_ITEMS == []
    # Usage history keeps its own opt-in key, like Pause - never a hidden-items id.
    assert "usage_history" not in D.CONTEXT_MENU_HIDEABLE_ITEMS


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


def test_lock_item_shown_by_default_unchecked(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {})
    a = m.lock_position_action
    assert a.text() == i18n.LOCK_POSITION_MENU_ITEM
    assert a.isCheckable() and a.isVisible() and not a.isChecked()


def test_lock_item_toggles_and_persists(qt_owned, i18n):
    config = {}
    m = _menu(qt_owned, i18n, config)
    m.lock_position_action.trigger()
    m.widget.update_config.assert_called_with({"position_locked": True})
    assert config["position_locked"] is True
    _refresh(m)
    assert m.lock_position_action.isChecked()
    m.lock_position_action.trigger()
    m.widget.update_config.assert_called_with({"position_locked": False})


def test_lock_item_reflects_config_on_open(qt_owned, i18n):
    """A change made in Settings shows on the next open (the menu is built once)."""
    config = {}
    m = _menu(qt_owned, i18n, config)
    config["position_locked"] = True
    _refresh(m)
    assert m.lock_position_action.isChecked()


def test_lock_item_can_be_hidden(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {"context_menu_hidden_items": ["lock_position"]})
    assert not m.lock_position_action.isVisible()


def test_usage_item_hidden_by_default(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {})
    assert not m.usage_history_action.isVisible()
    assert i18n.USAGE_HISTORY_MENU_ITEM not in _shown(m)


def test_usage_item_shown_when_enabled_and_opens_the_sheet(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {"usage_history_in_menu": True})
    shown = _shown(m)
    assert shown.index(i18n.USAGE_HISTORY_MENU_ITEM) == shown.index(i18n.SHOW_MONITOR_MENU_ITEM) + 1
    m.usage_history_action.trigger()
    m.widget.open_usage_history.assert_called_once()


def test_default_menu_only_gains_the_lock_item(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {}, moved=False)
    assert _shown(m) == [i18n.SETTINGS_MENU_ITEM, i18n.SHOW_MONITOR_MENU_ITEM, i18n.CHECK_CONN_MENU_ITEM, "-",
                         i18n.LOCK_POSITION_MENU_ITEM, "-",
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
    assert "lock_position" in general.context_menu_toggles
    assert general.context_menu_items.isAncestorOf(general.usage_history_in_menu)


def test_settings_usage_row_defaults_off_and_round_trips(general):
    general.load_settings({}, is_startup_enabled=False)
    assert general.usage_history_in_menu.isChecked() is False
    out = general.get_settings()
    assert out["usage_history_in_menu"] is False and out["context_menu_hidden_items"] == []
    general.load_settings({"usage_history_in_menu": True}, is_startup_enabled=False)
    assert general.get_settings()["usage_history_in_menu"] is True


def test_settings_lock_row_round_trips_hidden(general):
    general.load_settings({"context_menu_hidden_items": ["lock_position"]}, is_startup_enabled=False)
    assert not general.context_menu_toggles["lock_position"].isChecked()
    assert general.get_settings()["context_menu_hidden_items"] == ["lock_position"]


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
    config = {}
    m = _menu(qt_owned, i18n, config)
    assert m.lock_position_action.icon().isNull()
    assert all(a is not m.lock_position_action for a, _cp in m._menu_icon_actions)
    config["position_locked"] = True
    _refresh(m)
    assert not m.lock_position_action.icon().isNull()
