"""
Taskbar Utilities for Windows System Integration.

Provides functions and data structures for detecting taskbar properties such as
position, visibility, state, associated screen, DPI, and system tray location.
Relies on Windows API calls via pywin32 and ctypes.
"""

import os
import logging
import ctypes
import subprocess
import threading
import time
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


# --- Win11 Widgets/weather element detection (#200 / #24) --------------------------------
# The Windows 11 Widgets/weather element has NO classic Win32 HWND - it lives inside a XAML island, so
# EnumChildWindows/GetWindowRect can't see it. It IS reachable via UI Automation. Rather than add a COM
# dependency we shell out to PowerShell's built-in UIAutomationClient (the same approach used for
# nvidia-smi etc.), match the STABLE AutomationId 'WidgetsButton' (never the Name - it's localized and
# embeds live weather text), and return the VISIBLE CONTENT extent (min-left..max-right of its child
# icon/text), not the padded button box - so an overlap test reflects what's actually shown, not the
# element's dead space. Depending on taskbar alignment it sits far-left (centred) or just left of the
# tray (edge-aligned - the case that can overlap the widget). ~200ms, so it's cached + refreshed on a
# background thread; callers get the last-known rect instantly and never block. Fail-safe: any error /
# Widgets disabled / future-build rename -> None. Used only to nudge the user (never to auto-move).
_CREATE_NO_WINDOW = 0x08000000
_WIDGETS_CACHE_TTL_S = 30.0
_widgets_lock = threading.Lock()
_widgets_state: dict = {"rect": None, "ts": 0.0, "querying": False}
_WIDGETS_PS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "Add-Type -AssemblyName UIAutomationClient;"
    "Add-Type -AssemblyName UIAutomationTypes;"
    "$r=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd});"
    "if($r){{"
    "$c=New-Object System.Windows.Automation.PropertyCondition("
    "[System.Windows.Automation.AutomationElement]::AutomationIdProperty,'WidgetsButton');"
    "$e=$r.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$c);"
    "if($e){{$b=$e.Current.BoundingRectangle;$cl=$b.Right;$cr=$b.Left;"
    "$kids=$e.FindAll([System.Windows.Automation.TreeScope]::Descendants,"
    "[System.Windows.Automation.Condition]::TrueCondition);"
    "foreach($k in $kids){{$kr=$k.Current.BoundingRectangle;"
    "if($kr.Width -gt 0 -and $kr.Left -ge $b.Left -and $kr.Right -le ($b.Right+2)){{"
    "if($kr.Left -lt $cl){{$cl=$kr.Left}};if($kr.Right -gt $cr){{$cr=$kr.Right}}}}}}"
    "if($cr -le $cl){{$cl=$b.Left;$cr=$b.Right}};"
    "Write-Output ('{{0}},{{1}},{{2}},{{3}}' -f [int]$cl,[int]$b.Top,[int]$cr,[int]$b.Bottom)}}}}"
)


def _query_widgets_rect_blocking(taskbar_hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """Run the UIA probe via PowerShell and return the Widgets button's physical-pixel rect, or None.
    ``taskbar_hwnd`` must be the Shell_TrayWnd root (the WidgetsButton is a descendant of the taskbar
    frame, NOT of TrayNotifyWnd). Blocking (~200ms); always called from a background thread. Never raises."""
    try:
        ps = _WIDGETS_PS.format(hwnd=int(taskbar_hwnd))
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=6, creationflags=_CREATE_NO_WINDOW,
        )
        parts = (proc.stdout or "").strip().split(",")
        if len(parts) == 4:
            left, top, right, bottom = (int(p) for p in parts)
            if right > left and bottom > top:
                return (left, top, right, bottom)
    except Exception as e:  # noqa: BLE001 - fail-safe: any problem means "no Widgets rect"
        logger.debug("Widgets UIA probe failed (treating as no Widgets button): %s", e)
    return None


def rect_overlaps_x(left: float, right: float, widgets_rect: Optional[Tuple[int, int, int, int]],
                    dpi_scale: float) -> bool:
    """
    True if the horizontal span ``[left, right]`` (logical px, e.g. the widget's own geometry) overlaps
    the Widgets/weather element's x-extent (``widgets_rect`` is physical px; converted via ``dpi_scale``).

    Used to decide the one-time #200 nudge. ``widgets_rect`` is the visible content extent (dead space
    excluded), so a widget that merely grazes the element's padding is correctly NOT counted as overlap.
    """
    if not widgets_rect or dpi_scale <= 0:
        return False
    r_left = widgets_rect[0] / dpi_scale
    r_right = widgets_rect[2] / dpi_scale
    return left < r_right and right > r_left


def get_widgets_rect(taskbar_hwnd: Optional[int]) -> Optional[Tuple[int, int, int, int]]:
    """
    Non-blocking, cached rect of the Win11 Widgets/weather button (physical px), or None if it can't be
    detected (Widgets off, older Windows, or a future build that renamed the element).

    ``taskbar_hwnd`` is the Shell_TrayWnd root (NOT the TrayNotifyWnd - the WidgetsButton lives under the
    taskbar frame, not the tray). Returns the last-known cached value immediately; when the cache is older
    than the TTL it kicks off a one-shot background refresh so the ~200ms UIA probe never runs on the
    caller's thread. The rect changes only when Widgets is toggled or its weather text resizes.
    """
    if not taskbar_hwnd:
        return None
    now = time.monotonic()
    start_refresh = False
    with _widgets_lock:
        rect = _widgets_state["rect"]
        stale = (now - _widgets_state["ts"]) > _WIDGETS_CACHE_TTL_S
        if stale and not _widgets_state["querying"]:
            _widgets_state["querying"] = True
            start_refresh = True
    if start_refresh:
        def _refresh() -> None:
            result = _query_widgets_rect_blocking(taskbar_hwnd)
            with _widgets_lock:
                _widgets_state["rect"] = result
                _widgets_state["ts"] = time.monotonic()
                _widgets_state["querying"] = False
        threading.Thread(target=_refresh, name="nst-widgets-uia", daemon=True).start()
    return rect


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
    # Physical-pixel rect of the Win11 Widgets/weather button on this (primary) taskbar, or None when
    # it's absent/undetected. Used to keep the widget from landing on it in a left-aligned layout
    # (#200/#24). Defaulted so the fallback/no-taskbar constructions don't need to pass it.
    widgets_rect: Optional[Tuple[int, int, int, int]] = None

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
        Fetches the QScreen object associated with this taskbar using a robust,
        multi-layered geometric and name-based comparison.
        """
        try:
            all_screens = QApplication.screens()
            
            # Layer 1: Direct logical geometry match (fastest and most common)
            stored_geo_rect = QRect(
                self.screen_geometry[0], self.screen_geometry[1],
                self.screen_geometry[2], self.screen_geometry[3]
            )
            for screen in all_screens:
                if screen.geometry() == stored_geo_rect:
                    return screen

            # Layer 2: Geometric intersection (good for most edge cases)
            tb_qrect_phys = QRect(self.rect[0], self.rect[1], self.rect[2] - self.rect[0], self.rect[3] - self.rect[1])
            best_match_screen: Optional[QScreen] = None
            max_overlap: int = 0
            for screen in all_screens:
                geo_log = screen.geometry()
                dpi = screen.devicePixelRatio()
                geo_phys = QRect(int(geo_log.left() * dpi), int(geo_log.top() * dpi), int(geo_log.width() * dpi), int(geo_log.height() * dpi))
                overlap_rect = geo_phys.intersected(tb_qrect_phys)
                if overlap_rect.width() * overlap_rect.height() > max_overlap:
                    max_overlap = overlap_rect.width() * overlap_rect.height()
                    best_match_screen = screen
            
            if best_match_screen:
                logger.warning("Used intersection-based fallback to get screen for HWND %s", self.hwnd)
                return best_match_screen

            # Layer 3: Direct WinAPI to Qt name matching (most robust fallback)
            try:
                # Get the HMONITOR directly from the taskbar's HWND
                tb_monitor_handle = win32api.MonitorFromWindow(self.hwnd, MONITOR_DEFAULTTONEAREST)
                if tb_monitor_handle:
                    # Get the monitor's device name (e.g., "\\.\DISPLAY1") from the WinAPI
                    monitor_info = win32api.GetMonitorInfo(tb_monitor_handle)
                    winapi_screen_name = monitor_info.get('Device', '')
                    if winapi_screen_name:
                        # Find the QScreen that has the exact same device name
                        for screen in all_screens:
                            if screen.name() == winapi_screen_name:
                                logger.warning("Used robust name-based fallback to get screen for HWND %s", self.hwnd)
                                return screen
            except Exception as e:
                logger.error("Error during name-based screen matching fallback: %s", e)
                
            logger.warning("Could not find any matching QScreen for taskbar HWND %s. Falling back to primary screen.", self.hwnd)
            return QApplication.primaryScreen()
        except Exception as e:
            logger.error("Unexpected error fetching QScreen for taskbar HWND %s: %s. Using primary fallback.", self.hwnd, e, exc_info=True)
            return QApplication.primaryScreen()


    @staticmethod
    def create_primary_fallback_taskbar_info() -> 'TaskbarInfo':
        """
        Creates a fallback TaskbarInfo using the primary screen's details.
        Used when automatic taskbar detection fails entirely.
        """
        logger.warning("Creating fallback TaskbarInfo using primary screen details.")
        primary_screen = QApplication.primaryScreen()
        if not primary_screen:
            logger.warning("No primary screen detected during fallback creation. Using absolute defaults.")
            # Absolute recovery fallback (1080p assumption, 1.0 DPI)
            return TaskbarInfo(
                hwnd=0,
                tray_hwnd=None,
                tasklist_rect=None,
                rect=(0, 0, 0, 0),
                screen_name="SUPER_FALLBACK",
                screen_geometry=(0, 0, 1920, 1080),
                work_area=(0, 0, 1920, 1040),
                dpi_scale=1.0,
                is_primary=True,
                height=constants.taskbar.taskbar.DEFAULT_HEIGHT
            )

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
            # --- Validate the handle before using it ---
            if not win32gui.IsWindow(self.tray_hwnd):
                logger.warning("Failed to get tray rectangle for taskbar %s: Tray HWND %s is invalid.", self.hwnd, self.tray_hwnd)
                # Invalidate the cached handle so we don't try again
                self.tray_hwnd = None
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

            logger.debug("Taskbar edge position ambiguous for HWND %s. Comparing centers.", self.hwnd)
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


def _taskbar_window_is_ready(hwnd: int, class_name: str) -> bool:
    """Whether an enumerated taskbar window is ready to dock to.

    The PRIMARY taskbar (``Shell_TrayWnd``) is only ready once its ``TrayNotifyWnd`` tray/clock child is a
    valid, queryable window. A SECONDARY-monitor taskbar (``Shell_SecondaryTrayWnd``) never has a
    ``TrayNotifyWnd`` child, so requiring one used to drop EVERY secondary taskbar - which left the
    enumeration with only the primary, so multi-monitor "Preferred Monitor" always fell back to primary
    (#72). For a secondary, readiness is simply that its own window is queryable.
    """
    try:
        win32gui.GetWindowRect(hwnd)
    except win32gui.error:
        return False
    if class_name == "Shell_SecondaryTrayWnd":
        return True
    tray_hwnd = win32gui.FindWindowEx(hwnd, 0, "TrayNotifyWnd", None)
    if not tray_hwnd or not win32gui.IsWindow(tray_hwnd):
        return False
    try:
        win32gui.GetWindowRect(tray_hwnd)
    except win32gui.error:
        return False
    return True


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

            # Readiness gate (#72): the system tray/clock (TrayNotifyWnd) lives only on the PRIMARY taskbar,
            # so a secondary-monitor taskbar must NOT be required to have one - that requirement silently
            # dropped every secondary taskbar, leaving only the primary to match against.
            if not _taskbar_window_is_ready(hwnd, class_name):
                logger.debug(f"Taskbar HWND {hwnd} ({class_name}) is not ready yet. Ignoring.")
                return None

            rect_phys = win32gui.GetWindowRect(hwnd)
            tray_hwnd = win32gui.FindWindowEx(hwnd, 0, "TrayNotifyWnd", None) or None
            tasklist_rect_phys = find_tasklist_rect(hwnd)
            
            # Find associated QScreen using our robust geometric method FIRST.
            screen = find_screen_for_taskbar(rect_phys)
            if not screen:
                logger.error(f"Robust screen detection failed for HWND {hwnd}. This should not happen.")
                return None

            # Get the monitor info for the monitor that contains the taskbar's top-left corner.
            monitor = win32api.MonitorFromPoint((rect_phys[0], rect_phys[1]), MONITOR_DEFAULTTONEAREST)
            
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
                height=logical_height,
                # Only the primary taskbar hosts a Widgets button; cached + non-blocking. Pass the
                # Shell_TrayWnd root (`hwnd`), not tray_hwnd - the button is under the taskbar frame.
                widgets_rect=get_widgets_rect(hwnd) if is_primary_qt else None,
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
        primary_hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
        if primary_hwnd:
            taskbar_info = process_taskbar(primary_hwnd)
            if taskbar_info:
                taskbars.append(taskbar_info)

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


def _select_taskbar_for_screen(
    taskbars: List[TaskbarInfo], preferred_screen_name: str
) -> Optional[TaskbarInfo]:
    """
    Find the taskbar living on the user's preferred monitor (#72 / #166).

    Matches on any of several identities so that a single Qt-vs-WinAPI naming or
    DPI desync (common on mixed-DPI multi-monitor setups, e.g. dual 4K at 150%)
    does not silently drop the preference and fall back to primary:

      1. the name stored at enumeration time (``TaskbarInfo.screen_name``),
      2. the freshly re-resolved ``get_screen().name()``, and
      3. geometry equality with the preferred QScreen (name-independent).

    Returns the matching ``TaskbarInfo``, or ``None`` if the preferred monitor
    simply has no taskbar to attach to (e.g. "Show taskbar on all displays" is
    off) - the caller then falls back to primary and logs why.
    """
    preferred_screen = next(
        (s for s in QApplication.screens() if s.name() == preferred_screen_name), None
    )
    preferred_geo = preferred_screen.geometry() if preferred_screen is not None else None

    for tb in taskbars:
        if tb.screen_name == preferred_screen_name:
            return tb
        resolved = tb.get_screen()
        if resolved is not None and resolved.name() == preferred_screen_name:
            return tb
        if (
            preferred_geo is not None
            and resolved is not None
            and resolved.geometry() == preferred_geo
        ):
            return tb
    return None


# The preferred-monitor fallback is logged at most once per distinct state, not once per reposition
# tick. get_taskbar_info() runs on the ~1s safety-net loop, so an unmatched preferred monitor (a
# taskbar-less accessory display like the Corsair Xeneon Edge, #188) otherwise spammed this INFO line
# every second and bloated Support Bundles. Keyed on (preferred, taskbar set) so a genuine topology
# change (monitor plugged/unplugged, or a changed preference) still logs once more.
_last_fallback_log_key: Optional[tuple] = None


def _log_preferred_monitor_fallback(
    preferred_screen_name: str, taskbars: List[TaskbarInfo]
) -> None:
    """
    Log (at INFO, so it lands in Support Bundles) exactly why a preferred-monitor
    selection fell back to primary: the preferred name plus every enumerated
    taskbar's class, stored name, re-resolved name, and geometry. Turns an
    otherwise-silent fallback into an immediately diagnosable one (#72 / #166).

    Throttled to once per distinct (preferred, taskbar-set) state so the ~1s
    safety-net refresh can't spam it (#188).
    """
    global _last_fallback_log_key
    key = (preferred_screen_name, tuple(sorted((tb.hwnd, tb.screen_name) for tb in taskbars)))
    if key == _last_fallback_log_key:
        return
    _last_fallback_log_key = key

    details = []
    for tb in taskbars:
        resolved = tb.get_screen()
        details.append(
            "[%s hwnd=%s stored='%s' resolved='%s' geo=%s]"
            % (
                "primary" if tb.is_primary else "secondary",
                tb.hwnd,
                tb.screen_name,
                resolved.name() if resolved is not None else None,
                tb.screen_geometry,
            )
        )
    logger.info(
        "Preferred monitor '%s' did not match any of %d taskbar(s); using primary. "
        "Enumerated taskbars: %s. If the preferred monitor has no taskbar of its own, "
        "enabling 'Show taskbar on all displays' is the current workaround (a no-taskbar "
        "fallback is tracked in #166).",
        preferred_screen_name,
        len(taskbars),
        " ".join(details) if details else "(none)",
    )


def get_free_float_screen(preferred_screen_name: Optional[str]) -> Optional[QScreen]:
    """
    Return the QScreen the widget should FREE-FLOAT on, or None to dock to a taskbar as usual.

    Free-float applies only when the user's preferred monitor is (a) set, (b) currently connected, and
    (c) has NO taskbar of its own (e.g. the Corsair Xeneon Edge, or a secondary display with "Show
    taskbar on all displays" off). In every other case - no preference, preferred monitor disconnected,
    or the preferred monitor has a taskbar - this returns None and the normal docking path runs (#188).
    """
    if not preferred_screen_name:
        return None
    try:
        screen = next((s for s in QApplication.screens() if s.name() == preferred_screen_name), None)
        if screen is None:
            return None  # preferred monitor not connected -> the primary-taskbar fallback is correct
        taskbars = get_all_taskbar_info()
        if _select_taskbar_for_screen(taskbars, preferred_screen_name) is not None:
            return None  # it has a taskbar of its own -> dock to it normally
        return screen    # present but taskbar-less -> free-float on it
    except Exception as e:  # noqa: BLE001 - fail-safe: never break placement over float detection
        logger.error("get_free_float_screen failed for '%s': %s", preferred_screen_name, e, exc_info=True)
        return None


def get_taskbar_info(preferred_screen_name: Optional[str] = None) -> TaskbarInfo:
    """
    Retrieves the taskbar to attach the widget to.

    By default returns the primary taskbar. If `preferred_screen_name` is
    given and a secondary taskbar exists on that screen, returns *that*
    taskbar instead - this is the implementation hook for the user-facing
    "Preferred Monitor" setting (#72).

    If the preferred screen is no longer present (monitor disconnected,
    settings imported from a different machine, etc.), gracefully falls
    back to the primary taskbar and logs the fallback at INFO so users
    can see why their preference wasn't honored.

    Args:
        preferred_screen_name: Optional QScreen.name() (e.g. "\\\\.\\DISPLAY2").

    Returns:
        TaskbarInfo for the preferred screen if found, else primary, else fallback.
    """
    try:
        all_taskbars = get_all_taskbar_info()

        if preferred_screen_name:
            preferred_tb = _select_taskbar_for_screen(all_taskbars, preferred_screen_name)
            if preferred_tb is not None:
                logger.debug(
                    "Preferred taskbar selected for screen '%s': HWND=%s",
                    preferred_screen_name, preferred_tb.hwnd,
                )
                return preferred_tb
            _log_preferred_monitor_fallback(preferred_screen_name, all_taskbars)

        primary_taskbar = next((tb for tb in all_taskbars if tb.is_primary), all_taskbars[0])
        logger.debug("Primary taskbar selected: HWND=%s (Is fallback: %s)", primary_taskbar.hwnd, primary_taskbar.hwnd == 0)
        return primary_taskbar
    except Exception as e:
        logger.error("Error selecting taskbar info: %s. Returning fallback.", e, exc_info=True)
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
 
          

def is_taskbar_visible(taskbar_info: Optional[TaskbarInfo]) -> bool:
    """
    Checks if the taskbar is in a visible state based on its own properties.
    """
    if not taskbar_info:
        return False
    
    # If the handle is 0, it means we are using a fallback TaskbarInfo (e.g. detection failed).
    # In this case, we default to TRUE (visible) to prevent the widget from disappearing
    # just because the API is flaky.
    if taskbar_info.hwnd == 0:
        return True

    if not win32gui.IsWindow(taskbar_info.hwnd):
        return False

    try:
        # CHECK 1: Is the window itself programmatically visible?
        if not win32gui.IsWindowVisible(taskbar_info.hwnd):
            return False

        # CHECK 2: Is it an auto-hiding taskbar that is currently hidden?
        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(abd)
        abd.hWnd = taskbar_info.hwnd
        state_flags = windll.shell32.SHAppBarMessage(constants.shell.api.ABM_GETSTATE, byref(abd))
        auto_hide_enabled = bool(state_flags & constants.shell.api.ABS_AUTOHIDE)

        if auto_hide_enabled:
            screen = taskbar_info.get_screen()
            if not screen: return False

            screen_geo = screen.geometry()
            dpi = taskbar_info.dpi_scale
            screen_rect_phys = (
                int(screen_geo.left() * dpi), int(screen_geo.top() * dpi),
                int(screen_geo.right() * dpi) + 1, int(screen_geo.bottom() * dpi) + 1
            )
            tb_rect_phys = taskbar_info.rect
            edge = taskbar_info.get_edge_position()
            
            tolerance = constants.taskbar.taskbar.AUTOHIDE_TOLERANCE
            if edge == constants.taskbar.edge.BOTTOM and tb_rect_phys[1] >= screen_rect_phys[3] - tolerance: return False
            if edge == constants.taskbar.edge.TOP and tb_rect_phys[3] <= screen_rect_phys[1] + tolerance: return False
            if edge == constants.taskbar.edge.LEFT and tb_rect_phys[2] <= screen_rect_phys[0] + tolerance: return False
            if edge == constants.taskbar.edge.RIGHT and tb_rect_phys[0] >= screen_rect_phys[2] - tolerance: return False

        return True

    except Exception as e:
        logger.error(f"Error checking taskbar visibility for HWND={taskbar_info.hwnd}: {e}", exc_info=True)
        return False


def is_taskbar_obstructed(taskbar_info: Optional[TaskbarInfo], hwnd_to_check: int) -> bool:
    """
    Checks if the taskbar is obstructed by a specific window.
    
    The goal is simple: widget should be visible whenever the taskbar is visible.
    Only TRUE fullscreen windows (covering the entire monitor) should hide the widget.
    """
    try:
        if not hwnd_to_check or not win32gui.IsWindow(hwnd_to_check) or not taskbar_info or not taskbar_info.hwnd:
            return False

        # Ignore our own windows (e.g., context menu popups)
        own_pid = os.getpid()
        _, check_pid = win32process.GetWindowThreadProcessId(hwnd_to_check)
        if own_pid == check_pid:
            return False

        # Ignore desktop and shell windows
        class_name = win32gui.GetClassName(hwnd_to_check)
        if class_name in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False

        # Ignore explorer.exe (File Explorer, desktop, etc.)
        process_name = get_process_name_from_hwnd(hwnd_to_check)
        if process_name == "explorer.exe":
            return False

        # Check if window is on the same monitor as the taskbar
        try:
            fg_monitor = win32api.MonitorFromWindow(hwnd_to_check, MONITOR_DEFAULTTONEAREST)
            tb_monitor = win32api.MonitorFromWindow(taskbar_info.hwnd, MONITOR_DEFAULTTONEAREST)
            if fg_monitor != tb_monitor:
                return False

            window_rect = win32gui.GetWindowRect(hwnd_to_check)
            monitor_info = win32api.GetMonitorInfo(fg_monitor)
            monitor_rect = monitor_info.get('Monitor')
            if not monitor_rect:
                return False
        except win32gui.error:
            return False

        # ONLY obstruction: True fullscreen (window covers ENTIRE monitor)
        # This catches fullscreen games, F11 browser mode, fullscreen videos, etc.
        if window_rect == monitor_rect:
            return True

        # All other cases: NOT an obstruction (maximized apps, snapped windows, etc.)
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