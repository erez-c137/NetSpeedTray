"""
Input Handler for NetworkSpeedTray.

This module encapsulates all mouse and keyboard interaction logic, separating it
from the main widget processing. It handles:
1. Dragging operations (start, move, end).
2. Context menu triggers.
3. interactions (e.g., Double-Click to open Graph).
"""

import logging
from typing import Optional, TYPE_CHECKING
from PyQt6.QtCore import QObject, QPoint, QTimer, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants

if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget
    from netspeedtray.core.position_manager import PositionManager
    from netspeedtray.core.tray_manager import TrayIconManager

class InputHandler(QObject):
    """
    Handles mouse and keyboard input for the NetworkSpeedWidget.
    """
    def __init__(self, 
                 widget: 'NetworkSpeedWidget', 
                 position_manager: 'PositionManager',
                 tray_manager: 'TrayIconManager') -> None:
        super().__init__(widget)
        self.widget = widget
        self.position_manager = position_manager
        self.tray_manager = tray_manager
        self.logger = logging.getLogger("NetSpeedTray.Core.InputHandler")
        
        # State
        self._drag_start_pos: Optional[QPoint] = None
        self._is_dragging: bool = False

        # Click-to-pin the hover card (side-taskbar design §2). A single left click used to do
        # nothing; now: a card showing from hover is pinned at once; with no card up (touch has no
        # hover, or the click beat the hover delay) the click waits doubleClickInterval() first, so a
        # double-click still runs its action cleanly instead of flashing a card.
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click_settled)
        # The release that ends a double-click is not a click of its own.
        self._swallow_release: bool = False

        # A drag whose release never reaches us (UAC prompt, Win+L, Alt+Tab, a modal mid-drag) must not
        # leave ``widget._dragging`` set: the #24 yield reads it as BUSY and never hides the widget for
        # app buttons again (relu616, rc.4 on #373). While a drag runs, the real button is polled.
        self._drag_watchdog = QTimer(self)
        self._drag_watchdog.setInterval(constants.timeouts.DRAG_RELEASE_WATCHDOG_MS)
        self._drag_watchdog.timeout.connect(self._on_drag_watchdog)
        self._drag_watchdog_misses: int = 0
        # The watchdog reads the PHYSICAL mouse button, which a touch / pen drag may never set (Qt
        # takes those as WM_POINTER and synthesizes the mouse events): it only watches mouse drags.
        # Touch / pen still end on the button-up move, deactivation and hide.
        self._press_from_mouse: bool = True

    def handle_mouse_press(self, event: QMouseEvent) -> None:
        """Handles mouse press start (left = drag; middle = configurable click action)."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint() - self.widget.pos()
            self._is_dragging = False # Waiting for move to confirm drag
            self._press_from_mouse = self._is_real_mouse(event)
            # Qt sends a double-click's 2nd press as MouseButtonDblClick, never as a press: a real
            # press means any double-click tail is over (its release may have been lost to a menu or
            # a grab) - don't let it eat this click.
            self._swallow_release = False
            event.accept()
        elif event.button() == Qt.MouseButton.MiddleButton:
            # Configurable middle-click action (community PR #165, @rami123). Defaults to "Nothing",
            # so middle-click stays a no-op unless the user opts in via Settings → General.
            action = self.widget.config.get(
                "middle_click_action", constants.config.defaults.DEFAULT_MIDDLE_CLICK_ACTION)
            self.logger.debug("Middle-click detected. Running action: %s", action)
            self._execute_click_action(action)
            event.accept()

    def handle_mouse_move(self, event: QMouseEvent) -> None:
        """Handles dragging logic."""
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            # Mouse tracking is on, so a plain hover lands here too. Mid-drag it means the release
            # was lost on the way (UAC, Win+L, Alt+Tab): the drag is over - end it as a drop.
            if self._is_dragging:
                self.end_lost_drag("a move arrived with the left button up")
            return
        if not self._drag_start_pos:
            return

        # Check for minimum drag distance to prevent accidental moves (Fix for sticky Free Move)
        if (event.globalPosition().toPoint() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        # Start dragging if we haven't already. A hover card left up would stay at the old spot while
        # the widget moves away (or under it), so dismiss it - and any pending show - as the drag begins.
        if not self._is_dragging:
            self._dismiss_hover_card()
            self._drag_watchdog_misses = 0
            if self._press_from_mouse:
                self._drag_watchdog.start()
        self._is_dragging = True
        self.widget._dragging = True # Notify widget to stop position checks
        
        # Calculate desired position
        desired_global_pos = event.globalPosition().toPoint() - self._drag_start_pos
        
        # Constrain the position using PositionManager
        final_pos = self.position_manager.constrain_drag(desired_global_pos)
        
        # Apply move
        self.widget.move(final_pos)
        event.accept()

    def _dismiss_hover_card(self) -> None:
        self._click_timer.stop()
        try:
            timer = getattr(self.widget, "_hover_card_timer", None)
            if timer is not None:
                timer.stop()
            hide = getattr(self.widget, "_hide_usage_hover_card", None)
            if callable(hide):
                hide()
        except Exception as e:
            self.logger.debug("hover card dismiss on drag failed: %s", e)

    def handle_mouse_release(self, event: QMouseEvent) -> None:
        """Handles drag end and config saving."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_dragging:
                self._finish_drag()
            elif self._swallow_release:
                self._swallow_release = False      # the tail of a double-click
            elif self._drag_start_pos is not None:
                self._on_single_click()

            self._drag_start_pos = None
            event.accept()

    def _finish_drag(self) -> None:
        """The drag is over: clear the flags, save where the widget is, tell the yield it was a drop."""
        self._stop_drag_watchdog()
        self._is_dragging = False
        self._drag_start_pos = None
        self.widget._dragging = False
        self._save_dragged_position()
        self.logger.debug("Drag ended. Position saved: %s", self.widget.pos())
        # #24: a drop onto the taskbar's app buttons is the user's choice - no yield for it.
        note_drop = getattr(self.position_manager, "note_user_drop", None)
        if callable(note_drop):
            try:
                note_drop()
            except Exception as e:
                self.logger.debug("drop note failed: %s", e)

    def end_lost_drag(self, reason: str) -> None:
        """End a drag whose left-button release never arrived (a UAC prompt, Win+L, Alt+Tab, a
        modal, the widget hidden mid-drag). Treated exactly like a release at the last position: the
        widget is already drawn there, so saving it keeps what the user sees (not saving would let
        the next reposition snap it back, a move the user never asked for), and the #24 drop note
        is the same one a real drop sends - it only lasts until the widget is seen clear once.
        No-op when no drag is under way."""
        if not self._is_dragging:
            self._stop_drag_watchdog()
            return
        self.logger.info("Drag ended without a button release (%s) - treated as a drop.", reason)
        self._finish_drag()

    @staticmethod
    def _is_real_mouse(event: QMouseEvent) -> bool:
        """True for a press from a real mouse (not touch / pen). Unknown counts as a
        mouse: the watchdog is then on, as for every mouse drag."""
        try:
            from PyQt6.QtGui import QInputDevice
            # Qt 6 gives a mouse event synthesized from touch / pen the touchscreen / stylus device.
            return event.pointingDevice().type() == QInputDevice.DeviceType.Mouse
        except Exception:
            return True

    def _stop_drag_watchdog(self) -> None:
        self._drag_watchdog_misses = 0
        try:
            self._drag_watchdog.stop()
        except (AttributeError, RuntimeError):   # a handler built without __init__ (tests)
            pass

    def _left_button_down(self) -> bool:
        """The REAL state of the logical left button. Qt's own button state is what went stale, so
        ask Windows (GetAsyncKeyState reads PHYSICAL buttons - honour swapped buttons). On the secure
        desktop (UAC, Win+L) it reads 0: up, which is right - no drag survives that."""
        try:
            import win32api
            import win32con
            vk = win32con.VK_RBUTTON if win32api.GetSystemMetrics(win32con.SM_SWAPBUTTON) else win32con.VK_LBUTTON
            return bool(win32api.GetAsyncKeyState(vk) & 0x8000)
        except Exception:
            return bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)

    def _on_drag_watchdog(self) -> None:
        if not self._is_dragging:
            self._stop_drag_watchdog()
            return
        if self._left_button_down():
            self._drag_watchdog_misses = 0
            return
        self._drag_watchdog_misses += 1
        if self._drag_watchdog_misses >= constants.timeouts.DRAG_RELEASE_WATCHDOG_MISSES:
            self.end_lost_drag("the left button is no longer held")

    def _on_single_click(self) -> None:
        """A left click that was not a drag: the hover card's pin toggle."""
        state_fn = getattr(self.widget, "hover_card_state", None)
        state = state_fn() if callable(state_fn) else None
        if state == "pinned":
            self._call_widget("dismiss_hover_card")       # 2nd click closes it
        elif state == "hover":
            self._call_widget("pin_hover_card")           # pointer already resting: pin now
        else:
            # Touch (no hover) or a click before the hover delay: wait out the double-click window.
            self._click_timer.start(QApplication.doubleClickInterval())

    def cancel_pending_click(self) -> None:
        """Forget a click still waiting out the double-click window (the card was closed by
        something else meanwhile - it must not reopen pinned a moment later)."""
        self._click_timer.stop()

    def _on_single_click_settled(self) -> None:
        """No second click came: open (or pin) the card."""
        if getattr(self.widget, "_dragging", False):
            return
        self._call_widget("pin_hover_card")

    def _call_widget(self, name: str) -> None:
        fn = getattr(self.widget, name, None)
        if callable(fn):
            try:
                fn()
            except Exception as e:
                self.logger.debug("%s failed: %s", name, e)

    def handle_double_click(self, event: QMouseEvent) -> None:
        """Handles double-click via the configured action (default: open the unified Monitor)."""
        if event.button() == Qt.MouseButton.LeftButton:
            # The first click's pending pin is cancelled, and a card it pinned (or one up from hover)
            # goes away: the double-click is about its action, not the card.
            self._click_timer.stop()
            self._swallow_release = True
            self._call_widget("dismiss_hover_card")
            action = self.widget.config.get(
                "double_click_action", constants.config.defaults.DEFAULT_DOUBLE_CLICK_ACTION)
            self.logger.debug("Double-click detected. Running action: %s", action)
            self._execute_click_action(action)
            event.accept()

    def _execute_click_action(self, action: str) -> None:
        """
        Run a configured widget click action (community PR #165, @rami123 - adapted to the 2.0
        action set). Unknown/empty actions and "none" are silent no-ops.
        """
        defaults = constants.config.defaults
        try:
            if not action or action == defaults.CLICK_ACTION_NONE:
                return
            if action == defaults.CLICK_ACTION_OPEN_MONITOR:
                if hasattr(self.widget, "open_monitor_window"):
                    self.widget.open_monitor_window()
            elif action == defaults.CLICK_ACTION_SETTINGS:
                if hasattr(self.widget, "show_settings"):
                    self.widget.show_settings()
            elif action == defaults.CLICK_ACTION_PAUSE:
                # The widget exposes pause()/resume() + an is_paused flag (no single toggle method).
                if getattr(self.widget, "is_paused", False):
                    if hasattr(self.widget, "resume"):
                        self.widget.resume()
                elif hasattr(self.widget, "pause"):
                    self.widget.pause()
            else:
                self.logger.debug("Unknown click action %r - ignoring.", action)
        except Exception as e:
            self.logger.error("Failed to run click action %r: %s", action, e, exc_info=True)

    def _save_dragged_position(self) -> None:
        """
        Saves the final position based on the current mode:
        - Floating (Free Move, or #188 free-float on a taskbar-less display): absolute X/Y.
        - Docked: the offset relative to the tray/edge.
        """
        try:
            config = self.widget.config
            is_floating = self.position_manager.is_floating()
            updates = {}

            if is_floating:
                # Save absolute coordinates
                updates["position_x"] = self.widget.x()
                updates["position_y"] = self.widget.y()
            else:
                # Calculate constrained offset
                # We need to know WHICH offset to update (x or y) based on taskbar orientation
                from netspeedtray.utils.taskbar_utils import get_taskbar_info
                from netspeedtray import constants
                
                # Resolve against the PREFERRED taskbar (a secondary drag must not measure its offset
                # against the primary taskbar's geometry) (#186).
                tb_info = get_taskbar_info(preferred_screen_name=config.get("preferred_monitor"))
                edge = tb_info.get_edge_position()
                dpi_scale = tb_info.dpi_scale if tb_info.dpi_scale > 0 else 1.0
                
                if edge in (constants.taskbar.edge.BOTTOM, constants.taskbar.edge.TOP):
                    # Horizontal Taskbar: Variable is X offset from RIGHT side
                    # Offset = RightBoundary - WidgetRight
                    # Logic matches PositionCalculator: x = right_boundary - widget_width - offset
                    # So: offset = right_boundary - x - widget_width
                    
                    # Calculate Right Boundary (tray left or screen right edge)
                    tray_rect = tb_info.get_tray_rect()
                    if tray_rect:
                        right_boundary = tray_rect[0] / dpi_scale
                    else:
                        # Secondary taskbar (no tray HWND): match the calculator's clock reserve (#186)
                        # so the saved offset keeps the widget in the same clock-clear spot.
                        reserve = config.get("secondary_clock_reserve_px",
                                             constants.config.defaults.DEFAULT_SECONDARY_CLOCK_RESERVE_PX)
                        screen = tb_info.get_screen()
                        screen_right = float(screen.geometry().right() + 1) if screen else (tb_info.rect[2] / dpi_scale)
                        right_boundary = screen_right - reserve

                    # Current Widget X
                    current_x_log = self.widget.pos().x() # Use logical pos from pos()
                    widget_width = self.widget.width()
                    
                    new_offset = int(right_boundary - current_x_log - widget_width)
                    # The default (0) and the pre-1.3.0 defaults (10 / 1 / 3) read as "not placed by
                    # the user" (rc.2 W6), and the default spot steps left of a Widgets button beside
                    # the tray (rc.3): a drop that happens to land on one of them must still stick.
                    # 1 px further from the tray is invisible; the drag limit keeps it >= ~7 anyway.
                    defaults = constants.config.defaults
                    while (new_offset == defaults.DEFAULT_TRAY_OFFSET_X
                           or new_offset in defaults.LEGACY_DEFAULT_TRAY_OFFSETS_X):
                        new_offset += 1
                    updates["tray_offset_x"] = new_offset
                    self.logger.debug(f"Saved Horizontal Offset: {new_offset} (RightBound={right_boundary}, X={current_x_log}, W={widget_width})")
                    
                elif edge in (constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT):
                    # Vertical Taskbar: Variable is Y offset from BOTTOM
                    # Logic: y = bottom_boundary - widget_height - offset_y
                    # So: offset_y = bottom_boundary - y - widget_height
                    
                    tray_rect = tb_info.get_tray_rect()
                    if tray_rect:
                        bottom_boundary = tray_rect[1] / dpi_scale
                    else:
                        screen = tb_info.get_screen()
                        bottom_boundary = float(screen.geometry().bottom() + 1) if screen else (tb_info.rect[3] / dpi_scale)
                    
                    current_y_log = self.widget.pos().y()
                    widget_height = self.widget.height()
                    
                    new_offset = int(bottom_boundary - current_y_log - widget_height)
                    updates["tray_offset_y"] = new_offset
                    self.logger.debug(f"Saved Vertical Offset: {new_offset} (BottomBound={bottom_boundary}, Y={current_y_log}, H={widget_height})")

            if hasattr(self.widget, 'update_config') and updates:
                self.widget.update_config(updates)
                self.logger.debug(f"Drag ended. Updated config: {updates}")
                
        except Exception as e:
            self.logger.error("Failed to save dragged position: %s", e, exc_info=True)
