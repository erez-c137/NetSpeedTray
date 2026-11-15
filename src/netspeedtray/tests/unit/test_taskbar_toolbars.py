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


class _NoAnswer(Exception):
    """What pywin32's SendMessageTimeout raises when the ReBar times out (a hung Explorer)."""


def _hierarchy(children, rects, visible=(), bands=None):
    """children: ReBar children in order; rects: hwnd -> rect (REBAR included, as on a real bar);
    bands: what RB_GETBANDCOUNT answers - default one band per child, _NoAnswer = no answer."""
    gui = MagicMock()
    count = len(children) if bands is None else bands

    def find(parent, after, cls, _title):
        if parent == TB and cls == "ReBarWindow32":
            return REBAR
        if parent == REBAR and cls == "MSTaskSwWClass":
            return TASKSW if TASKSW in children else 0
        if parent == REBAR and cls is None:
            i = 0 if not after else children.index(after) + 1
            return children[i] if i < len(children) else 0
        return 0

    def send(hwnd, msg, _wp, _lp, _flags, _timeout):
        assert hwnd == REBAR and msg == constants.layout.REBAR_GETBANDCOUNT_MSG
        if count is _NoAnswer:
            raise _NoAnswer("timed out")
        return 1, count

    gui.FindWindowEx.side_effect = find
    gui.GetWindowRect.side_effect = lambda h: rects[h]
    gui.IsWindowVisible.side_effect = lambda h: h in visible or h == TASKSW
    gui.SendMessageTimeout.side_effect = send
    return patch.object(tu, "win32gui", gui)


def test_windows11_rebar_has_no_toolbars():
    # The owner's 26200 bar (2026-11-15 probe): one band, and the ReBar and the task band end together.
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


# A band shrunk to its title and chevron: no visible child, only room between the task band and the
# ReBar's end. That gap is believed only when the ReBar says it holds more than one band.
COLLAPSED = {REBAR: (100, 1040, 1290, 1080), TASKSW: TASK_RECT, DESKTOP: (1290, 1040, 1290, 1080)}


def test_collapsed_band_without_a_visible_child_counts():
    with _hierarchy([TASKSW, DESKTOP], COLLAPSED, bands=2):
        assert tu.find_toolbar_bands_rect(TB) == (1200, 1040, 1290, 1080)


@pytest.mark.parametrize("bands", [1, 0, _NoAnswer], ids=["one band", "no count", "no answer"])
def test_a_gap_the_rebar_does_not_back_is_not_a_toolbar(bands):
    """Something else shrinking the task band (a third-party taskbar tool, an Explorer restart) on a
    one-band Win11 bar must not read as toolbars mid-bar - nor a ReBar that never answers."""
    with _hierarchy([TASKSW, DESKTOP], COLLAPSED, bands=bands):
        assert tu.find_toolbar_bands_rect(TB) is None


def test_a_few_pixels_of_rebar_slack_are_not_a_toolbar():
    gap = constants.layout.TOOLBAR_BAND_MIN_GAP_PX
    with _hierarchy([TASKSW], {REBAR: (100, 1040, 1200 + gap, 1080), TASKSW: TASK_RECT}, bands=2):
        assert tu.find_toolbar_bands_rect(TB) is None


def test_vertical_bar_collapsed_band_below_the_task_list():
    task = (0, 0, 62, 900)
    with _hierarchy([TASKSW], {REBAR: (0, 0, 62, 980), TASKSW: task}, bands=2):
        assert tu.find_toolbar_bands_rect(TB) == (0, 900, 62, 980)


@pytest.mark.parametrize("rect,visible", [
    ((0, 1040, 100, 1080), True),         # dragged before the task list: the widget never sits there
    ((1200, 1040, 1290, 1080), False),    # hidden band: it takes no room, the task band fills the bar
    ((1200, 1040, 1200, 1080), True),     # empty rect
], ids=["before the task list", "hidden", "empty"])
def test_bands_taking_no_room_after_the_task_list_are_ignored(rect, visible):
    # Two bands, but the task band still runs to the ReBar's end: nothing sits between it and the tray.
    before = rect[0] == 0
    rects = {REBAR: (0 if before else 100, 1040, 1200, 1080), TASKSW: TASK_RECT, DESKTOP: rect}
    with _hierarchy([DESKTOP, TASKSW] if before else [TASKSW, DESKTOP], rects,
                    visible={DESKTOP} if visible else set()):
        assert tu.find_toolbar_bands_rect(TB) is None


def test_vertical_bar_toolbars_below_the_task_list():
    task = (0, 0, 62, 900)
    rects = {REBAR: (0, 0, 62, 980), TASKSW: task, DESKTOP: (0, 900, 62, 980)}
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


# --- a side bar with the tray at the bottom ---------------------------------------------------------
# The toolbar rule on a side bar runs only where a classic task list is reported (tasklist_rect: a
# ToolbarWindow32 directly under MSTaskSwWClass, find_tasklist_rect). Real Win10 / ExplorerPatcher bars
# most likely have MSTaskListWClass there instead (Win11 26200 does, probe 2026-11-15), so tasklist_rect
# is None and the native side-bar rules (side_bar_geometry) decide - see the last test. The tasklist
# rect faked below is the classic case the rule was written for, not a captured Win10 hierarchy.

SIDE_TRAY = (0, 900, 62, 1080)
SIDE_W, SIDE_H = 60, 40


def _side_taskbar(toolbars, tasklist=(0, 40, 62, 300)):
    screen = MagicMock()
    screen.name.return_value = "DISPLAY1"
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(62, 0, 1858, 1080)
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd = 0x10
    tb.rect = (0, 0, 62, 1080)
    tb.dpi_scale = 1.0
    tb.tasklist_rect = tasklist
    tb.widgets_rect = None
    tb.toolbars_rect = toolbars
    tb.get_tray_rect.return_value = SIDE_TRAY
    tb.get_edge_position.return_value = constants.taskbar.edge.LEFT
    tb.get_screen.return_value = screen
    return tb


def _placed_side_y(toolbars, config=None, tasklist=(0, 40, 62, 300)):
    widget = MagicMock()
    widget.width.return_value = SIDE_W
    widget.height.return_value = SIDE_H
    widget.pos.return_value = QPoint(-1, -1)
    m = PositionManager(WindowState(config=dict(config or {}), widget=widget, taskbar_info=None))
    m.update_position(fresh_taskbar_info=_side_taskbar(toolbars, tasklist), _float_refreshed=True)
    return widget.move.call_args[0][1]


def test_side_bar_widget_sits_above_the_toolbars():
    off = constants.config.defaults.DEFAULT_TRAY_OFFSET_Y
    assert _placed_side_y((0, 800, 62, 900)) == 800 - SIDE_H - off
    assert _placed_side_y(None) == SIDE_TRAY[1] - SIDE_H - off


def test_side_bar_dragged_widget_keeps_its_tray_offset():
    assert _placed_side_y((0, 800, 62, 900), {"tray_offset_y": 200}) == SIDE_TRAY[1] - SIDE_H - 200


def test_side_bar_without_a_classic_task_list_follows_the_native_rules():
    """The realistic hierarchy (MSTaskSwWClass > MSTaskListWClass, so tasklist_rect None):
    side_bar_geometry decides and the toolbar rect plays no part - the widget lands where it does with
    no toolbars at all, not above them."""
    with_toolbars = _placed_side_y((0, 800, 62, 900), tasklist=None)
    assert with_toolbars == _placed_side_y(None, tasklist=None)
    assert with_toolbars != 800 - SIDE_H - constants.config.defaults.DEFAULT_TRAY_OFFSET_Y
