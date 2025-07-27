"""
Taskbar Utilities for Windows System Integration.

Provides functions and data structures for detecting taskbar properties such as
position, visibility, state, associated screen, DPI, and system tray location.
Relies on Windows API calls via pywin32 and ctypes.
"""

import logging
import ctypes
from ctypes import wintypes, windll, byref, Structure
from dataclasses import dataclass
from typing import Tuple, Optional, List, Any

# Win32 Imports
import win32gui
import win32api
import win32con
from win32con import (
    MONITOR_DEFAULTTONEAREST, MONITORINFOF_PRIMARY
)

# Qt Imports
from PyQt6.QtCore import QRect, QPoint
from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import QApplication

# Local Imports
from ..constants.constants import TaskbarEdge, TaskbarConstants

logger = logging.getLogger("NetSpeedTray.TaskbarUtils")


# Windows API Structures
class RECT(ctypes.Structure):
    """Ctypes structure representing a Windows RECT."""
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class APPBARDATA(ctypes.Structure):
    """Ctypes structure for SHAppBarMessage function (used for auto-hide state)."""
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", RECT),
        ("lParam", wintypes.LPARAM),
    ]


# Dataclasses
@dataclass(slots=True)
class TaskbarInfo:
    """
    Holds comprehensive information about a detected taskbar.

    Attributes:
        hwnd: Window handle (HWND) of the taskbar window.
        tray_hwnd: Optional HWND of the system tray area ('TrayNotifyWnd').
        rect: Taskbar bounding rectangle (left, top, right, bottom) in PHYSICAL screen coordinates.
        screen_name: Name of the QScreen associated with the taskbar's monitor.
        screen_geometry: Geometry of the screen (left, top, width, height) in LOGICAL coordinates.
        work_area: Available work area on the monitor (left, top, right, bottom) in PHYSICAL coordinates.
        dpi_scale: DPI scaling factor (e.g., 1.0, 1.5, 2.0) for the associated screen.
        is_primary: True if this taskbar is on the primary monitor.
        height: Calculated height of the taskbar in LOGICAL pixels (physical height / dpi_scale).
    """
    hwnd: int
    tray_hwnd: Optional[int]
    rect: Tuple[int, int, int, int]
    screen_name: str
    screen_geometry: Tuple[int, int, int, int]  # (left, top, width, height) in logical coords
    work_area: Tuple[int, int, int, int]
    dpi_scale: float
    is_primary: bool
    height: int

    def __post_init__(self) -> None:
        """Basic validation of critical attributes after initialization."""
        if not (isinstance(self.rect, tuple) and len(self.rect) == 4):
            raise ValueError(f"TaskbarInfo.rect invalid: {self.rect}")
        if not (isinstance(self.work_area, tuple) and len(self.work_area) == 4):
            raise ValueError(f"TaskbarInfo.work_area invalid: {self.work_area}")
        if not (isinstance(self.screen_geometry, tuple) and len(self.screen_geometry) == 4):
            raise ValueError(f"TaskbarInfo.screen_geometry invalid: {self.screen_geometry}")
        if self.dpi_scale <= 0:
            raise ValueError(f"TaskbarInfo.dpi_scale must be positive: {self.dpi_scale}")
        if self.height <= 0 and self.hwnd != 0:
            raise ValueError(f"TaskbarInfo.height must be positive for non-fallback taskbar: {self.height}")
        logger.debug("TaskbarInfo initialized for HWND: %s (Primary: %s)", self.hwnd, self.is_primary)

    def get_screen(self) -> Optional[QScreen]:
        """
        Fetches a fresh QScreen object based on the stored screen name and geometry.

        Returns:
            Optional[QScreen]: The associated QScreen, or None if not found.
        """
        try:
            all_screens = QApplication.screens()
            for screen in all_screens:
                if screen.name() == self.screen_name:
                    # Verify the geometry matches to ensure it's the same screen
                    geo = screen.geometry()
                    stored_geo = self.screen_geometry
                    if (geo.left() == stored_geo[0] and geo.top() == stored_geo[1] and
                            geo.width() == stored_geo[2] and geo.height() == stored_geo[3]):
                        return screen
            # Fallback: Try to find a screen at the taskbar's position
            tb_rect = self.rect
            point = QPoint(tb_rect[0], tb_rect[1])
            screen = QApplication.screenAt(point)
            if screen:
                return screen
            logger.warning("Could not find QScreen for taskbar HWND %s (name: %s).", self.hwnd, self.screen_name)
            return None
        except Exception as e:
            logger.error("Error fetching QScreen for taskbar HWND %s: %s", self.hwnd, e, exc_info=True)
            return None

    @staticmethod
    def create_primary_fallback_taskbar_info() -> 'TaskbarInfo':
        """
        Creates a fallback TaskbarInfo using the primary screen's details.
        Used when automatic taskbar detection fails entirely.

        Returns:
            TaskbarInfo: A default TaskbarInfo instance based on the primary screen.

        Raises:
            RuntimeError: If no primary screen can be obtained from QApplication.
        """
        logger.warning("Creating fallback TaskbarInfo using primary screen details.")
        primary_screen = QApplication.primaryScreen()
        if not primary_screen:
            logger.critical("No primary screen available to create fallback TaskbarInfo.")
            raise RuntimeError("Cannot create fallback TaskbarInfo: No primary screen detected.")

        dpi_scale = get_dpi_for_monitor(win32api.MonitorFromPoint((0, 0), MONITOR_DEFAULTTONEAREST))
        if dpi_scale is None:
            dpi_scale = primary_screen.devicePixelRatio()
            logger.debug("Using QScreen.devicePixelRatio for DPI in fallback: %s", dpi_scale)
        dpi_scale = dpi_scale if dpi_scale > 0 else 1.0

        work_area_qrect: QRect = primary_screen.availableGeometry()
        work_area_physical = (
            int(round(work_area_qrect.left() * dpi_scale)),
            int(round(work_area_qrect.top() * dpi_scale)),
            int(round(work_area_qrect.right() * dpi_scale)) + 1,
            int(round(work_area_qrect.bottom() * dpi_scale)) + 1,
        )
        logical_height = TaskbarConstants.DEFAULT_HEIGHT
        screen_geo = primary_screen.geometry()

        return TaskbarInfo(
            hwnd=0,
            tray_hwnd=None,
            rect=(0, 0, 0, 0),
            screen_name=primary_screen.name(),
            screen_geometry=(screen_geo.left(), screen_geo.top(), screen_geo.width(), screen_geo.height()),
            work_area=work_area_physical,
            dpi_scale=dpi_scale,
            is_primary=True,
            height=logical_height
        )

    def get_tray_rect(self) -> Optional[Tuple[int, int, int, int]]:
        """
        Retrieves the bounding rectangle of the system tray area (physical coords).

        Returns:
            Optional[Tuple[int, int, int, int]]: Tray rectangle (left, top, right, bottom)
                                                 in physical coordinates, or None if unavailable/invalid.
        """
        if not self.tray_hwnd:
            return None
        try:
            if not win32gui.IsWindow(self.tray_hwnd):
                logger.warning("Failed to get tray rectangle for taskbar %s: Tray HWND %s is invalid.", self.hwnd, self.tray_hwnd)
                return None
            rect = win32gui.GetWindowRect(self.tray_hwnd)
            return rect
        except win32gui.error as e:
            logger.error("Failed to get tray rectangle for taskbar %s (Tray HWND %s): %s", self.hwnd, self.tray_hwnd, e)
            return None
        except Exception as e:
            logger.error("Unexpected error getting tray rectangle for taskbar %s (Tray HWND %s): %s", self.hwnd, self.tray_hwnd, e)
            return None

    def get_edge_position(self) -> TaskbarEdge:
        """
        Determines which edge of the screen the taskbar is docked to (logical coords).

        Returns:
            TaskbarEdge: Enum member representing the edge (TOP, BOTTOM, LEFT, RIGHT).
                         Defaults to BOTTOM if calculation fails or is ambiguous.
        """
        try:
            screen = self.get_screen()
            if not screen:
                logger.error("Cannot determine taskbar edge: No valid QScreen for HWND %s.", self.hwnd)
                return TaskbarEdge.BOTTOM

            screen_rect_log: QRect = screen.geometry()
            dpi_scale = self.dpi_scale

            tb_left_log = self.rect[0] / dpi_scale
            tb_top_log = self.rect[1] / dpi_scale
            tb_right_log = self.rect[2] / dpi_scale
            tb_bottom_log = self.rect[3] / dpi_scale

            tb_width_log = tb_right_log - tb_left_log
            tb_height_log = tb_bottom_log - tb_top_log

            is_horizontal = tb_width_log > tb_height_log

            tolerance = 5

            if is_horizontal:
                if abs(tb_top_log - screen_rect_log.top()) < tolerance:
                    return TaskbarEdge.TOP
                elif abs(tb_bottom_log - (screen_rect_log.bottom() + 1)) < tolerance:
                    return TaskbarEdge.BOTTOM
            else:
                if abs(tb_left_log - screen_rect_log.left()) < tolerance:
                    return TaskbarEdge.LEFT
                elif abs(tb_right_log - (screen_rect_log.right() + 1)) < tolerance:
                    return TaskbarEdge.RIGHT

            logger.warning("Taskbar edge position ambiguous for HWND %s. Comparing centers.", self.hwnd)
            taskbar_center_x = (tb_left_log + tb_right_log) / 2
            taskbar_center_y = (tb_top_log + tb_bottom_log) / 2
            screen_center_x = (screen_rect_log.left() + screen_rect_log.right()) / 2
            screen_center_y = (screen_rect_log.top() + screen_rect_log.bottom()) / 2

            if is_horizontal:
                return TaskbarEdge.TOP if taskbar_center_y < screen_center_y else TaskbarEdge.BOTTOM
            else:
                return TaskbarEdge.LEFT if taskbar_center_x < screen_center_x else TaskbarEdge.RIGHT

        except Exception as e:
            logger.error("Error calculating taskbar edge position for hwnd %s: %s. Defaulting to BOTTOM.", self.hwnd, e, exc_info=True)
            return TaskbarEdge.BOTTOM


# Core Utility Functions
def get_dpi_for_monitor(monitor: Any, hwnd: Optional[int] = None) -> Optional[float]:
    """
    Retrieves the DPI scaling factor for a given monitor using Windows API.

    Validates the monitor handle and caches results to reduce redundant calls.
    Falls back to QScreen if the handle is invalid or the API call fails.

    Args:
        monitor: Monitor handle (HMONITOR) from win32api, typically a PyHANDLE object.
        hwnd: Optional window handle (HWND) for context in logging (e.g., taskbar HWND).

    Returns:
        Optional[float]: DPI scaling factor (e.g., 1.0 for 96 DPI, 1.5 for 144 DPI).
                        Returns None if the API call fails or the handle is invalid.
    """
    context = f" for HWND {hwnd} (Class: {win32gui.GetClassName(hwnd)}, Title: {win32gui.GetWindowText(hwnd)})" if hwnd and win32gui.IsWindow(hwnd) else ""
    _dpi_cache: dict = {}  # Static cache for monitor handles
    _logged_warnings: set = set()  # Track warned handles to avoid log spam

    # Convert monitor handle to integer for consistent comparison
    try:
        monitor_id = int(monitor) if monitor else 0
    except (TypeError, ValueError) as e:
        monitor_id = 0
        if monitor_id not in _logged_warnings:
            logger.warning("Invalid monitor handle: %s%s: %s. Falling back to QScreen.", monitor, context, str(e))
            _logged_warnings.add(monitor_id)
        screen = QApplication.primaryScreen()
        dpi_scale = screen.devicePixelRatio() if screen else 1.0
        _dpi_cache[monitor_id] = dpi_scale
        return dpi_scale

    if monitor_id in _dpi_cache:
        return _dpi_cache[monitor_id]

    # Validate monitor handle
    if not monitor or monitor_id == 0:
        if monitor_id not in _logged_warnings:
            logger.warning("Invalid monitor handle: %s%s. Falling back to QScreen.", monitor, context)
            _logged_warnings.add(monitor_id)
        screen = QApplication.primaryScreen()
        dpi_scale = screen.devicePixelRatio() if screen else 1.0
        _dpi_cache[monitor_id] = dpi_scale
        return dpi_scale

    # Check if monitor handle is valid by comparing against active monitors
    active_monitors = [int(m[0]) for m in win32api.EnumDisplayMonitors(None, None)]
    if monitor_id not in active_monitors:
        if monitor_id not in _logged_warnings:
            logger.warning("Monitor handle %s (int: %d) not found in active monitors %s%s. Falling back to QScreen.",
                           monitor, monitor_id, active_monitors, context)
            _logged_warnings.add(monitor_id)
        screen = QApplication.primaryScreen()
        dpi_scale = screen.devicePixelRatio() if screen else 1.0
        _dpi_cache[monitor_id] = dpi_scale
        return dpi_scale

    try:
        # Validate monitor handle using GetMonitorInfo
        monitor_info = win32api.GetMonitorInfo(monitor)
        if not monitor_info:
            if monitor_id not in _logged_warnings:
                logger.warning("GetMonitorInfo failed for monitor handle: %s%s. Falling back to QScreen.", monitor, context)
                _logged_warnings.add(monitor_id)
            screen = QApplication.primaryScreen()
            dpi_scale = screen.devicePixelRatio() if screen else 1.0
            _dpi_cache[monitor_id] = dpi_scale
            return dpi_scale

        MDT_EFFECTIVE_DPI = 0
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        # Explicitly cast PyHANDLE to HMONITOR for GetDpiForMonitor
        monitor_handle = wintypes.HMONITOR(monitor_id)
        result = ctypes.windll.shcore.GetDpiForMonitor(
            monitor_handle,
            MDT_EFFECTIVE_DPI,
            byref(dpi_x),
            byref(dpi_y)
        )
        if result != 0:
            if monitor_id not in _logged_warnings:
                logger.warning("GetDpiForMonitor failed with HRESULT %s for monitor %s%s. Falling back to QScreen.", result, monitor, context)
                _logged_warnings.add(monitor_id)
            screen = QApplication.primaryScreen()
            dpi_scale = screen.devicePixelRatio() if screen else 1.0
            _dpi_cache[monitor_id] = dpi_scale
            return dpi_scale

        dpi = dpi_x.value / 96.0
        if dpi <= 0:
            if monitor_id not in _logged_warnings:
                logger.warning("Invalid DPI value %s for monitor %s%s. Falling back to QScreen.", dpi, monitor, context)
                _logged_warnings.add(monitor_id)
            screen = QApplication.primaryScreen()
            dpi_scale = screen.devicePixelRatio() if screen else 1.0
            _dpi_cache[monitor_id] = dpi_scale
            return dpi_scale

        logger.debug("DPI for monitor %s%s: %s (scale: %s)", monitor, context, dpi_x.value, dpi)
        _dpi_cache[monitor_id] = dpi
        return dpi

    except AttributeError:
        if monitor_id not in _logged_warnings:
            logger.warning("GetDpiForMonitor not available (requires Windows 8.1+)%s. Falling back to QScreen.", context)
            _logged_warnings.add(monitor_id)
        screen = QApplication.primaryScreen()
        dpi_scale = screen.devicePixelRatio() if screen else 1.0
        _dpi_cache[monitor_id] = dpi_scale
        return dpi_scale
    except Exception as e:
        if monitor_id not in _logged_warnings:
            logger.warning("Error getting DPI for monitor %s%s: %s. Falling back to QScreen.", monitor, context, str(e))
            _logged_warnings.add(monitor_id)
        screen = QApplication.primaryScreen()
        dpi_scale = screen.devicePixelRatio() if screen else 1.0
        _dpi_cache[monitor_id] = dpi_scale
        return dpi_scale


def get_all_taskbar_info() -> List[TaskbarInfo]:
    """
    Finds all taskbars (primary and secondary) and gathers their details.

    First attempts to find taskbars directly using FindWindow for efficiency.
    Falls back to EnumWindows if no taskbars are found.

    Returns:
        List[TaskbarInfo]: List of found taskbars. Returns a list containing only
                           a fallback TaskbarInfo if none are detected.
    """
    taskbars: List[TaskbarInfo] = []
    primary_screen = QApplication.primaryScreen()
    all_screens = QApplication.screens()

    def find_screen_for_taskbar(tb_rect_phys: Tuple[int, int, int, int]) -> Optional[QScreen]:
        """
        Finds the QScreen that best matches the taskbar's physical rectangle.

        Args:
            tb_rect_phys: Taskbar rectangle (left, top, right, bottom) in physical coordinates.

        Returns:
            Optional[QScreen]: The best matching QScreen, or primary screen as fallback.
        """
        best_match_screen: Optional[QScreen] = None
        max_overlap: int = 0

        tb_qrect_phys = QRect(tb_rect_phys[0], tb_rect_phys[1],
                              tb_rect_phys[2] - tb_rect_phys[0], tb_rect_phys[3] - tb_rect_phys[1])

        for screen in all_screens:
            geo_log = screen.geometry()
            dpi = screen.devicePixelRatio()
            geo_phys = QRect(int(round(geo_log.left() * dpi)), int(round(geo_log.top() * dpi)),
                             int(round(geo_log.width() * dpi)), int(round(geo_log.height() * dpi)))

            overlap_rect = geo_phys.intersected(tb_qrect_phys)
            overlap_area = overlap_rect.width() * overlap_rect.height()

            if overlap_area > max_overlap:
                max_overlap = overlap_area
                best_match_screen = screen

        if not best_match_screen:
            best_match_screen = QApplication.screenAt(QRect(tb_rect_phys[0], tb_rect_phys[1], 1, 1).topLeft())

        return best_match_screen if best_match_screen else primary_screen

    def process_taskbar(hwnd: int) -> Optional[TaskbarInfo]:
        """
        Processes a single taskbar window and constructs a TaskbarInfo object.

        Validates the taskbar's HWND, determines its associated monitor using physical coordinates,
        and constructs a TaskbarInfo object with screen and DPI information.

        Args:
            hwnd: Window handle (HWND) of the potential taskbar.

        Returns:
            Optional[TaskbarInfo]: TaskbarInfo if valid, None otherwise.
        """
        try:
            # Validate HWND
            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                logger.debug("Skipping HWND %s: Not a valid or visible window.", hwnd)
                return None

            class_name = win32gui.GetClassName(hwnd)
            if class_name not in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                logger.debug("Skipping HWND %s: Class '%s' is not a taskbar.", hwnd, class_name)
                return None

            rect_phys = win32gui.GetWindowRect(hwnd)
            tray_hwnd = win32gui.FindWindowEx(hwnd, 0, "TrayNotifyWnd", None) or None

            # Find associated QScreen
            screen = find_screen_for_taskbar(rect_phys)
            if not screen:
                logger.error("Could not determine QScreen for taskbar HWND %s (Class: %s, Title: %s, Rect: %s)", 
                            hwnd, class_name, win32gui.GetWindowText(hwnd), rect_phys)
                return None

            # Get monitor handle using taskbar's top-left corner
            monitor = win32api.MonitorFromPoint((rect_phys[0], rect_phys[1]), MONITOR_DEFAULTTONEAREST)
            if not monitor or int(monitor) == 0:
                logger.warning("MonitorFromPoint returned invalid handle for taskbar HWND %s (Class: %s, Title: %s, Rect: %s).", 
                            hwnd, class_name, win32gui.GetWindowText(hwnd), rect_phys)
                return None

            # Log active monitors and validate handle
            active_monitors = [int(m[0]) for m in win32api.EnumDisplayMonitors(None, None)]
            monitor_int = int(monitor)
            logger.debug("Active monitors: %s, Current monitor handle: %s (int: %d) for HWND %s (Rect: %s)", 
                        active_monitors, monitor, monitor_int, hwnd, rect_phys)

            # Validate monitor handle (log warning but proceed if GetMonitorInfo succeeds)
            if monitor_int not in active_monitors:
                logger.warning("Monitor handle %d not found in active monitors %s for HWND %s (Class: %s, Title: %s, Rect: %s). Proceeding to GetMonitorInfo.", 
                            monitor_int, active_monitors, hwnd, class_name, win32gui.GetWindowText(hwnd), rect_phys)

            # Get monitor info
            monitor_info = win32api.GetMonitorInfo(monitor)
            if not monitor_info:
                logger.error("GetMonitorInfo failed for monitor handle %d for HWND %s (Class: %s, Title: %s, Rect: %s).", 
                            monitor_int, hwnd, class_name, win32gui.GetWindowText(hwnd), rect_phys)
                return None

            logger.debug("Monitor info retrieved successfully for handle %d: %s", monitor_int, monitor_info)

            work_area_phys = monitor_info.get("Work", (0, 0, 0, 0))
            if work_area_phys == (0, 0, 0, 0):
                logger.warning("Could not get 'Work' area for monitor of taskbar HWND %s. Using screen geometry.", hwnd)
                geo_log = screen.geometry()
                dpi = screen.devicePixelRatio()
                work_area_phys = (
                    int(round(geo_log.left() * dpi)),
                    int(round(geo_log.top() * dpi)),
                    int(round(geo_log.right() * dpi)) + 1,
                    int(round(geo_log.bottom() * dpi)) + 1
                )

            # Get DPI scaling
            dpi_scale = get_dpi_for_monitor(monitor, hwnd)
            if dpi_scale is None:
                dpi_scale = screen.devicePixelRatio()
                logger.debug("Using QScreen.devicePixelRatio for DPI: %s", dpi_scale)
            dpi_scale = dpi_scale if dpi_scale > 0 else 1.0

            # Calculate logical height
            physical_height = rect_phys[3] - rect_phys[1]
            logical_height = int(round(physical_height / dpi_scale)) if dpi_scale > 0 else TaskbarConstants.DEFAULT_HEIGHT
            if physical_height > 0 and logical_height <= 0:
                logical_height = 1

            # Check primary monitor consistency
            is_primary_monitor = bool(monitor_info.get('Flags', 0) & MONITORINFOF_PRIMARY)
            is_primary_qt = (screen == primary_screen)
            if is_primary_monitor != is_primary_qt:
                logger.warning("Win32 primary monitor flag differs from Qt primary screen for HWND %s. Using Qt's.", hwnd)

            # Construct TaskbarInfo
            screen_geo = screen.geometry()
            taskbar_info = TaskbarInfo(
                hwnd=hwnd,
                tray_hwnd=tray_hwnd,
                rect=rect_phys,
                screen_name=screen.name(),
                screen_geometry=(screen_geo.left(), screen_geo.top(), screen_geo.width(), screen_geo.height()),
                work_area=work_area_phys,
                dpi_scale=dpi_scale,
                is_primary=is_primary_qt,
                height=logical_height
            )
            logger.debug("Found taskbar: HWND=%s, Class=%s, Rect=%s, Screen=%s, DPI=%.2f, Primary=%s, LogicalH=%d",
                        hwnd, class_name, rect_phys, screen.name(), dpi_scale, is_primary_qt, logical_height)
            return taskbar_info

        except win32gui.error as e:
            if e.winerror != 0:
                logger.warning("win32gui error during taskbar processing for HWND %s (Class: %s, Title: %s): %s", 
                            hwnd, win32gui.GetClassName(hwnd), win32gui.GetWindowText(hwnd), e)
            return None
        except Exception as e:
            logger.error("Unexpected error during taskbar processing for HWND %s (Class: %s, Title: %s): %s", 
                        hwnd, win32gui.GetClassName(hwnd), win32gui.GetWindowText(hwnd), e)
            return None

    try:
        # Try direct FindWindow for efficiency
        for class_name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            hwnd = win32gui.FindWindow(class_name, None)
            if hwnd:
                taskbar_info = process_taskbar(hwnd)
                if taskbar_info:
                    taskbars.append(taskbar_info)
    except Exception as e:
        logger.warning("Error using FindWindow to locate taskbars: %s. Falling back to EnumWindows.", e)

    if not taskbars:
        logger.debug("No taskbars found via FindWindow. Falling back to EnumWindows.")
        def enum_callback(hwnd: int, _: Any) -> bool:
            """
            Callback for EnumWindows to process potential taskbar windows.

            Args:
                hwnd: Window handle being enumerated.
                lparam: User-defined parameter (unused).

            Returns:
                bool: True to continue enumeration, False to stop.
            """
            taskbar_info = process_taskbar(hwnd)
            if taskbar_info:
                taskbars.append(taskbar_info)
            return True

        try:
            win32gui.EnumWindows(enum_callback, None)
        except Exception as e:
            logger.error("Failed to execute EnumWindows: %s", e, exc_info=True)
            return [TaskbarInfo.create_primary_fallback_taskbar_info()]

    if not taskbars:
        logger.warning("No taskbars found. Returning fallback.")
        return [TaskbarInfo.create_primary_fallback_taskbar_info()]

    return taskbars


def get_taskbar_info() -> TaskbarInfo:
    """
    Retrieves information about the primary taskbar.

    Selects the taskbar marked as primary from the list returned by
    `get_all_taskbar_info()`. If no primary is explicitly found, it defaults
    to the first taskbar in the list (which includes fallback handling).

    Returns:
        TaskbarInfo: Details of the primary taskbar (or fallback).
    """
    try:
        all_taskbars = get_all_taskbar_info()
        primary_taskbar = next((tb for tb in all_taskbars if tb.is_primary), all_taskbars[0])
        logger.debug("Primary taskbar selected: HWND=%s (Is fallback: %s)", primary_taskbar.hwnd, primary_taskbar.hwnd == 0)
        return primary_taskbar
    except Exception as e:
        logger.error("Error selecting primary taskbar info: %s. Returning fallback.", e, exc_info=True)
        try:
            return TaskbarInfo.create_primary_fallback_taskbar_info()
        except RuntimeError:
            raise


def get_taskbar_height() -> int:
    """
    Gets the logical height of the primary taskbar.

    Returns:
        int: Height in logical pixels. Returns default if detection fails.
    """
    try:
        taskbar_info = get_taskbar_info()
        if taskbar_info.hwnd == 0 and taskbar_info.height <= 0:
            logger.warning("Using default taskbar height as detection failed.")
            return TaskbarConstants.DEFAULT_HEIGHT
        return taskbar_info.height
    except Exception as e:
        logger.error("Error getting taskbar height: %s. Returning default.", e)
        return TaskbarConstants.DEFAULT_HEIGHT


def is_fullscreen_active(taskbar_info: Optional[TaskbarInfo]) -> bool:
    """
    Checks if the active foreground window is fullscreen on the same monitor as the given taskbar,
    explicitly excluding the Desktop window itself.

    Compares the foreground window's dimensions (physical) to its monitor's dimensions (physical),
    only if the window is not the desktop and is on the same monitor as the taskbar.

    Args:
        taskbar_info: TaskbarInfo for the taskbar to check against, or None.

    Returns:
        bool: True if a non-desktop fullscreen app is detected on the taskbar's monitor,
              False otherwise or on error.
    """
    try:
        if not taskbar_info:
            logger.debug("No valid TaskbarInfo provided for fullscreen check.")
            return False

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or not win32gui.IsWindow(hwnd):
            logger.debug("No valid foreground window found for fullscreen check.")
            return False

        class_name = win32gui.GetClassName(hwnd)
        if class_name in ("Progman", "WorkerW"):
            logger.debug("Foreground window is the Desktop (%s), not considered fullscreen.", class_name)
            return False

        fg_monitor = win32api.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not fg_monitor:
            logger.warning("Could not get monitor for foreground window HWND %s.", hwnd)
            return False

        tb_monitor = win32api.MonitorFromWindow(taskbar_info.hwnd, MONITOR_DEFAULTTONEAREST) if taskbar_info.hwnd else None
        if not tb_monitor:
            tb_rect = taskbar_info.rect
            tb_monitor = win32api.MonitorFromPoint((tb_rect[0], tb_rect[1]), MONITOR_DEFAULTTONEAREST)
            if not tb_monitor:
                logger.warning("Could not get monitor for taskbar HWND %s.", taskbar_info.hwnd)
                return False

        if fg_monitor != tb_monitor:
            logger.debug("Foreground window (HWND %s) is on a different monitor than taskbar (HWND %s). Not fullscreen for this taskbar.", hwnd, taskbar_info.hwnd)
            return False

        window_rect = win32gui.GetWindowRect(hwnd)
        monitor_info = win32api.GetMonitorInfo(fg_monitor)
        monitor_rect = monitor_info.get('Monitor')
        if not monitor_rect:
            logger.warning("Could not get Monitor rect for foreground window HWND %s.", hwnd)
            return False

        is_fullscreen = (window_rect == monitor_rect)

        if is_fullscreen:
            logger.debug(
                "Fullscreen check: FG Win=%s ('%s', Class='%s'), WinRect=%s, MonRect=%s, Monitor=%s, TaskbarMonitor=%s -> True",
                hwnd, win32gui.GetWindowText(hwnd), class_name, window_rect, monitor_rect, fg_monitor, tb_monitor
            )

        return is_fullscreen

    except win32gui.error as e:
        if e.winerror != 0:
            logger.warning("win32gui error checking fullscreen: %s", e)
        return False
    except Exception as e:
        logger.error("Unexpected error checking fullscreen: %s", e, exc_info=True)
        return False


def is_taskbar_visible(taskbar_info: Optional[TaskbarInfo]) -> bool:
    """
    Checks if the specified taskbar is currently visible (i.e., not auto-hidden).

    Uses the SHAppBarMessage Windows API function (ABM_GETSTATE) to query the
    taskbar's state flags and checks for the ABS_AUTOHIDE flag.

    Args:
        taskbar_info: TaskbarInfo for the taskbar to check, or None.

    Returns:
        bool: True if the taskbar is valid and NOT auto-hidden, False otherwise.
    """
    ABM_GETSTATE = 0x00000004
    ABS_AUTOHIDE = 0x00000001

    if not taskbar_info or not taskbar_info.hwnd:
        return False

    if not win32gui.IsWindow(taskbar_info.hwnd):
        logger.warning("Taskbar HWND=%s is invalid for visibility check.", taskbar_info.hwnd)
        return False

    try:
        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(abd)
        abd.hWnd = taskbar_info.hwnd

        state_flags = windll.shell32.SHAppBarMessage(ABM_GETSTATE, byref(abd))
        is_currently_autohidden = bool(state_flags & ABS_AUTOHIDE)
        return not is_currently_autohidden

    except AttributeError as e:
        logger.error("API call failed for SHAppBarMessage (check shell32 availability?): %s", e, exc_info=True)
        return False
    except Exception as e:
        logger.error("Error checking taskbar state via SHAppBarMessage for HWND=%s: %s", taskbar_info.hwnd, e, exc_info=True)
        return False