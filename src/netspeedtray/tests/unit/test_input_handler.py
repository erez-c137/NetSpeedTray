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
        self.real_widget.open_monitor_window = MagicMock()
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
                self.is_paused = False

            def move(self, *args):
                pass # Stub

            def update_config(self, *args):
                pass

            def open_monitor_window(self):
                pass

            def show_settings(self):
                pass

            def pause(self):
                pass

            def resume(self):
                pass

        self.mock_widget = MockWidget()
        self.mock_widget.move = MagicMock()
        self.mock_widget.update_config = MagicMock()
        self.mock_widget.open_monitor_window = MagicMock()
        self.mock_widget.show_settings = MagicMock()
        self.mock_widget.pause = MagicMock()
        self.mock_widget.resume = MagicMock()
        # Set geometry so pos() returns something
        self.mock_widget.setGeometry(100, 100, 200, 50)
        
        self.mock_position_manager = MagicMock()
        # _save_dragged_position asks the position manager whether we're "floating" (free-move OR the
        # #188 free-float); mirror the widget's free_move config so the docked/offset path is exercised.
        self.mock_position_manager.is_floating.side_effect = lambda: bool(self.mock_widget.config.get('free_move', False))
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
        from PyQt6.QtGui import QInputDevice
        event.pointingDevice.return_value.type.return_value = QInputDevice.DeviceType.Mouse
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

    def test_drag_start_dismisses_the_hover_card(self):
        """A hover card up when the drag begins is hidden (and a pending show cancelled) - once, at
        the start, not on every move."""
        self.mock_widget._hide_usage_hover_card = MagicMock()
        self.mock_widget._hover_card_timer = MagicMock()
        self.handler._drag_start_pos = QPoint(50, 50)
        self.mock_position_manager.constrain_drag.side_effect = lambda p: p
        for x in (200, 210, 220):
            self.handler.handle_mouse_move(self._create_mouse_event(global_x=x, global_y=200))
        self.mock_widget._hide_usage_hover_card.assert_called_once()
        self.mock_widget._hover_card_timer.stop.assert_called_once()

    def test_press_without_a_move_keeps_the_hover_card(self):
        self.mock_widget._hide_usage_hover_card = MagicMock()
        self.handler.handle_mouse_press(self._create_mouse_event(global_x=150, global_y=150))
        # Below the drag distance: not a drag.
        self.handler.handle_mouse_move(self._create_mouse_event(global_x=51, global_y=50))
        self.mock_widget._hide_usage_hover_card.assert_not_called()

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

    def test_double_click_opens_monitor(self):
        """Double-click opens the unified Monitor (default action, empty config)."""
        event = self._create_mouse_event(button=Qt.MouseButton.LeftButton)

        self.handler.handle_double_click(event)

        self.mock_widget.open_monitor_window.assert_called_once()

    # --- Configurable click actions (community PR #165, @rami123) ---

    def test_double_click_respects_configured_action(self):
        """A non-default double_click_action is honored (Settings instead of Monitor)."""
        self.mock_widget.config = {"double_click_action": "settings"}
        event = self._create_mouse_event(button=Qt.MouseButton.LeftButton)

        self.handler.handle_double_click(event)

        self.mock_widget.show_settings.assert_called_once()
        self.mock_widget.open_monitor_window.assert_not_called()

    def test_middle_click_runs_configured_action(self):
        """Middle-click dispatches its configured action."""
        self.mock_widget.config = {"middle_click_action": "open_monitor"}
        event = self._create_mouse_event(button=Qt.MouseButton.MiddleButton)

        self.handler.handle_mouse_press(event)

        self.mock_widget.open_monitor_window.assert_called_once()
        event.accept.assert_called_once()

    def test_middle_click_default_is_noop(self):
        """Middle-click defaults to 'Nothing' - nothing fires on an empty config."""
        event = self._create_mouse_event(button=Qt.MouseButton.MiddleButton)

        self.handler.handle_mouse_press(event)

        self.mock_widget.open_monitor_window.assert_not_called()
        self.mock_widget.show_settings.assert_not_called()
        self.mock_widget.pause.assert_not_called()
        event.accept.assert_called_once()

    def test_pause_action_pauses_when_running(self):
        """The pause_resume action calls pause() when the widget is running."""
        self.mock_widget.is_paused = False
        self.handler._execute_click_action("pause_resume")

        self.mock_widget.pause.assert_called_once()
        self.mock_widget.resume.assert_not_called()

    def test_pause_action_resumes_when_paused(self):
        """The pause_resume action calls resume() when the widget is already paused."""
        self.mock_widget.is_paused = True
        self.handler._execute_click_action("pause_resume")

        self.mock_widget.resume.assert_called_once()
        self.mock_widget.pause.assert_not_called()

    def test_none_action_is_noop(self):
        """The 'none' action does nothing and never raises."""
        self.handler._execute_click_action("none")
        self.handler._execute_click_action("")
        self.handler._execute_click_action("bogus_unknown_action")

        self.mock_widget.open_monitor_window.assert_not_called()
        self.mock_widget.show_settings.assert_not_called()
        self.mock_widget.pause.assert_not_called()

    # --- A lost left-button release (relu616, rc.4 on #373) ---

    def _start_drag(self):
        self.mock_position_manager.constrain_drag.side_effect = lambda p: p
        self.handler.handle_mouse_press(self._create_mouse_event(global_x=150, global_y=150))
        self.handler.handle_mouse_move(self._create_mouse_event(global_x=200, global_y=200))
        self.assertTrue(self.mock_widget._dragging)

    def _assert_dropped(self):
        self.assertFalse(self.handler._is_dragging)
        self.assertIs(self.mock_widget._dragging, False)
        self.assertIsNone(self.handler._drag_start_pos)
        self.mock_widget.update_config.assert_called()          # saved like a release
        self.mock_position_manager.note_user_drop.assert_called_once()
        self.assertFalse(self.handler._drag_watchdog.isActive())

    def test_move_with_the_button_up_ends_a_drag_whose_release_was_lost(self):
        self._start_drag()
        self.handler.handle_mouse_move(
            self._create_mouse_event(button=Qt.MouseButton.NoButton, global_x=210, global_y=200))
        self._assert_dropped()

    def test_hover_move_without_a_drag_does_nothing(self):
        self.handler.handle_mouse_move(
            self._create_mouse_event(button=Qt.MouseButton.NoButton, global_x=210, global_y=200))
        self.mock_widget.update_config.assert_not_called()
        self.mock_position_manager.note_user_drop.assert_not_called()

    def test_end_lost_drag_drops_and_is_a_noop_without_a_drag(self):
        self.handler.end_lost_drag("test")
        self.mock_widget.update_config.assert_not_called()
        self._start_drag()
        self.handler.end_lost_drag("deactivated")
        self._assert_dropped()
        # The real release arriving after all must not save or note a second time.
        self.handler.handle_mouse_release(self._create_mouse_event(type=QEvent.Type.MouseButtonRelease))
        self.mock_position_manager.note_user_drop.assert_called_once()

    def test_watchdog_ends_the_drag_once_the_button_is_up(self):
        self._start_drag()
        self.assertTrue(self.handler._drag_watchdog.isActive())
        with patch.object(self.handler, "_left_button_down", return_value=False):
            self.handler._on_drag_watchdog()          # one miss is not enough
            self.assertTrue(self.mock_widget._dragging)
            self.handler._on_drag_watchdog()
        self._assert_dropped()

    def test_a_touch_drag_is_not_watched(self):
        """GetAsyncKeyState reads the physical mouse button, which a touch / pen drag may never set:
        the watchdog would cut it ~0.5 s in. Touch still ends on the button-up move / deactivate / hide."""
        from PyQt6.QtGui import QInputDevice
        self.mock_position_manager.constrain_drag.side_effect = lambda p: p
        press = self._create_mouse_event(global_x=150, global_y=150)
        press.pointingDevice.return_value.type.return_value = QInputDevice.DeviceType.TouchScreen
        self.handler.handle_mouse_press(press)
        self.handler.handle_mouse_move(self._create_mouse_event(global_x=200, global_y=200))
        self.assertTrue(self.mock_widget._dragging)
        self.assertFalse(self.handler._drag_watchdog.isActive())
        self.handler.end_lost_drag("hidden")
        self._assert_dropped()

    def test_left_button_down_honours_swapped_buttons(self):
        import win32con
        fake = MagicMock()
        fake.GetSystemMetrics.side_effect = lambda m: 1 if m == win32con.SM_SWAPBUTTON else 0
        fake.GetAsyncKeyState.side_effect = lambda vk: 0x8000 if vk == win32con.VK_RBUTTON else 0
        with patch.dict("sys.modules", {"win32api": fake}):
            self.assertTrue(self.handler._left_button_down())
            fake.GetSystemMetrics.side_effect = lambda m: 0
            self.assertFalse(self.handler._left_button_down())     # unswapped: VK_LBUTTON is up

    def test_watchdog_never_cuts_a_long_real_drag_short(self):
        self._start_drag()
        with patch.object(self.handler, "_left_button_down", return_value=True):
            for _ in range(1000):                     # ~4 minutes of 250 ms polls
                self.handler._on_drag_watchdog()
        self.assertTrue(self.mock_widget._dragging)
        self.assertTrue(self.handler._is_dragging)
        # A single misread between held reads resets, never accumulates.
        seq = iter([False, True, False, True])
        with patch.object(self.handler, "_left_button_down", side_effect=lambda: next(seq)):
            for _ in range(4):
                self.handler._on_drag_watchdog()
        self.assertTrue(self.mock_widget._dragging)
        self.mock_position_manager.note_user_drop.assert_not_called()



def test_widget_ends_a_lost_drag_on_deactivate_and_hide():
    """The widget's own hooks: WindowDeactivate (UAC, Win+L, Alt+Tab take the activation) and a
    hide mid-drag both hand the drag to InputHandler.end_lost_drag."""
    from PyQt6.QtGui import QHideEvent
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    app = QApplication.instance() or QApplication([])
    w = NetworkSpeedWidget.__new__(NetworkSpeedWidget)
    QWidget.__init__(w)
    w.logger = MagicMock()
    w.input_handler = MagicMock()
    w._hover_card_timer = MagicMock()
    w._hide_usage_hover_card = MagicMock()
    w.event(QEvent(QEvent.Type.WindowDeactivate))
    w.input_handler.end_lost_drag.assert_called_once()
    w.hideEvent(QHideEvent())
    assert w.input_handler.end_lost_drag.call_count == 2

if __name__ == '__main__':
    unittest.main()
