"""#322 - a docked widget must stay inside its horizontal taskbar strip.

Reported with the taskbar moved to the TOP by a third-party tool on Win11: the widget's mini graph
hung below the taskbar onto the desktop. Two mechanisms put a docked widget outside its strip:

- Size: ``WidgetLayoutManager._horizontal_dock_height`` floored the height to two text rows even
  when the strip was shorter, so the widget became taller than the taskbar.
- Position: ``PositionCalculator._calculate_horizontal_position`` centred that over-tall widget in
  the strip (a negative y for a TOP bar), then ``ScreenUtils.validate_position`` only clamped it to
  the SCREEN - which pushes all the overflow down onto the desktop.

Also, the strip was taken from the work-area inset alone. A third-party tool (or another appbar on
the same edge) can reserve more work area than the taskbar window actually covers, so the strip is
now the inset capped by the taskbar window's own height.

These cover TOP and BOTTOM, each with a strip shorter than the two-row minimum, and with an inset
taller than the taskbar window.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.core.position_manager import PositionCalculator
from netspeedtray.utils.taskbar_utils import TaskbarInfo
from netspeedtray.views.widget.layout import WidgetLayoutManager


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


SCREEN_W, SCREEN_H = 1920, 1080
TOP = constants.taskbar.edge.TOP
BOTTOM = constants.taskbar.edge.BOTTOM


def _screen(inset: int, edge) -> MagicMock:
    """A 1920x1080 screen whose work area is inset by ``inset`` px on ``edge``."""
    full = QRect(0, 0, SCREEN_W, SCREEN_H)
    if edge == TOP:
        avail = QRect(0, inset, SCREEN_W, SCREEN_H - inset)
    else:
        avail = QRect(0, 0, SCREEN_W, SCREEN_H - inset)
    s = MagicMock()
    s.geometry.return_value = full
    s.availableGeometry.return_value = avail
    return s


def _taskbar(edge, *, inset: int, window_h: int) -> MagicMock:
    """A docked taskbar window ``window_h`` px tall on ``edge``; the work area is inset by ``inset``."""
    tb = MagicMock(spec=TaskbarInfo)
    if edge == TOP:
        rect = (0, 0, SCREEN_W, window_h)
    else:
        rect = (0, SCREEN_H - window_h, SCREEN_W, SCREEN_H)
    tb.rect = rect
    tb.height = window_h
    tb.dpi_scale = 1.0
    tb.hwnd = 12345
    tb.tasklist_rect = None
    tb.widgets_rect = None
    tb.get_tray_rect.return_value = (1700, rect[1], SCREEN_W, rect[3])
    tb.get_edge_position.return_value = edge
    tb.get_screen.return_value = _screen(inset, edge)
    return tb


def _strip(edge, window_h: int):
    """(top, bottom) of the visible strip - bottom exclusive."""
    return (0, window_h) if edge == TOP else (SCREEN_H - window_h, SCREEN_H)


def _lm(font_size: int = 9) -> WidgetLayoutManager:
    lm = WidgetLayoutManager(MagicMock())
    lm.metrics = QFontMetrics(QFont("Segoe UI", font_size))
    return lm


# --- 1. Size: never taller than the strip ------------------------------------------------------

@pytest.mark.parametrize("edge", [TOP, BOTTOM])
def test_dock_height_never_exceeds_a_strip_shorter_than_two_rows(edge):
    lm = _lm()
    two_rows = lm.metrics.height() * 2 + 2
    strip = 24
    assert strip < two_rows, "precondition: the strip must be shorter than the two-row minimum"
    tb = _taskbar(edge, inset=strip, window_h=strip)
    assert lm._horizontal_dock_height(edge, tb, 1.0) <= strip


@pytest.mark.parametrize("edge", [TOP, BOTTOM])
def test_dock_height_capped_by_taskbar_window_when_inset_is_taller(edge):
    # A tool reserves a 60px work area, but the taskbar window itself is 40px tall.
    lm = _lm()
    tb = _taskbar(edge, inset=60, window_h=40)
    assert lm._horizontal_dock_height(edge, tb, 1.0) == 40


# --- 2. Position: y kept inside the strip ------------------------------------------------------

@pytest.mark.parametrize("edge", [TOP, BOTTOM])
def test_position_stays_inside_strip_when_inset_is_taller_than_window(edge):
    calc = PositionCalculator()
    tb = _taskbar(edge, inset=60, window_h=40)
    widget = (100, 40)
    pos = calc.calculate_position(tb, widget, {"tray_offset_x": 10})
    top, bottom = _strip(edge, 40)
    assert top <= pos.y and pos.y + widget[1] <= bottom, (pos.y, (top, bottom))


@pytest.mark.parametrize("edge", [TOP, BOTTOM])
def test_raw_y_never_leaves_strip_for_an_over_tall_widget(edge):
    """Before the screen clamp: an over-tall widget is pinned to the strip's screen-edge side, not
    centred across it (a negative y for TOP that validate_position then pushed onto the desktop)."""
    calc = PositionCalculator()
    tb = _taskbar(edge, inset=24, window_h=24)
    widget = (100, 40)
    _, y = calc._calculate_horizontal_position(tb, widget, {"tray_offset_x": 10}, 1.0)
    if edge == TOP:
        assert y == 0                   # flush with the screen top, overflow cannot go up
    else:
        assert y + widget[1] == SCREEN_H  # flush with the screen bottom


@pytest.mark.parametrize("edge", [TOP, BOTTOM])
def test_normal_taskbar_unchanged(edge):
    # Inset == window height (a stock taskbar): centred exactly as before.
    calc = PositionCalculator()
    tb = _taskbar(edge, inset=48, window_h=48)
    pos = calc.calculate_position(tb, (100, 40), {"tray_offset_x": 10})
    assert pos.y == (4 if edge == TOP else SCREEN_H - 48 + 4)
    assert pos.x == 1700 - 100 - 10


# --- Drag constraint uses the same strip --------------------------------------------------------

@pytest.mark.parametrize("edge", [TOP, BOTTOM])
def test_drag_constraint_stays_inside_strip(edge):
    calc = PositionCalculator()
    tb = _taskbar(edge, inset=60, window_h=40)
    p = calc.constrain_drag_position(QPoint(500, 500), tb, QSize(100, 40))
    top, bottom = _strip(edge, 40)
    assert top <= p.y() and p.y() + 40 <= bottom, (p.y(), (top, bottom))


# --- Size + position together: the widget rect lies inside the strip ---------------------------

@pytest.mark.parametrize("edge", [TOP, BOTTOM])
@pytest.mark.parametrize("inset,window_h", [(24, 24), (60, 40), (30, 30)])
def test_docked_widget_rect_inside_strip(edge, inset, window_h):
    lm = _lm()
    tb = _taskbar(edge, inset=inset, window_h=window_h)
    h = int(lm._horizontal_dock_height(edge, tb, 1.0) + 0.999)
    pos = PositionCalculator().calculate_position(tb, (100, h), {"tray_offset_x": 10})
    top, bottom = _strip(edge, window_h)
    assert top <= pos.y and pos.y + h <= bottom, (h, pos.y, (top, bottom))
