"""
Unit tests for InputHandler.
"""
import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray.core.input_handler import InputHandler

class TestInputHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication([])
        else:
            cls.app = QApplication.instance()

    def setUp(self):
        # Use a real QWidget so QObject.__init__ works
        self.real_widget = QWidget()
        
        # Monkey-patch attributes and methods needed by InputHandler
        self.real_widget.move = MagicMock()
        self.real_widget.update_config = MagicMock()
        self.real_widget.open_graph_window = MagicMock()
        self.real_widget._dragging = False
        
        # For pos(), we can't easily mock the method of a C++ object unless we subclass.
        # But InputHandler calls self.widget.pos().
        # We can just rely on the real pos() which will be (0,0) by default.
        # Or move the widget to proper place first.
        self.real_widget.move(100, 100) # This calls the mock, not the real one!
        # Ah, we mocked move. But we haven't mocked pos.
        # So we can't set pos easily if we mocked move? 
        # Actually QWidget.move() sets position. If we replace it with Mock, it won't set position.
        
        # Better strategy: Subclass or don't mock 'move' yet?
        # InputHandler calls `self.widget.move(final_pos)`. We want to verify that call.
        # InputHandler calls `self.widget.pos()` in handle_mouse_press to calc offset.
        
        # Let's mock `pos` by assigning a lambda if possible? No, it's a slot.
        # We can just return a fixed value via specific attribute if InputHandler used a property, but it uses .pos().
        
        # WORKAROUND: Create a helper class.
        class MockWidget(QWidget):
            def __init__(self):
                super().__init__()
                self._dragging = False
                self.config = {}
                
            def move(self, *args):
                pass # Stub
                
            def update_config(self, *args):
                pass 
                
            def open_graph_window(self):
                pass

        self.mock_widget = MockWidget()
        self.mock_widget.move = MagicMock()
        self.mock_widget.update_config = MagicMock()
        self.mock_widget.open_graph_window = MagicMock()
        # Set geometry so pos() returns something
        self.mock_widget.setGeometry(100, 100, 200, 50)
        
        self.mock_position_manager = MagicMock()
        self.mock_tray_manager = MagicMock()
        
        self.handler = InputHandler(
            widget=self.mock_widget,
            position_manager=self.mock_position_manager,
            tray_manager=self.mock_tray_manager
        )

    def _create_mouse_event(self, button=Qt.MouseButton.LeftButton, global_x=0, global_y=0, type=QEvent.Type.MouseButtonPress):
        event = MagicMock(spec=QMouseEvent)
        event.button.return_value = button
        event.buttons.return_value = button # For mouseMove check
        event.globalPosition.return_value = QPointF(float(global_x), float(global_y))
        event.type.return_value = type
        return event

    def test_mouse_press_left_starts_drag_prep(self):
        """Test Left Click prepares for drag."""
        event = self._create_mouse_event(
            button=Qt.MouseButton.LeftButton, 
            global_x=150, global_y=150
        )
        
        self.handler.handle_mouse_press(event)
        
        # Start Global = 150, 150. Widget Pos = 100, 100 (from setGeometry).
        # Offset should be 50, 50.
        self.assertEqual(self.handler._drag_start_pos, QPoint(50, 50))
        self.assertFalse(self.handler._is_dragging)
        event.accept.assert_called_once()

    def test_mouse_press_right_ignores_event(self):
        """Test Right Click is ignored by InputHandler (handled by contextMenuEvent)."""
        event = self._create_mouse_event(button=Qt.MouseButton.RightButton)
        
        self.handler.handle_mouse_press(event)
        
        self.mock_tray_manager.show_context_menu.assert_not_called()
        event.accept.assert_not_called()

    def test_mouse_move_drags_widget(self):
        """Test dragging updates position via constraint."""
        self.handler._drag_start_pos = QPoint(50, 50)
        self.mock_position_manager.constrain_drag.side_effect = lambda p: p 
        
        event = self._create_mouse_event(
            button=Qt.MouseButton.LeftButton,
            global_x=200, global_y=200
        )
        
        self.handler.handle_mouse_move(event)
        
        # Global(200,200) - Offset(50,50) = (150,150)
        args, _ = self.mock_position_manager.constrain_drag.call_args
        self.assertEqual(args[0], QPoint(150, 150))
        
        self.mock_widget.move.assert_called_with(QPoint(150, 150))
        self.assertTrue(self.handler._is_dragging)
        self.assertEqual(self.mock_widget._dragging, True)

    def test_mouse_release_ends_drag(self):
        """Test release ends drag and saves config."""
        self.handler._is_dragging = True
        self.mock_widget._dragging = True
        
        event = self._create_mouse_event(button=Qt.MouseButton.LeftButton)
        
        self.handler.handle_mouse_release(event)
        
        self.assertFalse(self.handler._is_dragging)
        self.assertFalse(self.mock_widget._dragging)
        self.mock_widget.update_config.assert_called()
        
        args, _ = self.mock_widget.update_config.call_args
        updates = args[0]
        # Position is saved via tray_offset_x/y or position_x/y depending on mode/OS.
        # But 'free_move' is NOT part of the 'updates' dict sent to update_config.
        self.assertIn('position_x', updates) if self.mock_widget.config.get('free_move') else self.assertTrue(any(k in updates for k in ['tray_offset_x', 'tray_offset_y']))
        
        # Since we didn't mock x() and y(), only move(), it returns the design values (100, 100)
        self.assertEqual(updates.get('position_x', 100), 100)

    def test_double_click_opens_graph(self):
        """Test Double Click calls open_graph_window."""
        event = self._create_mouse_event(button=Qt.MouseButton.LeftButton)
        
        self.handler.handle_double_click(event)
        
        self.mock_widget.open_graph_window.assert_called_once()

if __name__ == '__main__':
    unittest.main()
