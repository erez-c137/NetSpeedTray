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
import time
import ctypes
from ctypes import wintypes
import win32con
import win32gui

from PyQt6.QtCore import QObject, QTimer, QPoint, QRect, QSize, pyqtSlot
from PyQt6.QtGui import QFontMetrics, QScreen
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants
# Screen names are logged pseudonymized: the support bundle's MANIFEST promises
# no display names ship (v2.1.5 item 7).
from netspeedtray.utils.config import stable_pseudonym
# We import taskbar_utils for low-level detection
from netspeedtray.utils import taskbar_utils
from netspeedtray.utils.taskbar_utils import (TaskbarInfo, get_taskbar_info, is_small_taskbar,
                                              horizontal_taskbar_strip, vertical_taskbar_strip)

if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget

# Logger Setup
logger = logging.getLogger("NetSpeedTray.Core.PositionManager")


# --- Owner-window (taskbar Z-order dock) ------------------------------------
# Making the widget an OWNED window of the taskbar (GWLP_HWNDPARENT) lets the
# window manager keep it ABOVE the taskbar in Z-order permanently, so shell
# activity (Start menu, quick-settings flyout, taskbar clicks) can never raise
# the taskbar over the widget -- the occlusion that made the widget appear to
# "vanish". Use the pointer-width (…Ptr) API with explicit 64-bit-safe types so
# the HWND value is not truncated on 64-bit Python.
_GWLP_HWNDPARENT = -8
_user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    _SetWindowLongPtr = _user32.SetWindowLongPtrW
    _GetWindowLongPtr = _user32.GetWindowLongPtrW
except AttributeError:  # 32-bit Python has no …Ptr variant
    _SetWindowLongPtr = _user32.SetWindowLongW
    _GetWindowLongPtr = _user32.GetWindowLongW
_SetWindowLongPtr.restype = ctypes.c_void_p
_SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
_GetWindowLongPtr.restype = ctypes.c_void_p
_GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]

# --- Burial recovery: re-seat directly above the taskbar (#314, #238, PR #327) ---
# The owner link keeps the widget above the taskbar in theory, but the shell can still
# raise the taskbar over it (Win10: maximize + restore; Win11: Start/flyout churn, hovering
# grouped previews). Recovery seats the widget DIRECTLY above its taskbar - never at the
# top of the topmost band - so anything already above the taskbar (open menus, jump lists,
# tooltips, Start/Search/Widgets flyouts) stays above the widget, and SWP_NOOWNERZORDER
# keeps the taskbar's owner cluster where it is (the #200 clipping came from dragging it
# up). With that insertion point the re-seat no longer has to recognise popups at all.
_SHELL_TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")
_GW_HWNDPREV = 3
_SWP_NOOWNERZORDER = 0x0200
_RESEAT_SWP_FLAGS = (win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                     | _SWP_NOOWNERZORDER)
# Upper bound for the walk from the taskbar up to the top of the Z-order. The taskbar sits
# near the top of the topmost band (a dozen windows above it on a live Win11 26200 probe),
# so this never binds in practice; it only stops a pathological list from spinning.
_ZORDER_WALK_LIMIT = 1024

# GetWindowBand (user32, undocumented but exported since Win8). Win11 lifts the taskbar into
# a higher Z-band while Start is open; a band-1 widget cannot be seated inside another band,
# so the re-seat waits for the taskbar to come back instead of fighting it. None = unknown.
try:
    _GetWindowBandRaw = _user32.GetWindowBand
    _GetWindowBandRaw.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _GetWindowBandRaw.restype = wintypes.BOOL
except AttributeError:
    _GetWindowBandRaw = None


def _window_band(hwnd: int) -> Optional[int]:
    """The window's Z-band, or None when the API is missing or the call fails."""
    if _GetWindowBandRaw is None or not hwnd:
        return None
    try:
        band = wintypes.DWORD(0)
        if _GetWindowBandRaw(hwnd, ctypes.byref(band)):
            return int(band.value)
    except Exception:
        pass
    return None


# The taskbar's normal Z-band (ZBID_DESKTOP). Opening Start or Task View lifts the taskbar into a
# higher band (6 on a live Win11 26200 probe) and the widget, owned by it, goes along. While it is
# up there the GW_HWNDPREV walk from the taskbar no longer finds the widget, so the burial check
# cannot tell burial from "moved with the taskbar" - it must not guess, re-seat or charge the
# backoff; the band dropping back (a foreground / reorder event, or the 1 s tick) re-checks.
# Only bands ABOVE the desktop band count as raised: a setup that keeps the taskbar in band 0
# (or the API reporting it) must not switch recovery off.
_ZBID_DESKTOP = 1
# A taskbar that stays "raised" this long is not Start / Task View: it is a shell that keeps it
# there. Log once and fall back to checking the Z-order as if the band were normal.
_BAND_RAISED_FALLBACK_S = 60.0
# Closing Start / Task View drops the taskbar back to band 1 with the widget UNDER it (~0.3 s
# later on a live Win11 26200 probe). The first re-seat within this long of the last raised
# observation is the shell's doing, not a fight: it is immediate and charges no backoff.
_BAND_RETURN_GRACE_S = 3.0


# Re-seat backoff, the safety net against a shell that re-buries the widget the instant it
# is re-seated: the first recovery fires immediately, then each re-seat that does not hold
# doubles the wait (1, 2, 4 ... 60 s). The seat above the taskbar cannot clip a menu, so the
# backoff only has to stop a fight, not every recovery: it retires after the widget has
# stayed on top for _RESEAT_HEALTHY_S. That is shorter than an ordinary gap between two user
# actions that bury it (pasharm's Win10 repro buries it on every maximize/restore), so those
# always recover at once, while a real fight still decays to one re-seat a minute.
_RESEAT_BACKOFF_BASE_S = 1.0
_RESEAT_BACKOFF_MAX_S = 60.0
_RESEAT_BACKOFF_EXP_CAP = 6
_RESEAT_HEALTHY_S = 10.0
# The on-top stretch must be OBSERVED: checks run at least once a second while the widget is
# shown, so a longer gap between two verified on-top checks (the widget was hidden, or nothing
# checked) restarts the stretch instead of counting the unobserved time as healthy.
_ON_TOP_MAX_GAP_S = 3.0


class _TrayOverride:
    """A TaskbarInfo view whose get_tray_rect() returns a substitute (or None), for placing the
    widget while the real tray rect is not ready (see PositionManager._tray_rect_ready)."""

    def __init__(self, info: Any, tray: Optional[Tuple[int, int, int, int]]) -> None:
        self._info = info
        self._tray = tray

    def get_tray_rect(self) -> Optional[Tuple[int, int, int, int]]:
        return self._tray

    def __getattr__(self, name: str) -> Any:
        return getattr(self._info, name)


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
                             x, y, valid_x, valid_y, stable_pseudonym(screen.name(), "DISPLAY"))

            return ScreenPosition(valid_x, valid_y)

        except Exception as e:
            logger.error("Error validating position (%s,%s) on screen '%s': %s",
                         x, y, stable_pseudonym(screen.name(), "DISPLAY"), e, exc_info=True)
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

            # Clamp widget size to reasonable bounds (scaled for DPI) to prevent off-screen/huge widgets
            ui_widget = getattr(constants.ui, 'widget', None)
            if ui_widget:
                max_width = int(ui_widget.MAX_WIDGET_WIDTH_PX * dpi_scale)
                max_height = int(ui_widget.MAX_WIDGET_HEIGHT_PX * dpi_scale)
                # Clamp width
                if widget_width > max_width:
                    logger.warning(
                        "Widget width %s exceeds max %spx (DPI-scaled). Clamping to prevent off-screen positioning.",
                        widget_width, max_width
                    )
                    widget_width = max_width
                # Clamp height - but not on a side taskbar, where side_by_side stacks its segments into a
                # widget taller than the cap, and positioning it by the clamped height would sink it over
                # the tray. There the real height is bounded by the taskbar icons and validate_position.
                if widget_height > max_height and edge not in (constants.taskbar.edge.LEFT,
                                                               constants.taskbar.edge.RIGHT):
                    logger.warning(
                        "Widget height %s exceeds max %spx (DPI-scaled). Clamping to prevent off-screen positioning.",
                        widget_height, max_height
                    )
                    widget_height = max_height   # DPI-scaled bound (mirror the width clamp; was the unscaled const)

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
        
        if edge in (constants.taskbar.edge.BOTTOM, constants.taskbar.edge.TOP):
            # The visible strip: work-area inset capped by the taskbar window, anchored to the
            # stable screen edge (#104/#110, #135, #221, #322 - see horizontal_taskbar_strip).
            y_origin, visible_tb_height = horizontal_taskbar_strip(taskbar_info, edge, dpi_scale)
        else:
            # Fallback to rect-based if we're not sure
            visible_tb_height = (taskbar_info.rect[3] - taskbar_info.rect[1]) / dpi_scale
            y_origin = taskbar_info.rect[1] / dpi_scale

        # Calculate Y: center widget vertically in the *visible* taskbar gap
        y_center = y_origin + (visible_tb_height - widget_height) / 2.0
        y = self._clamp_into_strip(round(y_center), widget_height, y_origin, visible_tb_height, edge)

        # Calculate X: align to the right (system-tray side), keeping the widget clear of the tray.
        tray_rect = taskbar_info.get_tray_rect()
        offset = config.get('tray_offset_x', constants.config.defaults.DEFAULT_TRAY_OFFSET_X)
        if tray_rect:
            # Normal taskbar: hug the tray's left edge, but floor the gap so the widget never abuts
            # the show-hidden-icons "^" chevron and steals its clicks (#161 pt1).
            right_boundary = round(tray_rect[0] / dpi_scale)
            offset = max(offset, constants.layout.TRAY_EDGE_MIN_GAP_PX)
        else:
            # A Win11 SECONDARY taskbar (Shell_SecondaryTrayWnd) has no TrayNotifyWnd child, so
            # get_tray_rect() is None. Its clock is shell-drawn with no HWND to hug; reserve an
            # estimate of the clock width off the screen edge instead of landing on it (#186).
            reserve = config.get('secondary_clock_reserve_px',
                                 constants.config.defaults.DEFAULT_SECONDARY_CLOCK_RESERVE_PX)
            right_boundary = (full_geom.right() + 1) - reserve

        # left_boundary is the right edge of the task list/app-icons region when available.
        tasklist_rect = taskbar_info.tasklist_rect
        left_boundary = round(tasklist_rect[2] / dpi_scale) if tasklist_rect else full_geom.left()

        x = round(right_boundary - widget_width - offset)

        # Safety check: don't overlap with app icons on left
        if x < left_boundary:
            logger.warning("Calculated position overlaps app icons; snapping to safe zone.")
            x = round(left_boundary + constants.layout.DEFAULT_PADDING)

        # NOTE: we deliberately do NOT auto-avoid the Win11 Widgets/weather element here. Its width
        # changes with the weather text, so auto-repositioning would make the widget jitter left/right
        # as the weather updates, and it would override a position the user chose. Instead the widget
        # stays put and the user is nudged once that they can drag it (main.py, using widgets_rect). The
        # user is always free to sit in the gap, or overlap it, or move it - their call (#200/#24).

        return x, y

    @staticmethod
    def _clamp_into_strip(y: int, widget_height: int, strip_top: float, strip_height: float,
                          edge: constants.TaskbarEdge) -> int:
        """Keep a horizontal-taskbar widget inside its strip, top AND bottom edge (#322).

        ScreenUtils.validate_position only keeps the widget on the SCREEN: an over-tall widget
        centred on a TOP bar got a negative y, was pushed down to the screen top, and every pixel of
        overflow landed on the desktop. Layout now sizes the widget to the strip, so this is the
        backstop: inside the strip when it fits; when it can't fit, flush with the screen edge the
        bar sits on, so overflow is at least bounded by the screen rather than centred off it.
        """
        top = math.ceil(strip_top)
        bottom = math.floor(strip_top + strip_height)   # exclusive
        if widget_height <= bottom - top:
            return max(top, min(y, bottom - widget_height))
        return top if edge == constants.taskbar.edge.TOP else bottom - widget_height

    @staticmethod
    def _centre_in_strip(widget_width: int, x_origin: float, strip_width: float) -> int:
        """x of a side-bar widget centred in its strip.

        No clamp is needed for a fractional-DPI strip (38.4 px at 125%): when the widget fits the
        whole pixels [ceil(x0), floor(x0 + strip)] - layout floors the width so it always does - the
        centre lies strictly within half a pixel of that span's centre, so rounding it can never move
        an edge outside the span (test_side_taskbar_compact checks this exhaustively)."""
        return round(x_origin + (strip_width - widget_width) / 2.0)

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

        # The visible strip: work-area inset capped by the taskbar window (the window when there is no
        # inset - auto-hide / #221), anchored to the STABLE screen edge, never rect[0], which slides
        # while an auto-hide bar animates (#135). Same strip the layout sizes the width from.
        x_origin, visible_tb_width = vertical_taskbar_strip(taskbar_info, edge, dpi_scale)
        x = self._centre_in_strip(widget_width, x_origin, visible_tb_width)
        
        # Calculate Y: align to bottom (system tray side) with offset.
        tray_rect = taskbar_info.get_tray_rect()
        bottom_boundary = round(tray_rect[1] / dpi_scale) if tray_rect else (full_geom.bottom() + 1)

        offset_y = config.get('tray_offset_y', constants.config.defaults.DEFAULT_TRAY_OFFSET_Y)
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

    def calculate_free_float_default_position(self, screen: QScreen,
                                              widget_size: Tuple[int, int]) -> ScreenPosition:
        """Default position for a free-floating widget on a taskbar-less display (#188): bottom-right of
        that screen's available geometry, inset by the edge margin. Mirrors `_get_safe_fallback_position`
        but targets the given screen instead of primary."""
        try:
            screen_rect: QRect = screen.availableGeometry()
            widget_width, widget_height = widget_size
            ui_widget = getattr(constants.ui, 'widget', None)
            margin_px = ui_widget.SCREEN_EDGE_MARGIN_PX if ui_widget else 10
            x = screen_rect.right() - widget_width - margin_px
            y = screen_rect.bottom() - widget_height - margin_px
            return ScreenPosition(max(screen_rect.left(), x), max(screen_rect.top(), y))
        except Exception:
            return self._get_safe_fallback_position(widget_size)

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
                # Horizontal Constraint - center on visible taskbar area
                # Same strip as the auto-placement (#322), so a drag can't park it off the bar either.
                vis_top, vis_h = horizontal_taskbar_strip(taskbar_info, edge, dpi_scale)
                fixed_y = self._clamp_into_strip(round(vis_top + (vis_h - widget_height) / 2.0),
                                                 widget_height, vis_top, vis_h, edge)
                
                # Keep the same tray-side gap as the auto-placement, so a user can't drag the widget
                # flush against the "^" chevron (#161 pt1).
                right_boundary = (round(taskbar_info.get_tray_rect()[0] / dpi_scale) - widget_width - constants.layout.TRAY_EDGE_MIN_GAP_PX) if taskbar_info.get_tray_rect() else (screen.geometry().right() - widget_width)
                left_boundary = (round(taskbar_info.tasklist_rect[2] / dpi_scale) + constants.layout.DEFAULT_PADDING) if taskbar_info.tasklist_rect else screen.geometry().left()
                
                constrained_x = max(left_boundary, min(desired_pos.x(), right_boundary))
                return QPoint(constrained_x, fixed_y)

            elif edge in (constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT):
                # Vertical Constraint - the same strip as the auto-placement and the width.
                strip_x, strip_w = vertical_taskbar_strip(taskbar_info, edge, dpi_scale)
                fixed_x = self._centre_in_strip(widget_width, strip_x, strip_w)
                
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
        # Free-float (#188): set by refresh_float_state() when the preferred monitor is a taskbar-less
        # display we should float on instead of docking to the primary taskbar.
        self._free_float_active: bool = False
        self._free_float_screen: Optional[QScreen] = None
        # Re-seat backoff state (see reseat_above_taskbar): consecutive acted re-seats grow
        # the delay; steady-state health retires the escalation between episodes.
        self._reseat_attempts: int = 0
        self._last_reseat_ts: float = 0.0
        self._first_recovery_logged: bool = False
        # Start of the current continuous on-top stretch (is_buried_behind_taskbar);
        # None while we have not verified we are on top. Gates backoff retirement.
        self._on_top_since: Optional[float] = None
        self._last_on_top_ts: float = 0.0   # last verified on-top check (gap detection)
        # (reason, backoff window) of the last DEBUG-logged deferral - one line per window.
        self._deferral_logged: Optional[tuple] = None
        # Raised taskbar band (Start / Task View), see _taskbar_band_raised.
        self._band_raised_since: Optional[float] = None
        self._band_raised_last_ts: float = 0.0
        self._band_fallback_logged: bool = False
        # Last time the burial check saw the taskbar raised; a re-seat soon after it drops
        # back is free (no backoff). 0.0 = no pending band return.
        self._band_return_pending_ts: float = 0.0
        # One INFO line per stretch of a not-ready tray rect (see _tray_rect_ready).
        self._tray_not_ready_logged: bool = False
        # Last good tray rect per (monitor, edge), as offsets from the taskbar's right/bottom.
        self._last_good_tray: Dict[tuple, Tuple[int, int, int, int]] = {}
        
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

    def is_floating(self) -> bool:
        """True when the widget should ignore taskbar docking - either the user's Free Move, or the
        runtime free-float mode active when the preferred monitor has no taskbar of its own (#188)."""
        return bool(self._state.config.get('free_move', False)) or self._free_float_active

    def is_free_float_active(self) -> bool:
        """True only when the runtime free-float mode is live (preferred monitor has no taskbar of its
        own, #188). Unlike is_floating() this excludes plain Free Move, so callers that need to protect
        the off-taskbar float specifically - e.g. not hiding it for a fullscreen app on another monitor -
        don't also change docked Free-Move behavior."""
        return self._free_float_active

    def refresh_float_state(self) -> Optional[QScreen]:
        """Re-evaluate whether the preferred monitor is a taskbar-less display to free-float on. Sets
        `_free_float_active` / `_free_float_screen` and returns the target screen (or None). Cheap;
        called each reposition. Honors the `free_float` opt-out."""
        screen: Optional[QScreen] = None
        if self._state.config.get('free_float', True):
            preferred = self._state.config.get('preferred_monitor')
            screen = taskbar_utils.get_free_float_screen(preferred)
        self._free_float_screen = screen
        self._free_float_active = screen is not None
        return screen

    @pyqtSlot()
    def update_position(self, fresh_taskbar_info: Optional[TaskbarInfo] = None,
                        _float_refreshed: bool = False) -> None:
        """
        Main entry point to update the widget's position.
        Uses fresh taskbar info if provided, otherwise fetches it - honoring
        the `preferred_monitor` setting (#72) when present. `_float_refreshed` lets the ~1s refresh
        loop (which already called refresh_float_state() for its visibility check) skip the duplicate
        taskbar enumeration here (#188).
        """
        try:
            if fresh_taskbar_info:
                self._state.taskbar_info = fresh_taskbar_info
            else:
                preferred = self._state.config.get('preferred_monitor')
                self._state.taskbar_info = get_taskbar_info(preferred_screen_name=preferred)

            # #188: is the preferred monitor a taskbar-less display we should float on?
            if not _float_refreshed:
                self.refresh_float_state()

            # A saved (dragged) position wins in both free-move and free-float.
            if self._apply_saved_position():
                return

            # No saved position + a taskbar-less preferred display -> default-place on that display.
            if self._free_float_active and self._apply_free_float_position():
                return

            # A just-restarted taskbar reports a degenerate tray rect for seconds (live: the
            # TrayNotifyWnd sat at (0,1032,0,1032) for ~11 s until TaskbarCreated); computing
            # from it snaps the widget to the far left, over the Widgets button. Place it on the
            # new taskbar from the last good tray geometry instead.
            if not self._tray_rect_ready():
                self._apply_position_with_substitute_tray()
                return

            if self._apply_calculated_position():
                pass # success
            else:
                logger.warning("Failed to calculate position.")
        except Exception as e:
            logger.error("Error updating position: %s", e, exc_info=True)

    @staticmethod
    def _tray_memory_key(info: Any) -> Optional[tuple]:
        """(monitor, edge) the remembered tray geometry belongs to; None if it can't be told."""
        try:
            return (str(info.get_screen().name()), info.get_edge_position())
        except Exception:
            return None

    def _remember_tray(self, info: Any, tray: Tuple[int, int, int, int]) -> None:
        """Store a good tray rect as offsets from its taskbar's right/bottom edges (the tray
        hugs that corner on every edge), so it can be re-applied to a new taskbar rect."""
        key = self._tray_memory_key(info)
        if key is None or not info.hwnd:
            return
        tb_right, tb_bottom = int(info.rect[2]), int(info.rect[3])
        self._last_good_tray[key] = (tb_right - int(tray[0]), tb_bottom - int(tray[1]),
                                     tb_right - int(tray[2]), tb_bottom - int(tray[3]))

    def _apply_position_with_substitute_tray(self) -> None:
        """
        The tray rect is not ready: position on the CURRENT taskbar as if its tray had the last
        good geometry seen on this monitor + edge. With nothing remembered (the very first
        launch met a not-ready rect), use the "no tray" estimate the secondary-taskbar path
        uses (screen edge minus a clock-width reserve).
        """
        info = self._state.taskbar_info
        offsets = self._last_good_tray.get(self._tray_memory_key(info))
        tray = None
        if offsets is not None:
            tb_right, tb_bottom = int(info.rect[2]), int(info.rect[3])
            tray = (tb_right - offsets[0], tb_bottom - offsets[1],
                    tb_right - offsets[2], tb_bottom - offsets[3])
        widget = self._state.widget
        size = (widget.width(), widget.height())
        if size[0] <= 0:
            return
        pos = self._calculator.calculate_position(_TrayOverride(info, tray), size, self._state.config)
        if pos:
            self._apply_geometry(pos.x, pos.y)

    def _tray_rect_ready(self) -> bool:
        """
        False while the docked taskbar's tray rect exists but cannot be real: zero width or
        height, or not touching the taskbar at all - what a taskbar mid-(re)creation reports
        after an Explorer restart. No tray rect at all (a Win11 secondary taskbar, the fallback
        taskbar) is a known layout with its own estimate, so it counts as ready. A ready rect
        is remembered for the next not-ready stretch. Logs once per not-ready stretch.
        """
        info = self._state.taskbar_info
        ready = True
        tray = None
        try:
            tray = info.get_tray_rect() if info is not None else None
            if tray is not None:
                left, top, right, bottom = (int(v) for v in tray)
                tb_left, tb_top, tb_right, tb_bottom = (int(v) for v in info.rect)
                if right <= left or bottom <= top:
                    ready = False
                elif right <= tb_left or left >= tb_right or bottom <= tb_top or top >= tb_bottom:
                    ready = False
        except Exception:
            ready = True   # can't judge it: keep the old behaviour, never strand the widget
            tray = None
        if ready:
            if tray is not None:
                try:
                    self._remember_tray(info, tray)
                except Exception:
                    pass
            if self._tray_not_ready_logged:
                logger.debug("Taskbar tray area is ready again - repositioning.")
            self._tray_not_ready_logged = False
            return True
        if not self._tray_not_ready_logged:
            logger.info("Taskbar tray area not ready yet (rect %s) - placing the widget from the "
                        "last known tray geometry.", tray)
            self._tray_not_ready_logged = True
        return False

    def _apply_free_float_position(self) -> bool:
        """Place the widget at a sensible default on the taskbar-less preferred display (#188). Returns
        False (so the caller falls through to normal docking) if the target screen is gone or the widget
        isn't laid out yet - a later reposition retries with valid dimensions."""
        screen = self._free_float_screen
        if screen is None:
            return False
        widget_width = self._state.widget.width()
        widget_height = self._state.widget.height()
        if widget_width <= 0 or widget_height <= 0:
            return False
        pos = self._calculator.calculate_free_float_default_position(screen, (widget_width, widget_height))
        if pos is None:
            return False
        self._apply_geometry(pos.x, pos.y)
        return True

    def _apply_saved_position(self) -> bool:
        """Applies saved position if 'free_move' is enabled.

        Multi-monitor aware: the saved coordinates are looked up against the
        screen they actually belong to via QApplication.screenAt(), not the
        primary screen's taskbar. If the saved monitor has been disconnected,
        falls through to the calculated position so the widget appears near
        the tray instead of off-screen. Fixes #133.
        """
        if not self.is_floating():
            return False

        saved_x = self._state.config.get('position_x')
        saved_y = self._state.config.get('position_y')

        if not isinstance(saved_x, int) or not isinstance(saved_y, int):
            return False

        widget_width = self._state.widget.width()
        widget_height = self._state.widget.height()
        widget_size = (widget_width, widget_height)

        # Defensive: if the widget hasn't been laid out yet, its width/height
        # report 0. A zero-size widget centered at (saved_x, saved_y) would land
        # in screenAt() lookup at the top-left corner, which could be on a
        # *different* screen than the actual widget will eventually occupy.
        # Fall through to the calculated position; a follow-up update_position()
        # after layout completes will retry with valid dimensions.
        if widget_width <= 0 or widget_height <= 0:
            logger.debug(
                "Widget size not yet known (w=%s, h=%s); deferring saved-position restore.",
                widget_width, widget_height,
            )
            return False

        # Use the widget's center to identify which monitor the saved position
        # belongs to. This handles the case where the user drags the widget to
        # a secondary screen - the primary-screen taskbar in self._state would
        # otherwise reject coordinates that are perfectly valid on monitor 2.
        center = QPoint(saved_x + widget_width // 2, saved_y + widget_height // 2)
        screen = QApplication.screenAt(center)

        if screen is None:
            logger.info(
                "Saved free-move position (%s,%s) is on a disconnected monitor; "
                "falling back to calculated position.",
                saved_x, saved_y,
            )
            return False

        clamped = ScreenUtils.validate_position(saved_x, saved_y, widget_size, screen)
        if (clamped.x, clamped.y) != (saved_x, saved_y):
            logger.info(
                "Clamped saved free-move position (%s,%s) onto screen '%s' -> (%s,%s).",
                saved_x, saved_y, stable_pseudonym(screen.name(), "DISPLAY"), clamped.x, clamped.y,
            )
        else:
            logger.info(
                "Restored saved free-move position (%s,%s) on screen '%s'.",
                saved_x, saved_y, stable_pseudonym(screen.name(), "DISPLAY"),
            )
        self._apply_geometry(clamped.x, clamped.y)
        return True

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
        if self.is_floating() or not self._state.widget.isVisible():
            return

        try:
            # We re-fetch info here to be accurate, honoring preferred monitor.
            preferred = self._state.config.get('preferred_monitor')
            tb_info = get_taskbar_info(preferred_screen_name=preferred)
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
             preferred = self._state.config.get('preferred_monitor')
             self._state.taskbar_info = get_taskbar_info(preferred_screen_name=preferred)

        # FIX for #87: specific check for Free Move (and #188 free-float - both are "floating")
        if self.is_floating():
            # If floating, only constrain to screen bounds (prevent total loss)
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
        Resets the widget to its default position: clears the saved free-move spot AND the docked
        drag offsets (tray_offset_x / tray_offset_y - a docked drag's offset otherwise outlived every
        reset), and repositions.
        """
        defaults = constants.config.defaults
        self._state.config['position_x'] = None
        self._state.config['position_y'] = None
        self._state.config['tray_offset_x'] = defaults.DEFAULT_TRAY_OFFSET_X
        self._state.config['tray_offset_y'] = defaults.DEFAULT_TRAY_OFFSET_Y
        self._last_applied_geometry = None
        # We rely on the caller (Widget) to persist this config change to disk if needed.
        self.update_position()
        self.ensure_topmost()

    def _ensure_taskbar_owner(self, hwnd: int) -> bool:
        """
        Dock the widget to the taskbar by making it an OWNED window of the taskbar
        (GWLP_HWNDPARENT). The window manager then keeps the widget above its owner
        at all times, so shell activity (Start menu, quick-settings, taskbar clicks)
        can no longer raise the taskbar over the widget and occlude it.

        Re-checked on the topmost cadence because Qt can reset the owner across
        show/flag changes, and the taskbar HWND changes on an explorer restart
        (we re-own to the fresh handle automatically via _state.taskbar_info). The
        check is a cheap no-op once the owner is already correct, so in steady state
        it never touches Z-order -- which is what keeps open shell menus undisturbed
        (see the #200 note in ensure_topmost()).

        Returns:
            True iff the owner was just (re)established on this call.
        """
        try:
            tb_info = self._state.taskbar_info or get_taskbar_info()
            tb_hwnd = int(tb_info.hwnd) if tb_info and tb_info.hwnd else 0
            if not tb_hwnd or not win32gui.IsWindow(tb_hwnd):
                return False
            if (_GetWindowLongPtr(hwnd, _GWLP_HWNDPARENT) or 0) != tb_hwnd:
                _SetWindowLongPtr(hwnd, _GWLP_HWNDPARENT, tb_hwnd)
                logger.debug("Widget docked to taskbar HWND %s (owner Z-order).", tb_hwnd)
                return True
        except Exception as e:
            logger.error("Failed to set taskbar owner: %s", e)
        return False

    def ensure_topmost(self) -> None:
        """
        Keep the widget docked above the taskbar -- WITHOUT re-inserting it at the top of
        the topmost Z-band on every call.

        The widget is an OWNED window of the taskbar (_ensure_taskbar_owner). That owner
        relationship alone keeps it above the taskbar through all shell activity (Start
        menu, taskbar clicks, the taskbar being explicitly raised) -- an owned window is
        always kept above its owner. So no periodic HWND_TOPMOST re-assert is needed to
        stop the taskbar occluding the widget.

        Why the old re-assert was actively harmful (#200): the previous code called
        SetWindowPos(HWND_TOPMOST) here on every foreground change and on a ~1 Hz timer.
        Because the widget is owned by the taskbar, each assert dragged the taskbar's whole
        owner-cluster back up to the top of the topmost band -- ABOVE any classic shell
        menu/flyout that happened to be open (taskbar right-click "Close", "Safely remove
        hardware", jump lists, ...). That clipped exactly the menu rows overlapping the
        taskbar strip. Measured with a genuine #32768 menu: with the periodic assert the
        taskbar sat above the open menu in 23/23 samples; without it, 0/23.

        We therefore assert HWND_TOPMOST only ONCE, at the moment we (re)establish the
        owner -- first dock at startup and after an explorer restart -- when no such menu
        can be up. In steady state this method only re-verifies the (unchanged) owner and
        reorders nothing.
        """
        try:
            hwnd = int(self._state.widget.winId())
            if not win32gui.IsWindow(hwnd):
                return

            # Dock (or re-dock) to the taskbar. Only reorders Z when the owner is actually
            # (re)established; a single topmost assert then seats it in the band. This
            # never runs in steady state, so open shell menus are left alone (#200).
            if self._ensure_taskbar_owner(hwnd):
                win32gui.SetWindowPos(
                    hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                )
            # A widget that fell out of the topmost band (Qt can drop WS_EX_TOPMOST across
            # show/flag changes) is necessarily BELOW the topmost taskbar, so it is "buried"
            # in is_buried_behind_taskbar()'s terms and the widget's guarded recovery path
            # (_recover_if_buried: menu / drag / fullscreen guards, backoff, deferral logging)
            # restores it with the safe seat. Nothing is re-seated from here (#327 review).
        except Exception as e:
            logger.error("Failed to ensure topmost status: %s", e)

    @pyqtSlot()
    def enforce_topmost_status(self) -> None:
        """
        Periodically ensures the widget's topmost status.
        """
        if self._state.widget.isVisible():
            self.ensure_topmost()

    def reset_reseat_state(self) -> None:
        """Forget the burial-recovery backoff: a fresh taskbar (explorer restart) is a fresh start."""
        self._reseat_attempts = 0
        self._last_reseat_ts = 0.0
        self._first_recovery_logged = False
        self._on_top_since = None
        self._deferral_logged = None
        self._band_raised_since = None
        self._band_raised_last_ts = 0.0
        self._band_fallback_logged = False
        self._band_return_pending_ts = 0.0

    def _taskbar_band_raised(self, tb: int, now: float) -> Optional[int]:
        """
        The taskbar's band when it is known to be ABOVE the desktop band (Start / Task View
        open), else None. A raised stretch that lasts over _BAND_RAISED_FALLBACK_S (observed
        continuously, same gap rule as the on-top stretch) is a shell that keeps the taskbar
        there: log once at INFO and report None, so the Z-order is checked as before.
        """
        band = _window_band(tb)
        if band is None or band <= _ZBID_DESKTOP:
            self._band_raised_since = None
            self._band_fallback_logged = False
            return None
        if self._band_raised_since is None or now - self._band_raised_last_ts > _ON_TOP_MAX_GAP_S:
            self._band_raised_since = now
        self._band_raised_last_ts = now
        if now - self._band_raised_since > _BAND_RAISED_FALLBACK_S:
            if not self._band_fallback_logged:
                logger.info("Taskbar has stayed in Z-band %s for over %.0fs - checking burial "
                            "as if it were in the normal band.", band, _BAND_RAISED_FALLBACK_S)
                self._band_fallback_logged = True
            return None
        return band

    def _docked_taskbar_hwnd(self, hwnd: int) -> int:
        """
        The taskbar the widget is docked to: its owner (GWLP_HWNDPARENT) when that is a
        taskbar window - the primary or a secondary one - else the detected taskbar. 0 if
        neither is a live window.
        """
        try:
            owner = int(_GetWindowLongPtr(hwnd, _GWLP_HWNDPARENT) or 0)
            if owner and win32gui.IsWindow(owner) and \
                    win32gui.GetClassName(owner) in _SHELL_TASKBAR_CLASSES:
                return owner
        except Exception:
            pass
        tb_info = self._state.taskbar_info
        tb = int(tb_info.hwnd) if tb_info and tb_info.hwnd else 0
        return tb if tb and win32gui.IsWindow(tb) else 0

    @staticmethod
    def _is_above_in_zorder(hwnd: int, other: int) -> Optional[bool]:
        """
        True when hwnd is above `other` in the Z-order, False when it is below, None when
        the walk could not tell (it never acts on a guess). Walks GW_HWNDPREV from `other`
        towards the top: a dozen steps for the taskbar.
        """
        cur = other
        for _ in range(_ZORDER_WALK_LIMIT):
            cur = win32gui.GetWindow(cur, _GW_HWNDPREV) or 0
            if not cur:
                return False
            if cur == hwnd:
                return True
        return None

    def is_buried_behind_taskbar(self) -> bool:
        """
        True when the widget is visible but sits BELOW its taskbar in Z-order - invisible to
        the user although isVisible() is True (#314, #238).

        The owner link (GWLP_HWNDPARENT) is supposed to make that impossible, yet the shell
        does it anyway (pasharm: maximize + restore on Win10 raises the taskbar over the
        widget with the owner link intact; kamal63442 saw it on a fresh Win11 launch). The
        check is a pure Z-order comparison: no hit-test, so no DPI conversion and no need to
        tell a covering menu from the taskbar - a menu above the widget is not burial, and
        a widget under the taskbar is burial even while a menu covers its centre.

        Also tracks how long the widget has stayed on top, which retires the re-seat backoff.
        """
        try:
            widget = self._state.widget
            if not widget.isVisible():
                self.note_on_top_unverified()
                return False
            hwnd = int(widget.winId())
            if not hwnd or not win32gui.IsWindow(hwnd):
                self.note_on_top_unverified()
                return False
            tb = self._docked_taskbar_hwnd(hwnd)
            if not tb:
                self.note_on_top_unverified()
                return False
            raised = self._taskbar_band_raised(tb, time.monotonic())
            if raised is not None:
                # Start / Task View is open: the Z-order walk cannot verify anything (PR #327 B1).
                # Closing it buries the widget; that re-seat must not count as a fight (M1).
                self._band_return_pending_ts = time.monotonic()
                self.note_on_top_unverified()
                self._log_deferral(
                    "tb-band", "Burial check deferred: the taskbar is in Z-band %s (Start / Task View).",
                    raised)
                return False
            above = self._is_above_in_zorder(hwnd, tb)
            if above is None:
                self.note_on_top_unverified()
                return False
            if above:
                now = time.monotonic()
                if self._on_top_since is None or now - self._last_on_top_ts > _ON_TOP_MAX_GAP_S:
                    self._on_top_since = now
                elif (self._reseat_attempts or self._last_reseat_ts) and \
                        now - self._on_top_since >= _RESEAT_HEALTHY_S:
                    # The last re-seat held: retire the escalation, so the next burial
                    # recovers at once and logs at INFO again.
                    self.reset_reseat_state()
                    self._on_top_since = now
                self._last_on_top_ts = now
                return False
            self._on_top_since = None
            return True
        except Exception as e:
            self.note_on_top_unverified()
            logger.debug("Buried-behind-taskbar check failed: %s", e)
            return False

    def note_on_top_unverified(self) -> None:
        """
        A check that could not confirm the widget is on top (hidden, a fullscreen app, our
        own menu open, ...) breaks the continuous on-top stretch that retires the backoff -
        otherwise a hide/show in the middle of a fight would count as "held" (#327 review).
        """
        self._on_top_since = None

    def reseat_above_taskbar(self) -> bool:
        """
        Re-insert the widget directly above its taskbar in Z-order (pasharm's insertion
        point): hWndInsertAfter = the window right above the taskbar, or HWND_TOPMOST when
        the taskbar is first in the Z-order. Flags: NOMOVE | NOSIZE | NOACTIVATE |
        NOOWNERZORDER - never moves, never takes focus, never drags the taskbar's owner
        cluster (#200).

        Because everything already above the taskbar stays above the widget, this cannot
        clip an open menu, jump list or flyout, and it never lifts the widget over a
        fullscreen app: the shell drops the taskbar out of the topmost band under one, and
        the re-seat refuses to follow it there.

        Guards, in order (each one defers; the next event or the 1 s tick retries):
        backoff -> taskbar not topmost -> taskbar out of its normal Z-band -> widget in another
        Z-band -> already seated.

        Returns True when the widget was actually re-seated.
        """
        try:
            hwnd = int(self._state.widget.winId())
            if not hwnd or not win32gui.IsWindow(hwnd):
                return False
            now = time.monotonic()
            # The burial the shell leaves behind when Start / Task View closes: re-seat at once
            # and charge nothing, or quick repeated Win+Tab escalates the backoff (M1).
            band_return = bool(self._band_return_pending_ts) and                 now - self._band_return_pending_ts <= _BAND_RETURN_GRACE_S
            delay = self._reseat_delay()
            if not band_return and self._last_reseat_ts and (now - self._last_reseat_ts) < delay:
                self._log_deferral("backoff", "Re-seat deferred: backoff (%.0fs window).", delay)
                return False
            tb = self._docked_taskbar_hwnd(hwnd)
            if not tb:
                return False
            if not (win32gui.GetWindowLong(tb, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST):
                # A fullscreen app (or anything else) took the taskbar out of the topmost band:
                # following it down would put the widget under that window - leave it alone.
                self._log_deferral("tb-not-topmost", "Re-seat deferred: the taskbar is not topmost.")
                return False
            raised = self._taskbar_band_raised(tb, now)
            if raised is not None:
                self._log_deferral(
                    "tb-band", "Re-seat deferred: the taskbar is in Z-band %s (Start / Task View).", raised)
                return False
            own_band, tb_band = _window_band(hwnd), _window_band(tb)
            if own_band is not None and tb_band is not None and own_band != tb_band:
                self._log_deferral(
                    "band", "Re-seat deferred: taskbar is in Z-band %s, the widget in %s.",
                    tb_band, own_band)
                return False
            prev = int(win32gui.GetWindow(tb, _GW_HWNDPREV) or 0)
            if prev == hwnd:
                return False  # already directly above the taskbar
            insert_after = prev if prev else win32con.HWND_TOPMOST
            try:
                win32gui.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, _RESEAT_SWP_FLAGS)
            except Exception as e:
                if prev and not win32gui.IsWindow(prev):
                    # The window above the taskbar (often a transient tooltip) was destroyed
                    # between GetWindow and SetWindowPos. Benign: no backoff charged, the
                    # next Z-order event or tick retries against the new neighbour.
                    logger.debug("Re-seat raced a closing window (%s) - retrying on the next check.", e)
                    return False
                raise
            self._deferral_logged = None
            if band_return:
                self._band_return_pending_ts = 0.0   # consumed; the backoff is untouched
                logger.debug("Widget re-seated above the taskbar after Start / Task View closed "
                             "(no backoff charged).")
                return True
            self._last_reseat_ts = now
            self._reseat_attempts = min(self._reseat_attempts + 1, _RESEAT_BACKOFF_EXP_CAP + 1)
            self._deferral_logged = None
            if self._first_recovery_logged:
                logger.debug("Widget re-seated above the taskbar (attempt %d).", self._reseat_attempts)
            else:
                logger.info("Widget recovered from taskbar burial - re-seated directly above the taskbar.")
                self._first_recovery_logged = True
            return True
        except Exception as e:
            logger.error("Failed to re-seat the widget above the taskbar: %s", e)
            return False

    def _log_deferral(self, reason: str, msg: str, *args) -> None:
        """DEBUG-log a deferred re-seat once per (reason, backoff window), not once per tick."""
        key = (reason, self._last_reseat_ts)
        if self._deferral_logged != key:
            self._deferral_logged = key
            logger.debug(msg, *args)

    def _reseat_delay(self) -> float:
        """Seconds of quiet required before the next re-seat: 0 first, then doubling, capped."""
        if not self._last_reseat_ts:
            return 0.0
        return min(_RESEAT_BACKOFF_BASE_S * (2 ** min(self._reseat_attempts - 1, _RESEAT_BACKOFF_EXP_CAP)),
                   _RESEAT_BACKOFF_MAX_S)
