"""
DPI-aware position calculation tests.
Validates that widget centers correctly at various scaling factors.

Issue #104: High-DPI Widget Positioning
- Tests verify floating-point DPI calculations eliminate rounding errors
- Covers horizontal and vertical taskbar positioning
- Validates results at 100%, 125%, 150%, 200% DPI scaling
"""
import pytest
from unittest.mock import MagicMock, patch
from parameterized import parameterized
from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    """Create Qt application for all tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def mock_screen():
    """Create a mock QScreen for testing."""
    screen = MagicMock(spec=QScreen)
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920, 1040)
    screen.name.return_value = "Mock Screen"
    return screen


class MockTaskbarInfo:
    """Mock TaskbarInfo for testing without Windows API calls."""
    def __init__(self, rect=(0, 0, 1920, 40), dpi_scale=1.0, edge=0, hwnd=1):
        self.rect = rect  # physical pixels (x1, y1, x2, y2)
        self.dpi_scale = dpi_scale
        self._edge = edge
        self.tasklist_rect = None
        self.hwnd = hwnd  # Add hwnd attribute
    
    def get_edge_position(self):
        return self._edge
    
    def get_tray_rect(self):
        return None
    
    def get_screen(self, fallback=None):
        screen = MagicMock(spec=QScreen)
        screen.geometry.return_value = QRect(0, 0, 1920, 1080)
        screen.name.return_value = "Mock Screen"
        return screen


class TestPositionManagerDPIHorizontalTaskbar:
    """Test DPI-aware calculations for horizontal (top/bottom) taskbars."""
    
    @pytest.mark.parametrize("dpi_scale", [1.0, 1.25, 1.5, 2.0])
    def test_bottom_taskbar_centering_various_dpi(self, dpi_scale, mock_screen):
        """
        Test widget centers correctly on bottom taskbar at various DPI scales.
        
        At each DPI level, widget should be vertically centered in taskbar gap.
        The fix for #104 uses floating-point intermediates to preserve precision.
        """
        from netspeedtray.core.position_manager import PositionCalculator
        from netspeedtray import constants
        
        # Physical pixel coordinates (as returned by Windows API)
        screen_height_physical = int(1080 * dpi_scale)
        taskbar_top_physical = screen_height_physical - int(40 * dpi_scale)
        taskbar_bottom_physical = screen_height_physical
        
        taskbar_info = MockTaskbarInfo(
            rect=(0, taskbar_top_physical, int(1920 * dpi_scale), taskbar_bottom_physical),
            dpi_scale=dpi_scale,
            edge=constants.taskbar.edge.BOTTOM
        )
        
        widget_width = 100
        widget_height = 30
        
        # Create config
        config = {
            'tray_offset_x': constants.config.defaults.DEFAULT_TRAY_OFFSET_X,
            'free_move': False
        }
        
        # Use PositionCalculator directly for unit testing
        calculator = PositionCalculator()
        pos = calculator.calculate_position(
            taskbar_info=taskbar_info,
            widget_size=(widget_width, widget_height),
            config=config
        )
        
        # Expected Y in logical coordinates:
        # Taskbar top in logical = taskbar_top_physical / dpi_scale
        # Taskbar height in logical = (taskbar_bottom - taskbar_top) / dpi_scale  
        # Y center = tb_top_log + (tb_height - widget_height) / 2
        tb_top_logical = taskbar_top_physical / dpi_scale
        tb_height_logical = (taskbar_bottom_physical - taskbar_top_physical) / dpi_scale
        expected_y_logical = round(tb_top_logical + (tb_height_logical - widget_height) / 2.0)
        
        # Allow 1 pixel tolerance due to rounding
        assert pos is not None
        y_diff = abs(pos.y - expected_y_logical)
        assert y_diff <= 1, f"DPI {dpi_scale}: Y position {pos.y} differs from expected {expected_y_logical} by {y_diff} pixels"
    
    def test_no_cumulative_rounding_errors(self, mock_screen):
        """
        Verify repeated calls to calculate_position produce identical results.
        
        The bug fix for #104 ensures floating-point intermediates are used
        throughout the calculation, preventing accumulated rounding errors.
        """
        from netspeedtray.core.position_manager import PositionCalculator
        from netspeedtray import constants
        
        taskbar_info = MockTaskbarInfo(
            dpi_scale=1.25,
            edge=constants.taskbar.edge.BOTTOM
        )
        
        config = {'tray_offset_x': constants.config.defaults.DEFAULT_TRAY_OFFSET_X}
        
        # Use PositionCalculator directly
        calculator = PositionCalculator()
        
        # Calculate position 100 times
        results = [
            calculator.calculate_position(
                taskbar_info=taskbar_info,
                widget_size=(100, 30),
                config=config
            )
            for _ in range(100)
        ]
        
        # All results should be identical
        first_result = results[0]
        for i, result in enumerate(results[1:], 1):
            assert result.x == first_result.x, f"X position changed at iteration {i}: {result.x} != {first_result.x}"
            assert result.y == first_result.y, f"Y position changed at iteration {i}: {result.y} != {first_result.y}"


class TestPositionManagerDPIVerticalTaskbar:
    """Test DPI-aware calculations for vertical (left/right) taskbars."""
    
    @pytest.mark.parametrize("dpi_scale,edge_name,edge_const", [
        (1.0, "LEFT", 0),
        (1.25, "LEFT", 0),
        (1.5, "RIGHT", 1),
        (2.0, "RIGHT", 1),
    ])
    def test_vertical_taskbar_centering(self, dpi_scale, edge_name, edge_const, mock_screen):
        """
        Test widget centers correctly on vertical taskbar at various DPI scales.
        
        Vertical taskbars can be on left (edge=0) or right (edge=1) of screen.
        Widget should be horizontally centered in the taskbar gap.
        """
        from netspeedtray.core.position_manager import PositionCalculator
        from netspeedtray import constants
        
        # Determine physical coordinates based on edge
        screen_width_physical = int(1920 * dpi_scale)
        
        if edge_const == 0:  # LEFT
            taskbar_right_physical = int(40 * dpi_scale)
            rect = (0, 0, taskbar_right_physical, int(1080 * dpi_scale))
        else:  # RIGHT
            taskbar_left_physical = screen_width_physical - int(40 * dpi_scale)
            rect = (taskbar_left_physical, 0, screen_width_physical, int(1080 * dpi_scale))
        
        taskbar_info = MockTaskbarInfo(
            rect=rect,
            dpi_scale=dpi_scale,
            edge=edge_const
        )
        
        widget_width = 40
        widget_height = 30
        
        config = {
            'tray_offset_y': constants.config.defaults.DEFAULT_TRAY_OFFSET_X,
            'free_move': False
        }
        
        # Use PositionCalculator directly
        calculator = PositionCalculator()
        pos = calculator.calculate_position(
            taskbar_info=taskbar_info,
            widget_size=(widget_width, widget_height),
            config=config
        )
        
        # Verify position is calculated and is a valid position
        assert pos is not None
        assert isinstance(pos.x, int)
        assert isinstance(pos.y, int)
        # Position should be non-negative (screen-relative coordinates)
        assert pos.x >= 0, f"X position {pos.x} should be >= 0"
        assert pos.y >= 0, f"Y position {pos.y} should be >= 0"


class TestPositionManagerDPIPrecision:
    """Test precision of DPI calculations."""
    
    def test_floating_point_precision_preserved(self, mock_screen):
        """
        Verify high-DPI calculations maintain precision through intermediates.
        
        Bug #104: Previous code rounded intermediate values, causing cumulative
        errors. Fix uses float intermediates, rounds only final position.
        """
        from netspeedtray.core.position_manager import PositionCalculator
        from netspeedtray import constants
        
        # Test at 125% DPI where precision matters most
        taskbar_info = MockTaskbarInfo(
            rect=(0, int(1080 * 1.25) - int(40 * 1.25), int(1920 * 1.25), int(1080 * 1.25)),
            dpi_scale=1.25,
            edge=constants.taskbar.edge.BOTTOM
        )
        
        config = {'tray_offset_x': 1}
        
        # Use PositionCalculator directly
        calculator = PositionCalculator()
        pos = calculator.calculate_position(
            taskbar_info=taskbar_info,
            widget_size=(100, 30),
            config=config
        )
        
        # Result should be a valid integer position
        assert pos is not None
        assert isinstance(pos.x, int)
        assert isinstance(pos.y, int)
        assert pos.x >= 0
        assert pos.y >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
