"""
#324 (rc.2 F12, best-effort, Windows 10): the classic taskbar toolbars (Desktop, Links, Address, a
folder) are ReBarWindow32 bands between the task list and the tray. The widget hugs the tray, so it
was drawn over them. Now the toolbars' left edge is the widget's right boundary.

Windows 11 has no taskbar toolbars - its ReBarWindow32 holds MSTaskSwWClass only (probed on 26200,
rc2-positioning scratch probe_rebar.py) - so the band rect is None there and placement is unchanged.
Not live-testable on the owner's PC; the reporter's Win10 box is the check.
"""
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QPoint, QRect

from netspeedtray import constants
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.utils import taskbar_utils as tu
from netspeedtray.utils.taskbar_utils import TaskbarInfo

TB, REBAR, TASKSW, DESKTOP, LINKS = 0x10, 0x20, 0x30, 0x40, 0x50
TASK_RECT = (100, 1040, 1200, 1080)


def _hierarchy(children, rects, visible=()):
    """children: ReBar children in order; rects: hwnd -> rect."""
    gui = MagicMock()

    def find(parent, after, cls, _title):
        if parent == TB and cls == "ReBarWindow32":
            return REBAR
        if parent == REBAR and cls == "MSTaskSwWClass":
            return TASKSW if TASKSW in children else 0
        if parent == REBAR and cls is None:
            i = 0 if not after else children.index(after) + 1
            return children[i] if i < len(children) else 0
        return 0

    gui.FindWindowEx.side_effect = find
    gui.GetWindowRect.side_effect = lambda h: rects[h]
    gui.IsWindowVisible.side_effect = lambda h: h in visible or h == TASKSW
    return patch.object(tu, "win32gui", gui)


def test_windows11_rebar_has_no_toolbars():
    with _hierarchy([TASKSW], {TASKSW: (673, 1032, 1158, 1080)}):
        assert tu.find_toolbar_bands_rect(TB) is None


def test_toolbars_after_the_task_list_are_found():
    rects = {TASKSW: TASK_RECT, DESKTOP: (1200, 1040, 1290, 1080), LINKS: (1290, 1040, 1380, 1080)}
    with _hierarchy([TASKSW, DESKTOP, LINKS], rects, visible={DESKTOP, LINKS}):
        assert tu.find_toolbar_bands_rect(TB) == (1200, 1040, 1380, 1080)


@pytest.mark.parametrize("rect,visible", [
    ((0, 1040, 100, 1080), True),         # dragged before the task list: the widget never sits there
    ((1200, 1040, 1290, 1080), False),    # hidden band
    ((1200, 1040, 1200, 1080), True),     # empty rect
])
def test_bands_that_do_not_matter_are_ignored(rect, visible):
    rects = {TASKSW: TASK_RECT, DESKTOP: rect}
    with _hierarchy([DESKTOP, TASKSW] if rect[0] == 0 else [TASKSW, DESKTOP], rects,
                    visible={DESKTOP} if visible else set()):
        assert tu.find_toolbar_bands_rect(TB) is None


def test_vertical_bar_toolbars_below_the_task_list():
    task = (0, 0, 62, 900)
    rects = {TASKSW: task, DESKTOP: (0, 900, 62, 980)}
    with _hierarchy([TASKSW, DESKTOP], rects, visible={DESKTOP}):
        assert tu.find_toolbar_bands_rect(TB) == (0, 900, 62, 980)


# --- placement ------------------------------------------------------------------------------------

W_WIDTH, W_HEIGHT = 190, 40
TRAY = (1694, 1040, 1920, 1080)
GAP = constants.layout.TRAY_EDGE_MIN_GAP_PX


def _taskbar(toolbars):
    screen = MagicMock()
    screen.name.return_value = "DISPLAY1"
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920, 1040)
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd = 0x10
    tb.rect = (0, 1040, 1920, 1080)
    tb.dpi_scale = 1.0
    tb.tasklist_rect = None
    tb.widgets_rect = None
    tb.toolbars_rect = toolbars
    tb.get_tray_rect.return_value = TRAY
    tb.get_edge_position.return_value = constants.taskbar.edge.BOTTOM
    tb.get_screen.return_value = screen
    return tb


def _placed_x(toolbars):
    widget = MagicMock()
    widget.width.return_value = W_WIDTH
    widget.height.return_value = W_HEIGHT
    widget.pos.return_value = QPoint(-1, -1)
    m = PositionManager(WindowState(config={}, widget=widget, taskbar_info=None))
    m.update_position(fresh_taskbar_info=_taskbar(toolbars), _float_refreshed=True)
    return widget.move.call_args[0][0]


def test_widget_sits_left_of_the_toolbars():
    assert _placed_x((1500, 1040, 1690, 1080)) == 1500 - W_WIDTH - GAP


def test_no_toolbars_places_exactly_as_before():
    assert _placed_x(None) == TRAY[0] - W_WIDTH - GAP
