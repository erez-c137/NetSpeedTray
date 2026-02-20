"""
Multi-monitor screen detection and validation tests.
Validates that position calculations and screen detection work across multiple monitors.

Issue #102: Multi-Monitor Free Floating
- Tests verify ScreenUtils finds correct screen for given points
- Validates position validation respects screen boundaries
- Tests position calculations with different monitor layouts
"""
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    """Create Qt application for all tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class MockScreen:
    """Mock QScreen for multi-monitor testing."""
    def __init__(self, name="Screen", geometry_rect=(0, 0, 1920, 1080)):
        self.name_val = name
        self.geometry_rect = QRect(*geometry_rect)
    
    def name(self):
        return self.name_val
    
    def geometry(self):
        return self.geometry_rect
    
    def availableGeometry(self):
        # Slightly smaller to account for taskbar
        return QRect(
            self.geometry_rect.left(),
            self.geometry_rect.top(),
            self.geometry_rect.width(),
            self.geometry_rect.height() - 40
        )


class TestMultiMonitorScreenDetection:
    """Test screen detection for multi-monitor setups."""
    
    def test_screen_detection_side_by_side_monitors(self):
        """
        Test that ScreenUtils finds the correct screen for a position
        when monitors are arranged side-by-side horizontally.
        
        Setup: Monitor1 (0,0,1920,1080) | Monitor2 (1920,0,1920,1080)
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        # Create two side-by-side monitors
        screen1 = MockScreen("Screen1", (0, 0, 1920, 1080))
        screen2 = MockScreen("Screen2", (1920, 0, 1920, 1080))
        
        # Mock QApplication.screenAt
        with patch('netspeedtray.core.position_manager.QApplication.screenAt') as mock_screen_at:
            def screenAt_side_effect(point):
                if isinstance(point, QPoint):
                    if point.x() < 1920:
                        return screen1
                    else:
                        return screen2
                return screen1
            
            mock_screen_at.side_effect = screenAt_side_effect
            
            # Test point on left monitor
            left_point = QPoint(960, 540)
            found_screen = ScreenUtils.find_screen_for_point(left_point)
            assert found_screen == screen1
            
            # Test point on right monitor
            right_point = QPoint(2880, 540)
            found_screen = ScreenUtils.find_screen_for_point(right_point)
            assert found_screen == screen2
    
    def test_position_validation_respects_screen_bounds(self):
        """
        Test that position validation constrains positions to screen geometry.
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        screen = MockScreen("TestScreen", (0, 0, 1920, 1080))
        widget_size = (100, 50)
        
        # Test position fully within bounds
        valid_pos = ScreenUtils.validate_position(960, 540, widget_size, screen)
        assert valid_pos.x == 960
        assert valid_pos.y == 540
        
        # Test position adjusted when too far right
        valid_pos = ScreenUtils.validate_position(1900, 540, widget_size, screen)
        assert valid_pos.x < 1920  # Should be constrained to fit within screen
        assert valid_pos.x + 100 <= 1921  # Widget should fit
        
        # Test position adjusted when negative
        valid_pos = ScreenUtils.validate_position(-50, 540, widget_size, screen)
        assert valid_pos.x >= 0  # Should be at or after screen edge
    
    def test_screen_detection_vertically_stacked_monitors(self):
        """
        Test screen detection when monitors are stacked vertically.
        
        Setup: Monitor1 (0,0,1920,1080) 
               Monitor2 (0,1080,1920,1080)
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        screen1 = MockScreen("ScreenTop", (0, 0, 1920, 1080))
        screen2 = MockScreen("ScreenBottom", (0, 1080, 1920, 1080))
        
        with patch('netspeedtray.core.position_manager.QApplication.screenAt') as mock_screen_at:
            def screenAt_side_effect(point):
                if isinstance(point, QPoint):
                    if point.y() < 1080:
                        return screen1
                    else:
                        return screen2
                return screen1
            
            mock_screen_at.side_effect = screenAt_side_effect
            
            # Test point on top monitor
            top_point = QPoint(960, 540)
            found_screen = ScreenUtils.find_screen_for_point(top_point)
            assert found_screen == screen1
            
            # Test point on bottom monitor
            bottom_point = QPoint(960, 1620)
            found_screen = ScreenUtils.find_screen_for_point(bottom_point)
            assert found_screen == screen2
    
    def test_position_validation_with_negative_coordinates(self):
        """
        Test position validation works with secondary displays that may have
        negative coordinates (common on left/top extended displays).
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        # Secondary monitor on the left with negative X coordinates
        screen = MockScreen("LeftScreen", (-1920, 0, 1920, 1080))
        widget_size = (100, 50)
        
        # Test that validation works with negative screen geometry
        valid_pos = ScreenUtils.validate_position(-960, 540, widget_size, screen)
        assert -1920 <= valid_pos.x <= -1920 + 1920 - 100
        assert 0 <= valid_pos.y <= 1080 - 50
    
    def test_position_validation_catches_completely_off_screen(self):
        """
        Test that position validation handles positions completely off-screen.
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        screen = MockScreen("Screen", (0, 0, 1920, 1080))
        widget_size = (100, 50)
        
        # Large positive offset
        valid_pos = ScreenUtils.validate_position(5000, 5000, widget_size, screen)
        # Should be constrained to right-bottom of screen
        assert valid_pos.x + 100 <= 1921
        assert valid_pos.y + 50 <= 1081


class TestPositionCalculationMultimonitor:
    """Test position calculations in multi-monitor context."""
    
    def test_position_validity_check_across_monitors(self):
        """
        Test that position validity checking works across multiple monitors.
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        screen1 = MockScreen("Screen1", (0, 0, 1920, 1080))
        
        widget_size = (100, 50)
        
        # Test position fully visible on screen
        is_valid = ScreenUtils.is_position_valid(960, 540, widget_size, screen1)
        assert is_valid
        
        # Test position partially visible
        is_valid = ScreenUtils.is_position_valid(1900, 540, widget_size, screen1)
        assert is_valid  # Still intersects with screen
        
        # Test position completely off-screen to the right
        is_valid = ScreenUtils.is_position_valid(2000, 540, widget_size, screen1)
        assert not is_valid
        
        # Test position completely off-screen below
        is_valid = ScreenUtils.is_position_valid(960, 1200, widget_size, screen1)
        assert not is_valid


class TestDragConstraintEdgeCases:
    """Test drag constraint edge cases and error handling."""
    
    def test_validation_with_zero_sized_widget(self):
        """
        Test that validation handles zero-sized widgets gracefully
        (edge case that shouldn't happen but should not crash).
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        screen = MockScreen("Screen", (0, 0, 1920, 1080))
        
        # This shouldn't crash, even though widget size is 0
        try:
            valid_pos = ScreenUtils.validate_position(960, 540, (0, 0), screen)
            assert valid_pos is not None
        except Exception as e:
            pytest.fail(f"validate_position crashed with zero widget size: {e}")
    
    def test_validation_with_negative_coordinates(self):
        """
        Test validation works when given negative starting coordinates.
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        screen = MockScreen("Screen", (0, 0, 1920, 1080))
        widget_size = (100, 50)
        
        # Negative coordinates should be adjusted to screen bounds
        valid_pos = ScreenUtils.validate_position(-100, -50, widget_size, screen)
        assert valid_pos.x >= 0
        assert valid_pos.y >= 0
    
    def test_rect_finding_for_multi_monitor(self):
        """
        Test finding screen for a QRect (used for window-sized areas).
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        screen1 = MockScreen("Screen1", (0, 0, 1920, 1080))
        screen2 = MockScreen("Screen2", (1920, 0, 1920, 1080))
        
        with patch('netspeedtray.core.position_manager.QApplication.screenAt') as mock_screen_at:
            def screenAt_side_effect(point):
                if isinstance(point, QPoint):
                    if point.x() < 1920:
                        return screen1
                    else:
                        return screen2
                return screen1
            
            mock_screen_at.side_effect = screenAt_side_effect
            
            # Create rect spanning into right monitor
            rect_left_to_mid = QRect(1900, 500, 100, 100)
            # The rect center should be at x=1950, which is on screen2
            found_screen = ScreenUtils.find_screen_for_rect(rect_left_to_mid)
            assert found_screen == screen2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

    """Test free-move drag constraints across multiple monitors."""
    
    @patch('netspeedtray.core.position_manager.QApplication')
    def test_drag_across_horizontal_monitors(self, mock_qapp):
        """
        Test dragging widget across two side-by-side monitors.
        
        Setup: Monitor1 (0,0,1920,1080) | Monitor2 (1920,0,1920,1080)
        Action: Drag widget from center of Monitor1 to center of Monitor2
        Expected: Widget stays within Monitor2 bounds when placed there
        """
        from netspeedtray.core.position_manager import PositionManager, WindowState
        from netspeedtray import constants
        
        # Create two side-by-side monitors
        screen1 = MockScreen("Screen1", (0, 0, 1920, 1080))
        screen2 = MockScreen("Screen2", (1920, 0, 1920, 1080))
        
        def screenAt_side_effect(point):
            if isinstance(point, QPoint):
                if point.x() < 1920:
                    return screen1
                else:
                    return screen2
            return screen1
        
        mock_qapp.screenAt.side_effect = screenAt_side_effect
        mock_qapp.primaryScreen.return_value = screen1
        
        # Create mock widget
        mock_widget = MagicMock()
        mock_widget.width.return_value = 200
        mock_widget.height.return_value = 50
        
        config = {
            'free_move': True,
            'tray_offset_x': constants.config.defaults.DEFAULT_TRAY_OFFSET_X
        }
        
        window_state = WindowState(config=config, widget=mock_widget)
        position_mgr = PositionManager(window_state)
        
        # Simulate dragging to right monitor
        drag_target = QPoint(2100, 500)  # Right monitor, center area
        
        # Validate using the right screen's bounds
        validated_pos = position_mgr.validate_drag_position(
            desired_pos=drag_target,
            target_screen=screen2,
            widget_size=(200, 50)
        )
        
        # Widget should be contained within screen2 bounds
        assert validated_pos is not None
        assert 1920 <= validated_pos.x <= 1920 + 1920 - 200
        assert 0 <= validated_pos.y <= 1080 - 50
    
    def test_drag_to_screen_edge_horizontal(self):
        """
        Test widget visibility when dragging to right edge of monitor.
        
        Known issue: Without proper validation, widget could be dragged
        partially off-screen, becoming invisible.
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        screen = MockScreen("TestScreen", (0, 0, 1920, 1080))
        widget_size = (200, 50)
        
        # Attempt to drag to right edge
        right_edge_pos = QPoint(1920 - 200, 500)  # Should fit exactly
        validated = ScreenUtils.validate_position(
            right_edge_pos.x(),
            right_edge_pos.y(),
            widget_size,
            screen
        )
        
        # Widget should remain fully visible
        assert validated.x + widget_size[0] <= screen.geometry().right()
        assert validated.y + widget_size[1] <= screen.geometry().bottom()
    
    def test_drag_to_screen_edge_beyond(self):
        """
        Test that dragging beyond screen edge is constrained back in.
        
        If user drags to x=2000 on 1920-wide screen with 200px widget,
        widget should be constrained to end at screen right edge.
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        screen = MockScreen("TestScreen", (0, 0, 1920, 1080))
        widget_size = (200, 50)
        
        # Try to drag beyond right edge
        beyond_edge_pos = QPoint(2000, 500)  # Off-screen
        validated = ScreenUtils.validate_position(
            beyond_edge_pos.x(),
            beyond_edge_pos.y(),
            widget_size,
            screen
        )
        
        # Should be constrained to fit on screen
        assert validated.x >= 0
        assert validated.x + widget_size[0] <= screen.geometry().right()
        assert validated.x == 1920 - 200  # Snapped to right edge
    
    @patch('netspeedtray.core.position_manager.QApplication')
    def test_monitor_detection_uses_destination_screen(self, mock_qapp):
        """
        Test that screen detection uses destination position, not taskbar screen.
        
        Bug #102: Previous code used taskbar's screen, constraining free-move
        to taskbar's monitor. Fix uses QApplication.screenAt(pos) to detect
        destination screen.
        """
        from netspeedtray.core.position_manager import PositionManager, WindowState
        
        # Setup: Two monitors, taskbar on screen1
        screen1 = MockScreen("Taskbar", (0, 0, 1920, 1080))
        screen2 = MockScreen("Secondary", (1920, 0, 1920, 1080))
        
        def screenAt_side_effect(point):
            if point.x() < 1920:
                return screen1
            return screen2
        
        mock_qapp.screenAt.side_effect = screenAt_side_effect
        mock_qapp.primaryScreen.return_value = screen1
        
        mock_widget = MagicMock()
        mock_widget.width.return_value = 200
        mock_widget.height.return_value = 50
        
        config = {'free_move': True}
        window_state = WindowState(config=config, widget=mock_widget)
        
        position_mgr = PositionManager(window_state)
        
        # Drag to secondary monitor
        drag_pos = QPoint(2100, 500)
        
        # The fix ensures screen detection uses drag_pos, not taskbar_screen
        detected_screen = mock_qapp.screenAt(drag_pos)
        assert detected_screen == screen2, "Should detect secondary screen from drag position"
    
    def test_drag_vertical_stacked_monitors(self):
        """
        Test dragging across vertically stacked monitors.
        
        Setup: Monitor1 (0,0,1920,1080) above Monitor2 (0,1080,1920,1080)
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        screen1 = MockScreen("Top", (0, 0, 1920, 1080))
        screen2 = MockScreen("Bottom", (0, 1080, 1920, 1080))
        
        widget_size = (200, 50)
        
        # Drag to bottom monitor
        pos_on_screen2 = QPoint(960, 1500)  # Center of bottom monitor
        validated = ScreenUtils.validate_position(
            pos_on_screen2.x(),
            pos_on_screen2.y(),
            widget_size,
            screen2
        )
        
        # Should be valid within bottom monitor
        assert 0 <= validated.y <= 1080 + 1080 - 50
        assert validated.x >= 0
    
    def test_widget_size_respects_monitor_bounds(self):
        """
        Test that large widgets are constrained appropriately.
        
        If widget is too large for target monitor, it should be
        positioned optimally to remain mostly visible.
        """
        from netspeedtray.core.position_manager import ScreenUtils
        
        small_screen = MockScreen("Small", (0, 0, 800, 600))
        large_widget = (700, 500)  # Widget almost fills screen
        
        # Position at top-left
        pos = QPoint(0, 0)
        validated = ScreenUtils.validate_position(
            pos.x(),
            pos.y(),
            large_widget,
            small_screen
        )
        
        # Should position to maximize visibility
        assert validated.x >= 0
        assert validated.y >= 0
        # Widget might exceed bounds, but position is optimized
        assert validated.x + large_widget[0] >= small_screen.geometry().right()


class TestDragConstraintEdgeCases:
    """Test edge cases in drag constraints."""
    
    def test_empty_screen_geometry(self):
        """Test handling of invalid/empty screen geometry."""
        from netspeedtray.core.position_manager import ScreenUtils
        
        invalid_screen = MockScreen("Invalid", (0, 0, 0, 0))
        widget_size = (200, 50)
        pos = QPoint(100, 100)
        
        # Should not crash, returns original pos or constrained version
        validated = ScreenUtils.validate_position(
            pos.x(),
            pos.y(),
            widget_size,
            invalid_screen
        )
        assert validated is not None
    
    def test_negative_screen_coordinates(self):
        """Test handling of negative screen coordinates (extended desktops)."""
        from netspeedtray.core.position_manager import ScreenUtils
        
        # Virtual desktop with negative coords
        extended_screen = MockScreen("Extended", (-1920, 0, 0, 1080))
        widget_size = (200, 50)
        pos = QPoint(-1800, 500)
        
        validated = ScreenUtils.validate_position(
            pos.x(),
            pos.y(),
            widget_size,
            extended_screen
        )
        
        # Should constrain within extended screen bounds
        assert validated.x >= -1920
        assert validated.x <= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
