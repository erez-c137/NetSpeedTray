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

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.TaskbarUtils")
# Caches to improve performance and prevent log spam for invalid handles
_dpi_cache: dict = {}
_logged_warnings: set = set()

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
        logical_height = constants.taskbar.taskbar.DEFAULT_HEIGHT
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

    def get_edge_position(self) -> constants.TaskbarEdge:
        """
        Determines which edge of the screen the taskbar is docked to (logical coords).

        Returns:
            constants.taskbar.edge: Enum member representing the edge (TOP, BOTTOM, LEFT, RIGHT).
                         Defaults to BOTTOM if calculation fails or is ambiguous.
        """
        try:
            screen = self.get_screen()
            if not screen:
                logger.error("Cannot determine taskbar edge: No valid QScreen for HWND %s.", self.hwnd)
                return constants.taskbar.edge.BOTTOM

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
                    return constants.taskbar.edge.TOP
                elif abs(tb_bottom_log - (screen_rect_log.bottom() + 1)) < tolerance:
                    return constants.taskbar.edge.BOTTOM
            else:
                if abs(tb_left_log - screen_rect_log.left()) < tolerance:
                    return constants.taskbar.edge.LEFT
                elif abs(tb_right_log - (screen_rect_log.right() + 1)) < tolerance:
                    return constants.taskbar.edge.RIGHT

            logger.warning("Taskbar edge position ambiguous for HWND %s. Comparing centers.", self.hwnd)
            taskbar_center_x = (tb_left_log + tb_right_log) / 2
            taskbar_center_y = (tb_top_log + tb_bottom_log) / 2
            screen_center_x = (screen_rect_log.left() + screen_rect_log.right()) / 2
            screen_center_y = (screen_rect_log.top() + screen_rect_log.bottom()) / 2

            if is_horizontal:
                return constants.taskbar.edge.TOP if taskbar_center_y < screen_center_y else constants.taskbar.edge.BOTTOM
            else:
                return constants.taskbar.edge.LEFT if taskbar_center_x < screen_center_x else constants.taskbar.edge.RIGHT

        except Exception as e:
            logger.error("Error calculating taskbar edge position for hwnd %s: %s. Defaulting to BOTTOM.", self.hwnd, e, exc_info=True)
            return constants.taskbar.edge.BOTTOM


# Core Utility Functions
def get_dpi_for_monitor(monitor: Any, hwnd: Optional[int] = None) -> Optional[float]:
    """
    Retrieves the DPI scaling factor for a given monitor using Windows API.
    Caches results and warnings to improve performance and reduce log spam.
    """
    try:
        monitor_id = int(monitor)
    except (TypeError, ValueError):
        monitor_id = 0

    if monitor_id in _dpi_cache:
        return _dpi_cache[monitor_id]

    if monitor_id in _logged_warnings:
        return 1.0  # Return a safe default for already-warned invalid handles

    def get_fallback_dpi() -> float:
        """Helper to get DPI from Qt and log a warning once per invalid handle."""
        logger.warning("Falling back to QScreen for DPI detection for monitor handle '%s'.", monitor_id)
        _logged_warnings.add(monitor_id)
        screen = QApplication.primaryScreen()
        dpi_scale = screen.devicePixelRatio() if screen else 1.0
        _dpi_cache[monitor_id] = dpi_scale
        return dpi_scale

    try:
        MDT_EFFECTIVE_DPI = 0
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        
        monitor_handle = wintypes.HMONITOR(monitor_id)

        result = ctypes.windll.shcore.GetDpiForMonitor(
            monitor_handle,
            MDT_EFFECTIVE_DPI,
            byref(dpi_x),
            byref(dpi_y)
        )
        
        if result != 0:  # S_OK is 0, anything else is an error
            return get_fallback_dpi()

        dpi = dpi_x.value / 96.0
        if dpi <= 0:
            return get_fallback_dpi()

        _dpi_cache[monitor_id] = dpi
        return dpi

    except (AttributeError, NameError):
        # GetDpiForMonitor is not available (e.g., older Windows)
        return get_fallback_dpi()
    except Exception as e:
        logger.error("Unexpected error getting DPI for monitor %s: %s", monitor_id, e, exc_info=True)
        return get_fallback_dpi()


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
            logical_height = int(round(physical_height / dpi_scale)) if dpi_scale > 0 else constants.taskbar.taskbar.DEFAULT_HEIGHT
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
            return constants.taskbar.taskbar.DEFAULT_HEIGHT
        return taskbar_info.height
    except Exception as e:
        logger.error("Error getting taskbar height: %s. Returning default.", e)
        return constants.taskbar.taskbar.DEFAULT_HEIGHT
 
      
      
def is_taskbar_obstructed(taskbar_info: Optional[TaskbarInfo]) -> bool:
    """
    Checks if the taskbar is obstructed by a true fullscreen app or an
    intrusive shell UI element that does not respect the screen's work area.
    """
    try:
        from netspeedtray import constants

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or not win32gui.IsWindow(hwnd) or not taskbar_info:
            return False

        class_name = win32gui.GetClassName(hwnd)
        if class_name in ("Progman", "WorkerW"):  # Ignore the Desktop
            return False

        # --- Context Gathering ---
        try:
            # Ensure the foreground window is on the same monitor as the taskbar
            fg_monitor = win32api.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            tb_monitor = win32api.MonitorFromPoint((taskbar_info.rect[0], taskbar_info.rect[1]), MONITOR_DEFAULTTONEAREST)
            if fg_monitor != tb_monitor:
                return False  # An app on another monitor is not an obstruction

            window_rect = win32gui.GetWindowRect(hwnd)
            monitor_info = win32api.GetMonitorInfo(fg_monitor)
            monitor_rect = monitor_info.get('Monitor')
            work_area_rect = monitor_info.get('Work')
            if not monitor_rect or not work_area_rect:
                return False
        except win32gui.error:
            # Window might have closed while we were checking it.
            return False

        # --- OBSTRUCTION CHECK 1: True Fullscreen ---
        # A window is fullscreen if its rectangle is identical to the monitor's rectangle.
        if window_rect == monitor_rect:
            return True

        # --- OBSTRUCTION CHECK 2: Intrusive Shell UI (Geometric Check) ---
        # Check if the window is a known shell type. We do this first as a quick filter.
        if class_name in constants.shell.shell.UI_CLASS_NAMES_TO_CHECK:
            # An intrusive UI element (like the Start Menu) will not be fully contained
            # within the available work area. A normal app (like Calculator) will be.
            
            # Check if the window's rectangle is completely inside the work area
            is_contained_in_work_area = (
                window_rect[0] >= work_area_rect[0] and  # Left edge
                window_rect[1] >= work_area_rect[1] and  # Top edge
                window_rect[2] <= work_area_rect[2] and  # Right edge
                window_rect[3] <= work_area_rect[3]      # Bottom edge
            )

            # If it's NOT contained, it's an intrusive element like the Start Menu.
            if not is_contained_in_work_area:
                return True

        # If it's not fullscreen and it's a normal app respecting the work area, it's not an obstruction.
        return False

    except Exception as e:
        logger.error(f"Unexpected error in is_taskbar_obstructed: {e}", exc_info=True)
        return False
    

def is_taskbar_visible(taskbar_info: Optional[TaskbarInfo]) -> bool:
    """
    Checks if the specified taskbar is currently visible on screen.
    This function is robust for auto-hiding taskbars.
    """
    ABM_GETSTATE = 0x00000004
    ABS_AUTOHIDE = 0x00000001

    if not taskbar_info or not taskbar_info.hwnd or not win32gui.IsWindow(taskbar_info.hwnd):
        return False

    try:
        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(abd)
        abd.hWnd = taskbar_info.hwnd
        state_flags = windll.shell32.SHAppBarMessage(ABM_GETSTATE, byref(abd))
        auto_hide_enabled = bool(state_flags & ABS_AUTOHIDE)

        if not auto_hide_enabled:
            return True # If auto-hide is off, it's always visible.

        # If auto-hide is ON, we must check its actual position.
        screen = taskbar_info.get_screen()
        if not screen:
            return False

        screen_geo = screen.geometry()
        dpi = taskbar_info.dpi_scale
        screen_rect_phys = (
            int(screen_geo.left() * dpi), int(screen_geo.top() * dpi),
            int(screen_geo.right() * dpi) + 1, int(screen_geo.bottom() * dpi) + 1
        )
        tb_rect_phys = taskbar_info.rect
        edge = taskbar_info.get_edge_position()

        if edge == constants.taskbar.edge.BOTTOM and tb_rect_phys[1] >= screen_rect_phys[3] - 5: return False
        if edge == constants.taskbar.edge.TOP and tb_rect_phys[3] <= screen_rect_phys[1] + 5: return False
        if edge == constants.taskbar.edge.LEFT and tb_rect_phys[2] <= screen_rect_phys[0] + 5: return False
        if edge == constants.taskbar.edge.RIGHT and tb_rect_phys[0] >= screen_rect_phys[2] - 5: return False

        return True # If not in a hidden position, it's visible.

    except Exception as e:
        logger.error(f"Error checking taskbar visibility for HWND={taskbar_info.hwnd}: {e}", exc_info=True)
        return False