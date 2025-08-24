"""
Utilities for calculating and managing the network widget's position.

Provides classes to determine the optimal placement relative to the Windows
taskbar (near the system tray), validate positions against screen boundaries,
handle DPI scaling, and constrain dragging movements.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple, List, Protocol, runtime_checkable, TYPE_CHECKING, Dict, Any

# Type Checking Imports
if TYPE_CHECKING:
    from PyQt6.QtCore import QPoint, QRect, QSize
    from PyQt6.QtGui import QFontMetrics, QScreen
    from PyQt6.QtWidgets import QWidget
    from .taskbar_utils import TaskbarInfo

# Qt Imports
from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtGui import QFontMetrics, QScreen
from PyQt6.QtWidgets import QApplication

# Local Imports
from netspeedtray import constants
from .taskbar_utils import TaskbarInfo

# Logger Setup
logger = logging.getLogger("NetSpeedTray.PositionUtils")


# Protocols
@runtime_checkable
class PositionAwareProtocol(Protocol):
    """
    Defines the interface required by PositionManager for interacting with the widget.
    Ensures necessary methods for getting size/position and moving are available.
    """
    def move(self, x: int, y: int) -> None: ...
    def width(self) -> int: ...
    def height(self) -> int: ...
    def pos(self) -> QPoint: ...
    def size(self) -> QSize: ...


# Data Classes
@dataclass(frozen=True, slots=True)
class ScreenPosition:
    """
    Represents an immutable screen position using logical pixel coordinates.

    Attributes:
        x: X-coordinate (logical pixels).
        y: Y-coordinate (logical pixels).
    """
    x: int
    y: int


@dataclass(slots=True)
class WindowState:
    """
    Encapsulates the configuration and references needed for position calculations.

    Attributes:
        config: Application configuration dictionary (used for saved position).
        widget: The widget instance being managed (must conform to PositionAwareProtocol).
        taskbar_info: Information about the relevant taskbar, if detected.
        font_metrics: QFontMetrics for calculating text height (optional).
    """
    config: Dict[str, Any]
    widget: PositionAwareProtocol
    taskbar_info: Optional[TaskbarInfo] = None
    font_metrics: Optional[QFontMetrics] = None  # Added for text height calculation


# Core Position Management
class PositionManager:
    """
    Manages the widget's position, orchestrating calculation and application.

    Uses saved configuration values when available and valid, otherwise calculates
    an optimal position relative to the taskbar using PositionCalculator.
    Provides methods to update the widget's position and retrieve the calculated target.
    """
    def __init__(self, window_state: WindowState) -> None:
        """
        Initializes the PositionManager.

        Args:
            window_state: The initial state containing widget reference, config,
                          and optionally taskbar info.

        Raises:
            TypeError: If the widget does not fully conform to PositionAwareProtocol.
        """
        if not isinstance(window_state.widget, PositionAwareProtocol):
            required_methods = ["move", "width", "height", "pos", "size"]
            missing_methods = [method for method in required_methods if not hasattr(window_state.widget, method)]
            if missing_methods:
                raise TypeError(
                    f"Widget does not fully conform to PositionAwareProtocol. Missing methods: {missing_methods}"
                )
            logger.warning("Widget type check passed, but ensure it implements PositionAwareProtocol methods correctly.")
        self._state: WindowState = window_state
        self._calculator: PositionCalculator = PositionCalculator()
        logger.debug("PositionManager initialized.")

    def update_position(self) -> None:
        """
        Sets the widget's position.

        Attempts to use a valid saved position from configuration first.
        If no valid saved position exists, calculates a new position based on
        taskbar information and applies it. Handles potential errors during the process.
        """
        try:
            if self._apply_saved_position():
                logger.info("Widget position set from saved coordinates: %s", self._state.widget.pos())
                return

            if self._apply_calculated_position():
                logger.info("Widget position set from calculated coordinates: %s", self._state.widget.pos())
            else:
                logger.warning("Failed to calculate and apply position. Widget may be misplaced.")

        except Exception as e:
            logger.error("Unexpected error during position update: %s", e, exc_info=True)

    def get_calculated_position(self) -> Optional[QPoint]:
        """
        Calculates the target position based on taskbar info without moving the widget.

        Returns:
            Optional[QPoint]: The calculated QPoint in logical coordinates,
                              or None if calculation is not possible (e.g., no taskbar info).
        """
        if not self._state.taskbar_info:
            logger.warning("Cannot calculate position: Taskbar info is missing.")
            return None
        if not self._state.widget:
            logger.warning("Cannot calculate position: Widget reference is missing.")
            return None

        try:
            widget_size: Tuple[int, int] = (self._state.widget.width(), self._state.widget.height())
            if widget_size[0] <= 0 or widget_size[1] <= 0:
                logger.warning("Cannot calculate position: Invalid widget size %s.", widget_size)
                return None

            # Pass the config from the state into the calculator method.
            screen_pos: ScreenPosition = self._calculator.calculate_position(
                self._state.taskbar_info,
                widget_size,
                self._state.config,
                self._state.font_metrics
            )
            return QPoint(screen_pos.x, screen_pos.y)

        except Exception as e:
            logger.error("Error calculating target position: %s", e, exc_info=True)
            return None

    def _apply_saved_position(self) -> bool:
        """
        Attempts to retrieve, validate, and apply a saved position from configuration,
        but ONLY if 'free_move' mode is enabled.
        """
        # 1. Check the 'free_move' flag first. If it's disabled, there is no
        #    valid saved position to apply, regardless of what's in the file.
        if not self._state.config.get('free_move', False):
            logger.debug("Skipping saved position because 'free_move' is disabled.")
            return False

        # 2. If free_move is enabled, proceed with the original logic.
        saved_x = self._state.config.get('position_x')
        saved_y = self._state.config.get('position_y')

        if not isinstance(saved_x, int) or not isinstance(saved_y, int):
            logger.debug("No valid saved position: X=%s, Y=%s", saved_x, saved_y)
            return False

        screen = self._state.taskbar_info.get_screen() if self._state.taskbar_info else QApplication.primaryScreen()
        if not screen:
            logger.error("Cannot validate saved position: No screen available")
            return False

        widget_size = (self._state.widget.width(), self._state.widget.height())
        if widget_size[0] <= 0 or widget_size[1] <= 0:
            logger.warning("Cannot apply saved position: Invalid widget size %s", widget_size)
            return False

        if ScreenUtils.is_position_valid(saved_x, saved_y, widget_size, screen):
            self._state.widget.move(saved_x, saved_y)
            logger.debug("Applied saved position: (%s, %s)", saved_x, saved_y)
            return True

        logger.debug("Saved position (%s, %s) is invalid for screen '%s'", saved_x, saved_y, screen.name())
        return False

    def _apply_calculated_position(self) -> bool:
        """
        Calculates the position using PositionCalculator and applies it to the widget.

        Returns:
            bool: True if calculation and move were successful, False otherwise.
        """
        target_pos: Optional[QPoint] = self.get_calculated_position()

        if target_pos:
            logger.debug("Applying calculated position: (%s, %s)", target_pos.x(), target_pos.y())
            try:
                self._state.widget.move(target_pos.x(), target_pos.y())
                return True
            except Exception as move_err:
                logger.error("Error applying calculated position via widget.move: %s", move_err, exc_info=True)
                return False
        else:
            logger.warning("Could not apply calculated position: No valid position calculated. Taskbar info: %s", 
                        self._state.taskbar_info)
            # Fallback to safe position
            try:
                widget_size = (self._state.widget.width(), self._state.widget.height())
                safe_pos = self._calculator._get_safe_fallback_position(widget_size)
                self._state.widget.move(safe_pos.x, safe_pos.y)
                logger.info("Applied safe fallback position: (%s, %s)", safe_pos.x, safe_pos.y)
                return True
            except Exception as e:
                logger.error("Failed to apply safe fallback position: %s", e, exc_info=True)
                return False

    @property
    def calculator(self) -> 'PositionCalculator':
        """Provides access to the PositionCalculator instance."""
        return self._calculator


# Helper Classes with Static Methods
class TaskbarManager:
    """
    Provides static utility methods for finding and interacting with taskbars.
    Note: Most core taskbar detection logic resides in `taskbar_utils.py`.
    """
    @staticmethod
    def find_all_taskbars() -> List[TaskbarInfo]:
        """
        Retrieves information about all detected taskbars in the system.

        Delegates to `taskbar_utils.get_all_taskbar_info`.

        Returns:
            List[TaskbarInfo]: A list of TaskbarInfo objects, one for each detected taskbar.
                               Returns an empty list if none are found or on error.
        """
        try:
            from .taskbar_utils import get_all_taskbar_info
            taskbars = get_all_taskbar_info()
            if not taskbars:
                logger.warning("No taskbars detected by get_all_taskbar_info.")
            return taskbars
        except ImportError as e:
            logger.error("Failed to import get_all_taskbar_info from taskbar_utils: %s", e)
            return []
        except Exception as e:
            logger.error("Error finding all taskbars: %s", e, exc_info=True)
            return []

    @staticmethod
    def find_nearest_taskbar(pos: QPoint) -> TaskbarInfo:
        """
        Finds the taskbar whose edge is nearest to a given point (logical coordinates).

        Used primarily for determining which taskbar the widget should associate with
        if multiple monitors/taskbars are present.

        Args:
            pos: The reference position (e.g., mouse cursor, widget center) in logical pixels.

        Returns:
            TaskbarInfo: Information about the nearest taskbar. Returns a fallback
                         TaskbarInfo representing the primary screen if no taskbars are found.
        """
        taskbars: List[TaskbarInfo] = TaskbarManager.find_all_taskbars()
        if not taskbars:
            logger.warning("No taskbars found during nearest search, using primary screen fallback.")
            try:
                from .taskbar_utils import create_primary_fallback_taskbar_info
                return create_primary_fallback_taskbar_info()
            except ImportError as e:
                logger.error("Failed to import create_primary_fallback_taskbar_info from taskbar_utils: %s", e)
                primary_screen = QApplication.primaryScreen()
                return TaskbarInfo(
                    hwnd=0,
                    tray_hwnd=None,
                    rect=(0, 0, 0, 0),
                    screen_name=primary_screen.name() if primary_screen else "",
                    screen_geometry=(0, 0, 0, 0) if not primary_screen else (
                        primary_screen.geometry().left(),
                        primary_screen.geometry().top(),
                        primary_screen.geometry().width(),
                        primary_screen.geometry().height()
                    ),
                    work_area=(0, 0, 0, 0),
                    dpi_scale=1.0,
                    is_primary=True,
                    height=constants.taskbar.DEFAULT_HEIGHT
                )
            except Exception as e:
                logger.error("Error creating primary fallback taskbar info: %s", e, exc_info=True)
                primary_screen = QApplication.primaryScreen()
                return TaskbarInfo(
                    hwnd=0,
                    tray_hwnd=None,
                    rect=(0, 0, 0, 0),
                    screen_name=primary_screen.name() if primary_screen else "",
                    screen_geometry=(0, 0, 0, 0) if not primary_screen else (
                        primary_screen.geometry().left(),
                        primary_screen.geometry().top(),
                        primary_screen.geometry().width(),
                        primary_screen.geometry().height()
                    ),
                    work_area=(0, 0, 0, 0),
                    dpi_scale=1.0,
                    is_primary=True,
                    height=constants.taskbar.DEFAULT_HEIGHT
                )

        def distance_to_taskbar_edge(tb: TaskbarInfo) -> float:
            """Calculates distance from point `pos` to the relevant edge of the taskbar."""
            try:
                edge: constants.TaskbarEdge = tb.get_edge_position()
                rect_phys = tb.rect
                dpi_scale = tb.dpi_scale if tb.dpi_scale > 0 else 1.0

                left_log = rect_phys[0] / dpi_scale
                top_log = rect_phys[1] / dpi_scale
                right_log = rect_phys[2] / dpi_scale
                bottom_log = rect_phys[3] / dpi_scale

                if edge == constants.taskbar.edge.TOP:
                    return abs(pos.y() - bottom_log)
                elif edge == constants.taskbar.edge.BOTTOM:
                    return abs(pos.y() - top_log)
                elif edge == constants.taskbar.edge.LEFT:
                    return abs(pos.x() - right_log)
                elif edge == constants.taskbar.edge.RIGHT:
                    return abs(pos.x() - left_log)
                else:
                    logger.warning("Unknown taskbar edge '%s' for HWND %s. Calculating center distance.", edge, tb.hwnd)
                    center_x = (left_log + right_log) / 2
                    center_y = (top_log + bottom_log) / 2
                    return ((pos.x() - center_x)**2 + (pos.y() - center_y)**2)**0.5

            except Exception as e:
                logger.error("Error calculating distance to taskbar HWND %s: %s", tb.hwnd, e, exc_info=True)
                return float('inf')

        try:
            nearest = min(taskbars, key=distance_to_taskbar_edge)
            logger.debug("Nearest taskbar found: HWND=%s, Edge=%s, Screen='%s'",
                         nearest.hwnd, nearest.get_edge_position(), nearest.screen_name)
            return nearest
        except ValueError:
            logger.warning("Could not determine nearest taskbar after errors, using primary screen fallback.")
            try:
                from .taskbar_utils import create_primary_fallback_taskbar_info
                return create_primary_fallback_taskbar_info()
            except ImportError as e:
                logger.error("Failed to import create_primary_fallback_taskbar_info from taskbar_utils: %s", e)
                primary_screen = QApplication.primaryScreen()
                return TaskbarInfo(
                    hwnd=0,
                    tray_hwnd=None,
                    rect=(0, 0, 0, 0),
                    screen_name=primary_screen.name() if primary_screen else "",
                    screen_geometry=(0, 0, 0, 0) if not primary_screen else (
                        primary_screen.geometry().left(),
                        primary_screen.geometry().top(),
                        primary_screen.geometry().width(),
                        primary_screen.geometry().height()
                    ),
                    work_area=(0, 0, 0, 0),
                    dpi_scale=1.0,
                    is_primary=True,
                    height=constants.taskbar.DEFAULT_HEIGHT
                )
            except Exception as e:
                logger.error("Error creating primary fallback taskbar info: %s", e, exc_info=True)
                primary_screen = QApplication.primaryScreen()
                return TaskbarInfo(
                    hwnd=0,
                    tray_hwnd=None,
                    rect=(0, 0, 0, 0),
                    screen_name=primary_screen.name() if primary_screen else "",
                    screen_geometry=(0, 0, 0, 0) if not primary_screen else (
                        primary_screen.geometry().left(),
                        primary_screen.geometry().top(),
                        primary_screen.geometry().width(),
                        primary_screen.geometry().height()
                    ),
                    work_area=(0, 0, 0, 0),
                    dpi_scale=1.0,
                    is_primary=True,
                    height=constants.taskbar.DEFAULT_HEIGHT
                )


class ScreenUtils:
    """Provides static utility methods for screen-related operations using Qt."""
    @staticmethod
    def find_screen_for_point(point: QPoint) -> Optional[QScreen]:
        """
        Finds the QScreen that contains the given point (logical coordinates).

        Args:
            point: The QPoint to check (in logical coordinates).

        Returns:
            Optional[QScreen]: The screen containing the point, or None if not found.
        """
        return QApplication.screenAt(point)

    @staticmethod
    def find_screen_for_rect(rect: QRect) -> Optional[QScreen]:
        """
        Finds the QScreen that contains the center of the given QRect (logical coordinates).

        Args:
            rect: The QRect to check (in logical coordinates).

        Returns:
            Optional[QScreen]: The screen containing the center, or None.
        """
        return QApplication.screenAt(rect.center())

    @staticmethod
    def validate_position(x: int, y: int, widget_size: Tuple[int, int], screen: QScreen) -> ScreenPosition:
        """
        Adjusts a desired position (logical coordinates) to ensure the widget
        (defined by widget_size) remains fully within the given screen's full geometry.
        Uses screen.geometry() to allow positioning over areas like the taskbar.

        Args:
            x: Desired x-coordinate (logical pixels).
            y: Desired y-coordinate (logical pixels).
            widget_size: Tuple (width, height) of the widget (logical pixels).
            screen: The QScreen object representing the target screen.

        Returns:
            ScreenPosition: The adjusted (validated) position in logical pixels.
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
        """
        Checks if a given position is at least partially visible on the specified screen.
        Uses the full screen geometry to allow for free placement, including over taskbars.
        """
        try:
            # Use the full screen geometry, not just the available work area.
            screen_rect: QRect = screen.geometry()
            widget_width, widget_height = widget_size
            widget_rect = QRect(x, y, widget_width, widget_height)

            # Check if the widget's rectangle intersects with the screen's rectangle.
            return screen_rect.intersects(widget_rect)

        except Exception as e:
            logger.error("Error checking position validity (%s,%s) on screen '%s': %s", x, y, screen.name(), e, exc_info=True)
            return False


# Position Calculation Logic

class PositionCalculator:
    """
    Calculates the optimal widget position relative to a specified taskbar.

    Takes into account the taskbar's edge (top, bottom, left, right), the
    location of the system tray (if available), DPI scaling, and configured padding.
    Also provides logic for constraining drag movements along the taskbar edge.
    """
    def __init__(self) -> None:
        """Initializes the calculator with logging throttle state."""
        self._last_drag_log_time: float = 0.0
        self._drag_log_interval: float = getattr(constants.taskbar.position, 'DRAG_LOG_INTERVAL_SECONDS', 1.0)
        logger.debug("PositionCalculator initialized.")

    def calculate_position(self, taskbar_info: TaskbarInfo, widget_size: Tuple[int, int], config: Dict[str, Any], font_metrics: Optional[QFontMetrics] = None) -> ScreenPosition:
        """
        Calculates the widget's optimal position, respecting other taskbar elements.

        This method implements a robust heuristic to find a "safe zone" on the
        taskbar, bounded by running application icons and the system tray. This
        prevents overlap with elements like the Windows Weather widget, regardless
        of taskbar orientation (horizontal or vertical).

        Args:
            taskbar_info: A dataclass containing detailed info about the target taskbar.
            widget_size: A tuple of the widget's (width, height) in logical pixels.
            config: The application's configuration dictionary.
            font_metrics: Not used in this method but kept for signature consistency.

        Returns:
            A ScreenPosition object with the calculated and validated x, y coordinates.
        """
        try:
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
                offset = config.get('tray_offset_x', constants.layout.DEFAULT_PADDING + 5)
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
                
                # Vertically center the widget in the available "safe" space.
                safe_zone_height = bottom_boundary - top_boundary
                y = top_boundary + (safe_zone_height - widget_height) // 2
            
            else:
                logger.error("Unknown taskbar edge detected: '%s'. Using fallback.", edge)
                return self._get_safe_fallback_position(widget_size)

            return ScreenUtils.validate_position(x, y, widget_size, screen)

        except Exception as e:
            logger.error(f"Error calculating position: {e}", exc_info=True)
            return self._get_safe_fallback_position(widget_size)
        
    
    def constrain_drag_position(self, desired_pos: QPoint, taskbar_info: TaskbarInfo, widget_size_q: QSize, config: Dict[str, Any]) -> Optional[QPoint]:
        """
        Constrains a desired widget position during dragging, ensuring it stays
        within the 'safe zone' between app icons and the system tray for any
        taskbar orientation.
        """
        try:
            screen = taskbar_info.get_screen()
            if not screen:
                raise RuntimeError("No associated QScreen for taskbar.")

            widget_width, widget_height = widget_size_q.width(), widget_size_q.height()
            edge = taskbar_info.get_edge_position()
            dpi_scale = taskbar_info.dpi_scale if taskbar_info.dpi_scale > 0 else 1.0

            if edge in (constants.taskbar.edge.BOTTOM, constants.taskbar.edge.TOP):
                # --- HORIZONTAL CONSTRAINT LOGIC ---
                tb_top_log = round(taskbar_info.rect[1] / dpi_scale)
                tb_height_log = round((taskbar_info.rect[3] - taskbar_info.rect[1]) / dpi_scale)
                fixed_y = tb_top_log + (tb_height_log - widget_height) // 2
                
                right_boundary = (round(taskbar_info.get_tray_rect()[0] / dpi_scale) - widget_width - constants.layout.DEFAULT_PADDING) if taskbar_info.get_tray_rect() else (screen.geometry().right() - widget_width)
                left_boundary = (round(taskbar_info.tasklist_rect[2] / dpi_scale) + constants.layout.DEFAULT_PADDING) if taskbar_info.tasklist_rect else screen.geometry().left()
                
                constrained_x = max(left_boundary, min(desired_pos.x(), right_boundary))
                return QPoint(constrained_x, fixed_y)

            elif edge in (constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT):
                # --- VERTICAL CONSTRAINT LOGIC ---
                tb_left_log = round(taskbar_info.rect[0] / dpi_scale)
                tb_width_log = round((taskbar_info.rect[2] - taskbar_info.rect[0]) / dpi_scale)
                fixed_x = tb_left_log + (tb_width_log - widget_width) // 2
                
                bottom_boundary = (round(taskbar_info.get_tray_rect()[1] / dpi_scale) - widget_height - constants.layout.DEFAULT_PADDING) if taskbar_info.get_tray_rect() else (screen.geometry().bottom() - widget_height)
                top_boundary = (round(taskbar_info.tasklist_rect[3] / dpi_scale) + constants.layout.DEFAULT_PADDING) if taskbar_info.tasklist_rect else screen.geometry().top()
                
                constrained_y = max(top_boundary, min(desired_pos.y(), bottom_boundary))
                return QPoint(fixed_x, constrained_y)
            
            else:
                return desired_pos # Should not happen

        except Exception as e:
            logger.error(f"Error constraining drag position: {e}", exc_info=True)
            return None


    @staticmethod
    def _get_safe_fallback_position(widget_size: Tuple[int, int]) -> ScreenPosition:
        """
        Provides a default fallback position if calculation fails.
        Places the widget near the bottom-right of the primary screen's available geometry.

        Args:
            widget_size: Tuple (width, height) of the widget in logical pixels.

        Returns:
            ScreenPosition: The calculated fallback position in logical pixels.
        """
        try:
            primary_screen: Optional[QScreen] = QApplication.primaryScreen()
            if not primary_screen:
                logger.error("Cannot get fallback position: No primary screen found.")
                return ScreenPosition(0, 0)

            screen_rect: QRect = primary_screen.availableGeometry()
            widget_width, widget_height = widget_size
            margin = constants.taskbar.position.SCREEN_EDGE_MARGIN

            fallback_x = screen_rect.right() - widget_width - margin + 1
            fallback_y = screen_rect.bottom() - widget_height - margin + 1

            fallback_x = max(screen_rect.left(), fallback_x)
            fallback_y = max(screen_rect.top(), fallback_y)

            logger.warning("Using safe fallback position: (%s, %s)", fallback_x, fallback_y)
            return ScreenPosition(fallback_x, fallback_y)

        except Exception as e:
            logger.error("Error calculating safe fallback position: %s", e, exc_info=True)
            return ScreenPosition(0, 0)