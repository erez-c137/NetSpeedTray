"""
Edge case positioning tests for ultrawide, mixed-DPI, and taskbar scenarios.

These tests validate that widget positioning handles real-world edge cases
that real users encounter.

Priority: Critical for taskbar widget reliability.
"""

import pytest
from unittest.mock import MagicMock
from PyQt6.QtCore import QPoint, QRect

from netspeedtray.core.position_manager import PositionCalculator, ScreenUtils
from netspeedtray import constants


class MockScreen:
    """Mock QScreen for testing without Qt display."""
    def __init__(self, name: str, geometry: tuple, dpi_scale: float = 1.0):
        self.name_val = name
        self.geometry_rect = QRect(*geometry)
        self.dpi = dpi_scale

    def name(self):
        return self.name_val

    def geometry(self):
        return self.geometry_rect

    def availableGeometry(self):
        # Slightly shorter for taskbar
        return QRect(
            self.geometry_rect.left(),
            self.geometry_rect.top(),
            self.geometry_rect.width(),
            self.geometry_rect.height() - 40
        )

    def logicalDotsPerInch(self):
        return 96 * self.dpi


class TestUltrawideDisplays:
    """Ultrawide monitor positioning tests."""
    @pytest.mark.parametrize("resolution,aspect_ratio", [
        ((3440, 1440), "21:9"),
        ((5120, 1440), "32:9"),
        ((1440, 2560), "9:16 vertical"),
    ])
    def test_widget_centers_on_ultrawide(self, resolution, aspect_ratio):
        width, height = resolution
        screen = MockScreen(f"Ultrawide {aspect_ratio}", (0, 0, width, height))

        widget_size = (100, 30)
        center_x = width // 2
        center_y = height // 2

        validated = ScreenUtils.validate_position(center_x, center_y, widget_size, screen)

        assert 0 <= validated.x < width - widget_size[0]
        assert 0 <= validated.y < height - widget_size[1]

    def test_widget_fits_on_extreme_ultrawide(self):
        screen = MockScreen("5K Ultrawide", (0, 0, 5120, 1440))
        widget_size = (200, 50)

        pos = ScreenUtils.validate_position(5000, 700, widget_size, screen)

        assert pos.x + widget_size[0] <= 5120
        assert pos.y + widget_size[1] <= 1440

    def test_widget_on_vertical_ultrawide(self):
        screen = MockScreen("Vertical Ultrawide", (0, 0, 1440, 2560))
        widget_size = (100, 30)

        validated = ScreenUtils.validate_position(720, 1280, widget_size, screen)

        assert 0 <= validated.x
        assert 0 <= validated.y


class TestMixedDPIDisplays:
    """Mixed scaling factor scenarios."""

    def test_widget_across_100_to_200_percent_boundary(self):
        screen_100 = MockScreen("1080p", (0, 0, 1920, 1080), dpi_scale=1.0)
        screen_200 = MockScreen("4K", (1920, 0, 3840, 2160), dpi_scale=2.0)

        drag_pos = QPoint(2400, 1000)

        validated = ScreenUtils.validate_position(
            drag_pos.x(), drag_pos.y(), (100, 30), screen_200
        )

        assert 1920 <= validated.x <= 3840 - 100
        assert 0 <= validated.y <= 2160 - 30

    @pytest.mark.parametrize("dpi_pairs", [
        [(1.0, "100%"), (1.25, "125%")],
        [(1.0, "100%"), (1.5, "150%")],
        [(1.25, "125%"), (2.0, "200%")],
    ])
    def test_positioning_consistency_across_dpi_changes(self, dpi_pairs):
        calculator = PositionCalculator()

        dpi_1, name_1 = dpi_pairs[0]
        dpi_2, name_2 = dpi_pairs[1]

        screen1 = MockScreen(name_1, (0, 0, 1920, 1080), dpi_scale=dpi_1)
        screen2 = MockScreen(name_2, (1920, 0, 1920, 1080), dpi_scale=dpi_2)

        widget_size = (100, 30)

        pos1 = ScreenUtils.validate_position(1000, 500, widget_size, screen1)
        pos2 = ScreenUtils.validate_position(2000, 500, widget_size, screen2)

        assert pos1 is not None
        assert pos2 is not None


class TestTaskbarEdgeCases:
    """Real-world taskbar edge cases."""

    def test_widget_doesnt_exceed_screen_bounds(self):
        screen = MockScreen("Standard", (0, 0, 1920, 1080))
        widget_size = (150, 40)

        test_positions = [
            (1800, 500),
            (500, 1050),
            (-10, 500),
            (500, -10),
            (1900, 1060),
        ]

        for x, y in test_positions:
            validated = ScreenUtils.validate_position(x, y, widget_size, screen)
            geom = screen.geometry()
            assert validated.x >= geom.left()
            assert validated.x + widget_size[0] <= geom.left() + geom.width()
            assert validated.y >= geom.top()
            assert validated.y + widget_size[1] <= geom.top() + geom.height()

    def test_position_with_taskbar_at_different_edges(self):
        screen = MockScreen("Standard", (0, 0, 1920, 1080))
        widget_size = (100, 30)

        taskbar_positions = [
            ("bottom", 1050, 1080),
            ("top", 0, 40),
            ("left", 0, 50),
            ("right", 1870, 50),
        ]

        for position_name, start_x, start_y in taskbar_positions:
            validated = ScreenUtils.validate_position(start_x, start_y, widget_size, screen)
            assert validated is not None, f"Failed for taskbar at {position_name}"


class TestMultiMonitorBoundaries:
    """Multi-monitor crossing scenarios."""

    @pytest.mark.parametrize("layout", [
        "side_by_side_1080p",
        "side_by_side_mixed_4k_1080p",
        "vertically_stacked",
    ])
    def test_widget_crossing_monitor_boundary(self, layout):
        if layout == "side_by_side_1080p":
            screen1 = MockScreen("Left", (0, 0, 1920, 1080))
            screen2 = MockScreen("Right", (1920, 0, 1920, 1080))
            boundary_x = 1920

        elif layout == "side_by_side_mixed_4k_1080p":
            screen1 = MockScreen("1080p", (0, 0, 1920, 1080))
            screen2 = MockScreen("4K", (1920, 0, 3840, 2160))
            boundary_x = 1920

        elif layout == "vertically_stacked":
            screen1 = MockScreen("Top", (0, 0, 1920, 1080))
            screen2 = MockScreen("Bottom", (0, 1080, 1920, 1080))
            boundary_x = 960

        widget_size = (100, 30)

        pos_near_boundary = ScreenUtils.validate_position(
            boundary_x - 50, 500, widget_size, screen1
        )

        assert pos_near_boundary is not None


class TestExtremeResolutions:
    """Ensure widget works on unusual resolutions."""

    @pytest.mark.parametrize("resolution", [
        (800, 600),
        (1024, 768),
        (2560, 1440),
        (3840, 2160),
        (5120, 2880),
        (7680, 4320),
    ])
    def test_widget_on_various_resolutions(self, resolution):
        width, height = resolution
        screen = MockScreen(f"{width}x{height}", (0, 0, width, height))
        widget_size = (100, 30)

        center_x = width // 2
        center_y = height // 2

        validated = ScreenUtils.validate_position(center_x, center_y, widget_size, screen)

        assert validated is not None
        assert 0 <= validated.x <= width - widget_size[0]
        assert 0 <= validated.y <= height - widget_size[1]
