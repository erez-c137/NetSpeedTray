"""
A docked widget that something ELSE moved must be put back by the next reposition (#314, RDP).

``PositionManager._apply_geometry`` skipped the move whenever the target rect equalled the last rect
IT applied - without looking at where the widget actually is. Windows moves top-level windows on a
display-topology change (a Remote Desktop connect swaps the console monitor for the RDP display; the
widget was reported in the middle of the screen for the whole session, and back on the taskbar at the
console). The taskbar spot computed afterwards is the same as before, so neither the 1 s refresh nor
the screenAdded / screenRemoved recovery (``_on_environment_changed``) ever moved it back.
"""
from unittest.mock import MagicMock

from PyQt6.QtCore import QPoint, QRect

from netspeedtray import constants
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.utils.taskbar_utils import TaskbarInfo

TB_RECT = (0, 1032, 1920, 1080)
TRAY = (1694, 1032, 1920, 1080)
W_WIDTH, W_HEIGHT = 190, 48


def _taskbar():
    screen = MagicMock()
    screen.name.return_value = "DISPLAY1"
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920, 1032)
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd = 2884060
    tb.rect = TB_RECT
    tb.dpi_scale = 1.0
    tb.tasklist_rect = (651, 1032, 1136, 1080)
    tb.widgets_rect = None
    tb.get_tray_rect.return_value = TRAY
    tb.get_edge_position.return_value = constants.taskbar.edge.BOTTOM
    tb.get_screen.return_value = screen
    return tb


def _manager():
    """A widget whose pos() follows move(), like a real QWidget."""
    widget = MagicMock()
    widget.width.return_value = W_WIDTH
    widget.height.return_value = W_HEIGHT
    widget.isVisible.return_value = True
    state = {"pos": QPoint(0, 0)}
    widget.pos.side_effect = lambda: state["pos"]
    widget.move.side_effect = lambda x, y: state.__setitem__("pos", QPoint(x, y))
    return PositionManager(WindowState(config={}, widget=widget, taskbar_info=None)), widget, state


def _refresh(m):
    m.update_position(fresh_taskbar_info=_taskbar(), _float_refreshed=True)


def test_a_widget_moved_by_windows_goes_back_to_its_taskbar_spot():
    m, widget, state = _manager()
    _refresh(m)
    docked = state["pos"]
    assert 1032 <= docked.y() <= 1080 - W_HEIGHT          # on the taskbar

    state["pos"] = QPoint(865, 516)                       # Windows moved it (RDP connect)
    widget.move.reset_mock()
    _refresh(m)                                           # same taskbar -> same target

    widget.move.assert_called_once_with(docked.x(), docked.y())
    assert state["pos"] == docked


def test_no_redundant_move_while_the_widget_is_already_there():
    m, widget, _state = _manager()
    _refresh(m)
    widget.move.reset_mock()
    for _ in range(5):
        _refresh(m)
    widget.move.assert_not_called()


def test_apply_geometry_corrects_an_external_move_to_the_same_target():
    m, widget, state = _manager()
    m._apply_geometry(1496, 1032)
    state["pos"] = QPoint(10, 10)
    widget.move.reset_mock()
    m._apply_geometry(1496, 1032)
    widget.move.assert_called_once_with(1496, 1032)
