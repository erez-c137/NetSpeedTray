"""Settings > General > Behavior > "Pin the widget 15px from the left edge" (`pin_left_edge`).

The pin is enforced in PositionManager._apply_geometry - the single funnel every placement
path ends in (saved free-move spot, free-float, calculated, substitute-tray) - so it works at
any resolution (re-derived from the CURRENT screen geometry on each placement) and outranks a
dragged spot while it is on. While pinned, the widget cannot be dragged (InputHandler).
"""
from unittest.mock import MagicMock, patch

import pytest

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants
from netspeedtray.core.input_handler import InputHandler
from netspeedtray.core.position_manager import PositionManager, WindowState

PIN_LEFT_EDGE_OFFSET_PX = constants.layout.PIN_LEFT_EDGE_OFFSET_PX


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


def _screen(left: int, width: int, height: int = 1080) -> MagicMock:
    """A QScreen stand-in with a real geometry so validate/screenAt-style logic can run."""
    scr = MagicMock()
    scr.geometry.return_value = QRect(left, 0, width, height)
    scr.availableGeometry.return_value = QRect(left, 0, width, height - 40)
    scr.name.return_value = f"\\\\_DISPLAY_{left}"
    return scr


def _manager(config: dict, screen: MagicMock) -> tuple:
    """A PositionManager over a MagicMock widget, with QApplication.screenAt patched to the
    given screen (the pin resolves the target screen from the incoming position)."""
    widget = MagicMock()
    widget.width.return_value = 85
    widget.height.return_value = 48
    widget.pos.return_value = QPoint(0, 0)
    widget.screen.return_value = screen
    pm = PositionManager(WindowState(config=config, widget=widget, taskbar_info=None))
    pm._last_applied_geometry = None
    return pm, widget


# --- the pin in _apply_geometry ---------------------------------------------------------------

def test_pin_fixes_the_widget_15px_from_the_left_edge():
    pm, widget = _manager({"pin_left_edge": True}, _screen(0, 1920))
    with patch("netspeedtray.core.position_manager.QApplication.screenAt",
               return_value=_screen(0, 1920)):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(PIN_LEFT_EDGE_OFFSET_PX, 600)


def test_pin_repins_when_the_resolution_changes():
    """Same toggle, different resolution / a secondary screen: the edge is re-derived from the
    CURRENT screen geometry, never from a stored pixel value."""
    pm, widget = _manager({"pin_left_edge": True}, _screen(1920, 2560, 1440))
    with patch("netspeedtray.core.position_manager.QApplication.screenAt",
               return_value=_screen(1920, 2560, 1440)):
        pm._apply_geometry(4000, 300)
    widget.move.assert_called_once_with(1920 + PIN_LEFT_EDGE_OFFSET_PX, 300)


def test_pin_overrides_a_saved_free_move_spot():
    """A dragged spot stays saved, but while the pin is on the widget is placed at the edge."""
    cfg = {"pin_left_edge": True, "free_move": True, "position_x": 1700, "position_y": 990}
    pm, widget = _manager(cfg, _screen(0, 1920))
    with patch("netspeedtray.core.position_manager.QApplication.screenAt",
               return_value=_screen(0, 1920)):
        assert pm._apply_saved_position() is True
    widget.move.assert_called_once_with(PIN_LEFT_EDGE_OFFSET_PX, 990)


def test_pin_off_leaves_every_path_untouched():
    pm, widget = _manager({}, _screen(0, 1920))
    with patch("netspeedtray.core.position_manager.QApplication.screenAt",
               return_value=_screen(0, 1920)) as screen_at:
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1500, 600)
    screen_at.assert_not_called()


def test_pin_resolves_the_screen_from_the_unclamped_probe():
    """A monitor arranged fully ABOVE the primary has a negative y-range; the probe must not
    clamp y to 0 (it would land in the primary and re-pin the widget to the wrong screen's
    edge, flapping invisible every refresh tick)."""
    above = _screen(960, 3840, 2160)      # geometry QRect(960, -2160, 3840, 2160) after shift
    above.geometry.return_value = QRect(960, -2160, 3840, 2160)
    above.availableGeometry.return_value = QRect(960, -2160, 3840, 2120)
    primary = _screen(0, 1920)
    screens = [above, primary]

    def fake_screen_at(point):
        for scr in screens:
            g = scr.geometry.return_value
            if g.left() <= point.x() < g.right() and g.top() <= point.y() < g.bottom():
                return scr
        return None

    pm, widget = _manager({"pin_left_edge": True}, above)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt",
               side_effect=fake_screen_at):
        pm._apply_geometry(1000, -2100)   # the widget sits on the upper display
    widget.move.assert_called_once_with(960 + PIN_LEFT_EDGE_OFFSET_PX, -2100)


def test_app_overlap_yield_never_hides_a_pinned_widget():
    """With hide_when_apps_cover on (the factory default) and a left-aligned taskbar, the
    pinned widget's 15px spot IS covered by the app buttons - yielding there would hide the
    widget the user just anchored and flap hide/show every refresh. The pin must exempt it."""
    pm, _ = _manager({"pin_left_edge": True, "hide_when_apps_cover": True}, _screen(0, 1920))
    ctx = pm._app_overlap_context()
    assert isinstance(ctx, str) and "pinned to the left edge" in ctx


# --- dragging is blocked while pinned ---------------------------------------------------------

class _DragWidget(QWidget):
    def __init__(self, config):
        super().__init__()
        self._dragging = False
        self.config = config
        self.is_paused = False

    def move(self, *args):        # replaced with a MagicMock per test
        pass


def _drag_setup(config: dict) -> tuple:
    w = _DragWidget(dict(config))
    w.move = MagicMock()
    w.setGeometry(100, 100, 85, 48)
    handler = InputHandler(widget=w, position_manager=MagicMock(), tray_manager=MagicMock())
    press = MagicMock(spec=QMouseEvent)
    press.button.return_value = Qt.MouseButton.LeftButton
    press.globalPosition.return_value = QPointF(120.0, 130.0)
    handler.handle_mouse_press(press)
    return w, handler


def _move_event(global_x: float, global_y: float) -> MagicMock:
    event = MagicMock(spec=QMouseEvent)
    event.buttons.return_value = Qt.MouseButton.LeftButton
    event.button.return_value = Qt.MouseButton.LeftButton
    event.globalPosition.return_value = QPointF(global_x, global_y)
    return event


def test_dragging_is_blocked_while_pinned():
    w, handler = _drag_setup({"pin_left_edge": True})
    handler.handle_mouse_move(_move_event(400, 300))
    assert handler._is_dragging is False
    w.move.assert_not_called()


def test_dragging_works_when_the_pin_is_off():
    w, handler = _drag_setup({})
    handler.handle_mouse_move(_move_event(400, 300))
    assert handler._is_dragging is True
    w.move.assert_called()
