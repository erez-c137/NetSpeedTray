"""
Taskbar edge on a secondary monitor with a different scale (#7, rc check).

get_edge_position divided the PHYSICAL taskbar rect by the monitor's DPI and compared it with Qt's
logical screen geometry - but Qt keeps each screen's NATIVE origin and only scales its size. On a
150% monitor stacked below another, rect / 1.5 lands far above the screen Qt reports, so a bottom
bar read as TOP. The edge is now decided in physical px against the bar's own monitor rect
(TaskbarInfo.monitor_rect, from GetMonitorInfo); the logical path stays only for when that is unknown.

Qt's geometry is mocked the way Qt 6 reports it on Windows with per-monitor DPI: native top-left,
size divided by the scale.
"""
import itertools
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QRect

from netspeedtray import constants
from netspeedtray.utils.taskbar_utils import TaskbarInfo

E = constants.TaskbarEdge
BAR = 48  # logical


def _qt_screen(mon, dpi):
    s = MagicMock()
    l, t, r, b = mon
    s.geometry.return_value = QRect(l, t, round((r - l) / dpi), round((b - t) / dpi))
    return s


def _bar_rect(mon, edge, dpi, autohide=False):
    l, t, r, b = mon
    th = round(BAR * dpi)
    shift = (th - 2) if autohide else 0   # an auto-hide bar slid away, 2 px left showing
    return {E.BOTTOM: (l, b - th + shift, r, b + shift), E.TOP: (l, t - shift, r, t + th - shift),
            E.LEFT: (l - shift, t, l + th - shift, b), E.RIGHT: (r - th + shift, t, r + shift, b)}[edge]


def _info(rect, mon, dpi, with_monitor=True):
    return TaskbarInfo(hwnd=77, tray_hwnd=None, tasklist_rect=None, rect=rect, screen_name="S2",
                       screen_geometry=(0, 0, 1, 1), work_area=mon, dpi_scale=dpi, is_primary=False,
                       height=BAR, monitor_rect=mon if with_monitor else None)


def _layouts():
    """(name, primary phys rect, primary dpi, secondary phys rect, secondary dpi)."""
    out = []
    for p_dpi, s_dpi in itertools.product([1.0, 1.25, 1.5, 2.0], repeat=2):
        pw, ph = round(1920 * p_dpi), round(1080 * p_dpi)
        sw, sh = round(1920 * s_dpi), round(1080 * s_dpi)
        prim = (0, 0, pw, ph)
        out += [
            (f"below-{p_dpi}-{s_dpi}", prim, p_dpi, (0, ph, sw, ph + sh), s_dpi),
            (f"above-{p_dpi}-{s_dpi}", prim, p_dpi, (0, -sh, sw, 0), s_dpi),
            (f"right-{p_dpi}-{s_dpi}", prim, p_dpi, (pw, 0, pw + sw, sh), s_dpi),
            (f"left-{p_dpi}-{s_dpi}", prim, p_dpi, (-sw, 0, 0, sh), s_dpi),
            (f"right-offset-{p_dpi}-{s_dpi}", prim, p_dpi, (pw, -400, pw + sw, sh - 400), s_dpi),
        ]
    return out


LAYOUTS = _layouts()


@pytest.mark.parametrize("autohide", [False, True], ids=["docked", "autohide"])
@pytest.mark.parametrize("edge", [E.BOTTOM, E.TOP, E.LEFT, E.RIGHT], ids=lambda e: e.name)
@pytest.mark.parametrize("layout", LAYOUTS, ids=[x[0] for x in LAYOUTS])
def test_every_monitor_reads_its_own_edge(layout, edge, autohide):
    _, prim, p_dpi, sec, s_dpi = layout
    for mon, dpi in ((prim, p_dpi), (sec, s_dpi)):
        info = _info(_bar_rect(mon, edge, dpi, autohide), mon, dpi)
        with patch.object(TaskbarInfo, "get_screen", return_value=_qt_screen(mon, dpi)):
            assert info.get_edge_position() == edge, (layout[0], mon, dpi, edge, info.rect)


def test_without_a_monitor_rect_the_logical_path_still_answers():
    mon, dpi = (0, 0, 1920, 1080), 1.0
    info = _info(_bar_rect(mon, E.RIGHT, dpi), mon, dpi, with_monitor=False)
    with patch.object(TaskbarInfo, "get_screen", return_value=_qt_screen(mon, dpi)):
        assert info.get_edge_position() == E.RIGHT
