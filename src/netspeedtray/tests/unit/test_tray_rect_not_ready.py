"""
Explorer restart: the new taskbar's TrayNotifyWnd reports a degenerate rect until the shell has
laid it out (live, Win11 26200: (0,1032,0,1032) - zero width at x=0 - for ~11 s, until
TaskbarCreated). Computing from it gave x = 0 - 190 - 8, the app-icons guard snapped the widget
to x=4 over the Widgets button and logged "overlaps app icons" every second.

While the rect is not ready the widget is placed ON the new taskbar as if its tray had the last
good geometry seen on that monitor + edge; with nothing remembered, the "no tray" estimate
(screen edge minus a clock-width reserve) applies. One log line per not-ready stretch.
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
W_WIDTH, W_HEIGHT = 190, 48
PRE_RESTART_X = 1496                # the recorded widget x before the restart
GAP = constants.layout.TRAY_EDGE_MIN_GAP_PX
NO_TRAY_X = (1920 - constants.config.defaults.DEFAULT_SECONDARY_CLOCK_RESERVE_PX - W_WIDTH
             - constants.config.defaults.DEFAULT_TRAY_OFFSET_X)


def _taskbar(tray, hwnd=2884060, screen_name="DISPLAY1", tasklist=(2, 1032, 150, 1072)):
    screen = MagicMock()
    screen.name.return_value = screen_name
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920, 1032)
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd = hwnd
    tb.rect = TB_RECT
    tb.dpi_scale = 1.0
    tb.tasklist_rect = tasklist             # default: the transient restart value
    tb.widgets_rect = None
    tb.get_tray_rect.return_value = tray
    tb.get_edge_position.return_value = constants.taskbar.edge.BOTTOM
    tb.get_screen.return_value = screen
    return tb


def _manager(start=QPoint(1719, 973)):
    widget = MagicMock()
    widget.width.return_value = W_WIDTH
    widget.height.return_value = W_HEIGHT
    widget.isVisible.return_value = True
    widget.pos.return_value = start
    return PositionManager(WindowState(config={}, widget=widget, taskbar_info=None)), widget


def _update(m, tray, **kw):
    m.update_position(fresh_taskbar_info=_taskbar(tray, **kw), _float_refreshed=True)


def _last_x(widget):
    return widget.move.call_args[0][0]


def test_restart_tray_rect_does_not_snap_left_and_logs_once(caplog):
    m, widget = _manager()
    _update(m, READY_TRAY, hwnd=2557336, tasklist=(651, 1032, 1136, 1080))   # pre-restart taskbar
    with caplog.at_level(logging.DEBUG, logger=pm_mod.logger.name):
        for _ in range(11):                               # one per second until TaskbarCreated
            _update(m, RESTART_TRAY)
    assert all(c[0][0] > 1000 for c in widget.move.call_args_list)
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("overlaps app icons" in s for s in msgs)
    assert sum("tray area not ready" in s for s in msgs) == 1


def test_remembered_tray_width_puts_the_widget_back_at_its_pre_restart_x():
    m, widget = _manager()
    _update(m, READY_TRAY, hwnd=2557336, tasklist=(651, 1032, 1136, 1080))
    assert _last_x(widget) == PRE_RESTART_X
    m._apply_geometry(1719, 973)        # no taskbar for a moment: the screen fallback spot
    widget.pos.return_value = QPoint(1719, 973)
    widget.move.reset_mock()
    _update(m, RESTART_TRAY)
    x, y = widget.move.call_args[0]
    assert abs(x - PRE_RESTART_X) <= 2
    assert 1032 <= y <= 1080 - W_HEIGHT                   # on the new taskbar, not floating


def test_no_memory_falls_back_to_the_no_tray_estimate():
    """The very first launch met a not-ready rect: screen edge minus the clock reserve."""
    m, widget = _manager()
    _update(m, RESTART_TRAY)
    assert _last_x(widget) == NO_TRAY_X


def test_memory_is_per_monitor():
    m, widget = _manager()
    _update(m, (1500, 1032, 1920, 1080), screen_name="DISPLAY2", tasklist=None)   # wide tray there
    _update(m, RESTART_TRAY, screen_name="DISPLAY1")
    assert _last_x(widget) == NO_TRAY_X


def test_position_resumes_once_the_tray_rect_is_real():
    m, widget = _manager()
    _update(m, RESTART_TRAY)
    _update(m, (1738, 1032, 1920, 1080))
    assert _last_x(widget) == 1738 - W_WIDTH - GAP        # hugging the real tray again


def test_not_ready_logs_again_on_a_later_restart(caplog):
    m, _w = _manager()
    with caplog.at_level(logging.INFO, logger=pm_mod.logger.name):
        for tray in (RESTART_TRAY, RESTART_TRAY, READY_TRAY, RESTART_TRAY):
            _update(m, tray)
    assert sum("tray area not ready" in r.getMessage() for r in caplog.records) == 2


def test_zero_height_and_off_taskbar_tray_rects_are_not_ready():
    for tray in ((1694, 1032, 1920, 1032), (1694, 0, 1920, 40), (-300, 1032, -100, 1080)):
        m, widget = _manager()
        _update(m, tray)
        assert _last_x(widget) == NO_TRAY_X, tray


def test_no_tray_rect_still_positions():
    """A Win11 secondary taskbar has no TrayNotifyWnd: its clock-reserve estimate still applies."""
    m, widget = _manager()
    _update(m, None)
    widget.move.assert_called_once()


def test_unjudgeable_tray_rect_keeps_the_old_behaviour():
    m, _widget = _manager()
    tb = _taskbar(READY_TRAY)
    tb.get_tray_rect.side_effect = RuntimeError("boom")
    m._state.taskbar_info = tb
    assert m._tray_rect_ready() is True
