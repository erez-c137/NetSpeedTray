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
import psutil

# Win32 Imports
import win32gui
import win32api
import win32con
import win32process
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


def get_process_name_from_hwnd(hwnd: int) -> Optional[str]:
    """Gets the executable name of the process that owns the given window handle."""
    try:
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == 0:
            return None
        process = psutil.Process(pid)
        return process.name().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, win32gui.error):
        return None # Gracefully handle cases where process info isn't available
    except Exception as e:
        logger.error(f"Error getting process name for HWND {hwnd}: {e}")
        return None


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


def find_tasklist_rect(taskbar_hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """
    Finds the bounding rectangle of the task list (running app icons) area on the taskbar.

    This is the most reliable way to determine the left-hand boundary for our widget.
    """
    try:
        # The hierarchy is Shell_TrayWnd -> ReBarWindow32 -> MSTaskSwWClass -> ToolbarWindow32
        rebar_hwnd = win32gui.FindWindowEx(taskbar_hwnd, 0, "ReBarWindow32", None)
        if not rebar_hwnd:
            return None
        
        tasklist_hwnd = win32gui.FindWindowEx(rebar_hwnd, 0, "MSTaskSwWClass", None)
        if not tasklist_hwnd:
            return None
        
        # The final container for the icons is a ToolbarWindow32
        toolbar_hwnd = win32gui.FindWindowEx(tasklist_hwnd, 0, "ToolbarWindow32", None)
        if not toolbar_hwnd:
            return None
            
        return win32gui.GetWindowRect(toolbar_hwnd)
    except Exception as e:
        logger.error(f"Error finding tasklist rect for taskbar HWND {taskbar_hwnd}: {e}")
        return None


# Dataclasses
@dataclass(slots=True)
class TaskbarInfo:
    """
    Holds comprehensive information about a detected taskbar.
    """
    hwnd: int
    tray_hwnd: Optional[int]
    tasklist_rect: Optional[Tuple[int, int, int, int]]
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
        logical_height = constants.taskbar.DEFAULT_HEIGHT
        screen_geo = primary_screen.geometry()

        return TaskbarInfo(
            hwnd=0,
            tray_hwnd=None,
            tasklist_rect=None,
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

    This function is robust for complex multi-monitor and virtualized (RDP)
    environments by using a multi-layered geometric heuristic to find the
    correct screen for each taskbar instead of relying on fragile name matching.

    Returns:
        List[TaskbarInfo]: List of found taskbars. Returns a list containing only
                           a fallback TaskbarInfo if none are detected.
    """
    taskbars: List[TaskbarInfo] = []
    primary_screen = QApplication.primaryScreen()
    all_screens = QApplication.screens()


    def find_screen_for_taskbar(tb_rect_phys: Tuple[int, int, int, int]) -> Optional[QScreen]:
        """
        Finds the QScreen that a taskbar belongs to using a robust, multi-layered
        geometric heuristic.
        """
        # --- Layer 1: Find the screen with the largest geometric intersection (most reliable) ---
        best_match_screen: Optional[QScreen] = None
        max_overlap: int = -1

        try:
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
            
            if best_match_screen:
                logger.debug(f"Found best screen match for taskbar via intersection: {best_match_screen.name()}")
                return best_match_screen
        except Exception as e:
            logger.error(f"Error during screen intersection check: {e}. Proceeding to fallbacks.")

        # --- Layer 2: Fallback to a point-based check ---
        try:
            point = QPoint(tb_rect_phys[0], tb_rect_phys[1])
            screen_at_point = QApplication.screenAt(point)
            if screen_at_point:
                logger.warning(f"Used point-based fallback to find screen: {screen_at_point.name()}")
                return screen_at_point
        except Exception as e:
            logger.error(f"Error during point-based screen check: {e}. Proceeding to final fallback.")

        # --- Layer 3: Final safety net - return the primary screen ---
        logger.warning("All screen detection methods failed. Returning primary screen as a final fallback.")
        return primary_screen


    def process_taskbar(hwnd: int) -> Optional[TaskbarInfo]:
        """
        Processes a single taskbar window and constructs a TaskbarInfo object.
        """
        try:
            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return None

            class_name = win32gui.GetClassName(hwnd)
            if class_name not in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                return None

            rect_phys = win32gui.GetWindowRect(hwnd)
            tray_hwnd = win32gui.FindWindowEx(hwnd, 0, "TrayNotifyWnd", None) or None
            tasklist_rect_phys = find_tasklist_rect(hwnd)
            
            # Use MonitorFromWindow for a direct, unambiguous link between the taskbar and its monitor.
            monitor = win32api.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            
            # Find associated QScreen using our robust geometric method
            screen = find_screen_for_taskbar(rect_phys)
            if not screen:
                logger.error(f"Robust screen detection failed for HWND {hwnd}. This should not happen.")
                return None

            monitor_info = win32api.GetMonitorInfo(monitor)
            work_area_phys = monitor_info.get("Work", (0, 0, 0, 0))
            dpi_scale = get_dpi_for_monitor(monitor, hwnd) or screen.devicePixelRatio()
            physical_height = rect_phys[3] - rect_phys[1]
            logical_height = int(round(physical_height / dpi_scale)) if dpi_scale > 0 else 0
            is_primary_qt = (screen == primary_screen)
            
            screen_geo = screen.geometry()
            return TaskbarInfo(
                hwnd=hwnd,
                tray_hwnd=tray_hwnd,
                tasklist_rect=tasklist_rect_phys,
                rect=rect_phys,
                screen_name=screen.name(),
                screen_geometry=(screen_geo.left(), screen_geo.top(), screen_geo.width(), screen_geo.height()),
                work_area=work_area_phys,
                dpi_scale=dpi_scale,
                is_primary=is_primary_qt,
                height=logical_height
            )
        except win32gui.error as e:
            if e.winerror != 0:
                logger.warning(f"win32gui error during taskbar processing for HWND {hwnd}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during taskbar processing for HWND {hwnd}: {e}")
            return None

    # --- Main execution block of get_all_taskbar_info ---
    try:
        # Try direct FindWindow for efficiency first
        primary_hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
        if primary_hwnd:
            taskbar_info = process_taskbar(primary_hwnd)
            if taskbar_info:
                taskbars.append(taskbar_info)

        # Then enumerate to find all others
        def enum_callback(hwnd: int, _: Any) -> bool:
            if hwnd != primary_hwnd:
                taskbar_info = process_taskbar(hwnd)
                if taskbar_info:
                    taskbars.append(taskbar_info)
            return True

        win32gui.EnumWindows(enum_callback, None)
    except Exception as e:
        logger.error(f"Failed during taskbar enumeration: {e}", exc_info=True)

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
            return constants.taskbar.DEFAULT_HEIGHT
        return taskbar_info.height
    except Exception as e:
        logger.error("Error getting taskbar height: %s. Returning default.", e)
        return constants.taskbar.DEFAULT_HEIGHT
 
          
def is_taskbar_obstructed(taskbar_info: Optional[TaskbarInfo], hwnd_to_check: int) -> bool:
    """
    Checks if the taskbar is obstructed by a specific window (hwnd_to_check).
    This is the definitive, stable, hybrid implementation.
    """
    try:
        if not hwnd_to_check or not win32gui.IsWindow(hwnd_to_check) or not taskbar_info:
            return False

        class_name = win32gui.GetClassName(hwnd_to_check)
        if class_name in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False

        # --- Context Gathering ---
        try:
            fg_monitor = win32api.MonitorFromWindow(hwnd_to_check, MONITOR_DEFAULTTONEAREST)
            tb_monitor = win32api.MonitorFromWindow(taskbar_info.hwnd, MONITOR_DEFAULTTONEAREST)
            if fg_monitor != tb_monitor:
                return False

            window_rect = win32gui.GetWindowRect(hwnd_to_check)
            monitor_info = win32api.GetMonitorInfo(fg_monitor)
            monitor_rect = monitor_info.get('Monitor')
            work_area_rect = monitor_info.get('Work')
            if not monitor_rect or not work_area_rect:
                return False
        except win32gui.error:
            return False

        # --- CHECK 1: True Fullscreen (Games, F11 Browser) ---
        if window_rect == monitor_rect:
            return True

        # --- CHECK 2: Work Area Violation (Start Menu, Task View) ---
        is_contained = (
            window_rect[0] >= work_area_rect[0] and
            window_rect[1] >= work_area_rect[1] and
            window_rect[2] <= work_area_rect[2] and
            window_rect[3] <= work_area_rect[3]
        )
        if not is_contained:
            return True
            
        # --- CHECK 3: Work Area Fullscreen (Borderless) vs. Maximized Apps ---
        is_work_area_sized = (
            window_rect[0] == work_area_rect[0] and
            window_rect[1] == work_area_rect[1] and
            window_rect[2] == work_area_rect[2] and
            window_rect[3] == work_area_rect[3]
        )
        if is_work_area_sized:
            style = win32gui.GetWindowLong(hwnd_to_check, win32con.GWL_STYLE)
            if style & win32con.WS_MAXIMIZE:
                return False  # It's a normal maximized app, NOT an obstruction.
            
            # It fills the work area but isn't a "maximized" window, so it's a borderless fullscreen app.
            return True

        return False

    except Exception as e:
        logger.error(f"Unexpected error in is_taskbar_obstructed: {e}", exc_info=True)
        return False
    

def is_taskbar_visible(taskbar_info: Optional[TaskbarInfo]) -> bool:
    """
    Checks if the taskbar is in a visible state based on its own properties.
    This check is now independent of other application windows. Obstruction is
    handled separately by is_taskbar_obstructed.
    """
    ABM_GETSTATE = 0x00000004
    ABS_AUTOHIDE = 0x00000001

    if not taskbar_info or not taskbar_info.hwnd or not win32gui.IsWindow(taskbar_info.hwnd):
        return False

    try:
        # --- CHECK 1: Is the window itself programmatically visible? ---
        if not win32gui.IsWindowVisible(taskbar_info.hwnd):
            return False

        # --- CHECK 2: Is it an auto-hiding taskbar that is currently hidden? ---
        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(abd)
        abd.hWnd = taskbar_info.hwnd
        state_flags = windll.shell32.SHAppBarMessage(ABM_GETSTATE, byref(abd))
        auto_hide_enabled = bool(state_flags & ABS_AUTOHIDE)

        if auto_hide_enabled:
            screen = taskbar_info.get_screen()
            if not screen: return False # Cannot determine state without screen info

            screen_geo = screen.geometry()
            dpi = taskbar_info.dpi_scale
            # Calculate the screen's physical rectangle
            screen_rect_phys = (
                int(screen_geo.left() * dpi), int(screen_geo.top() * dpi),
                int(screen_geo.right() * dpi) + 1, int(screen_geo.bottom() * dpi) + 1
            )
            tb_rect_phys = taskbar_info.rect
            edge = taskbar_info.get_edge_position()

            # The taskbar is "hidden" if it's mostly off-screen.
            # A small tolerance (e.g., 5 pixels) is used to account for animations/rounding.
            if edge == constants.taskbar.edge.BOTTOM and tb_rect_phys[1] >= screen_rect_phys[3] - 5: return False
            if edge == constants.taskbar.edge.TOP and tb_rect_phys[3] <= screen_rect_phys[1] + 5: return False
            if edge == constants.taskbar.edge.LEFT and tb_rect_phys[2] <= screen_rect_phys[0] + 5: return False
            if edge == constants.taskbar.edge.RIGHT and tb_rect_phys[0] >= screen_rect_phys[2] - 5: return False

        # If it passes all its own state checks, it is considered visible.
        return True

    except Exception as e:
        logger.error(f"Error checking taskbar visibility for HWND={taskbar_info.hwnd}: {e}", exc_info=True)
        return False


def is_taskbar_obstructed(taskbar_info: Optional[TaskbarInfo], hwnd_to_check: int) -> bool:
    """
    Checks if the taskbar is obstructed by a specific window (hwnd_to_check).
    This is the definitive, stable, hybrid implementation.
    """
    try:
        # We now use the specific window handle passed to us, not a fresh API call.
        if not hwnd_to_check or not win32gui.IsWindow(hwnd_to_check) or not taskbar_info:
            return False

        # The taskbar cannot obstruct itself, and the desktop is always behind it.
        class_name = win32gui.GetClassName(hwnd_to_check)
        if class_name in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False

        # --- Context Gathering ---
        try:
            fg_monitor = win32api.MonitorFromWindow(hwnd_to_check, MONITOR_DEFAULTTONEAREST)
            tb_monitor = win32api.MonitorFromWindow(taskbar_info.hwnd, MONITOR_DEFAULTTONEAREST)
            if fg_monitor != tb_monitor:
                return False

            window_rect = win32gui.GetWindowRect(hwnd_to_check)
            monitor_info = win32api.GetMonitorInfo(fg_monitor)
            monitor_rect = monitor_info.get('Monitor')
            work_area_rect = monitor_info.get('Work')
            if not monitor_rect or not work_area_rect:
                return False
        except win32gui.error:
            return False # Window might have closed.

        # --- OBSTRUCTION CHECK 1: True Fullscreen (Games, F11 Browser) ---
        if window_rect == monitor_rect:
            return True

        # --- OBSTRUCTION CHECK 2: Work Area Violation (Start Menu, Browser Fullscreen Video) ---
        if class_name in constants.shell.shell.UI_CLASS_NAMES_TO_CHECK:
            is_contained = (
                window_rect[0] >= work_area_rect[0] and
                window_rect[1] >= work_area_rect[1] and
                window_rect[2] <= work_area_rect[2] and
                window_rect[3] <= work_area_rect[3]
            )
            if not is_contained:
                return True
        
        return False

    except Exception as e:
        logger.error(f"Unexpected error in is_taskbar_obstructed: {e}", exc_info=True)
        return False


def is_small_taskbar(taskbar_info: Optional[TaskbarInfo]) -> bool:
    """
    Checks if the taskbar is using the 'small buttons' mode by checking its logical height.
    """
    if not taskbar_info:
        return False
    return 0 < taskbar_info.height <= constants.layout.SMALL_TASKBAR_HEIGHT_THRESHOLD