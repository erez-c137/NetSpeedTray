"""#221 - the docked widget height must never collapse below the two-row readout.

When Windows does not inset the work area for the taskbar (some Win11 26200 single-monitor
setups report ``QScreen.availableGeometry() == geometry()``), the old height calc derived 0 from
the work-area diff and clamped to a 20px floor, while the renderer always draws a ~33px two-row
stack - so the text was cropped equally top and bottom. ``WidgetLayoutManager._horizontal_dock_height``
now (A) falls back to the real taskbar height when the diff is non-positive and (B) floors to the
two-row content so text can never crop.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.views.widget.layout import WidgetLayoutManager


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def _screen(geo: QRect, avail: QRect):
    s = MagicMock()
    s.geometry.return_value = geo
    s.availableGeometry.return_value = avail
    return s


def _taskbar(screen, *, height: int, rect=(0, 1392, 2560, 1440), dpi_scale=1.0):
    tb = MagicMock()
    tb.get_screen.return_value = screen
    tb.height = height
    tb.rect = rect
    tb.dpi_scale = dpi_scale
    return tb


def _lm(font_size: int = 9) -> WidgetLayoutManager:
    lm = WidgetLayoutManager(MagicMock())
    lm.metrics = QFontMetrics(QFont("Segoe UI", font_size))
    return lm


def _two_row_min(lm: WidgetLayoutManager) -> int:
    """The renderer's two-row content height (draw_network_speeds: 2*line_height + 1)."""
    return lm.metrics.height() * 2 + 1


def test_bug221_workarea_not_inset_falls_back_to_taskbar_height():
    # The #221 condition: availableGeometry == geometry, so the work-area diff is 0.
    # Old code returned 0 here -> clamped to a 20px box -> the two-row readout cropped top & bottom.
    full = QRect(0, 0, 2560, 1440)
    lm = _lm()
    tb = _taskbar(_screen(full, full), height=48)
    h = lm._horizontal_dock_height(constants.TaskbarEdge.BOTTOM, tb, 1.0)
    assert h == 48                      # Part A: real taskbar height, not the 20px floor
    assert h >= _two_row_min(lm)        # both rows fit -> no crop


def test_normal_bottom_taskbar_uses_work_area_inset_unchanged():
    # availableGeometry inset by a 48px bottom taskbar: diff is used as-is (no regression to #104/#110).
    full = QRect(0, 0, 2560, 1440)
    avail = QRect(0, 0, 2560, 1392)     # 48px shorter
    lm = _lm()
    tb = _taskbar(_screen(full, avail), height=48)
    assert lm._horizontal_dock_height(constants.TaskbarEdge.BOTTOM, tb, 1.0) == 48


def test_top_taskbar_workarea_not_inset_falls_back():
    full = QRect(0, 0, 2560, 1440)
    lm = _lm()
    tb = _taskbar(_screen(full, full), height=44)
    h = lm._horizontal_dock_height(constants.TaskbarEdge.TOP, tb, 1.0)
    assert h == 44
    assert h >= _two_row_min(lm)


def test_small_taskbar_floored_to_two_row_content():
    # A genuinely small taskbar (below the two-row content) must still not crop: Part B floor.
    full = QRect(0, 0, 1920, 1080)
    avail = QRect(0, 0, 1920, 1058)     # ~22px taskbar, positive so Part A does not fire
    lm = _lm()
    tb = _taskbar(_screen(full, avail), height=22)
    assert lm._horizontal_dock_height(constants.TaskbarEdge.BOTTOM, tb, 1.0) >= _two_row_min(lm)
