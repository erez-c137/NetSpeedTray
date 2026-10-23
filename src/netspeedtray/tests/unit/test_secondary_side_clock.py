"""A Win11 SECONDARY side taskbar's clock (rc.2 final VM session, 2026-10-23).

Repro: 3 displays, "Show my taskbar on all displays", NetSpeedTray "Show on all taskbars" (#434
mirrors), taskbar position Right. Both mirrors sat at y 1014..1072, painted over each secondary bar's
clock: Shell_SecondaryTrayWnd has no TrayNotifyWnd, so ``get_tray_rect()`` is None and the side-bar
placement took the bar's BOTTOM EDGE as the tray end (log: ``tray 1080.0, from None``). The horizontal
path has reserved a clock width there since #186; the side path had nothing.

Fix: the side placement's tray end on such a bar is the clock UIA saw on THAT bar (the layout probe
already walks it: ``SystemTray.OmniButton``), else a reserved clock height from constants.
"""
import types
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.core.position_manager import (
    PositionCalculator, choose_side_placement, side_bar_geometry,
)
from netspeedtray.utils import taskbar_utils, uia_widgets
from netspeedtray.utils.taskbar_utils import TaskbarInfo

LEFT, RIGHT = constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT
BOTTOM, TOP = constants.taskbar.edge.BOTTOM, constants.taskbar.edge.TOP
GAP = constants.layout.TRAY_EDGE_MIN_GAP_PX
RESERVE = constants.layout.SECONDARY_SIDE_CLOCK_RESERVE_PX
H = 58                       # the mirror's height in the VM (3 rows)
W = 48
HWND = 65764

# The VM's secondary right bar (display 3, x 5712..5760), UIA dump 2026-10-23, (l, t, r, b).
X0, X1 = 5712, 5760
VM_SECONDARY_RIGHT_BAR = [
    ("Taskbar.TaskbarFrameAutomationPeer", "TaskbarFrame", (X0, 0, X1, 1080)),
    ("ToggleButton", "WidgetsButton", (X0, 10, X1, 54)),
    ("ToggleButton", "StartButton", (X0, 320, X1, 365)),
    ("ToggleButton", "SearchButton", (X0, 365, X1, 409)),
    ("ToggleButton", "TaskViewButton", (X0, 409, X1, 453)),
    ("SystemTray.OmniButton", "SystemTrayIcon", (X0, 1036, X1, 1072)),   # the clock
    ("WorkerW", "", (X0, 319, X1, 628)),
    ("MSTaskListWClass", "", (X0, 319, X1, 628)),
]
CLOCK = (X0, 1036, X1, 1072)


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clean_probe_cache():
    taskbar_utils._probed_tray.clear()
    yield
    taskbar_utils._probed_tray.clear()


def _secondary_bar(edge=RIGHT, hwnd=HWND):
    """A Win11 secondary side bar on a 1920x1080 screen at x 3840: no tray rect, no task list."""
    sx = 3840
    x0 = sx + 1920 - W if edge == RIGHT else sx
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd = hwnd
    tb.dpi_scale = 1.0
    tb.rect = (x0, 0, x0 + W, 1080)
    tb.height = W
    tb.tasklist_rect = None
    tb.toolbars_rect = None
    tb.apps_extent = (x0, 320, x0 + W, 453)
    tb.widgets_extent = (x0, 10, x0 + W, 54)
    tb.widgets_rect = None
    tb.get_edge_position.return_value = edge
    screen = MagicMock()
    screen.geometry.return_value = QRect(sx, 0, 1920, 1080)
    screen.availableGeometry.return_value = (QRect(sx, 0, 1920 - W, 1080) if edge == RIGHT
                                             else QRect(sx + W, 0, 1920 - W, 1080))
    tb.get_screen.return_value = screen
    tb.get_tray_rect.return_value = None          # Shell_SecondaryTrayWnd: no TrayNotifyWnd
    return tb


def _place(tb, h=H):
    geo = side_bar_geometry(tb, 1.0)
    placement = choose_side_placement(geo, h)
    pos = PositionCalculator().calculate_position(tb, (W, h), {"tray_offset_y": 3},
                                                  side_placement=placement)
    return geo, placement, pos


# --- the probe finds the secondary clock -----------------------------------------------------------

def test_tray_extent_is_the_secondary_bars_clock():
    assert uia_widgets.tray_extent_from_elements(VM_SECONDARY_RIGHT_BAR) == CLOCK


def test_tray_extent_on_the_main_bar_is_the_whole_tray_and_ignores_other_buttons():
    els = [("ToggleButton", "StartButton", (1872, 320, 1920, 365)),
           ("SystemTray.NormalButton", "SystemTrayIcon", (1872, 904, 1920, 932)),
           ("SystemTray.OmniButton", "SystemTrayIcon", (1872, 1028, 1920, 1064)),
           ("SystemTray.ShowDesktopButton", "SystemTrayIcon", (1872, 1068, 1920, 1080)),
           ("SystemTray.NormalButton", "x", (1872, 0, 1872, 0))]                  # empty: skipped
    assert uia_widgets.tray_extent_from_elements(els) == (1872, 904, 1920, 1080)
    assert uia_widgets.tray_extent_from_elements([]) is None


def test_full_layout_query_carries_the_tray_extent():
    full = uia_widgets._layout_from_elements_full(VM_SECONDARY_RIGHT_BAR, None)
    assert len(full) == 5 and full[4] == CLOCK
    assert full[0] == (X0, 320, X1, 453)          # the clock is not an app button


def test_the_probe_worker_remembers_each_bars_tray_across_other_bars_probes():
    """The layout cache holds ONE taskbar; the main widget and every mirror probe their own bar, so
    the clock must survive another bar's probe."""
    results = {HWND: (None, None, None, None, CLOCK),
               111: ((0, 300, 48, 500), None, None, None, (0, 904, 48, 1080))}
    with patch.object(taskbar_utils, "_start_layout_worker"), \
            patch.object(taskbar_utils, "_query_taskbar_layout_blocking",
                         side_effect=lambda h: results[h]):
        for hwnd in (HWND, 111):
            taskbar_utils.request_taskbar_layout_probe(hwnd)
            taskbar_utils._layout_worker()
    assert taskbar_utils.probed_tray_rect(HWND) == CLOCK
    assert taskbar_utils.probed_tray_rect(111) == (0, 904, 48, 1080)
    assert taskbar_utils.probed_tray_rect(None) is None
    assert taskbar_utils.probed_tray_rect(999) is None


# --- the placement keeps clear of it ---------------------------------------------------------------

@pytest.mark.parametrize("edge", [RIGHT, LEFT])
def test_secondary_side_bar_without_a_probe_reserves_the_clock(edge):
    """The VM bug: y must end above the clock's estimated top, never at the bar's bottom edge."""
    geo, placement, pos = _place(_secondary_bar(edge))
    assert geo.tray_top == 1080 - RESERVE
    assert placement.mode == "tray"
    assert pos.y == 1080 - RESERVE - GAP - H
    assert pos.y + H <= CLOCK[1]                  # clear of the real clock (1036..1072)


@pytest.mark.parametrize("edge", [RIGHT, LEFT])
def test_secondary_side_bar_uses_the_probed_clock(edge):
    tb = _secondary_bar(edge)
    x0 = tb.rect[0]
    taskbar_utils._remember_probed_tray(HWND, (x0, 1012, x0 + W, 1072))   # e.g. a bigger clock
    geo, _placement, pos = _place(tb)
    assert geo.tray_top == 1012
    assert pos.y == 1012 - GAP - H


def test_a_probed_tray_from_another_layout_of_the_same_hwnd_is_ignored():
    """Bottom -> Right keeps the HWND: a clock probed on the bottom bar is not inside the side bar."""
    tb = _secondary_bar(RIGHT)
    taskbar_utils._remember_probed_tray(HWND, (5600, 1032, 5712, 1080))
    geo, _placement, _pos = _place(tb)
    assert geo.tray_top == 1080 - RESERVE


def test_a_probed_rect_in_the_upper_half_is_not_taken_for_the_clock():
    tb = _secondary_bar(RIGHT)
    taskbar_utils._remember_probed_tray(HWND, (X0, 10, X1, 54))
    assert side_bar_geometry(tb, 1.0).tray_top == 1080 - RESERVE


def test_drag_limits_on_a_secondary_side_bar_stop_above_the_clock():
    tb = _secondary_bar(RIGHT)
    from PyQt6.QtCore import QPoint, QSize
    pt = PositionCalculator().constrain_drag_position(QPoint(X0, 1070), tb, QSize(W, H))
    assert pt.y() + H <= 1080 - RESERVE


def test_main_bar_with_a_real_tray_is_unchanged():
    tb = _secondary_bar(RIGHT)
    tb.get_tray_rect.return_value = (X0, 904, X1, 1080)
    taskbar_utils._remember_probed_tray(HWND, CLOCK)            # never consulted with a tray rect
    assert side_bar_geometry(tb, 1.0).tray_top == 904


@pytest.mark.parametrize("edge", [BOTTOM, TOP])
def test_horizontal_secondary_bar_keeps_the_clock_width_reserve(edge):
    """The horizontal path (#186) is separate and unchanged: x stays clear of the clock width."""
    tb = MagicMock(spec=TaskbarInfo)
    tb.hwnd = HWND
    tb.dpi_scale = 1.0
    y0 = 1032 if edge == BOTTOM else 0
    tb.rect = (3840, y0, 5760, y0 + 48)
    tb.height = 48
    tb.tasklist_rect = None
    tb.toolbars_rect = None
    tb.get_edge_position.return_value = edge
    screen = MagicMock()
    screen.geometry.return_value = QRect(3840, 0, 1920, 1080)
    screen.availableGeometry.return_value = (QRect(3840, 0, 1920, 1032) if edge == BOTTOM
                                             else QRect(3840, 48, 1920, 1032))
    tb.get_screen.return_value = screen
    tb.get_tray_rect.return_value = None
    pos = PositionCalculator().calculate_position(tb, (85, 40), {"tray_offset_x": 0})
    reserve = constants.config.defaults.DEFAULT_SECONDARY_CLOCK_RESERVE_PX
    assert pos.x == 5760 - reserve - 85


def test_a_new_dock_drops_the_clock_probed_on_the_old_one():
    """#444: a live scale change (100 -> 150 %) re-docks the SAME HWND with the same 1080 physical px
    height, so a 100 % clock rect still lies inside the bar, in its lower half - but the clock is now
    taller (top ~1014 physical). Until the fresh probe lands, the DPI-scaled reserve must be used,
    never the old layout's clock (690.7 logical, 15 px down into the 150 % clock)."""
    with patch.object(taskbar_utils, "_start_layout_worker"), \
            patch.object(taskbar_utils, "_query_taskbar_layout_blocking",
                         return_value=(None, None, None, None, CLOCK)):
        taskbar_utils.request_taskbar_layout_probe(HWND)
        taskbar_utils._layout_worker()
    assert taskbar_utils.probed_tray_rect(HWND) == CLOCK
    tb = _secondary_bar(RIGHT)
    tb.dpi_scale = 1.5
    tb.rect = (X1 - 72, 0, X1, 1080)
    with patch.object(taskbar_utils, "_start_layout_worker"):
        taskbar_utils.request_taskbar_layout_probe(HWND)       # the new dock's probe, in flight
    geo = side_bar_geometry(tb, 1.5)
    assert geo.tray_top == 1080 / 1.5 - RESERVE
