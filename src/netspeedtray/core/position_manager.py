"""
Core Position Manager for NetworkSpeedTray.

This module consolidates all widget positioning logic, including:
1. Calculation of optimal positions relative to the taskbar.
2. Active monitoring of position drift.
3. specific monitoring of tray geometry changes (Smart Polling).
4. Handling of drag constraints.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple, List, Protocol, runtime_checkable, Dict, Any, TYPE_CHECKING
import math
import ctypes
from ctypes import wintypes
import win32con
import win32gui

from PyQt6.QtCore import QObject, QTimer, QPoint, QRect, QSize, pyqtSlot
from PyQt6.QtGui import QFontMetrics, QScreen
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants
# We import taskbar_utils for low-level detection
from netspeedtray.utils import taskbar_utils
from netspeedtray.utils.taskbar_utils import TaskbarInfo, get_taskbar_info, is_small_taskbar

if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget

# Logger Setup
logger = logging.getLogger("NetSpeedTray.Core.PositionManager")


# Protocols
@runtime_checkable
class PositionAwareProtocol(Protocol):
    """
    Defines the interface required by PositionManager for interacting with the widget.
    """
    def move(self, x: int, y: int) -> None: ...
    def width(self) -> int: ...
    def height(self) -> int: ...
    def pos(self) -> QPoint: ...
    def size(self) -> QSize: ...
    def isVisible(self) -> bool: ...


# Data Classes
@dataclass(frozen=True, slots=True)
class ScreenPosition:
    """Represents an immutable screen position using logical pixel coordinates."""
    x: int
    y: int


@dataclass(slots=True)
class WindowState:
    """Encapsulates configuration and references needed for position calculations."""
    config: Dict[str, Any]
    widget: PositionAwareProtocol
    taskbar_info: Optional[TaskbarInfo] = None
    font_metrics: Optional[QFontMetrics] = None


class ScreenUtils:
    """Provides static utility methods for screen-related operations using Qt."""
    @staticmethod
    def find_screen_for_point(point: QPoint) -> Optional[QScreen]:
        """
        Finds the QScreen that contains the given point (logical coordinates).
        """
        return QApplication.screenAt(point)

    @staticmethod
    def find_screen_for_rect(rect: QRect) -> Optional[QScreen]:
        """
        Finds the QScreen that contains the center of the given QRect (logical coordinates).
        """
        return QApplication.screenAt(rect.center())


    @staticmethod
    def validate_position(x: int, y: int, widget_size: Tuple[int, int], screen: QScreen) -> ScreenPosition:
        """
        Adjusts a desired position to ensure the widget remains fully within the given screen's full geometry.
        """
        try:
            screen_rect: QRect = screen.geometry()
            widget_width, widget_height = widget_size

            valid_x = max(screen_rect.left(), min(x, screen_rect.right() - widget_width + 1))
            valid_y = max(screen_rect.top(), min(y, screen_rect.bottom() - widget_height + 1))

            if valid_x != x or valid_y != y:
                logger.debug("Position (%s,%s) validated to (%s,%s) using full geometry for screen '%s'",
                             x, y, valid_x, valid_y, screen.name())

            return ScreenPosition(valid_x, valid_y)

        except Exception as e:
            logger.error("Error validating position (%s,%s) on screen '%s': %s", x, y, screen.name(), e, exc_info=True)
            return ScreenPosition(x, y)

    @staticmethod
    def is_position_valid(x: int, y: int, widget_size: Tuple[int, int], screen: QScreen) -> bool:
        """Checks if a given position is at least partially visible on the specified screen."""
        try:
            screen_rect: QRect = screen.geometry()
            widget_width, widget_height = widget_size
            widget_rect = QRect(x, y, widget_width, widget_height)
            return screen_rect.intersects(widget_rect)
        except Exception as e:
            logger.error("Error checking position validity: %s", e, exc_info=True)
            return False


class PositionCalculator:
    """Calculates the optimal widget position relative to a specified taskbar."""
    
    def __init__(self) -> None:
        self._last_drag_log_time: float = 0.0

    def calculate_position(self, taskbar_info: TaskbarInfo, widget_size: Tuple[int, int], config: Dict[str, Any]) -> ScreenPosition:
        """
        Calculates the widget's optimal position based on taskbar edge and tray location.
        
        Args:
            taskbar_info: TaskbarInfo object with position/edge information
            widget_size: Tuple of (width, height) in pixels
            config: Configuration dictionary with positioning options
            
        Returns:
            ScreenPosition with calculated x, y coordinates
            
        Raises:
            ValueError: If inputs are invalid/malformed
        """
        try:
            # Input validation
            if taskbar_info is None:
                raise ValueError("taskbar_info cannot be None")
            if not isinstance(widget_size, (tuple, list)) or len(widget_size) != 2:
                raise ValueError(f"widget_size must be a tuple of (width, height), got {widget_size}")
            if not all(isinstance(d, int) and d > 0 for d in widget_size):
                raise ValueError(f"widget_size dimensions must be positive integers, got {widget_size}")
            if config is None or not isinstance(config, dict):
                raise ValueError(f"config must be a dictionary, got {config}")
            
            # Fallback for invalid taskbar
            if taskbar_info.hwnd == 0:
                logger.warning("Calculation requested for fallback taskbar. Using safe fallback.")
                return self._get_safe_fallback_position(widget_size)

            edge = taskbar_info.get_edge_position()
            screen = taskbar_info.get_screen()
            if not screen:
                raise RuntimeError("No associated QScreen found for taskbar.")

            dpi_scale = taskbar_info.dpi_scale if taskbar_info.dpi_scale > 0 else 1.0
            widget_width, widget_height = widget_size

            # NEW: Clamp widget size to reasonable bounds to prevent off-screen/huge widgets
            ui_widget = getattr(constants.ui, 'widget', None)
            if ui_widget:
                # Clamp width
                if widget_width > ui_widget.MAX_WIDGET_WIDTH_PX:
                    logger.warning(
                        "Widget width %s exceeds max %spx. Clamping to prevent off-screen positioning.",
                        widget_width, ui_widget.MAX_WIDGET_WIDTH_PX
                    )
                    widget_width = ui_widget.MAX_WIDGET_WIDTH_PX
                # Clamp height
                if widget_height > ui_widget.MAX_WIDGET_HEIGHT_PX:
                    logger.warning(
                        "Widget height %s exceeds max %spx. Clamping to prevent off-screen positioning.",
                        widget_height, ui_widget.MAX_WIDGET_HEIGHT_PX
                    )
                    widget_height = ui_widget.MAX_WIDGET_HEIGHT_PX

            # Use possibly-clamped size for subsequent calculations
            widget_size = (widget_width, widget_height)

            x, y = 0, 0
            
            if edge in (constants.taskbar.edge.BOTTOM, constants.taskbar.edge.TOP):
                # Horizontal taskbar: calculate Y position (centered in taskbar gap)
                x, y = self._calculate_horizontal_position(taskbar_info, widget_size, config, dpi_scale)
            elif edge in (constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT):
                # Vertical taskbar: calculate X position (centered in taskbar gap)
                x, y = self._calculate_vertical_position(taskbar_info, widget_size, config, dpi_scale)
            else:
                return self._get_safe_fallback_position(widget_size)

            return ScreenUtils.validate_position(x, y, widget_size, screen)

        except ValueError as e:
            logger.error("Invalid parameter in position calculation: %s", e, exc_info=True)
            return self._get_safe_fallback_position(widget_size)
        except Exception as e:
            logger.error("Error calculating position: %s", e, exc_info=True)
            return self._get_safe_fallback_position(widget_size)

    def _calculate_horizontal_position(self, taskbar_info: TaskbarInfo, widget_size: Tuple[int, int], 
                                       config: Dict[str, Any], dpi_scale: float) -> Tuple[int, int]:
        """
        Calculate position for horizontal (top/bottom) taskbar.
        
        Returns:
            (x, y) tuple with calculated position
            
        For horizontal taskbars:
        - Y position: centered vertically in the taskbar gap
        - X position: aligned to right edge (near system tray)
        """
        widget_width, widget_height = widget_size
        
        # Use screen available geometry to find the TRUE visible taskbar region (Fixes #104/PR #110)
        screen = taskbar_info.get_screen()
        full_geom = screen.geometry()
        avail_geom = screen.availableGeometry()
        if not isinstance(avail_geom, QRect):
            avail_geom = full_geom
        
        edge = taskbar_info.get_edge_position()
        
        if edge == constants.taskbar.edge.BOTTOM:
            # Visible taskbar is the space below available geometry
            visible_tb_height = full_geom.bottom() - avail_geom.bottom()
            y_origin = avail_geom.bottom() + 1
            if visible_tb_height <= 0:
                visible_tb_height = (taskbar_info.rect[3] - taskbar_info.rect[1]) / dpi_scale
                y_origin = taskbar_info.rect[1] / dpi_scale
        elif edge == constants.taskbar.edge.TOP:
            # Visible taskbar is the space above available geometry
            visible_tb_height = avail_geom.top() - full_geom.top()
            y_origin = full_geom.top()
            if visible_tb_height <= 0:
                visible_tb_height = (taskbar_info.rect[3] - taskbar_info.rect[1]) / dpi_scale
                y_origin = taskbar_info.rect[1] / dpi_scale
        else:
            # Fallback to rect-based if we're not sure
            visible_tb_height = (taskbar_info.rect[3] - taskbar_info.rect[1]) / dpi_scale
            y_origin = taskbar_info.rect[1] / dpi_scale

        # Calculate Y: center widget vertically in the *visible* taskbar gap
        y_center = y_origin + (visible_tb_height - widget_height) / 2.0
        y = round(y_center)
        
        # Calculate X: align to right (system tray side) with offset.
        # right_boundary is the left edge of the tray/notification area when available.
        tray_rect = taskbar_info.get_tray_rect()
        right_boundary = round(tray_rect[0] / dpi_scale) if tray_rect else (full_geom.right() + 1)

        # left_boundary is the right edge of the task list/app-icons region when available.
        tasklist_rect = taskbar_info.tasklist_rect
        left_boundary = round(tasklist_rect[2] / dpi_scale) if tasklist_rect else full_geom.left()

        offset = config.get('tray_offset_x', constants.config.defaults.DEFAULT_TRAY_OFFSET_X)
        x = round(right_boundary - widget_width - offset)
        
        # Safety check: don't overlap with app icons on left
        if x < left_boundary:
            logger.warning("Calculated position overlaps app icons; snapping to safe zone.")
            x = round(left_boundary + constants.layout.DEFAULT_PADDING)
        
        return x, y

    def _calculate_vertical_position(self, taskbar_info: TaskbarInfo, widget_size: Tuple[int, int],
                                     config: Dict[str, Any], dpi_scale: float) -> Tuple[int, int]:
        """
        Calculate position for vertical (left/right) taskbar.
        
        Returns:
            (x, y) tuple with calculated position
            
        For vertical taskbars:
        - X position: centered horizontally in the taskbar gap
        - Y position: aligned to bottom edge (near system tray)
        """
        widget_width, widget_height = widget_size
        
        # Use screen available geometry to find the TRUE visible taskbar region (Fixes #104/PR #110)
        screen = taskbar_info.get_screen()
        full_geom = screen.geometry()
        avail_geom = screen.availableGeometry()
        if not isinstance(avail_geom, QRect):
            avail_geom = full_geom
        
        edge = taskbar_info.get_edge_position()
        
        if edge == constants.taskbar.edge.RIGHT:
            # Visible taskbar is the space to the right of available geometry
            visible_tb_width = full_geom.right() - avail_geom.right()
            x_origin = avail_geom.right() + 1
            if visible_tb_width <= 0:
                visible_tb_width = (taskbar_info.rect[2] - taskbar_info.rect[0]) / dpi_scale
                x_origin = taskbar_info.rect[0] / dpi_scale
        else: # LEFT
            # Visible taskbar is the space to the left of available geometry
            visible_tb_width = avail_geom.left() - full_geom.left()
            x_origin = full_geom.left()
            if visible_tb_width <= 0:
                visible_tb_width = (taskbar_info.rect[2] - taskbar_info.rect[0]) / dpi_scale
                x_origin = taskbar_info.rect[0] / dpi_scale
            
        # Calculate X: center widget horizontally in taskbar gap
        x_center = x_origin + (visible_tb_width - widget_width) / 2.0
        x = round(x_center)
        
        # Calculate Y: align to bottom (system tray side) with offset.
        tray_rect = taskbar_info.get_tray_rect()
        bottom_boundary = round(tray_rect[1] / dpi_scale) if tray_rect else (full_geom.bottom() + 1)

        offset_y = config.get('tray_offset_y', constants.config.defaults.DEFAULT_TRAY_OFFSET_X)
        y = round(bottom_boundary - widget_height - offset_y)

        # Safety check: avoid overlapping top-side icons on vertical taskbars.
        tasklist_rect = taskbar_info.tasklist_rect
        top_boundary = round(tasklist_rect[3] / dpi_scale) if tasklist_rect else full_geom.top()
        if y < top_boundary:
            logger.warning("Calculated position overlaps taskbar icons; snapping to safe zone.")
            y = round(top_boundary + constants.layout.DEFAULT_PADDING)
        
        return x, y

    def _get_safe_fallback_position(self, widget_size: Tuple[int, int]) -> ScreenPosition:
        """Provides a default fallback position (bottom-right of primary screen)."""
        try:
            primary_screen: Optional[QScreen] = QApplication.primaryScreen()
            if not primary_screen:
                return ScreenPosition(0, 0)

            screen_rect: QRect = primary_screen.availableGeometry()
            widget_width, widget_height = widget_size
            ui_widget = getattr(constants.ui, 'widget', None)
            margin_px = ui_widget.SCREEN_EDGE_MARGIN_PX if ui_widget else 10

            fallback_x = screen_rect.right() - widget_width - margin_px
            fallback_y = screen_rect.bottom() - widget_height - margin_px

            return ScreenPosition(
                max(screen_rect.left(), fallback_x),
                max(screen_rect.top(), fallback_y)
            )
        except Exception:
            return ScreenPosition(0, 0)

    def constrain_drag_position(self, desired_pos: QPoint, taskbar_info: TaskbarInfo, widget_size_q: QSize) -> Optional[QPoint]:
        """Constrains a desired widget position during dragging to the 'safe zone'."""
        try:
            screen = taskbar_info.get_screen()
            if not screen:
                return None

            widget_width, widget_height = widget_size_q.width(), widget_size_q.height()
            edge = taskbar_info.get_edge_position()
            dpi_scale = taskbar_info.dpi_scale if taskbar_info.dpi_scale > 0 else 1.0

            if edge in (constants.taskbar.edge.BOTTOM, constants.taskbar.edge.TOP):
                # Horizontal Constraint — center on visible taskbar area
                screen_obj = taskbar_info.get_screen()
                if screen_obj and edge == constants.taskbar.edge.BOTTOM:
                    vis_top = screen_obj.availableGeometry().bottom() + 1
                    vis_bot = screen_obj.geometry().bottom() + 1
                elif screen_obj and edge == constants.taskbar.edge.TOP:
                    vis_top = screen_obj.geometry().top()
                    vis_bot = screen_obj.availableGeometry().top()
                else:
                    vis_top = round(taskbar_info.rect[1] / dpi_scale)
                    vis_bot = round(taskbar_info.rect[3] / dpi_scale)
                fixed_y = round((vis_top + vis_bot) / 2.0 - widget_height / 2.0)
                
                right_boundary = (round(taskbar_info.get_tray_rect()[0] / dpi_scale) - widget_width - constants.layout.DEFAULT_PADDING) if taskbar_info.get_tray_rect() else (screen.geometry().right() - widget_width)
                left_boundary = (round(taskbar_info.tasklist_rect[2] / dpi_scale) + constants.layout.DEFAULT_PADDING) if taskbar_info.tasklist_rect else screen.geometry().left()
                
                constrained_x = max(left_boundary, min(desired_pos.x(), right_boundary))
                return QPoint(constrained_x, fixed_y)

            elif edge in (constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT):
                # Vertical Constraint
                tb_left_log = round(taskbar_info.rect[0] / dpi_scale)
                tb_width_log = round((taskbar_info.rect[2] - taskbar_info.rect[0]) / dpi_scale)
                fixed_x = tb_left_log + (tb_width_log - widget_width) // 2
                
                # Keep within the safe zone, but allow bottom-alignment as calculated choice
                bottom_boundary = (round(taskbar_info.get_tray_rect()[1] / dpi_scale) - widget_height - constants.layout.DEFAULT_PADDING) if taskbar_info.get_tray_rect() else (screen.geometry().bottom() - widget_height)
                top_boundary = (round(taskbar_info.tasklist_rect[3] / dpi_scale) + constants.layout.DEFAULT_PADDING) if taskbar_info.tasklist_rect else screen.geometry().top()
                
                constrained_y = max(top_boundary, min(desired_pos.y(), bottom_boundary))
                return QPoint(fixed_x, constrained_y)
            
            return desired_pos

        except Exception as e:
            logger.error("Error dragging constraint: %s", e, exc_info=True)
            return None


class PositionManager(QObject):
    """
    Orchestrates all positioning logic, including calculation, application,
    and active monitoring of system changes.
    """
    
    def __init__(self, window_state: WindowState, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._state = window_state
        self._calculator = PositionCalculator()
        
        # Internal State
        self._last_tray_rect: Optional[Tuple[int, int, int, int]] = None
        self._last_applied_geometry: Optional[QRect] = None
        self._taskbar_lost_count: int = 0
        
        # Timers
        self._tray_watcher_timer = QTimer(self)
        self._tray_watcher_timer.timeout.connect(self._check_for_tray_changes)
        
        logger.debug("Core PositionManager initialized.")

    def start_monitoring(self) -> None:
        """Starts the periodic tray watcher."""
        if not self._tray_watcher_timer.isActive():
            self._tray_watcher_timer.start(10000) # Check every 10s
            logger.debug("PositionManager monitoring started.")

    def stop_monitoring(self) -> None:
        """Stops the periodic tray watcher."""
        self._tray_watcher_timer.stop()
        logger.debug("PositionManager monitoring stopped.")

    @pyqtSlot()
    def update_position(self, fresh_taskbar_info: Optional[TaskbarInfo] = None) -> None:
        """
        Main entry point to update the widget's position.
        Uses fresh taskbar info if provided, otherwise fetches it.
        """
        try:
            if fresh_taskbar_info:
                self._state.taskbar_info = fresh_taskbar_info
            else:
                self._state.taskbar_info = get_taskbar_info()

            if self._apply_saved_position():
                return

            if self._apply_calculated_position():
                pass # success
            else:
                logger.warning("Failed to calculate position.")
        except Exception as e:
            logger.error("Error updating position: %s", e, exc_info=True)

    def _apply_saved_position(self) -> bool:
        """Applies saved position if 'free_move' is enabled."""
        if not self._state.config.get('free_move', False):
            return False

        saved_x = self._state.config.get('position_x')
        saved_y = self._state.config.get('position_y')

        if not isinstance(saved_x, int) or not isinstance(saved_y, int):
            return False

        # Validate against current screen
        screen = self._state.taskbar_info.get_screen() if self._state.taskbar_info else QApplication.primaryScreen()
        if not screen:
            return False

        widget_size = (self._state.widget.width(), self._state.widget.height())
        if ScreenUtils.is_position_valid(saved_x, saved_y, widget_size, screen):
            self._apply_geometry(saved_x, saved_y)
            return True
        
        return False

    def _apply_calculated_position(self) -> bool:
        """Calculates and applies position based on taskbar rules."""
        target_pos = self.get_calculated_position()
        if target_pos:
            self._apply_geometry(target_pos.x, target_pos.y)
            return True
        return False

    def _apply_geometry(self, x: int, y: int) -> None:
        """Moves the widget with geometry debouncing to prevent redundant OS calls."""
        # Note: We use QRect to track both position and size, as a size change 
        # (calculated in layout.py) should also invalidate the debounce.
        new_rect = QRect(x, y, self._state.widget.width(), self._state.widget.height())
        
        if self._last_applied_geometry == new_rect:
            return

        # Double check against current actual position to avoid even one redundant call
        # if the widget was moved by external means but matches our target.
        if self._state.widget.pos() == QPoint(x, y):
            self._last_applied_geometry = new_rect
            return

        self._state.widget.move(x, y)
        self._last_applied_geometry = new_rect
        logger.debug("Widget geometry updated to: %s", new_rect)

    def get_calculated_position(self) -> Optional[ScreenPosition]:
        """Returns the intended position without moving the widget."""
        if not self._state.taskbar_info or not self._state.widget:
            return None
            
        widget_size = (self._state.widget.width(), self._state.widget.height())
        if widget_size[0] <= 0:
            return None

        return self._calculator.calculate_position(
            self._state.taskbar_info,
            widget_size,
            self._state.config
        )

    @pyqtSlot()
    def _check_for_tray_changes(self) -> None:
        """Checks if the system tray geometry has changed (Stub for smart polling)."""
        if self._state.config.get("free_move", False) or not self._state.widget.isVisible():
            return

        try:
            # We re-fetch info here to be accurate
            tb_info = get_taskbar_info()
            if not tb_info:
                return

            current_tray_rect = tb_info.get_tray_rect()
            
            if self._last_tray_rect is None:
                self._last_tray_rect = current_tray_rect
                return

            if self._last_tray_rect != current_tray_rect:
                logger.debug("Tray geometry changed. Triggering reposition.")
                self.update_position(fresh_taskbar_info=tb_info)
                self._last_tray_rect = current_tray_rect

        except Exception as e:
            logger.error("Error checking tray changes: %s", e)

    def constrain_drag(self, pos: QPoint) -> QPoint:
        """
        Helper for InputHandler to constrain dragging.
        """
        if not self._state.taskbar_info:
             self._state.taskbar_info = get_taskbar_info()

        # FIX for #87: specific check for Free Move
        if self._state.config.get("free_move", False):
            # If Free Move is enabled, only constrain to screen bounds (prevent total loss)
            # FIX for #102: Use the screen at the drag destination, not the taskbar's screen
            # This allows the widget to be dragged freely across all connected monitors
            screen = QApplication.screenAt(pos)
            if not screen:
                # Fallback to taskbar screen if no screen found at pos (shouldn't happen)
                screen = self._state.taskbar_info.get_screen() if self._state.taskbar_info else None
            
            if screen:
                widget_size = (self._state.widget.width(), self._state.widget.height())
                validated = ScreenUtils.validate_position(pos.x(), pos.y(), widget_size, screen)
                return QPoint(validated.x, validated.y)
            return pos

        # Otherwise, snap/constrain to taskbar
        res = self._calculator.constrain_drag_position(
            pos, 
            self._state.taskbar_info, 
            self._state.widget.size()
        )
        return res if res else pos

    def reset_to_default(self) -> None:
        """
        Resets the widget to its default position by clearing explicit position Config
        and triggering a recalculation.
        """
        self._state.config['position_x'] = None
        self._state.config['position_y'] = None
        # We rely on the caller (Widget) to persist this config change to disk if needed.
        self.update_position()
        self.ensure_topmost()

    def ensure_topmost(self) -> None:
        """
        Uses the Windows API to forcefully re-assert the widget's topmost status.
        Uses the 're-promotion' technique (NOTOPMOST -> TOPMOST) to fix 'stuck' Z-order.
        """
        try:
            hwnd = int(self._state.widget.winId())
            if not win32gui.IsWindow(hwnd):
                return

            # 1. Temporarily drop topmost (but keep position)
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
            )

            # 2. Re-assert topmost
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
            )
        except Exception as e:
            logger.error("Failed to ensure topmost status: %s", e)

    @pyqtSlot()
    def enforce_topmost_status(self) -> None:
        """
        Periodically ensures the widget's topmost status.
        """
        if self._state.widget.isVisible():
            self.ensure_topmost()
