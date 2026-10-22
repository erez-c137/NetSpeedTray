"""
Explorer restart: the new taskbar's TrayNotifyWnd reports a degenerate rect until the shell has
laid it out (live, Win11 26200: (0,1032,0,1032) - zero width at x=0 - for ~11 s, until
TaskbarCreated). Computing from it gave x = 0 - 190 - 8, the app-icons guard snapped the widget
to x=4 over the Widgets button and logged "overlaps app icons" every second. The position manager
now holds the widget still while the tray rect cannot be real, and logs that once.
"""
import logging
from unittest.mock import MagicMock

from PyQt6.QtCore import QPoint, QRect

from netspeedtray import constants
from netspeedtray.core import position_manager as pm_mod
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.utils.taskbar_utils import TaskbarInfo

TB_RECT = (0, 1032, 1920, 1080)
READY_TRAY = (1694, 1032, 1920, 1080)
RESTART_TRAY = (0, 1032, 0, 1032)   # recorded after the explorer restart


def _taskbar(tray):
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920, 1032)
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd = 2884060
    tb.rect = TB_RECT
    tb.dpi_scale = 1.0
    tb.tasklist_rect = (2, 1032, 150, 1072)   # the transient restart value
    tb.widgets_rect = None
    tb.get_tray_rect.return_value = tray
    tb.get_edge_position.return_value = constants.taskbar.edge.BOTTOM
    tb.get_screen.return_value = screen
    return tb


def _manager(start=QPoint(1719, 973)):
    widget = MagicMock()
    widget.width.return_value = 190
    widget.height.return_value = 48
    widget.isVisible.return_value = True
    widget.pos.return_value = start
    return PositionManager(WindowState(config={}, widget=widget, taskbar_info=None)), widget


def test_restart_tray_rect_holds_position_instead_of_snapping_left(caplog):
    m, widget = _manager()
    with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
        for _ in range(11):                               # one per second until TaskbarCreated
            m.update_position(fresh_taskbar_info=_taskbar(RESTART_TRAY), _float_refreshed=True)
    widget.move.assert_not_called()
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("overlaps app icons" in s for s in msgs)
    assert sum("tray area not ready" in s for s in msgs) == 1


def test_position_resumes_once_the_tray_rect_is_real():
    m, widget = _manager()
    m.update_position(fresh_taskbar_info=_taskbar(RESTART_TRAY), _float_refreshed=True)
    m.update_position(fresh_taskbar_info=_taskbar(READY_TRAY), _float_refreshed=True)
    x, _y = widget.move.call_args[0]
    assert x == 1694 - 190 - constants.layout.TRAY_EDGE_MIN_GAP_PX   # hugging the tray again


def test_not_ready_logs_again_on_a_later_restart(caplog):
    m, _w = _manager()
    with caplog.at_level(logging.INFO, logger=pm_mod.logger.name):
        for tray in (RESTART_TRAY, RESTART_TRAY, READY_TRAY, RESTART_TRAY):
            m.update_position(fresh_taskbar_info=_taskbar(tray), _float_refreshed=True)
    assert sum("tray area not ready" in r.getMessage() for r in caplog.records) == 2


def test_zero_height_and_off_taskbar_tray_rects_are_not_ready():
    for tray in ((1694, 1032, 1920, 1032), (1694, 0, 1920, 40), (-300, 1032, -100, 1080)):
        m, widget = _manager()
        m.update_position(fresh_taskbar_info=_taskbar(tray), _float_refreshed=True)
        widget.move.assert_not_called()


def test_no_tray_rect_still_positions():
    """A Win11 secondary taskbar has no TrayNotifyWnd: its clock-reserve estimate still applies."""
    m, widget = _manager()
    m.update_position(fresh_taskbar_info=_taskbar(None), _float_refreshed=True)
    widget.move.assert_called_once()


def test_unjudgeable_tray_rect_keeps_the_old_behaviour():
    m, _widget = _manager()
    tb = _taskbar(READY_TRAY)
    tb.get_tray_rect.side_effect = RuntimeError("boom")
    m._state.taskbar_info = tb
    assert m._tray_rect_ready() is True
