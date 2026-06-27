"""
Multi-monitor screen-resolution tests for TaskbarInfo.get_screen() (#138 diagnosis).

The owner has only a single ultrawide, so these mock a two-monitor layout to drive the
real get_screen() matching logic. The bug pattern in #138 ("takes resolution from the
second monitor, places the widget on the first") is a screen MIS-resolution: a taskbar
that is physically on monitor B must resolve to monitor B, never fall back to primary.
"""
import pytest
from unittest.mock import MagicMock
from PyQt6.QtCore import QRect

from netspeedtray.utils import taskbar_utils
from netspeedtray.utils.taskbar_utils import TaskbarInfo


def _screen(x, y, w, h, name, dpr=1.0):
    s = MagicMock()
    s.geometry.return_value = QRect(x, y, w, h)
    s.devicePixelRatio.return_value = dpr
    s.name.return_value = name
    return s


@pytest.fixture
def two_monitors(monkeypatch, qtbot):
    primary = _screen(0, 0, 3440, 1440, r"\\.\DISPLAY1")
    secondary = _screen(3440, 0, 1920, 1080, r"\\.\DISPLAY2")
    monkeypatch.setattr(taskbar_utils.QApplication, "screens", staticmethod(lambda: [primary, secondary]))
    monkeypatch.setattr(taskbar_utils.QApplication, "primaryScreen", staticmethod(lambda: primary))
    return primary, secondary


def _taskbar_on_secondary(screen_geometry, dpi_scale=1.0):
    """A taskbar whose PHYSICAL rect sits on the secondary monitor (3440..5360)."""
    return TaskbarInfo(
        hwnd=999, tray_hwnd=None, tasklist_rect=None,
        rect=(3440, 1040, 5360, 1080),
        screen_name=r"\\.\DISPLAY2",
        screen_geometry=screen_geometry,
        work_area=(3440, 0, 5360, 1040), dpi_scale=dpi_scale, is_primary=False, height=40,
    )


def test_exact_geometry_match_resolves_to_secondary(two_monitors):
    _, secondary = two_monitors
    tb = _taskbar_on_secondary((3440, 0, 1920, 1080))  # exactly the secondary's geometry
    assert tb.get_screen() is secondary


def test_intersection_fallback_resolves_to_secondary(two_monitors):
    # Layer-1 exact match fails (off by 1) -> Layer-2 intersection must still pick B, not primary.
    primary, secondary = two_monitors
    tb = _taskbar_on_secondary((3440, 0, 1921, 1080))
    got = tb.get_screen()
    assert got is secondary, "intersection fallback resolved to the wrong monitor (#138)"
    assert got is not primary


def test_taskbar_on_secondary_never_resolves_to_primary(two_monitors):
    primary, _ = two_monitors
    tb = _taskbar_on_secondary((3440, 0, 1920, 1080))
    assert tb.get_screen() is not primary
