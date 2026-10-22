"""Right-click > Reset Position (side-taskbar design §3, Phase B).

``reset_to_default_position`` existed but nothing called it, and it cleared only the free-move spot
(``position_x`` / ``position_y``): a docked drag's ``tray_offset_x`` / ``tray_offset_y`` outlived it.
Now it clears both, the position manager's geometry debounce is dropped so the widget really moves
back, and a tray-menu item (shown only once the widget has been moved) calls it.
"""
import types
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.views.widget.main import NetworkSpeedWidget as NSW

DEFAULTS = constants.config.defaults


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


def test_position_manager_reset_clears_the_free_spot_and_the_docked_offsets():
    cfg = {"tray_offset_y": 709, "tray_offset_x": 250, "position_x": 5, "position_y": 6}
    widget = MagicMock()
    widget.pos.return_value = QPoint(-1, -1)
    pm = PositionManager(WindowState(config=cfg, widget=widget, taskbar_info=None))
    pm._last_applied_geometry = QRect(10, 10, 85, 48)       # the spot before the drag
    with patch.object(pm, "update_position") as update, patch.object(pm, "ensure_topmost"):
        pm.reset_to_default()
    assert cfg == {"tray_offset_y": DEFAULTS.DEFAULT_TRAY_OFFSET_Y,
                   "tray_offset_x": DEFAULTS.DEFAULT_TRAY_OFFSET_X,
                   "position_x": None, "position_y": None}
    # The widget was dragged away from the last applied rect: without dropping the debounce the
    # re-placement onto that same rect would be skipped and the widget would stay where it was.
    assert pm._last_applied_geometry is None
    update.assert_called_once()


def _reset_fake(config):
    fake = types.SimpleNamespace(config=config, logger=MagicMock(), position_manager=MagicMock(),
                                 update_config=MagicMock(), _hide_usage_hover_card=MagicMock())
    fake.reset_to_default_position = types.MethodType(NSW.reset_to_default_position, fake)
    fake.has_custom_position = types.MethodType(NSW.has_custom_position, fake)
    return fake


def test_widget_reset_persists_every_cleared_key():
    fake = _reset_fake({"tray_offset_y": 709})
    fake.reset_to_default_position()
    fake.position_manager.reset_to_default.assert_called_once()
    fake._hide_usage_hover_card.assert_called_once()
    (updates,), _ = fake.update_config.call_args
    assert updates == {"position_x": None, "position_y": None,
                       "tray_offset_x": DEFAULTS.DEFAULT_TRAY_OFFSET_X,
                       "tray_offset_y": DEFAULTS.DEFAULT_TRAY_OFFSET_Y}


@pytest.mark.parametrize("config,moved", [
    ({}, False),
    ({"tray_offset_y": DEFAULTS.DEFAULT_TRAY_OFFSET_Y, "tray_offset_x": DEFAULTS.DEFAULT_TRAY_OFFSET_X,
      "position_x": None}, False),
    ({"tray_offset_y": 709}, True),
    ({"tray_offset_x": 120}, True),
    ({"position_x": 10, "position_y": 20}, True),
])
def test_has_custom_position(config, moved):
    assert _reset_fake(config).has_custom_position() is moved


def test_reset_menu_item_shows_once_moved_and_triggers_the_reset():
    from netspeedtray.core.tray_manager import TrayIconManager

    class W(QWidget):
        def __init__(self):
            super().__init__()
            self.config = {}
            self.moved = False
            self.reset_to_default_position = MagicMock()

        def has_custom_position(self):
            return self.moved

    w = W()
    m = TrayIconManager(w, I18nStrings("en_US"))
    m._load_and_set_icon = lambda: None
    m.initialize()
    assert m.reset_position_action.text() == "Reset P&osition"
    m._refresh_dynamic_items()
    assert not m.reset_position_action.isVisible() and not m.pause_separator.isVisible()
    w.moved = True
    m._refresh_dynamic_items()
    assert m.reset_position_action.isVisible() and m.pause_separator.isVisible()
    m.reset_position_action.trigger()
    w.reset_to_default_position.assert_called_once()


def test_reset_all_keeps_the_pin_hint_counter():
    from netspeedtray.views.settings.dialog import SettingsDialog
    assert "pin_hint_shown_count" in SettingsDialog.RESET_ALL_KEEPS
    assert "pin_hint_shown_count" in constants.config.defaults.DEFAULT_CONFIG
