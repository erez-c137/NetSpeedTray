"""
Core Position Manager for NetworkSpeedTray.

This module consolidates all widget positioning logic, including:
1. Calculation of optimal positions relative to the taskbar.
2. Active monitoring of position drift.
3. specific monitoring of tray geometry changes (Smart Polling).
4. Handling of drag constraints.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from collections import deque
from typing import Optional, Tuple, List, Protocol, runtime_checkable, Dict, Any, Callable, Deque, TYPE_CHECKING
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


def _is_access_denied(exc: Exception) -> bool:
    """True for a pywin32 error carrying ERROR_ACCESS_DENIED (winerror or the first arg)."""
    code = getattr(exc, "winerror", None)
    if code is None and getattr(exc, "args", None):
        code = exc.args[0]
    return code == _ERROR_ACCESS_DENIED


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
# Since rc.2 the backoff is charged only for a burial found within the fight window of our own
# last re-seat (_BURIAL_FIGHT_WINDOW_S below): pasharm toggled maximize/restore faster than the
# 10 s reset and the widget sat out 1/2/4 s windows. A later burial is fresh: immediate, free.
_RESEAT_BACKOFF_BASE_S = 1.0
_RESEAT_BACKOFF_MAX_S = 60.0
_RESEAT_BACKOFF_EXP_CAP = 6
_RESEAT_HEALTHY_S = 10.0
# Only a burial found within this long of our own last re-seat is charged to the backoff (a
# real fight re-buries within a frame or two); a later one is fresh - immediate and free. The
# rationale lives with the constant (constants/timeouts.py, BURIAL_FIGHT_WINDOW_MS).
_BURIAL_FIGHT_WINDOW_S = constants.timeouts.BURIAL_FIGHT_WINDOW_MS / 1000.0
# ... unless fresh burials come faster than a person can cause them (rationale with the constants).
_BURIAL_FRESH_RATE_MAX = constants.timeouts.BURIAL_FRESH_RATE_MAX
_BURIAL_FRESH_RATE_WINDOW_S = constants.timeouts.BURIAL_FRESH_RATE_WINDOW_MS / 1000.0
# A recovery this close to a dock / tray change logs at DEBUG and keeps the INFO line (rc.2 W8).
_BURIAL_LOG_SETTLE_S = constants.timeouts.BURIAL_LOG_SETTLE_MS / 1000.0
_ERROR_ACCESS_DENIED = 5
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


@dataclass(frozen=True, slots=True)
class SideBarGeometry:
    """A native side taskbar's spans along its length, logical px (bottoms exclusive).

    ``apps_*`` is the app-button group (Start .. last app, plus every other non-tray button such as the
    "..." overflow), None when unknown; ``limit_top`` is where a widget may start - below the Widgets
    button when there is one at the top, else the bar's top. ``end_top`` is where the tray END starts
    for placement: the tray's top, or - "Top" icon alignment, where Win11 moves the Widgets button down
    beside the tray - the Widgets button's top (rc.1 live test B4). ``tray_top`` stays the tray itself:
    a user's drag offset (``tray_offset_y``) is measured from it."""
    bar_top: float
    tray_top: float
    limit_top: float
    apps_top: Optional[float]
    apps_bottom: Optional[float]
    end_top: Optional[float] = None
    # The "..." overflow button's span (rc.2 L2): the only way to reach the apps Win11 hides on a
    # full bar, so a no-room widget never covers it. None when there is none / it is unknown.
    overflow_top: Optional[float] = None
    overflow_bottom: Optional[float] = None

    @property
    def end(self) -> float:
        return self.tray_top if self.end_top is None else self.end_top


@dataclass(frozen=True, slots=True)
class SidePlacement:
    """Where the automatic side-bar placement put the widget (decided rarely - see PositionManager).

    ``mode``: "tray" (tray end, the default), "top" (the gap between the Widgets button / bar top and
    the first app) or "tray_no_room" (tray end although the apps reach it - the user gets a one-time
    "you can drag it" heads-up). ``y`` is the absolute logical y for "top", else None."""
    mode: str
    y: Optional[int] = None


_FROM_INFO = object()


def _secondary_side_clock_top(taskbar_info: TaskbarInfo, rect: Optional[Tuple[int, int, int, int]],
                              scale: float, bar_top: float, bar_bottom: float) -> float:
    """Where the tray end starts (logical y) on a side bar with no tray rect - a Win11 SECONDARY
    taskbar (Shell_SecondaryTrayWnd has no TrayNotifyWnd), or a main bar whose tray is not ready.

    Its clock still sits at the bottom (rc.2 final VM session: the #434 mirrors were painted over
    it, because the tray end used to be the bar's bottom edge). Prefer the clock UIA last saw on
    THIS bar - only when it lies inside the bar's current rect, in its lower half, so a rect probed
    on an older layout of the same HWND can't be used - else reserve
    SECONDARY_SIDE_CLOCK_RESERVE_PX, the horizontal path's clock-width reserve turned on end."""
    try:
        probed = taskbar_utils.valid_rect(
            taskbar_utils.probed_tray_rect(getattr(taskbar_info, "hwnd", None)))
    except Exception:
        probed = None
    if probed is not None and rect is not None:
        inside = (probed[0] >= rect[0] and probed[2] <= rect[2]
                  and probed[1] >= rect[1] and probed[3] <= rect[3])
        top = probed[1] / scale
        if inside and top > (bar_top + bar_bottom) / 2.0:
            return top
    return max(bar_top, bar_bottom - constants.layout.SECONDARY_SIDE_CLOCK_RESERVE_PX)


def side_bar_geometry(taskbar_info: TaskbarInfo, dpi_scale: float, *, apps: Any = _FROM_INFO,
                      widgets: Any = _FROM_INFO, overflow: Any = None) -> Optional[SideBarGeometry]:
    """The spans the side-bar placement and drag limits work from, or None when this is not a NATIVE
    side bar we own the rules for: a bar with a real ``ToolbarWindow32`` task list (ExplorerPatcher,
    Win10-style) keeps today's tasklist-based behaviour unchanged.

    ``apps`` / ``widgets`` override the TaskbarInfo's ``apps_extent`` / ``widgets_extent`` (the
    placement decision passes one consistent probe snapshot instead of fields read at another time)."""
    if taskbar_utils.valid_rect(getattr(taskbar_info, "tasklist_rect", None)) is not None:
        return None
    rect = taskbar_utils.valid_rect(getattr(taskbar_info, "rect", None))
    screen = taskbar_info.get_screen()
    if screen is None:
        return None
    scale = dpi_scale if dpi_scale and dpi_scale > 0 else 1.0
    full = screen.geometry()
    bar_top = rect[1] / scale if rect else float(full.top())
    bar_bottom = rect[3] / scale if rect else float(full.bottom() + 1)
    tray = taskbar_utils.valid_rect(taskbar_info.get_tray_rect())
    if tray:
        tray_top = tray[1] / scale
    else:
        tray_top = _secondary_side_clock_top(taskbar_info, rect, scale, bar_top, bar_bottom)

    if apps is _FROM_INFO:
        apps = getattr(taskbar_info, "apps_extent", None)
    apps = taskbar_utils.valid_rect(apps)
    apps_top = apps[1] / scale if apps else None
    apps_bottom = apps[3] / scale if apps else None

    # Never park the widget on the Widgets button. With Center alignment it sits at the TOP of a side
    # bar (above Start): the widget may start below it. With "Top" alignment Win11 moves it down to
    # just above the tray (owner's PC 2026-10-22: y 852-896, tray from ~900): it then belongs to the
    # tray end, and the widget goes above it. Prefer the layout probe's full button box, else the
    # #200 probe's content rect.
    limit_top = bar_top
    end_top = None
    if widgets is _FROM_INFO:
        widgets = getattr(taskbar_info, "widgets_extent", None)
    widgets = (taskbar_utils.valid_rect(widgets)
               or taskbar_utils.valid_rect(getattr(taskbar_info, "widgets_rect", None)))
    if widgets is not None:
        w_top, w_bottom = widgets[1] / scale, widgets[3] / scale
        if apps_top is not None and apps_bottom is not None:
            above = w_bottom <= apps_top
            below = w_top >= apps_bottom
        else:                                   # apps unknown: whichever half of the bar it is in
            middle = (w_top + w_bottom) / 2.0
            above = middle < (bar_top + tray_top) / 2.0
            below = not above
        if above and bar_top <= w_bottom <= tray_top:
            limit_top = w_bottom
        elif below and bar_top <= w_top < tray_top:
            end_top = w_top
    overflow = taskbar_utils.valid_rect(overflow)
    return SideBarGeometry(bar_top=bar_top, tray_top=tray_top, limit_top=limit_top,
                           apps_top=apps_top, apps_bottom=apps_bottom, end_top=end_top,
                           overflow_top=overflow[1] / scale if overflow else None,
                           overflow_bottom=overflow[3] / scale if overflow else None)


def side_bar_y_limits(geo: SideBarGeometry, widget_height: int) -> Tuple[int, int]:
    """``(y_min, y_max)`` a side-bar widget may sit at: from below the Widgets button (or the bar
    top) down to the tray end (the tray, or a Widgets button beside it) minus the #161 gap. When the
    widget can't fit between the two, both are the tray-end y (never above the bar top)."""
    gap = constants.layout.TRAY_EDGE_MIN_GAP_PX
    y_max = math.floor(geo.end - gap - widget_height)
    y_min = math.ceil(geo.limit_top)
    if y_max < y_min:
        y_max = max(y_max, math.ceil(geo.bar_top))
        return y_max, y_max
    return y_min, y_max


def choose_side_placement(geo: SideBarGeometry, widget_height: int) -> SidePlacement:
    """The side-bar placement rule (side-taskbar design §3):

    1. tray end, when the gap between the last app and the tray fits the widget (or the apps'
       extent is unknown - today's behaviour);
    2. else the top gap, between the Widgets button (or the bar top) and the first app, centred;
    3. else tray end anyway, flagged so the user is told once that they can drag it - but never on
       the "..." overflow button (rc.2 L2): when the tray-end spot would cover it, the widget goes
       just above it instead (covering an app button, which "..." still reaches).
    """
    clearance = constants.layout.DEFAULT_PADDING
    y_min, y_max = side_bar_y_limits(geo, widget_height)
    if geo.apps_top is None or geo.apps_bottom is None:
        return SidePlacement("tray")
    if geo.apps_bottom + clearance <= y_max:
        return SidePlacement("tray")
    room = geo.apps_top - geo.limit_top
    if room >= widget_height + 2 * clearance:
        y = round(geo.limit_top + (room - widget_height) / 2.0)
        return SidePlacement("top", max(y_min, min(y, y_max)))
    y = y_max
    if (geo.overflow_top is not None and geo.overflow_bottom is not None
            and y < geo.overflow_bottom and y + widget_height > geo.overflow_top):
        y = max(y_min, math.floor(geo.overflow_top - widget_height))
        return SidePlacement("tray_no_room", y)
    return SidePlacement("tray_no_room")


def user_placed_side_offset(config: Dict[str, Any]) -> bool:
    """True when the user dragged the widget along a side bar: the drag saves ``tray_offset_y``, and a
    side-bar drag can't produce the default (the drag limit keeps it >= TRAY_EDGE_MIN_GAP_PX, above
    the default 3). Reset position puts the default back. The user's drag always wins."""
    default = constants.config.defaults.DEFAULT_TRAY_OFFSET_Y
    value = config.get('tray_offset_y', default)
    return isinstance(value, int) and not isinstance(value, bool) and value != default


def user_placed_horizontal_offset(config: Mapping) -> bool:
    """True when the user dragged the widget along a top/bottom bar: the drop saves ``tray_offset_x``
    (measured from the tray). The default, and the pre-1.3.0 defaults older configs still carry
    (LEGACY_DEFAULT_TRAY_OFFSETS_X, rc.2 W6), are not a choice the user made."""
    defaults = constants.config.defaults
    value = config.get('tray_offset_x', defaults.DEFAULT_TRAY_OFFSET_X)
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    return value != defaults.DEFAULT_TRAY_OFFSET_X and value not in defaults.LEGACY_DEFAULT_TRAY_OFFSETS_X


def widgets_button_span_x(taskbar_info: Any, dpi_scale: float) -> Optional[Tuple[float, float]]:
    """The Win11 Widgets/weather button's x span (logical px) on a top/bottom bar, or None when unknown.
    Prefers the layout probe's full button box (``widgets_extent`` - the button's whole click target,
    which reaches past the weather text), else the #200 probe's content rect (``widgets_rect``) - the
    same preference as side_bar_geometry."""
    rect = (taskbar_utils.valid_rect(getattr(taskbar_info, "widgets_extent", None))
            or taskbar_utils.valid_rect(getattr(taskbar_info, "widgets_rect", None)))
    if rect is None:
        return None
    scale = dpi_scale if dpi_scale and dpi_scale > 0 else 1.0
    return rect[0] / scale, rect[2] / scale


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

    def calculate_position(self, taskbar_info: TaskbarInfo, widget_size: Tuple[int, int], config: Dict[str, Any],
                           side_placement: Optional[SidePlacement] = None,
                           widgets_step: bool = True) -> ScreenPosition:
        """
        Calculates the widget's optimal position based on taskbar edge and tray location.

        Args:
            taskbar_info: TaskbarInfo object with position/edge information
            widget_size: Tuple of (width, height) in pixels
            config: Configuration dictionary with positioning options
            side_placement: the automatic side-bar placement PositionManager decided (None = tray end)
            widgets_step: False keeps a top/bottom bar's default spot by the tray even with a Widgets
                button beside it (the rc.2 spot) - the #24 yield's full-bar fallback (rc.3)
            
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
            # Any read-only mapping: a "Show on all taskbars" mirror passes its config overlay (#229).
            if config is None or not isinstance(config, Mapping):
                raise ValueError(f"config must be a mapping, got {config}")
            
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
                x, y = self._calculate_horizontal_position(taskbar_info, widget_size, config, dpi_scale,
                                                           widgets_step)
            elif edge in (constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT):
                # Vertical taskbar: calculate X position (centered in taskbar gap)
                x, y = self._calculate_vertical_position(taskbar_info, widget_size, config, dpi_scale,
                                                         side_placement)
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
                                       config: Dict[str, Any], dpi_scale: float,
                                       widgets_step: bool = True) -> Tuple[int, int]:
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
            # Classic toolbars (Desktop / Links, Win10) between the apps and the tray: hug their left
            # edge instead of drawing over them (#324, best-effort). Always None on Win11.
            toolbars = taskbar_utils.valid_rect(getattr(taskbar_info, "toolbars_rect", None))
            if toolbars is not None and toolbars[0] < tray_rect[0]:
                right_boundary = round(toolbars[0] / dpi_scale)
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

        # The Win11 Widgets/weather button beside the tray (a LEFT-aligned bar puts it there; a centred
        # bar keeps it at the far left, where this never applies) belongs to the tray END, as on a side
        # bar (side_bar_geometry): the DEFAULT spot sits left of it, WIDGETS_BUTTON_MIN_GAP_PX clear,
        # instead of on its right half with the arrows touching the weather text (rc.3 VM 2026-10-29).
        # A position the user chose (a drag's tray_offset_x) is never overridden: the user is free to
        # sit in the gap, overlap the button or move it (#200/#24), and gets the one-time #200 nudge
        # (main.py) when it covers the weather. The button only resizes when the weather text does, so
        # the widget follows it the way it follows a tray that gains an icon.
        # widgets_step=False: the #24 yield found the app buttons reaching that spot while the tray spot
        # is still clear (a left-aligned bar full up to the Widgets button) AND that tray spot clears
        # the weather text/icon (#466) - it stays by the tray instead of hiding (AppOverlapYield's
        # retreat). Otherwise the yield hides it, as on any full bar.
        if widgets_step and tray_rect and not user_placed_horizontal_offset(config):
            x = self._clear_of_widgets_button(taskbar_info, x, widget_width, right_boundary, dpi_scale,
                                              full_geom.left())

        # Safety check: don't overlap with app icons on left
        if x < left_boundary:
            logger.warning("Calculated position overlaps app icons; snapping to safe zone.")
            x = round(left_boundary + constants.layout.DEFAULT_PADDING)

        return x, y

    @staticmethod
    def _clear_of_widgets_button(taskbar_info: Any, x: int, widget_width: int, right_boundary: float,
                                 dpi_scale: float, screen_left: int) -> int:
        """``x`` moved left of the Widgets button when the button lies between the widget's default
        spot and the tray and the widget would cover it or come within WIDGETS_BUTTON_MIN_GAP_PX of
        it; else ``x`` unchanged (also when the spot left of it would leave the screen)."""
        span = widgets_button_span_x(taskbar_info, dpi_scale)
        if span is None:
            return x
        w_left, w_right = span
        gap = constants.layout.WIDGETS_BUTTON_MIN_GAP_PX
        # Tray side only: the button ends at (or before) the tray boundary. One logical px of slack
        # absorbs fractional-DPI rounding of the two rects.
        if not (w_left < right_boundary and w_right <= right_boundary + 1):
            return x
        if x >= w_right + gap or x + widget_width + gap <= w_left:
            return x
        new_x = math.floor(w_left - gap - widget_width)
        if new_x < screen_left:
            return x
        return new_x

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
                                     config: Dict[str, Any], dpi_scale: float,
                                     side_placement: Optional[SidePlacement] = None) -> Tuple[int, int]:
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
        
        offset_y = config.get('tray_offset_y', constants.config.defaults.DEFAULT_TRAY_OFFSET_Y)

        # A native Win11 side bar (no ToolbarWindow32): the Phase B rules - the automatic placement
        # unless the user dragged it, and either way clamped to the bar (so a stale offset saved on
        # another layout can't park it on the Widgets button or off the bar).
        geo = side_bar_geometry(taskbar_info, dpi_scale)
        if geo is not None:
            y_min, y_max = side_bar_y_limits(geo, widget_height)
            if user_placed_side_offset(config):
                y = round(geo.tray_top - widget_height - offset_y)
            elif (side_placement is not None and side_placement.mode in ("top", "tray_no_room")
                  and side_placement.y is not None):
                y = side_placement.y
            else:
                y = y_max                       # tray end, #161 gap
            return x, max(y_min, min(y, y_max))

        # Calculate Y: align to bottom (system tray side) with offset.
        tray_rect = taskbar_info.get_tray_rect()
        bottom_boundary = round(tray_rect[1] / dpi_scale) if tray_rect else (full_geom.bottom() + 1)
        # Classic toolbars above the tray on a Win10 side bar (#324, best-effort): sit above them.
        toolbars = taskbar_utils.valid_rect(getattr(taskbar_info, "toolbars_rect", None))
        if tray_rect and toolbars is not None and toolbars[1] < tray_rect[1]:
            bottom_boundary = round(toolbars[1] / dpi_scale)

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

                # Native Win11 side bar: from below the Widgets button (or the bar top) to the tray
                # top minus the #161 gap - the same limits the placement clamps to.
                geo = side_bar_geometry(taskbar_info, dpi_scale)
                if geo is not None:
                    y_min, y_max = side_bar_y_limits(geo, widget_height)
                    return QPoint(fixed_x, max(y_min, min(desired_pos.y(), y_max)))

                # Keep within the safe zone, but allow bottom-alignment as calculated choice
                bottom_boundary = (round(taskbar_info.get_tray_rect()[1] / dpi_scale) - widget_height - constants.layout.DEFAULT_PADDING) if taskbar_info.get_tray_rect() else (screen.geometry().bottom() - widget_height)
                top_boundary = (round(taskbar_info.tasklist_rect[3] / dpi_scale) + constants.layout.DEFAULT_PADDING) if taskbar_info.tasklist_rect else screen.geometry().top()
                
                constrained_y = max(top_boundary, min(desired_pos.y(), bottom_boundary))
                return QPoint(fixed_x, constrained_y)
            
            return desired_pos

        except Exception as e:
            logger.error("Error dragging constraint: %s", e, exc_info=True)
            return None


class _TaggedLogger(logging.LoggerAdapter):
    """Prefixes each message with ``[<tag>]`` (see PositionManager._logger)."""

    def process(self, msg, kwargs):
        return "[%s] %s" % (self.extra["tag"], msg), kwargs


class PositionManager(QObject):
    """
    Orchestrates all positioning logic, including calculation, application,
    and active monitoring of system changes.
    """

    # The logger every method writes to. An instance created with a ``log_tag`` (the "Show on all
    # taskbars" mirrors, #229) gets an adapter that prefixes its lines, so a support bundle tells
    # which window was buried / re-seated / re-docked. Untagged (the main widget): this module logger.
    _logger: Any = logger

    def __init__(self, window_state: WindowState, parent: Optional[QObject] = None,
                 log_tag: str = "", side_watcher_enabled: bool = True, *,
                 app_yield_enabled: bool = True) -> None:
        """``app_yield_enabled=False``: no #24 app-button yield (a paint-only mirror's manager, #434 -
        mirrors don't yield in v1; the yield then never probes and never hides anything)."""
        super().__init__(parent)
        self._state = window_state
        if log_tag:
            self._logger = _TaggedLogger(logger, {"tag": log_tag})
        # The side-taskbar watcher (#406: a WinEvent hook thread + UIA probes that keep a native side
        # bar's placement right as apps come and go) is for the MAIN widget only. A "Show on all
        # taskbars" mirror (#229) passes False: one hook thread per mirror, re-created on every
        # rebuild, is not worth it in v1 - a mirror on a secondary side bar keeps the static placement.
        self._side_watcher_enabled: bool = bool(side_watcher_enabled)
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
        # rc.2 W8: when the widget last docked (owner set) or saw the tray rect change, and when the
        # INFO recovery line was logged - see _log_recovery / note_layout_settle. 0.0 = never.
        self._layout_settle_ts: float = 0.0
        self._recovery_info_ts: float = 0.0
        self._last_seen_tray: Optional[tuple] = None
        self._last_dock_owner: int = 0   # taskbar HWND of the last dock that started a settle window
        self._owner_set_to: int = 0      # taskbar HWND _ensure_taskbar_owner last set as the owner
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
        # Fight window (see _BURIAL_FIGHT_WINDOW_S): when we last actually re-seated (charged or
        # free), and whether the current burial was classified as fresh - decided ONCE, on its
        # first re-seat attempt, so a burial we sit out in a backoff window stays a fight.
        # None = no burial classified since our last re-seat / the last verified on-top check.
        self._last_acted_reseat_ts: float = 0.0
        self._burial_fresh: Optional[bool] = None
        # Times of the last free (fresh) re-seats, and the rate cap they can trip - which holds
        # until the healthy on-top stretch (or an explorer restart) resets the backoff state.
        self._fresh_reseat_times: Deque[float] = deque(maxlen=_BURIAL_FRESH_RATE_MAX)
        self._fresh_rate_capped: bool = False
        # One INFO line per burial episode when SetWindowPos refuses the neighbour (Win10).
        self._access_denied_logged: bool = False
        # One INFO line per stretch of a not-ready tray rect (see _tray_rect_ready).
        self._tray_not_ready_logged: bool = False
        # No taskbar at all (Explorer restarting, rc.2 W5) - see holding_for_taskbar.
        self._had_real_taskbar: bool = False
        self._taskbar_absent_since: Optional[float] = None
        self._taskbar_hold_expired_logged: bool = False
        # Last good tray rect per (monitor, edge), as offsets from the taskbar's right/bottom.
        self._last_good_tray: Dict[tuple, Tuple[int, int, int, int]] = {}
        # Side-bar automatic placement (Phase B). Decided at startup, on a dock change (edge /
        # thickness / DPI / Small, or the widget's height), on Reset position and on an icon-alignment
        # flip; as buttons come and go it changes ONLY when they really reach the widget (the side-bar
        # watcher's re-check, rc.2 W1) - never just because an app opened (#200 jitter). "Provisional" while
        # the UIA layout probe is still in flight: decided from the classic HWND rect, re-decided
        # once when the probe lands.
        self._side_placement: Optional[SidePlacement] = None
        self._side_placement_key: Optional[tuple] = None
        self._side_placement_provisional: bool = False
        self._side_placement_source: Optional[str] = None   # "uia" / "hwnd" / None - what it used
        # Called once per decision that ends in "tray_no_room" (the widget shows the one-time heads-up).
        self.on_side_no_room: Optional[Callable[[], None]] = None
        # Runtime re-check requested by the side-bar watcher ("full" after an alignment flip, else
        # "collision"), applied on the next reposition from the watcher's fresh UIA probe.
        self._side_recheck: Optional[str] = None
        # The app span (top, bottom) the current decision was taken from: a re-check that finds the
        # same span changes nothing (rc.2 M2 - a no-room widget "collides" with it every time).
        self._side_decided_span: Optional[tuple] = None
        # Polls for the startup probe after a provisional decision, so the UIA re-decision lands
        # within ~50 ms instead of on the next 1 s tick (rc.2 L1).
        self._provisional_timer = QTimer(self)
        self._provisional_timer.setSingleShot(True)
        self._provisional_timer.setInterval(constants.timeouts.SIDE_BAR_PROBE_POLL_MS)
        self._provisional_timer.timeout.connect(self._on_provisional_poll)
        # Keeps the placement right as apps come and go and the alignment flips (rc.2 W1/W2). Only
        # ever attached to a native side bar.
        from netspeedtray.core.side_bar_watcher import SideBarWatcher
        self._side_watcher = SideBarWatcher(self._side_watch_context, self._apply_side_recheck, parent=self)
        # #24: on a native horizontal Win11 bar the widget steps aside (hides) while the app buttons
        # reach it - it never moves for them (#200). The widget wires both callbacks.
        self.on_app_yield_changed: Optional[Callable[[], None]] = None
        self.on_app_yield_first: Optional[Callable[[], None]] = None
        # Persists config updates the yield makes (the "dropped on the buttons" offset, rc.2 H2).
        self.on_app_yield_persist: Optional[Callable[[Dict[str, Any]], None]] = None
        self._app_overlap_info: Optional[TaskbarInfo] = None
        from netspeedtray.core.app_overlap_yield import AppOverlapYield
        self._app_yield = AppOverlapYield(self._app_overlap_context, self._app_yield_changed,
                                          self._app_yield_first, parent=self,
                                          on_persist_drop=self._persist_app_drop,
                                          enabled=app_yield_enabled)

        # Timers
        self._tray_watcher_timer = QTimer(self)
        self._tray_watcher_timer.timeout.connect(self._check_for_tray_changes)
        
        self._logger.debug("Core PositionManager initialized.")

    def start_monitoring(self) -> None:
        """Starts the periodic tray watcher."""
        if not self._tray_watcher_timer.isActive():
            self._tray_watcher_timer.start(10000) # Check every 10s
            self._logger.debug("PositionManager monitoring started.")

    def stop_monitoring(self) -> None:
        """Stops the periodic tray watcher."""
        self._tray_watcher_timer.stop()
        self._logger.debug("PositionManager monitoring stopped.")

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
            if self.is_floating():
                # Free Move / free-float: nothing docked to watch - unhook (L5). Turning it off
                # re-attaches through the next side-bar placement.
                self._side_watcher.detach()

            # A saved (dragged) position wins in both free-move and free-float.
            if self._apply_saved_position():
                return

            # No saved position + a taskbar-less preferred display -> default-place on that display.
            if self._free_float_active and self._apply_free_float_position():
                return

            # No taskbar at all while Explorer restarts: keep the last position (the widget hides
            # meanwhile) instead of moving to the off-bar fallback spot (rc.2 W5).
            if self.holding_for_taskbar(self._state.taskbar_info):
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
                self._logger.warning("Failed to calculate position.")
        except Exception as e:
            self._logger.error("Error updating position: %s", e, exc_info=True)

    def holding_for_taskbar(self, info: Any) -> bool:
        """
        True while a DOCKED widget that has had a real taskbar sees none at all (``hwnd == 0``, the
        fallback TaskbarInfo get_taskbar_info returns while Explorer restarts) - for at most
        TASKBAR_ABSENT_HOLD_MS. Callers then keep the last position and the widget hides, instead
        of floating at the screen-fallback spot for the ~2 s until the new taskbar exists (rc.2 W5).

        False with a real taskbar (which it records), before any real taskbar was ever seen (a
        start with no shell keeps the old fallback), for Free Move / free-float (nothing docked),
        and once the hold has run out (the old behaviour; logged once per absence). The "tray rect
        not ready" stretch after the new taskbar appears is #419's (_tray_rect_ready), not this.
        """
        hwnd = getattr(info, "hwnd", 0) if info is not None else 0
        if hwnd:
            if self._taskbar_absent_since is not None:
                self._logger.info("Taskbar is back after %.1fs.", time.monotonic() - self._taskbar_absent_since)
            self._had_real_taskbar = True
            self._taskbar_absent_since = None
            self._taskbar_hold_expired_logged = False
            return False
        if not self._had_real_taskbar or self.is_floating():
            return False
        now = time.monotonic()
        if self._taskbar_absent_since is None:
            self._taskbar_absent_since = now
            self._logger.info("No taskbar found (Explorer restarting?) - hiding the widget at its last "
                        "position for up to %.0fs.", constants.timeouts.TASKBAR_ABSENT_HOLD_MS / 1000.0)
        if (now - self._taskbar_absent_since) * 1000.0 < constants.timeouts.TASKBAR_ABSENT_HOLD_MS:
            return True
        if not self._taskbar_hold_expired_logged:
            self._taskbar_hold_expired_logged = True
            self._logger.warning("Still no taskbar after %.0fs - showing the widget at the fallback position.",
                           now - self._taskbar_absent_since)
        return False

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
        pos = self._calculator.calculate_position(_TrayOverride(info, tray), size, self._state.config,
                                                  widgets_step=not self._app_yield.retreated)
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
                    seen = (int(info.hwnd or 0),) + tuple(int(v) for v in tray)
                    if self._last_seen_tray is not None and seen != self._last_seen_tray:
                        self.note_layout_settle("tray change")   # e.g. a tray icon arrived (W8)
                    self._last_seen_tray = seen
                except Exception:
                    pass
            if self._tray_not_ready_logged:
                self._logger.debug("Taskbar tray area is ready again - repositioning.")
            self._tray_not_ready_logged = False
            return True
        if not self._tray_not_ready_logged:
            self._logger.info("Taskbar tray area not ready yet (rect %s) - placing the widget from the "
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
            self._logger.debug(
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
            self._logger.info(
                "Saved free-move position (%s,%s) is on a disconnected monitor; "
                "falling back to calculated position.",
                saved_x, saved_y,
            )
            return False

        clamped = ScreenUtils.validate_position(saved_x, saved_y, widget_size, screen)
        if (clamped.x, clamped.y) != (saved_x, saved_y):
            self._logger.info(
                "Clamped saved free-move position (%s,%s) onto screen '%s' -> (%s,%s).",
                saved_x, saved_y, stable_pseudonym(screen.name(), "DISPLAY"), clamped.x, clamped.y,
            )
        else:
            self._logger.info(
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
        self._logger.debug("Widget geometry updated to: %s", new_rect)

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
            self._state.config,
            side_placement=self._side_placement_for(self._state.taskbar_info, widget_size[1]),
            widgets_step=not self._app_yield.retreated,
        )

    def _side_placement_for(self, taskbar_info: TaskbarInfo, widget_height: int) -> Optional[SidePlacement]:
        """The automatic side-bar placement for this dock, re-decided only when the dock changed (or a
        decision taken without the UIA layout can now use it). None on TOP/BOTTOM bars,
        ExplorerPatcher-style bars and fallback taskbars, where nothing changes."""
        try:
            if taskbar_info is None or not taskbar_info.hwnd:
                self._side_placement_key = None
                return None
            edge = taskbar_info.get_edge_position()
            dpi = taskbar_info.dpi_scale if taskbar_info.dpi_scale > 0 else 1.0
            if (edge not in (constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT)
                    or side_bar_geometry(taskbar_info, dpi) is None):
                # Not a native side bar (any more): forget the dock, so coming back to the same side
                # bar (side -> bottom -> side, same signature) decides and probes afresh.
                self._side_placement_key = None
                self._side_recheck = None
                self._side_watcher.detach()
                return None
            hwnd = int(taskbar_info.hwnd)
            # Never for a "Show on all taskbars" mirror (side_watcher_enabled=False, #229): no hook
            # thread per mirror; a mirror keeps the placement decided here, without W1-W4 updates.
            if self._side_watcher_enabled:
                self._side_watcher.attach(hwnd)
            key = (taskbar_utils.dock_signature(taskbar_info), hwnd, int(widget_height))
            if key != self._side_placement_key:
                # New dock (or first sight): probe the layout afresh; decide now from what we have.
                self._side_placement_key = key
                self._side_recheck = None
                self._side_decided_span = None
                taskbar_utils.request_taskbar_layout_probe(hwnd)
                # Appear once (L1): give the probe a short, bounded wait before the first decision.
                taskbar_utils.wait_for_taskbar_layout(
                    hwnd, constants.timeouts.SIDE_BAR_FIRST_LAYOUT_WAIT_MS / 1000.0)
                placement = self._decide_side_placement(taskbar_info, dpi, widget_height)
                if self._side_placement_provisional:
                    self._provisional_timer.start()
                return placement
            if self._side_recheck is not None and not self._widget_busy():
                kind, self._side_recheck = self._side_recheck, None
                return self._recheck_side_placement(taskbar_info, dpi, widget_height, kind)
            if self._side_placement_source != "uia":
                # Decided from the classic HWND rect: re-decide once the probe has finished - with
                # its result, or (failed / timed out) to make the HWND-based decision final.
                apps, _widgets, pending = taskbar_utils.taskbar_layout_snapshot(hwnd)
                if apps is not None or (self._side_placement_provisional and not pending):
                    return self._decide_side_placement(taskbar_info, dpi, widget_height)
            return self._side_placement
        except Exception as e:
            self._logger.error("Side-bar placement failed: %s", e, exc_info=True)
            return None

    def _decide_side_placement(self, taskbar_info: TaskbarInfo, dpi: float,
                               widget_height: int) -> Optional[SidePlacement]:
        # ONE snapshot of the probe: the rects used and the provisional flag describe the same
        # moment, so a probe landing mid-decision can't leave a lagging-rect decision marked final.
        # The TaskbarInfo's own apps/widgets fields are not trusted here (read at another time).
        apps, source, widgets, pending = taskbar_utils.taskbar_layout_for(taskbar_info.hwnd)
        overflow = taskbar_utils.taskbar_layout_overflow(taskbar_info.hwnd) if source == "uia" else None
        geo = side_bar_geometry(taskbar_info, dpi, apps=apps, widgets=widgets, overflow=overflow)
        if geo is None:
            return None
        placement = choose_side_placement(geo, widget_height)
        previous = self._side_placement
        was_provisional = self._side_placement_provisional
        self._side_placement = placement
        self._side_placement_source = source
        self._side_placement_provisional = bool(pending) and source != "uia"
        self._side_decided_span = (geo.apps_top, geo.apps_bottom)
        # INFO only when the outcome changes (M2: a re-check every few seconds that lands on the same
        # spot used to log two INFO lines each time).
        changed = (previous is None or previous != placement
                   or was_provisional != self._side_placement_provisional)
        self._logger.log(logging.INFO if changed else logging.DEBUG,
                   "Side-bar placement: %s%s (apps %s..%s, overflow %s..%s, tray %s, from %s%s).",
                   placement.mode, "" if placement.y is None else " y=%d" % placement.y,
                   geo.apps_top, geo.apps_bottom, geo.overflow_top, geo.overflow_bottom, geo.tray_top,
                   source, ", provisional" if self._side_placement_provisional else "")
        if (placement.mode == "tray_no_room" and not self._side_placement_provisional
                and not user_placed_side_offset(self._state.config) and self.on_side_no_room is not None):
            try:
                self.on_side_no_room()
            except Exception as e:
                self._logger.debug("no-room callback failed: %s", e)
        return placement

    def _recheck_side_placement(self, taskbar_info: TaskbarInfo, dpi: float, widget_height: int,
                                kind: str) -> Optional[SidePlacement]:
        """Apply the side-bar watcher's fresh UIA probe. ``"full"`` (the icon alignment flipped: the
        Widgets button and the app group moved) decides the placement afresh. ``"collision"`` moves the
        widget only when the fresh app span really reaches it - apps coming and going elsewhere never
        move it (#200) - and never while the user's drag offset is in force."""
        if kind == "full" or self._side_placement is None:
            return self._decide_side_placement(taskbar_info, dpi, widget_height)
        if user_placed_side_offset(self._state.config):
            return self._side_placement
        apps, source, widgets, _pending = taskbar_utils.taskbar_layout_for(taskbar_info.hwnd)
        geo = side_bar_geometry(taskbar_info, dpi, apps=apps, widgets=widgets)
        if geo is None or geo.apps_top is None or geo.apps_bottom is None or source != "uia":
            return self._side_placement
        span = (geo.apps_top, geo.apps_bottom)
        if self._side_decided_span is not None and all(
                abs(a - b) < 1.0 for a, b in zip(span, self._side_decided_span)):
            # The decision was taken from this very span (a no-room widget overlaps it by design).
            return self._side_placement
        try:
            top = float(self._state.widget.pos().y())
        except Exception:
            return self._side_placement
        clearance = constants.layout.DEFAULT_PADDING
        bottom = top + widget_height
        if geo.apps_bottom + clearance <= top or bottom + clearance <= geo.apps_top:
            self._logger.debug("Side-bar re-check: apps %s..%s clear of the widget %s..%s - staying.",
                         geo.apps_top, geo.apps_bottom, top, bottom)
            return self._side_placement
        self._logger.debug("Side-bar re-check: apps %s..%s now reach the widget %s..%s - re-deciding.",
                     geo.apps_top, geo.apps_bottom, top, bottom)
        return self._decide_side_placement(taskbar_info, dpi, widget_height)

    def _apply_side_recheck(self, full: bool) -> None:
        """SideBarWatcher callback: its probe landed; re-place from it on this (GUI) thread.

        Never while the user drags the widget or has its menu open (rc.2 live test M1: a probe landing
        mid-drag moved the widget 183 -> 92 -> 186 in 13 ms, and the release could save the automatic
        spot as the user's): the request is kept and applied by the first reposition after it."""
        if full or self._side_recheck is None:
            self._side_recheck = "full" if full else "collision"
        if self._widget_busy():
            self._logger.debug("Side-bar re-check deferred: the widget is being dragged / its menu is open.")
            return
        self._last_applied_geometry = None
        self.update_position()

    def _widget_busy(self) -> bool:
        widget = self._state.widget
        # ``is True``: the real widget keeps plain bools; a stand-in without them is never "busy".
        return (getattr(widget, "_dragging", False) is True
                or getattr(widget, "_is_context_menu_visible", False) is True)

    def _on_provisional_poll(self) -> None:
        """A provisional (HWND-rect) first decision: re-place as soon as its UIA probe is in (L1)."""
        info = self._state.taskbar_info
        if not self._side_placement_provisional or info is None or not info.hwnd:
            return
        _apps, _widgets, pending = taskbar_utils.taskbar_layout_snapshot(info.hwnd)
        if pending:
            self._provisional_timer.start()
            return
        if self._widget_busy():
            self._provisional_timer.start()
            return
        self._last_applied_geometry = None
        self.update_position()

    def _side_watch_context(self):
        """What the side-bar watcher compares against: None while there is nothing to watch (not a
        native side bar, floating, hidden, or mid-drag)."""
        from netspeedtray.core.side_bar_watcher import SideWatchContext
        info = self._state.taskbar_info
        widget = self._state.widget
        if info is None or not info.hwnd or self._side_placement_key is None or self.is_floating():
            return None
        if self._widget_busy() or not widget.isVisible():
            return None
        dpi = info.dpi_scale if info.dpi_scale > 0 else 1.0
        top = float(widget.pos().y())
        return SideWatchContext(hwnd=int(info.hwnd), dpi=dpi, widget=(top, top + widget.height()),
                                user_placed=user_placed_side_offset(self._state.config))

    def note_foreground_change(self) -> None:
        """The widget saw a foreground change: a new app's button may be growing the app group toward
        it (side-bar watcher on a side bar, the #24 yield on a horizontal one; each a no-op elsewhere)."""
        self._side_watcher.note_foreground_change()
        self._app_yield.note_foreground_change()

    # --- #24: app-button yield on a native horizontal Win11 bar --------------------------------

    def sync_app_overlap(self, taskbar_info: Optional[TaskbarInfo]) -> None:
        """Every refresh (1 s tick + foreground changes), BEFORE the visibility gate: attach / detach
        the yield for this taskbar and run its bounded re-check."""
        self._app_overlap_info = taskbar_info
        self._app_yield.sync()

    def is_widgets_fallback_active(self) -> bool:
        """True while a full left-aligned bar keeps the widget at the rc.2 tray spot, on the Widgets
        button's padding, instead of left of it (AppOverlapYield.retreated, rc.3)."""
        return self._app_yield.retreated

    def is_yielding_to_apps(self) -> bool:
        """True while the widget steps aside for app buttons that reach it (#24)."""
        return self._app_yield.yielded

    def note_user_drop(self) -> None:
        """A drag ended: a drop onto the app buttons is the user's choice (no yield until clear once)."""
        self._app_yield.note_user_drop()

    def stop_app_overlap(self) -> None:
        """App shutdown."""
        self._app_yield.detach("shutting down")

    def suspend_app_yield(self) -> bool:
        """A second launch asked for the widget (#440): no yield for the rest of this session. True
        when the widget was yielded at that moment."""
        return self._app_yield.suspend_for_session()

    def _persist_app_drop(self, value: Optional[int]) -> None:
        self._state.config["app_overlap_drop_offset"] = value
        if self.on_app_yield_persist is not None:
            self.on_app_yield_persist({"app_overlap_drop_offset": value})

    def _app_overlap_context(self):
        """The yield's view of the widget. A string when it must not yield (the reason, for the log):
        not a native Win11 TOP/BOTTOM bar (side bars, ExplorerPatcher / Win10 bars with a real task
        list), floating, or the user turned the setting off; NO_TASKBAR with no taskbar at all (only
        reached once holding_for_taskbar's own hold has run out - _execute_refresh returns before it);
        BUSY while the widget is dragged or its menu is open."""
        from netspeedtray.core.app_overlap_yield import BUSY, NO_TASKBAR, OverlapContext
        if not self._state.config.get("hide_when_apps_cover", constants.config.defaults.DEFAULT_HIDE_WHEN_APPS_COVER):
            return "the 'Hide while app buttons cover the widget' setting is off"
        if self.is_floating():
            return "the widget is floating (Free Move or a display without a taskbar)"
        info = self._app_overlap_info or self._state.taskbar_info
        if info is None or not getattr(info, "hwnd", 0):
            return NO_TASKBAR
        try:
            edge = info.get_edge_position()
        except Exception:
            return None
        if edge not in (constants.taskbar.edge.TOP, constants.taskbar.edge.BOTTOM):
            return "the widget is not on a top/bottom taskbar"
        if taskbar_utils.valid_rect(getattr(info, "tasklist_rect", None)) is not None:
            return "this taskbar has a classic task list"
        if self._widget_busy():
            return BUSY
        widget = self._state.widget
        dpi = info.dpi_scale if info.dpi_scale and info.dpi_scale > 0 else 1.0
        # One coordinate space per judgement: the widget span below (and its would-be spot, computed
        # from ``info``) is logical px at ``info``'s scale, and the UIA group is converted with ``dpi``.
        # The monitor's scale is read NOW; a snapshot that disagrees (a live display-scale change in the
        # last tick) decides nothing until the next tick brings a fresh one and the widget is re-sized.
        current = taskbar_utils.current_dpi_for_hwnd(int(info.hwnd))
        if current is not None and abs(current - dpi) > 1e-3:
            return BUSY
        width = float(widget.width())
        left = float(widget.pos().x())
        # The two default spots when a Widgets button sits beside the tray (rc.3): left of it
        # (``stepped``) and the rc.2 spot by the tray (``tray_spot``). Both None when they coincide (no
        # such button, a centred bar, a user's drag): the yield then has no fallback to weigh.
        stepped_x = self._would_be_x(info, None, widgets_step=True)
        tray_x = self._would_be_x(info, None, widgets_step=False)
        if not widget.isVisible():
            # Hidden: it is never re-placed, so its window can sit at a stale spot (rc.2 H3: 28 px
            # off after a tray-width change) - judge where it WOULD go.
            would = tray_x if self._app_yield.retreated else stepped_x
            left = would if would is not None else left
        stepped = tray_spot = None
        tray_spot_clear = False
        if stepped_x is not None and tray_x is not None and abs(stepped_x - tray_x) >= 1.0:
            stepped, tray_spot = (stepped_x, stepped_x + width), (tray_x, tray_x + width)
            # The full-bar fallback may use the tray spot only when it clears the weather text/icon
            # by the usual gap; an unknown extent counts as not clear (hide rather than collide).
            content = taskbar_utils.valid_rect(
                taskbar_utils.taskbar_layout_widgets_content(int(info.hwnd)))
            if content is not None:
                tray_spot_clear = (tray_x >= content[2] / dpi + constants.layout.WIDGETS_BUTTON_MIN_GAP_PX
                                   or tray_x + width + constants.layout.WIDGETS_BUTTON_MIN_GAP_PX
                                   <= content[0] / dpi)
        offset = self._state.config.get("tray_offset_x", constants.config.defaults.DEFAULT_TRAY_OFFSET_X)
        # A drag's offset - the same rule the placement uses, so a pre-1.3.0 default (10 / 1 / 3) is no
        # drop key either.
        drop_key = int(offset) if user_placed_horizontal_offset(self._state.config) else None
        saved = self._state.config.get("app_overlap_drop_offset")
        try:
            from netspeedtray.core.system_events import read_taskbar_alignment
            align = read_taskbar_alignment()
        except Exception:
            align = None
        try:
            tray = info.get_tray_rect()
            tray_left = int(tray[0]) if tray else None
        except Exception:
            tray_left = None
        layout_key = (align, tray_left, taskbar_utils.widgets_button_enabled())
        return OverlapContext(hwnd=int(info.hwnd), dpi=dpi, widget=(left, left + width),
                              centered=None if align is None else align == 1,
                              drop_key=drop_key, saved_drop=saved if isinstance(saved, int) else None,
                              layout_key=layout_key, stepped=stepped, tray_spot=tray_spot,
                              tray_spot_clear=tray_spot_clear)

    def _would_be_x(self, info: TaskbarInfo, fallback: Optional[float],
                    widgets_step: Optional[bool] = None) -> Optional[float]:
        """The x the docked widget would be placed at on ``info`` right now (its window may be stale
        while hidden); ``fallback`` when it can't be computed. ``widgets_step`` None: as placed now
        (the yield's full-bar fallback decides); True / False: left of / ignoring a Widgets button
        beside the tray."""
        try:
            widget = self._state.widget
            if widgets_step is None:
                widgets_step = not self._app_yield.retreated
            pos = self._calculator.calculate_position(info, (widget.width(), widget.height()),
                                                      self._state.config, widgets_step=widgets_step)
            return float(pos.x) if pos is not None else fallback
        except Exception as e:
            logger.debug("would-be position for the app yield failed: %s", e)
            return fallback

    def _app_yield_changed(self) -> None:
        if self.on_app_yield_changed is not None:
            self.on_app_yield_changed()

    def _app_yield_first(self) -> None:
        if self.on_app_yield_first is not None:
            self.on_app_yield_first()

    def note_taskbar_alignment_hint(self) -> None:
        """A WM_SETTINGCHANGE / alignment signal: let the side-bar watcher compare TaskbarAl."""
        self._side_watcher.note_alignment_maybe_changed()

    def side_bar_tick(self) -> None:
        """The widget's 1 s safety-net tick (side-bar watcher: alignment + bounded near re-check)."""
        self._side_watcher.on_tick()

    def stop_side_watcher(self) -> None:
        """Unhook (app shutdown)."""
        self._provisional_timer.stop()
        self._side_watcher.detach()

    def forget_side_placement(self) -> None:
        """Re-decide the side-bar placement on the next reposition (Reset position)."""
        self._side_recheck = None
        self._side_decided_span = None
        self._side_placement = None
        self._side_placement_key = None
        self._side_placement_provisional = False
        self._side_placement_source = None

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
                self._logger.debug("Tray geometry changed. Triggering reposition.")
                self.update_position(fresh_taskbar_info=tb_info)
                self._last_tray_rect = current_tray_rect

        except Exception as e:
            self._logger.error("Error checking tray changes: %s", e)

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
        reset), forgets the side-bar placement so it is decided afresh, and repositions.
        """
        defaults = constants.config.defaults
        self._state.config['position_x'] = None
        self._state.config['position_y'] = None
        self._state.config['tray_offset_x'] = defaults.DEFAULT_TRAY_OFFSET_X
        self._state.config['tray_offset_y'] = defaults.DEFAULT_TRAY_OFFSET_Y
        self.forget_side_placement()
        self._last_applied_geometry = None
        # We rely on the caller (Widget) to persist this config change to disk if needed.
        self.update_position()
        self.ensure_topmost()
        self._app_yield.note_position_reset()

    def move_to_default_for_session(self) -> bool:
        """
        Put the widget at its automatic docked spot NOW, without touching the saved Free Move spot or
        the drag offsets (unlike reset_to_default). For the second launch (#234): a duplicate start at
        sign-in can arrive before a slow monitor holding the saved spot connects, and must not wipe
        that spot (#440 review L3). Returns True if the widget was moved.
        """
        defaults = constants.config.defaults
        info = self._state.taskbar_info or get_taskbar_info(
            preferred_screen_name=self._state.config.get('preferred_monitor'))
        widget = self._state.widget
        size = (widget.width(), widget.height())
        if info is None or size[0] <= 0 or size[1] <= 0:
            return False
        session_cfg = dict(self._state.config)
        session_cfg.update(position_x=None, position_y=None,
                           tray_offset_x=defaults.DEFAULT_TRAY_OFFSET_X,
                           tray_offset_y=defaults.DEFAULT_TRAY_OFFSET_Y)
        pos = self._calculator.calculate_position(
            info, size, session_cfg, side_placement=self._side_placement_for(info, size[1]))
        if not pos:
            return False
        self._last_applied_geometry = None
        self._apply_geometry(pos.x, pos.y)
        return True

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
            # Fallback: the widget's own (preferred-monitor) taskbar, never a bare lookup - that is
            # the PRIMARY's, and would dock a secondary-monitor widget to it (rc.2 W12).
            tb_info = self._state.taskbar_info or get_taskbar_info(
                preferred_screen_name=self._state.config.get('preferred_monitor'))
            tb_hwnd = int(tb_info.hwnd) if tb_info and tb_info.hwnd else 0
            if not tb_hwnd or not win32gui.IsWindow(tb_hwnd):
                return False
            if (_GetWindowLongPtr(hwnd, _GWLP_HWNDPARENT) or 0) != tb_hwnd:
                _SetWindowLongPtr(hwnd, _GWLP_HWNDPARENT, tb_hwnd)
                self._owner_set_to = tb_hwnd
                self._logger.debug("Widget docked to taskbar HWND %s (owner Z-order).", tb_hwnd)
                return True
        except Exception as e:
            self._logger.error("Failed to set taskbar owner: %s", e)
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
                # A burial right after docking to a NEW taskbar (startup, Explorer restart) is the
                # shell laying it out (W8). Qt also drops the owner across show / flag changes (e.g.
                # after a fullscreen app) and it is re-set to the SAME taskbar: a burial then is real
                # and keeps its INFO line (#440 review L4).
                owner = self._owner_set_to
                if owner != self._last_dock_owner:
                    self._last_dock_owner = owner
                    self.note_layout_settle("dock")
            # A widget that fell out of the topmost band (Qt can drop WS_EX_TOPMOST across
            # show/flag changes) is necessarily BELOW the topmost taskbar, so it is "buried"
            # in is_buried_behind_taskbar()'s terms and the widget's guarded recovery path
            # (_recover_if_buried: menu / drag / fullscreen guards, backoff, deferral logging)
            # restores it with the safe seat. Nothing is re-seated from here (#327 review).
        except Exception as e:
            self._logger.error("Failed to ensure topmost status: %s", e)

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
        self._last_acted_reseat_ts = 0.0
        self._burial_fresh = None
        self._fresh_reseat_times.clear()
        self._fresh_rate_capped = False
        self._access_denied_logged = False

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
                self._logger.info("Taskbar has stayed in Z-band %s for over %.0fs - checking burial "
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
                self._burial_fresh = None   # any earlier burial is over (fight window)
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
            self._logger.debug("Buried-behind-taskbar check failed: %s", e)
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
        Z-band -> already seated. The backoff applies only to a burial found within the fight
        window of our last re-seat; a fresh burial and the Start / Task View band return skip
        it and charge nothing. If SetWindowPos refuses the neighbour with
        Access denied, see _reseat_after_access_denied.

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
            # Fight window: a burial first seen more than _BURIAL_FIGHT_WINDOW_S after our last
            # re-seat is a fresh one (Win10 buries the widget on every maximize AND restore) -
            # immediate and free, whatever window an earlier fight left behind. Classified once
            # per burial: while we sit out a backoff we are not re-seating, so a later check of
            # the SAME burial must stay a fight.
            if self._burial_fresh is None:
                fresh = bool(self._last_acted_reseat_ts) and \
                    now - self._last_acted_reseat_ts > _BURIAL_FIGHT_WINDOW_S
                times = self._fresh_reseat_times
                if fresh and not self._fresh_rate_capped and len(times) == times.maxlen and \
                        now - times[0] < _BURIAL_FRESH_RATE_WINDOW_S:
                    # Faster than a person buries it: a periodic re-burier just outside the
                    # fight window, or a fight only the 1 s tick sees. Charge it from here on.
                    self._fresh_rate_capped = True
                    self._logger.info("Widget buried %d times in %.0fs - treating further burials as a "
                                "fight (backoff) until it stays on top.",
                                len(times) + 1, now - times[0])
                self._burial_fresh = fresh and not self._fresh_rate_capped
            fresh = self._burial_fresh
            delay = self._reseat_delay()
            if not (band_return or fresh) and self._last_reseat_ts and \
                    (now - self._last_reseat_ts) < delay:
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
                self._burial_fresh = None
                return False  # already directly above the taskbar
            insert_after = prev if prev else win32con.HWND_TOPMOST
            try:
                win32gui.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, _RESEAT_SWP_FLAGS)
            except Exception as e:
                if prev and not win32gui.IsWindow(prev):
                    # The window above the taskbar (often a transient tooltip) was destroyed
                    # between GetWindow and SetWindowPos. Benign: no backoff charged, the
                    # next Z-order event or tick retries against the new neighbour.
                    self._logger.debug("Re-seat raced a closing window (%s) - retrying on the next check.", e)
                    return False
                if not (prev and _is_access_denied(e)):
                    raise
                if not self._reseat_after_access_denied(hwnd, tb, prev, tb_band):
                    return False   # not charged; the next check retries
            self._last_acted_reseat_ts = now
            self._burial_fresh = None
            self._access_denied_logged = False
            self._deferral_logged = None
            if band_return:
                self._band_return_pending_ts = 0.0   # consumed; the backoff is untouched
                self._logger.debug("Widget re-seated above the taskbar after Start / Task View closed "
                             "(no backoff charged).")
                return True
            if fresh:
                self._fresh_reseat_times.append(now)
                self._log_recovery(now, "Widget re-seated above the taskbar after a fresh burial "
                                        "(no backoff charged).")
                return True
            self._last_reseat_ts = now
            self._reseat_attempts = min(self._reseat_attempts + 1, _RESEAT_BACKOFF_EXP_CAP + 1)
            self._deferral_logged = None
            self._log_recovery(now, "Widget re-seated above the taskbar (attempt %d).", self._reseat_attempts)
            return True
        except Exception as e:
            self._logger.error("Failed to re-seat the widget above the taskbar: %s", e)
            return False

    def _reseat_after_access_denied(self, hwnd: int, tb: int, prev: int,
                                    tb_band: Optional[int]) -> bool:
        """
        SetWindowPos refused to insert the widget after `prev` with ERROR_ACCESS_DENIED
        (pasharm, Win10: 4x in 1.6 s, nothing elevated open). The likely cause is that `prev`
        - often a hidden explorer tooltip - sits in another Z-band than the taskbar (he saw
        band 1 and band 16 tooltips right above it). Only reached after that failure, so the
        normal re-seat path is unchanged.

        Fallback: insert after the first window above the taskbar that is in the TASKBAR'S
        band. Every same-band window that was above the taskbar (open menus, jump lists, the
        #200 guarantee) stays above the widget; the windows skipped are in other bands, whose
        order against a band-1 window the bands decide anyway. If no window above is in the
        taskbar's band, HWND_TOPMOST puts the widget at the top of its own band - which, with
        nothing of that band above the taskbar, is the same seat. When the bands are unknown
        (no API, a call fails) or the walk runs out, it does not guess: nothing moves, nothing
        is charged, the next check retries. One INFO line per burial episode names `prev`.

        Returns True when the widget was re-seated.
        """
        target = None
        if tb_band is not None:
            cur = tb
            for _ in range(_ZORDER_WALK_LIMIT):
                cur = int(win32gui.GetWindow(cur, _GW_HWNDPREV) or 0)
                if not cur:
                    target = win32con.HWND_TOPMOST
                    break
                if cur == hwnd:
                    continue
                band = _window_band(cur)
                if band is None:
                    break
                if band == tb_band:
                    target = cur
                    break
        if target == prev:
            target = None   # the refused window IS in the taskbar's band: nothing safer to try
        outcome = "not retried (no safe neighbour)"
        ok = False
        if target is not None:
            try:
                win32gui.SetWindowPos(hwnd, target, 0, 0, 0, 0, _RESEAT_SWP_FLAGS)
                ok = True
                outcome = ("re-seated at the top of the taskbar's band"
                           if target == win32con.HWND_TOPMOST
                           else "re-seated after the first window in the taskbar's band")
            except Exception as e2:
                outcome = "fallback also failed (%s)" % (e2,)
        if not self._access_denied_logged:
            self._access_denied_logged = True
            try:
                prev_class = win32gui.GetClassName(prev)
            except Exception:
                prev_class = "?"
            self._logger.info("Re-seat refused (Access denied) inserting after window class %r, "
                        "Z-band %s (taskbar Z-band %s): %s.",
                        prev_class, _window_band(prev), tb_band, outcome)
        return ok

    def _log_recovery(self, now: float, debug_msg: str, *args) -> None:
        """
        Log a re-seat: INFO for the first one of an episode, DEBUG for the rest (#327). A re-seat
        within _BURIAL_LOG_SETTLE_S of the widget docking or of a tray change is the shell laying
        the taskbar out, not a burial worth a support-bundle line: DEBUG, and the INFO line stays
        for the next real one (rc.2 W8 - a startup false burial or a tray-icon arrival used it up).
        """
        settling = bool(self._layout_settle_ts) and now - self._layout_settle_ts < _BURIAL_LOG_SETTLE_S
        if self._first_recovery_logged or settling:
            self._logger.debug(debug_msg + (" (taskbar settling after a dock / tray change)" if settling else ""),
                         *args)
            return
        self._logger.info("Widget recovered from taskbar burial - re-seated directly above the taskbar.")
        self._first_recovery_logged = True
        self._recovery_info_ts = now

    def note_layout_settle(self, reason: str = "") -> None:
        """
        The widget just docked (owner set) or the tray rect changed: the shell is re-laying the
        taskbar, and a burial now is part of that (rc.2 W8). Starts the settle window; an INFO
        recovery logged just BEFORE it (the tray-icon arrival buries the widget first, the tray rect
        changes on the next ~1 s refresh) is handed back, so the next real burial logs at INFO.
        """
        now = time.monotonic()
        self._layout_settle_ts = now
        if self._first_recovery_logged and self._recovery_info_ts and \
                now - self._recovery_info_ts < _BURIAL_LOG_SETTLE_S:
            self._first_recovery_logged = False
            self._logger.debug("The last burial recovery came with a %s - the next one logs at INFO again.",
                         reason or "layout change")

    def _log_deferral(self, reason: str, msg: str, *args) -> None:
        """DEBUG-log a deferred re-seat once per (reason, backoff window), not once per tick."""
        key = (reason, self._last_reseat_ts)
        if self._deferral_logged != key:
            self._deferral_logged = key
            self._logger.debug(msg, *args)

    def _reseat_delay(self) -> float:
        """Seconds of quiet required before the next re-seat: 0 first, then doubling, capped."""
        if not self._last_reseat_ts:
            return 0.0
        return min(_RESEAT_BACKOFF_BASE_S * (2 ** min(self._reseat_attempts - 1, _RESEAT_BACKOFF_EXP_CAP)),
                   _RESEAT_BACKOFF_MAX_S)
