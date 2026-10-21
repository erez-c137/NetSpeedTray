"""
A fallback taskbar (no Explorer taskbar found yet, e.g. at logon) reads as BOTTOM, not LEFT.

Its rect is (0, 0, 0, 0): width 0 is not greater than height 0, so get_edge_position took the vertical
branch and answered LEFT, and the widget sized itself as a compact side-bar block parked at the
desktop's bottom-right until the real taskbar appeared.
"""
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QRect

from netspeedtray import constants
from netspeedtray.utils.taskbar_utils import TaskbarInfo

E = constants.TaskbarEdge


def _info(hwnd, rect, height=48):
    return TaskbarInfo(hwnd=hwnd, tray_hwnd=None, tasklist_rect=None, rect=rect, screen_name="X",
                       screen_geometry=(0, 0, 1920, 1080), work_area=(0, 0, 1920, 1032), dpi_scale=1.0,
                       is_primary=True, height=height)


def _screen():
    s = MagicMock()
    s.geometry.return_value = QRect(0, 0, 1920, 1080)
    return s


@pytest.mark.parametrize("hwnd,rect", [
    (0, (0, 0, 0, 0)),            # create_primary_fallback_taskbar_info
    (0, (1872, 0, 1920, 1080)),   # no HWND: whatever the rect, there is no taskbar to read
    (123, (0, 0, 0, 0)),          # a live HWND whose rect could not be read
    (123, (0, 1032, 0, 1080)),    # zero width
    (123, (5, 5, 5, 5)),
])
def test_no_taskbar_or_degenerate_rect_is_bottom(hwnd, rect):
    info = _info(hwnd, rect)
    with patch.object(TaskbarInfo, "get_screen", return_value=_screen()):
        assert info.get_edge_position() == E.BOTTOM


def test_primary_fallback_taskbar_is_bottom(q_app):
    info = TaskbarInfo.create_primary_fallback_taskbar_info()
    assert info.hwnd == 0
    assert info.get_edge_position() == E.BOTTOM


@pytest.mark.parametrize("rect,edge", [
    ((0, 1032, 1920, 1080), E.BOTTOM), ((0, 0, 1920, 48), E.TOP),
    ((0, 0, 48, 1080), E.LEFT), ((1872, 0, 1920, 1080), E.RIGHT),
])
def test_a_real_taskbar_still_reads_its_edge(rect, edge):
    with patch.object(TaskbarInfo, "get_screen", return_value=_screen()):
        assert _info(123, rect).get_edge_position() == edge
