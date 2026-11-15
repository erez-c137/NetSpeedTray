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
    # The owner's 26200 bar (2026-11-15 probe): the ReBar and the task band end together.
    task = (651, 1032, 1136, 1080)
    with _hierarchy([TASKSW], {REBAR: task, TASKSW: task}):
        assert tu.find_toolbar_bands_rect(TB) is None


def test_toolbars_after_the_task_list_are_found():
    rects = {REBAR: (100, 1040, 1380, 1080), TASKSW: TASK_RECT,
             DESKTOP: (1200, 1040, 1290, 1080), LINKS: (1290, 1040, 1380, 1080)}
    with _hierarchy([TASKSW, DESKTOP, LINKS], rects, visible={DESKTOP, LINKS}):
        assert tu.find_toolbar_bands_rect(TB) == (1200, 1040, 1380, 1080)


def test_band_title_before_the_toolbar_child_counts():
    """The ReBar draws a band's gripper and title ("Desktop") itself, left of the toolbar child: the
    rect starts where the task band ends, so the widget can't sit on the title (2.2.1)."""
    rects = {REBAR: (100, 1040, 1380, 1080), TASKSW: TASK_RECT, DESKTOP: (1262, 1040, 1380, 1080)}
    with _hierarchy([TASKSW, DESKTOP], rects, visible={DESKTOP}):
        assert tu.find_toolbar_bands_rect(TB) == (1200, 1040, 1380, 1080)


def test_collapsed_band_without_a_visible_child_counts():
    """A band shrunk to its title and chevron: no visible child, but the task band stops short of the
    ReBar's end - that room is the toolbar's."""
    rects = {REBAR: (100, 1040, 1290, 1080), TASKSW: TASK_RECT, DESKTOP: (1290, 1040, 1290, 1080)}
    with _hierarchy([TASKSW, DESKTOP], rects):
        assert tu.find_toolbar_bands_rect(TB) == (1200, 1040, 1290, 1080)


def test_a_few_pixels_of_rebar_slack_are_not_a_toolbar():
    gap = constants.layout.TOOLBAR_BAND_MIN_GAP_PX
    with _hierarchy([TASKSW], {REBAR: (100, 1040, 1200 + gap, 1080), TASKSW: TASK_RECT}):
        assert tu.find_toolbar_bands_rect(TB) is None


def test_vertical_bar_collapsed_band_below_the_task_list():
    task = (0, 0, 62, 900)
    with _hierarchy([TASKSW], {REBAR: (0, 0, 62, 980), TASKSW: task}):
        assert tu.find_toolbar_bands_rect(TB) == (0, 900, 62, 980)


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


def _placed(tb, config=None):
    widget = MagicMock()
    widget.width.return_value = W_WIDTH
    widget.height.return_value = W_HEIGHT
    widget.pos.return_value = QPoint(-1, -1)
    m = PositionManager(WindowState(config=dict(config or {}), widget=widget, taskbar_info=None))
    m.update_position(fresh_taskbar_info=tb, _float_refreshed=True)
    return widget.move.call_args[0]


def _placed_x(toolbars, config=None):
    return _placed(_taskbar(toolbars), config)[0]


def test_widget_sits_left_of_the_toolbars():
    assert _placed_x((1500, 1040, 1690, 1080)) == 1500 - W_WIDTH - GAP


def test_no_toolbars_places_exactly_as_before():
    assert _placed_x(None) == TRAY[0] - W_WIDTH - GAP


def test_a_dragged_widget_stays_where_it_was_dropped():
    """A drag saves tray_offset_x measured from the TRAY (input_handler); measured from the toolbars
    instead, the widget jumped a whole toolbar width left of the drop."""
    offset = 400
    assert _placed_x((1500, 1040, 1690, 1080), {"tray_offset_x": offset}) == TRAY[0] - W_WIDTH - offset
    assert _placed_x(None, {"tray_offset_x": offset}) == TRAY[0] - W_WIDTH - offset


def test_task_list_snap_never_pushes_the_widget_back_onto_the_toolbars():
    """A classic task list runs right up to the toolbars: its "overlaps app icons" snap would put the
    widget on them again."""
    tb = _taskbar((1500, 1040, 1690, 1080))
    tb.tasklist_rect = (100, 1040, 1500, 1080)
    assert _placed(tb)[0] == 1500 - W_WIDTH - GAP


# --- a classic side bar (ExplorerPatcher / Win10: a real task list, the tray at the bottom) --------

SIDE_TRAY = (0, 900, 62, 1080)
SIDE_W, SIDE_H = 60, 40


def _side_taskbar(toolbars):
    screen = MagicMock()
    screen.name.return_value = "DISPLAY1"
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(62, 0, 1858, 1080)
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd = 0x10
    tb.rect = (0, 0, 62, 1080)
    tb.dpi_scale = 1.0
    tb.tasklist_rect = (0, 40, 62, 300)
    tb.widgets_rect = None
    tb.toolbars_rect = toolbars
    tb.get_tray_rect.return_value = SIDE_TRAY
    tb.get_edge_position.return_value = constants.taskbar.edge.LEFT
    tb.get_screen.return_value = screen
    return tb


def _placed_side_y(toolbars, config=None):
    widget = MagicMock()
    widget.width.return_value = SIDE_W
    widget.height.return_value = SIDE_H
    widget.pos.return_value = QPoint(-1, -1)
    m = PositionManager(WindowState(config=dict(config or {}), widget=widget, taskbar_info=None))
    m.update_position(fresh_taskbar_info=_side_taskbar(toolbars), _float_refreshed=True)
    return widget.move.call_args[0][1]


def test_side_bar_widget_sits_above_the_toolbars():
    off = constants.config.defaults.DEFAULT_TRAY_OFFSET_Y
    assert _placed_side_y((0, 800, 62, 900)) == 800 - SIDE_H - off
    assert _placed_side_y(None) == SIDE_TRAY[1] - SIDE_H - off


def test_side_bar_dragged_widget_keeps_its_tray_offset():
    assert _placed_side_y((0, 800, 62, 900), {"tray_offset_y": 200}) == SIDE_TRAY[1] - SIDE_H - 200
