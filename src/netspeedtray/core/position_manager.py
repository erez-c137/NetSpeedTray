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
        """
        try:
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

            x, y = 0, 0
            
            if edge in (constants.taskbar.edge.BOTTOM, constants.taskbar.edge.TOP):
                # --- HORIZONTAL TASKBAR LOGIC ---
                tb_top_log = round(taskbar_info.rect[1] / dpi_scale)
                tb_height_log = round((taskbar_info.rect[3] - taskbar_info.rect[1]) / dpi_scale)
                
                right_boundary = round(taskbar_info.get_tray_rect()[0] / dpi_scale) if taskbar_info.get_tray_rect() else round(taskbar_info.rect[2] / dpi_scale)
                left_boundary = round(taskbar_info.tasklist_rect[2] / dpi_scale) if taskbar_info.tasklist_rect else round(taskbar_info.rect[0] / dpi_scale)

                y = tb_top_log + (tb_height_log - widget_height) // 2
                offset = config.get('tray_offset_x', constants.config.defaults.DEFAULT_TRAY_OFFSET_X)
                x = right_boundary - widget_width - offset

                if x < left_boundary:
                    logger.warning("Calculated position overlaps app icons; snapping to safe zone.")
                    x = left_boundary + constants.layout.DEFAULT_PADDING

            elif edge in (constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT):
                # --- VERTICAL TASKBAR LOGIC ---
                tb_left_log = round(taskbar_info.rect[0] / dpi_scale)
                tb_width_log = round((taskbar_info.rect[2] - taskbar_info.rect[0]) / dpi_scale)
                
                bottom_boundary = round(taskbar_info.get_tray_rect()[1] / dpi_scale) if taskbar_info.get_tray_rect() else round(taskbar_info.rect[3] / dpi_scale)
                top_boundary = round(taskbar_info.tasklist_rect[3] / dpi_scale) if taskbar_info.tasklist_rect else round(taskbar_info.rect[1] / dpi_scale)
                
                x = tb_left_log + (tb_width_log - widget_width) // 2
                
                # Align to bottom (near tray) instead of absolute center
                offset_y = config.get('tray_offset_y', constants.config.defaults.DEFAULT_TRAY_OFFSET_X)
                y = bottom_boundary - widget_height - offset_y
            
            else:
                return self._get_safe_fallback_position(widget_size)

            return ScreenUtils.validate_position(x, y, widget_size, screen)

        except Exception as e:
            logger.error("Error calculating position: %s", e, exc_info=True)
            return self._get_safe_fallback_position(widget_size)

    def _get_safe_fallback_position(self, widget_size: Tuple[int, int]) -> ScreenPosition:
        """Provides a default fallback position (bottom-right of primary screen)."""
        try:
            primary_screen: Optional[QScreen] = QApplication.primaryScreen()
            if not primary_screen:
                return ScreenPosition(0, 0)

            screen_rect: QRect = primary_screen.availableGeometry()
            widget_width, widget_height = widget_size
            margin = constants.taskbar.position.SCREEN_EDGE_MARGIN

            fallback_x = screen_rect.right() - widget_width - margin + 1
            fallback_y = screen_rect.bottom() - widget_height - margin + 1

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
                # Horizontal Constraint
                tb_top_log = round(taskbar_info.rect[1] / dpi_scale)
                tb_height_log = round((taskbar_info.rect[3] - taskbar_info.rect[1]) / dpi_scale)
                fixed_y = tb_top_log + (tb_height_log - widget_height) // 2
                
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
            self._state.widget.move(saved_x, saved_y)
            return True
        
        return False

    def _apply_calculated_position(self) -> bool:
        """Calculates and applies position based on taskbar rules."""
        target_pos = self.get_calculated_position()
        if target_pos:
            self._state.widget.move(target_pos.x, target_pos.y)
            return True
        return False

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
