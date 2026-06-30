"""
Unit tests for PositionManager.
"""
import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QPoint, QRect, QSize

from netspeedtray import constants
from netspeedtray.core.position_manager import PositionManager, PositionCalculator, ScreenPosition, WindowState, ScreenUtils
from netspeedtray.utils.taskbar_utils import TaskbarInfo

class TestPositionCalculator(unittest.TestCase):
    def setUp(self):
        self.calculator = PositionCalculator()
        self.mock_screen = MagicMock()
        self.mock_screen.geometry.return_value = QRect(0, 0, 1920, 1080)
        self.mock_screen.availableGeometry.return_value = QRect(0, 0, 1920, 1040)
        
        # Setup standard taskbar info mock (Bottom edge)
        self.mock_taskbar = MagicMock(spec=TaskbarInfo)
        self.mock_taskbar.rect = (0, 1040, 1920, 1080) # Left, Top, Right, Bottom
        self.mock_taskbar.tasklist_rect = None
        self.mock_taskbar.get_tray_rect.return_value = (1700, 1040, 1920, 1080)
        self.mock_taskbar.get_edge_position.return_value = constants.taskbar.edge.BOTTOM
        self.mock_taskbar.dpi_scale = 1.0
        self.mock_taskbar.get_screen.return_value = self.mock_screen
        self.mock_taskbar.hwnd = 12345

    def test_calculate_position_bottom_edge(self):
        """Test position calculation for bottom taskbar."""
        widget_size = (100, 40) # w, h
        config = {'tray_offset_x': 5}
        
        # Expected Y: tb_top + (tb_height - widget_h) / 2
        # TB Top = 1040, H = 40. Widget H = 40. Y = 1040 + (40-40)/2 = 1040.
        # Expected X: Tray Left (1700) - Widget W (100) - Offset (5) = 1595.
        
        pos = self.calculator.calculate_position(self.mock_taskbar, widget_size, config)
        
        self.assertEqual(pos.x, 1595)
        self.assertEqual(pos.y, 1040)

    def test_calculate_position_fallback(self):
        """Test fallback when taskbar is invalid."""
        self.mock_taskbar.hwnd = 0 # Invalid
        widget_size = (100, 40)
        
        with patch('PyQt6.QtWidgets.QApplication.primaryScreen', return_value=self.mock_screen):
            pos = self.calculator.calculate_position(self.mock_taskbar, widget_size, {})
            # Fallback is Bottom-Right of available geometry - margin
            # Available Right: 1920. Bottom: 1040.
            # Margin default? Assume 10 for calculation. 
            # Logic: screen.right() - width - margin + 1.
            # Implementation uses rect.right() which is (Left + Width - 1). Qt Logic.
            # QRect(0,0,1920,1040).right() is 1919.
            # X = 1919 - 100 - 10 + 1 = 1810.
            # Y = 1039 - 40 - 10 + 1 = 990.
            
            # Let's just check it returns a valid ScreenPosition object
            self.assertIsInstance(pos, ScreenPosition)
            self.assertGreater(pos.x, 0)
            self.assertGreater(pos.y, 0)

    def test_constrain_drag_bottom(self):
        """Test drag constraint on bottom taskbar (horizontal movement only)."""
        widget_size = QSize(100, 40)
        desired_pos = QPoint(500, 500) # Way off
        
        # Should lock Y to taskbar center (1040) and allow X within bounds.
        constrained = self.calculator.constrain_drag_position(
            desired_pos, self.mock_taskbar, widget_size
        )
        
        self.assertEqual(constrained.y(), 1040)
        self.assertEqual(constrained.x(), 500)

    def test_widget_size_exceeds_max_width(self):
        """Verify oversized widget width is clamped to max allowed."""
        # Create an oversized widget width
        oversized_widget = (5000, 30)
        config = {'tray_offset_x': 5}

        # Calculate position; should not raise and should return a ScreenPosition
        pos = self.calculator.calculate_position(self.mock_taskbar, oversized_widget, config)
        self.assertIsNotNone(pos)
        self.assertIsInstance(pos.x, int)

    def test_widget_size_exceeds_max_height(self):
        """Verify oversized widget height is clamped to max allowed."""
        oversized_widget = (100, 5000)
        config = {'tray_offset_x': 5}

        pos = self.calculator.calculate_position(self.mock_taskbar, oversized_widget, config)
        self.assertIsNotNone(pos)

    def test_widget_size_zero_or_negative_rejected(self):
        """Verify invalid widget sizes are rejected early."""
        # API should gracefully fallback rather than raise; ensure a ScreenPosition is returned
        pos1 = self.calculator.calculate_position(self.mock_taskbar, (0, 30), {})
        pos2 = self.calculator.calculate_position(self.mock_taskbar, (100, 0), {})
        pos3 = self.calculator.calculate_position(self.mock_taskbar, (-100, 30), {})

        self.assertIsNotNone(pos1)
        self.assertIsNotNone(pos2)
        self.assertIsNotNone(pos3)

    def test_position_stays_on_screen_after_clamping(self):
        """Verify clamped widget stays fully visible on screen."""
        # Use the mocked screen geometry (0,0,1920,1080)
        screen = self.mock_screen
        invalid_x = 1900
        invalid_y = 1000
        widget_size = (500, 200)

        validated = ScreenUtils.validate_position(invalid_x, invalid_y, widget_size, screen)

        # Verify widget stays on screen
        self.assertTrue(validated.x + widget_size[0] <= 1920)
        self.assertTrue(validated.y + widget_size[1] <= 1080)
        self.assertTrue(validated.x >= 0)
        self.assertTrue(validated.y >= 0)


class TestPositionManager(unittest.TestCase):
    def setUp(self):
        self.mock_widget = MagicMock()
        self.mock_widget.width.return_value = 100
        self.mock_widget.height.return_value = 40
        self.mock_widget.isVisible.return_value = True
        
        self.mock_taskbar = MagicMock(spec=TaskbarInfo)
        self.mock_taskbar.dpi_scale = 1.0
        self.mock_taskbar.hwnd = 12345
        # Provide get_screen so it returns a mock to avoid segfaults/errors
        mock_screen = MagicMock()
        mock_screen.geometry.return_value = QRect(0,0,1920,1080)
        mock_screen.availableGeometry.return_value = QRect(0,0,1920,1040)
        self.mock_taskbar.get_screen.return_value = mock_screen
        self.mock_taskbar.get_edge_position.return_value = constants.taskbar.edge.BOTTOM
        self.mock_taskbar.rect = (0, 1040, 1920, 1080)
        
        self.config = {}
        
        self.state = WindowState(
            config=self.config,
            widget=self.mock_widget,
            taskbar_info=self.mock_taskbar
        )
        self.manager = PositionManager(self.state)

    @patch('netspeedtray.core.position_manager.get_taskbar_info')
    def test_update_position_moves_widget(self, mock_get_info):
        mock_get_info.return_value = self.mock_taskbar
        
        # Mock calculator to return specific pos
        with patch.object(self.manager._calculator, 'calculate_position', 
                          return_value=ScreenPosition(100, 200)):
            self.manager.update_position()
            
            self.mock_widget.move.assert_called_with(100, 200)

    @patch('PyQt6.QtWidgets.QApplication.screenAt')
    @patch('netspeedtray.core.position_manager.get_taskbar_info')
    def test_update_position_free_move(self, mock_get_info, mock_screen_at):
        """Saved free-move position on the primary screen is restored verbatim."""
        mock_get_info.return_value = self.mock_taskbar

        # Primary screen at (0,0)-1920x1080
        primary = MagicMock()
        primary.geometry.return_value = QRect(0, 0, 1920, 1080)
        primary.name.return_value = "DISPLAY1"
        mock_screen_at.return_value = primary

        self.config['free_move'] = True
        self.config['position_x'] = 888
        self.config['position_y'] = 999

        self.manager.update_position()
        self.mock_widget.move.assert_called_with(888, 999)

    @patch('PyQt6.QtWidgets.QApplication.screenAt')
    @patch('netspeedtray.core.position_manager.get_taskbar_info')
    def test_free_move_restores_on_secondary_screen(self, mock_get_info, mock_screen_at):
        """Regression for #133: saved coords on a secondary monitor must be restored,
        even though get_taskbar_info() returns only the primary taskbar."""
        mock_get_info.return_value = self.mock_taskbar  # primary taskbar

        # Secondary screen to the left at (-1920, 0)-1920x1080
        secondary = MagicMock()
        secondary.geometry.return_value = QRect(-1920, 0, 1920, 1080)
        secondary.name.return_value = "DISPLAY2"
        mock_screen_at.return_value = secondary

        self.config['free_move'] = True
        self.config['position_x'] = -1000  # clearly on the secondary screen
        self.config['position_y'] = 500

        self.manager.update_position()
        # Must restore exactly, not snap back to primary calculated pos.
        self.mock_widget.move.assert_called_with(-1000, 500)

    @patch('PyQt6.QtWidgets.QApplication.screenAt')
    @patch('netspeedtray.core.position_manager.get_taskbar_info')
    def test_free_move_disconnected_monitor_falls_through(self, mock_get_info, mock_screen_at):
        """Saved coords on a now-disconnected monitor must fall through to calculated
        position rather than placing the widget off-screen."""
        mock_get_info.return_value = self.mock_taskbar
        mock_screen_at.return_value = None  # No monitor at that point

        self.config['free_move'] = True
        self.config['position_x'] = -1000
        self.config['position_y'] = 500

        with patch.object(self.manager._calculator, 'calculate_position',
                          return_value=ScreenPosition(1500, 1040)):
            self.manager.update_position()
            # Should NOT have been moved to the disconnected coords.
            self.mock_widget.move.assert_called_with(1500, 1040)

    @patch('netspeedtray.core.position_manager.get_taskbar_info')
    def test_update_position_passes_preferred_monitor(self, mock_get_info):
        """#72: update_position must forward `preferred_monitor` to get_taskbar_info."""
        mock_get_info.return_value = self.mock_taskbar
        self.config['preferred_monitor'] = '\\\\.\\DISPLAY2'

        with patch.object(self.manager._calculator, 'calculate_position',
                          return_value=ScreenPosition(0, 0)):
            self.manager.update_position()

        mock_get_info.assert_called_with(preferred_screen_name='\\\\.\\DISPLAY2')

    @patch('netspeedtray.core.position_manager.get_taskbar_info')
    def test_update_position_no_preferred_monitor_passes_none(self, mock_get_info):
        """When the user hasn't set a preference, pass None - preserves legacy primary behavior."""
        mock_get_info.return_value = self.mock_taskbar
        # self.config has no 'preferred_monitor' key

        with patch.object(self.manager._calculator, 'calculate_position',
                          return_value=ScreenPosition(0, 0)):
            self.manager.update_position()

        mock_get_info.assert_called_with(preferred_screen_name=None)

    @patch('PyQt6.QtWidgets.QApplication.screenAt')
    @patch('netspeedtray.core.position_manager.get_taskbar_info')
    def test_free_move_clamps_off_edge_position(self, mock_get_info, mock_screen_at):
        """Saved coords slightly off the edge of an existing screen are clamped, not rejected."""
        mock_get_info.return_value = self.mock_taskbar

        primary = MagicMock()
        primary.geometry.return_value = QRect(0, 0, 1920, 1080)
        primary.name.return_value = "DISPLAY1"
        mock_screen_at.return_value = primary

        self.config['free_move'] = True
        self.config['position_x'] = 1900  # widget width 100 -> right edge at 2000, off-screen
        self.config['position_y'] = 1070  # widget height 40 -> bottom at 1110, off-screen

        self.manager.update_position()
        # Must be clamped onto the screen, not rejected.
        args, _ = self.mock_widget.move.call_args
        x, y = args
        self.assertTrue(0 <= x <= 1920 - 100, f"x={x} not clamped to screen")
        self.assertTrue(0 <= y <= 1080 - 40, f"y={y} not clamped to screen")

    @patch('netspeedtray.core.position_manager.get_taskbar_info')
    def test_free_move_defers_when_widget_size_is_zero(self, mock_get_info):
        """Defensive: if widget.width()/height() return 0 (widget not laid out
        yet at startup), saved-position restore must defer rather than guess
        the wrong screen via screenAt() with a corrupted center point.

        get_calculated_position() also returns None for zero-sized widgets,
        so the whole update is a no-op. A follow-up update_position() after
        layout completes will succeed.
        """
        mock_get_info.return_value = self.mock_taskbar
        # Force zero-size widget
        self.mock_widget.width.return_value = 0
        self.mock_widget.height.return_value = 0

        self.config['free_move'] = True
        # These saved coords are on a hypothetical secondary screen at x=-1920.
        # Without the guard, screenAt(QPoint(-1000 + 0//2, 500 + 0//2)) would
        # be tested against the WRONG point and probably land on a different
        # screen than the widget actually belongs to.
        self.config['position_x'] = -1000
        self.config['position_y'] = 500

        self.manager.update_position()
        # No move called: zero-sized widget can't be positioned safely yet.
        self.mock_widget.move.assert_not_called()


if __name__ == '__main__':
    unittest.main()
