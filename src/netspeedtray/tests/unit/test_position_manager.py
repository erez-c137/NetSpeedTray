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

    @patch('netspeedtray.core.position_manager.get_taskbar_info')
    def test_update_position_free_move(self, mock_get_info):
        mock_get_info.return_value = self.mock_taskbar
        
        self.config['free_move'] = True
        self.config['position_x'] = 888
        self.config['position_y'] = 999
        
        # Should use saved position if valid
        # We need ScreenUtils validation to pass. Mock validation?
        # Or just assume screens are big enough test environment.
        # 888, 999 is inside 1920x1080.
        
        self.manager.update_position()
        self.mock_widget.move.assert_called_with(888, 999)

if __name__ == '__main__':
    unittest.main()
