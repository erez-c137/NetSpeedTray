"""
Taskbar Utilities for Windows System Integration.

Provides functions and data structures for detecting taskbar properties such as
position, visibility, state, associated screen, DPI, and system tray location.
Relies on Windows API calls via pywin32 and ctypes.
"""

import os
import logging
import ctypes
import threading
import time
from ctypes import wintypes, windll, byref, Structure
from dataclasses import dataclass
from typing import Tuple, Optional, List, Any, Dict
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
# Screen names are logged pseudonymized: the support bundle's MANIFEST promises
# no display names ship (v2.1.5 item 7).
from netspeedtray.utils.config import stable_pseudonym

logger = logging.getLogger("NetSpeedTray.TaskbarUtils")
# Monitor handles whose DPI query failed once: the fallback warning is logged once per handle.
# (No DPI VALUE cache: an HMONITOR survives a live display-scale change, so a cached scale went stale
# until restart - the rc.2 live-DPI bugs. GetDpiForMonitor is a cheap user-mode call.)
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


def find_toolbar_bands_rect(taskbar_hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """
    Physical-px union of the classic taskbar toolbars (Desktop, Links, Address, a folder toolbar) that
    sit AFTER the task list - between the app buttons and the tray - or None (#324, rc.2 F12;
    best-effort, Windows 10). Each toolbar is a ReBarWindow32 band beside the MSTaskSwWClass one; the
    widget hugs the tray, so it was drawn over them.

    Windows 11 has no taskbar toolbars: its ReBarWindow32 holds MSTaskSwWClass only (probed on
    26200), so this is None there and nothing changes. A band before the task list (dragged to the
    start of the bar) is left out: the widget never sits there.
    """
    try:
        rebar = win32gui.FindWindowEx(taskbar_hwnd, 0, "ReBarWindow32", None)
        if not rebar:
            return None
        tasksw = win32gui.FindWindowEx(rebar, 0, "MSTaskSwWClass", None)
        if not tasksw:
            return None
        task = valid_rect(tuple(win32gui.GetWindowRect(tasksw)))
        if task is None:
            return None
        vertical = (task[3] - task[1]) > (task[2] - task[0])
        tol = constants.layout.TOOLBAR_BAND_ADJACENCY_TOLERANCE_PX
        bands = []
        child = win32gui.FindWindowEx(rebar, 0, None, None)
        for _ in range(constants.layout.TOOLBAR_BAND_SCAN_LIMIT):
            if not child:
                break
            if child != tasksw and win32gui.IsWindowVisible(child):
                r = valid_rect(tuple(win32gui.GetWindowRect(child)))
                if r is not None and ((r[1] >= task[3] - tol) if vertical else (r[0] >= task[2] - tol)):
                    bands.append(r)
            child = win32gui.FindWindowEx(rebar, child, None, None)
        if not bands:
            return None
        return (min(b[0] for b in bands), min(b[1] for b in bands),
                max(b[2] for b in bands), max(b[3] for b in bands))
    except Exception as e:
        logger.debug("Toolbar band scan failed for taskbar HWND %s: %s", taskbar_hwnd, e)
        return None


def valid_rect(rect: Any) -> Optional[Tuple[int, int, int, int]]:
    """``rect`` as a ``(left, top, right, bottom)`` tuple when it is a real, non-empty rect, else None.

    The side-bar placement reads several optional rects (apps extent, Widgets button, tasklist);
    a mocked or half-built TaskbarInfo can carry anything there, and an empty rect means "absent"."""
    if not isinstance(rect, (tuple, list)) or len(rect) != 4:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in rect):
        return None
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def find_apps_rect(taskbar_hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """The Win11 Start-to-last-app span from the classic HWNDs: ``MSTaskSwWClass`` (else its
    ``ReBarWindow32`` parent), physical px, or None.

    Only a FALLBACK for ``apps_extent``: probed live on Win11 26200 (2026-10-21) the rect stopped at
    the 10th of 12 app buttons (651..1136 while UIA put the last button at 1225..1269) - Explorer
    no longer resizes these XAML-era HWNDs as buttons come and go. The UIA union
    (``request_taskbar_layout_probe``) is the real source. Unlike ``find_tasklist_rect`` this needs
    no ``ToolbarWindow32`` (which Win11 doesn't have), and it is never used on horizontal bars, so
    it can't trigger their app-icon safe-zone snap."""
    try:
        rebar = win32gui.FindWindowEx(taskbar_hwnd, 0, "ReBarWindow32", None)
        if not rebar:
            return None
        tasksw = win32gui.FindWindowEx(rebar, 0, "MSTaskSwWClass", None)
        return valid_rect(tuple(win32gui.GetWindowRect(tasksw or rebar)))
    except Exception as e:  # noqa: BLE001 - fail-safe: no rect
        logger.debug("apps rect lookup failed for taskbar HWND %s: %s", taskbar_hwnd, e)
        return None


# --- Side-taskbar layout probe (apps extent + Widgets button) ------------------------------------
# Where the app buttons and the Widgets button sit on a SIDE taskbar decides where the widget goes
# (PositionManager side placement). Win11's buttons live in a XAML island, so the truth comes from
# UI Automation (utils/uia_widgets.query_taskbar_layout, in-process, ~10 ms cross-process) on a
# background thread. Unlike the #200 Widgets probe this one has NO periodic refresh: it is requested
# at startup, on a dock change, on Reset position, and - as re-checks that KEEP the last result until
# the new one lands (``keep_last``) - when core/side_bar_watcher predicts the app group reaching the
# widget or sees the icon alignment flip. On a TOP/BOTTOM bar only the #24 app-button yield
# (core/app_overlap_yield.py) runs it - keep_last re-checks that read the contiguous app group and
# never change the horizontal placement.
_layout_lock = threading.Lock()
# Notified (under _layout_lock) whenever a probe lands or a request ends: wait_for_taskbar_layout.
_layout_landed = threading.Condition(_layout_lock)
# gen: bumped by every request (a result for an older gen is dropped); running: a worker thread is
# alive (at most one at a time - repeated requests re-run it instead of piling up threads);
# started: monotonic time of the current gen's request, for the timeout.
# group: the contiguous Start-led button run (uia_widgets.app_group_from_elements), for consumers that
# must not count far-away buttons; landed: how many probe results have landed (a sequence number).
_layout_state: dict = {"hwnd": 0, "apps": None, "widgets": None, "group": None, "overflow": None,
                       "pending": False,
                       "done": False, "gen": 0, "running": False, "started": 0.0, "landed": 0}
# A UIA call into a hung Explorer can block for a long time. Past this, the request counts as
# finished WITHOUT a result (placement goes final on the classic HWND rect); a late result is dropped.
LAYOUT_PROBE_TIMEOUT_S = 5.0


def _query_taskbar_layout_blocking(taskbar_hwnd: int):
    """(apps_rect, widgets_rect, app_group_rect) in physical px from UIA, or None. Never raises."""
    try:
        from netspeedtray.utils import uia_widgets
        return uia_widgets.query_taskbar_layout_full(int(taskbar_hwnd))
    except Exception as e:  # noqa: BLE001
        logger.debug("Taskbar layout UIA probe failed: %s", e)
        return None


def _layout_worker() -> None:
    """Probe until the newest request has been answered (requests made meanwhile re-run it)."""
    while True:
        with _layout_lock:
            gen, hwnd = _layout_state["gen"], _layout_state["hwnd"]
            if not _layout_state["pending"]:
                _layout_state["running"] = False
                return
        result = _query_taskbar_layout_blocking(hwnd)
        overflow = None
        if isinstance(result, tuple) and len(result) == 4:
            apps, widgets, group, overflow = result
        elif isinstance(result, tuple) and len(result) == 3:
            apps, widgets, group = result
        elif isinstance(result, tuple) and len(result) == 2:
            (apps, widgets), group = result, None
        else:
            apps = widgets = group = None
        with _layout_lock:
            if _layout_state["gen"] == gen and _layout_state["pending"]:
                _layout_state.update(apps=valid_rect(apps), widgets=valid_rect(widgets),
                                     group=valid_rect(group), overflow=valid_rect(overflow),
                                     pending=False, done=True,
                                     landed=_layout_state.get("landed", 0) + 1)
                _layout_landed.notify_all()
                logger.debug("Taskbar layout probe: apps=%s widgets=%s", apps, widgets)
            # else: superseded (loop re-runs for the newer request) or timed out (loop exits)


def request_taskbar_layout_probe(taskbar_hwnd: Optional[int], keep_last: bool = False) -> None:
    """Probe ``taskbar_hwnd``'s layout afresh on a background thread.

    Default: drop the cached layout first - until the probe lands (or times out) the snapshot says
    ``pending`` and ``get_apps_extent`` answers from the classic HWNDs (a one-shot placement).
    ``keep_last=True`` (a runtime re-check of the SAME taskbar): the last landed result keeps
    answering until the new one replaces it, so nothing falls back to the lagging HWND rect meanwhile;
    a timed-out re-check keeps it too."""
    if not taskbar_hwnd:
        return
    with _layout_lock:
        _layout_state["gen"] += 1
        same = _layout_state["hwnd"] == int(taskbar_hwnd) and _layout_state["done"]
        if keep_last and same:
            _layout_state.update(pending=True, started=time.monotonic())
        else:
            _layout_state.update(hwnd=int(taskbar_hwnd), apps=None, widgets=None, group=None,
                                 overflow=None, pending=True, done=False, started=time.monotonic())
        start = not _layout_state["running"]
        _layout_state["running"] = True
    if start:
        _start_layout_worker()


def _start_layout_worker() -> None:
    threading.Thread(target=_layout_worker, name="nst-taskbar-layout-uia", daemon=True).start()


def taskbar_layout_snapshot(taskbar_hwnd: Optional[int]):
    """``(apps_rect, widgets_rect, pending)`` for ``taskbar_hwnd``, read under ONE lock so the rects
    and the pending flag always describe the same moment (a decision taken from them can't use a
    stale rect yet be marked final). Rects are None until a probe for this taskbar has landed;
    ``pending`` is True while a requested probe is in flight and not yet timed out."""
    with _layout_lock:
        if not taskbar_hwnd or _layout_state["hwnd"] != int(taskbar_hwnd):
            return None, None, False
        if _layout_state["pending"] and time.monotonic() - _layout_state["started"] > LAYOUT_PROBE_TIMEOUT_S:
            if _layout_state["done"]:   # a keep_last re-check: the last result stays
                logger.info("Taskbar layout re-check timed out after %.0f s; keeping the last layout.",
                            LAYOUT_PROBE_TIMEOUT_S)
                _layout_state.update(pending=False)
            else:
                logger.info("Taskbar layout probe timed out after %.0f s; using the taskbar's own rect.",
                            LAYOUT_PROBE_TIMEOUT_S)
                _layout_state.update(pending=False, done=True, apps=None, widgets=None, group=None,
                                     overflow=None)
        if _layout_state["done"]:
            return _layout_state["apps"], _layout_state["widgets"], bool(_layout_state["pending"])
        return None, None, bool(_layout_state["pending"])


def taskbar_layout_overflow(taskbar_hwnd: Optional[int]) -> Optional[Tuple[int, int, int, int]]:
    """The last probed "..." overflow button (physical px) for ``taskbar_hwnd``, or None."""
    taskbar_layout_snapshot(taskbar_hwnd)   # applies the timeout rule
    with _layout_lock:
        if not taskbar_hwnd or _layout_state["hwnd"] != int(taskbar_hwnd) or not _layout_state["done"]:
            return None
        return _layout_state["overflow"]


def wait_for_taskbar_layout(taskbar_hwnd: Optional[int], timeout_s: float) -> bool:
    """Block up to ``timeout_s`` until the requested probe for ``taskbar_hwnd`` is no longer pending.
    True when it landed (or nothing was pending). For the FIRST side-bar placement only, so the
    widget appears once instead of hopping from the lagging HWND rect to the UIA spot (rc.2 L1);
    the timeout keeps a hung Explorer from holding the GUI thread."""
    if not taskbar_hwnd:
        return False
    deadline = time.monotonic() + max(0.0, timeout_s)
    with _layout_landed:
        while _layout_state["hwnd"] == int(taskbar_hwnd) and _layout_state["pending"]:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _layout_landed.wait(left)
        return _layout_state["hwnd"] == int(taskbar_hwnd) and bool(_layout_state["done"])


def taskbar_layout_group(taskbar_hwnd: Optional[int]) -> Optional[Tuple[int, int, int, int]]:
    """The last probed contiguous Start-led button run (physical px) for ``taskbar_hwnd``, or None."""
    taskbar_layout_snapshot(taskbar_hwnd)   # applies the timeout rule
    with _layout_lock:
        if not taskbar_hwnd or _layout_state["hwnd"] != int(taskbar_hwnd) or not _layout_state["done"]:
            return None
        return _layout_state["group"]


def taskbar_layout_landed_count(taskbar_hwnd: Optional[int]) -> int:
    """How many probe results have landed (for ``taskbar_hwnd``; -1 for another taskbar): a caller
    that notes it before a request knows its probe landed - rather than timed out - when it grows."""
    with _layout_lock:
        if not taskbar_hwnd or _layout_state["hwnd"] != int(taskbar_hwnd):
            return -1
        return int(_layout_state.get("landed", 0))


def find_classic_layout_hwnds(taskbar_hwnd: int) -> Tuple[int, int, int]:
    """``(start, rebar, tasksw)``: the hidden legacy ``Start`` child of the taskbar and the
    ``ReBarWindow32`` / ``MSTaskSwWClass`` pair, 0 for any that is missing. On Win11 26200 Explorer
    still moves the ``Start`` window with the XAML Start button - 15-30 ms BEFORE UI Automation
    reports it (rc.1 live probe) - while the task-switch pair's far edge lags behind the buttons."""
    start = rebar = tasksw = 0
    try:
        start = int(win32gui.FindWindowEx(taskbar_hwnd, 0, "Start", None) or 0)
        rebar = int(win32gui.FindWindowEx(taskbar_hwnd, 0, "ReBarWindow32", None) or 0)
        if rebar:
            tasksw = int(win32gui.FindWindowEx(rebar, 0, "MSTaskSwWClass", None) or 0)
    except Exception as e:  # noqa: BLE001 - fail-safe
        logger.debug("classic layout HWND lookup failed for taskbar HWND %s: %s", taskbar_hwnd, e)
    return start, rebar, tasksw


def taskbar_layout_probe_pending(taskbar_hwnd: Optional[int]) -> bool:
    """True while a requested layout probe for ``taskbar_hwnd`` hasn't landed (or timed out)."""
    return taskbar_layout_snapshot(taskbar_hwnd)[2]


def cached_taskbar_layout(taskbar_hwnd: Optional[int]):
    """The last probed (apps_rect, widgets_rect) for ``taskbar_hwnd``, (None, None) when unknown."""
    apps, widgets, _pending = taskbar_layout_snapshot(taskbar_hwnd)
    return apps, widgets


def taskbar_layout_for(taskbar_hwnd: Optional[int]):
    """``(apps_rect, apps_source, widgets_rect, pending)`` from ONE snapshot: the probed app span
    (source ``"uia"``) when there is one, else the classic MSTaskSwWClass rect (``"hwnd"``)."""
    if not taskbar_hwnd:
        return None, None, None, False
    apps, widgets, pending = taskbar_layout_snapshot(taskbar_hwnd)
    if apps is not None:
        return apps, "uia", widgets, pending
    rect = find_apps_rect(taskbar_hwnd)
    return rect, ("hwnd" if rect is not None else None), widgets, pending


def get_apps_extent(taskbar_hwnd: Optional[int]) -> Tuple[Optional[Tuple[int, int, int, int]], Optional[str]]:
    """``(rect, source)``: the app-button span in physical px and where it came from - ``"uia"`` (the
    probed union of Start/Search/Task View/app buttons), ``"hwnd"`` (the classic MSTaskSwWClass
    rect, which can lag behind the real buttons), or ``(None, None)``. Non-blocking."""
    apps, source, _widgets, _pending = taskbar_layout_for(taskbar_hwnd)
    return apps, source


# --- Win11 Widgets/weather element detection (#200 / #24) --------------------------------
# The Windows 11 Widgets/weather element has NO classic Win32 HWND - it lives inside a XAML island, so
# EnumChildWindows/GetWindowRect can't see it. It IS reachable via UI Automation: utils/uia_widgets.py
# calls UIA in-process (raw ctypes COM, ~8 ms) and matches the STABLE AutomationId 'WidgetsButton' (never
# the Name - it's localized and embeds live weather text), returning the VISIBLE CONTENT extent
# (min-left..max-right of its child icon/text), not the padded button box. Depending on taskbar
# alignment it sits far-left (centered) or just left of the tray (edge-aligned - the case that can
# overlap the widget). Cached + refreshed on a background thread; callers get the last-known rect
# instantly and never block. Fail-safe: any error / Widgets disabled / future-build rename -> None.
# Used only for the one-time #200 nudge (never to auto-move).
#
# This used to shell out to a hidden powershell.exe on every 30 s refresh, for the whole session
# (2.2.0-beta.1 lifecycle review). Now: zero process spawns; the probe is skipped when the Widgets
# button is off in Settings (TaskbarDa = 0, a microsecond registry read), and disabled outright once
# the nudge it feeds has been shown (set_widgets_probe_enabled).
_WIDGETS_CACHE_TTL_S = 30.0
_widgets_lock = threading.Lock()
_widgets_state: dict = {"rect": None, "ts": 0.0, "querying": False}
_widgets_probe_enabled = True
_ADVANCED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"


def set_widgets_probe_enabled(enabled: bool) -> None:
    """Turn the Widgets-button probe on/off. Off once the one-time #200 nudge has been shown - nothing
    else reads the rect, so probing after that is pure cost. Off also drops the cached rect."""
    global _widgets_probe_enabled
    with _widgets_lock:
        _widgets_probe_enabled = bool(enabled)
        if not enabled:
            _widgets_state["rect"] = None


def _widgets_button_enabled() -> bool:
    r"""Taskbar settings > Widgets: HKCU\...\Explorer\Advanced\TaskbarDa, 0 = off; absent = the Win11
    default, on. Any read problem counts as on (the UIA probe then decides)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ADVANCED_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "TaskbarDa")
        return int(value) != 0
    except Exception:
        return True


def _query_widgets_rect_blocking(taskbar_hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """The Widgets button's physical-pixel content rect, or None. ``taskbar_hwnd`` must be the
    Shell_TrayWnd root (the WidgetsButton is a descendant of the taskbar frame, NOT of TrayNotifyWnd).
    In-process UIA, no child process; always called from a background thread. Never raises."""
    try:
        if not _widgets_button_enabled():
            return None
        from netspeedtray.utils import uia_widgets
        return uia_widgets.query_widgets_rect(int(taskbar_hwnd))
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
    than the TTL it kicks off a one-shot background refresh so the UIA probe never runs on the
    caller's thread. The rect changes only when Widgets is toggled or its weather text resizes.
    """
    if not taskbar_hwnd:
        return None
    now = time.monotonic()
    start_refresh = False
    with _widgets_lock:
        if not _widgets_probe_enabled:
            return None
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
    # Physical-pixel rect of the monitor the taskbar is on (GetMonitorInfo "Monitor", the same call that
    # gives work_area), or None when unknown. Diagnostics only (support bundle, #322).
    monitor_rect: Optional[Tuple[int, int, int, int]] = None
    # Physical-pixel span of the app buttons (Start .. last app) and where it came from ("uia" /
    # "hwnd", see get_apps_extent), for SIDE-taskbar placement and drag limits only. Deliberately
    # not tasklist_rect: that one drives the horizontal app-icon safe-zone snap, and Win11 has none.
    apps_extent: Optional[Tuple[int, int, int, int]] = None
    apps_extent_source: Optional[str] = None
    # The Widgets button's full rect from the same layout probe (independent of the #200 nudge's
    # widgets_rect, which stops probing once the nudge has been shown).
    widgets_extent: Optional[Tuple[int, int, int, int]] = None
    # Physical-pixel union of the classic toolbars (Desktop / Links / ...) between the task list and
    # the tray, or None - always None on Windows 11 (find_toolbar_bands_rect, #324).
    toolbars_rect: Optional[Tuple[int, int, int, int]] = None

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


    @staticmethod
    def _edge_in_rect(r: Tuple[int, int, int, int], mon: Tuple[int, int, int, int],
                      tolerance: float) -> constants.TaskbarEdge:
        """The edge of ``mon`` the bar rect ``r`` (both (l, t, r, b), same coordinate space) is
        docked to: flush with an edge, else (an auto-hide bar slid off it) the nearer half."""
        edge = constants.taskbar.edge
        if r[2] - r[0] > r[3] - r[1]:
            if abs(r[1] - mon[1]) < tolerance:
                return edge.TOP
            if abs(r[3] - mon[3]) < tolerance:
                return edge.BOTTOM
            return edge.TOP if (r[1] + r[3]) / 2 < (mon[1] + mon[3]) / 2 else edge.BOTTOM
        if abs(r[0] - mon[0]) < tolerance:
            return edge.LEFT
        if abs(r[2] - mon[2]) < tolerance:
            return edge.RIGHT
        return edge.LEFT if (r[0] + r[2]) / 2 < (mon[0] + mon[2]) / 2 else edge.RIGHT

    def get_edge_position(self) -> constants.TaskbarEdge:
        """
        Determines which edge of the screen the taskbar is docked to (logical coords).

        Returns:
            constants.taskbar.edge: Enum member representing the edge (TOP, BOTTOM, LEFT, RIGHT).
                         Defaults to BOTTOM if calculation fails or is ambiguous.
        """
        try:
            # A fallback taskbar (hwnd 0, e.g. Explorer not up yet at logon) has a (0,0,0,0) rect.
            # Width 0 is not > height 0, so it read as VERTICAL -> LEFT and the widget sized itself as a
            # compact side-bar block at the desktop's bottom-right. No taskbar = the default bottom.
            r = self.rect
            if not self.hwnd or not r or len(r) != 4 or r[2] - r[0] <= 0 or r[3] - r[1] <= 0:
                return constants.taskbar.edge.BOTTOM
            # Decide in PHYSICAL px against the taskbar's own monitor (GetMonitorInfo) when known. The
            # logical path below divides a physical rect by this monitor's DPI and compares it with
            # Qt's logical screen geometry, which keeps each screen's native origin: a 150% monitor
            # stacked below a 100% primary has a physical top of 1080 but a Qt top of 1080 too, while
            # rect/1.5 puts it at 720 - so both its bottom and its top bar read as TOP (#7).
            mon = self.monitor_rect
            if mon and len(mon) == 4 and mon[2] - mon[0] > 0 and mon[3] - mon[1] > 0:
                return self._edge_in_rect(r, mon, 5 * max(self.dpi_scale, 1.0))
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
    The CURRENT DPI scaling factor of a monitor (Windows API), read on every call.

    Never cached: a monitor keeps its HMONITOR across a live Settings > Display > Scale change, and a
    per-handle cache (1.0.5 - rc.2) kept the old scale until restart - a 100% -> 150% change made the
    #24 app-button yield compare physical px with logical px and hide the widget, 150% -> 100% sized it
    for a 32-px "Small" bar. A failed query falls back to the primary QScreen's ratio, read fresh too;
    its warning is logged once per handle.
    """
    try:
        monitor_id = int(monitor)
    except (TypeError, ValueError):
        monitor_id = 0

    def get_fallback_dpi() -> float:
        """The primary QScreen's ratio; the warning once per invalid handle."""
        if monitor_id not in _logged_warnings:
            logger.warning("Falling back to QScreen for DPI detection for monitor handle '%s'.", monitor_id)
            _logged_warnings.add(monitor_id)
        screen = QApplication.primaryScreen()
        dpi_scale = screen.devicePixelRatio() if screen else 1.0
        return dpi_scale if dpi_scale and dpi_scale > 0 else 1.0

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
        return dpi

    except (AttributeError, NameError):
        # GetDpiForMonitor is not available (e.g., older Windows)
        return get_fallback_dpi()
    except Exception as e:
        logger.error("Unexpected error getting DPI for monitor %s: %s", monitor_id, e, exc_info=True)
        return get_fallback_dpi()


def current_dpi_for_hwnd(hwnd: int) -> Optional[float]:
    """The scale of the monitor ``hwnd`` is on RIGHT NOW (a TaskbarInfo snapshot can be a tick old);
    None when it can't be read (no such window). Never the primary's scale for a dead handle -
    MonitorFromWindow(DEFAULTTONEAREST) would answer that for anything."""
    try:
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        monitor = win32api.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        dpi = get_dpi_for_monitor(monitor, hwnd)
        return dpi if dpi and dpi > 0 else None
    except Exception as e:
        logger.debug("current DPI for HWND %s failed: %s", hwnd, e)
        return None


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
                logger.debug("Found best screen match for taskbar via intersection: %s",
                             stable_pseudonym(best_match_screen.name(), "DISPLAY"))
                return best_match_screen
        except Exception as e:
            logger.error(f"Error during screen intersection check: {e}. Proceeding to fallbacks.")

        # --- Layer 2: Fallback to a point-based check ---
        try:
            point = QPoint(tb_rect_phys[0], tb_rect_phys[1])
            screen_at_point = QApplication.screenAt(point)
            if screen_at_point:
                logger.warning("Used point-based fallback to find screen: %s",
                               stable_pseudonym(screen_at_point.name(), "DISPLAY"))
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
            if rect_phys[2] <= rect_phys[0] or rect_phys[3] <= rect_phys[1]:
                # A taskbar collapsed to zero width/height is going away (its display was unplugged,
                # or Explorer is tearing it down). Not an error: the next enumeration no longer has it
                # (#434 live run - this was 3-4 ERROR lines per unplug, from the 1 s mirror tick).
                logger.debug("Taskbar HWND %s (%s) has an empty rect %s - going away. Ignoring.",
                             hwnd, class_name, rect_phys)
                return None
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
            # One snapshot: the apps span and the Widgets box always come from the same probe.
            apps_extent, apps_source, widgets_extent, _pending = taskbar_layout_for(hwnd)
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
                monitor_rect=tuple(monitor_info["Monitor"]) if monitor_info.get("Monitor") else None,
                apps_extent=apps_extent,
                apps_extent_source=apps_source,
                widgets_extent=widgets_extent,
                toolbars_rect=find_toolbar_bands_rect(hwnd),
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
# Throttled PER preferred name ({preferred: taskbar set}): with "Show on all taskbars" (#229) the main
# widget and each mirror resolve different preferred screens every tick, and one shared key made
# them evict each other - every lookup logged again. None (tests reset it so) means empty.
_last_fallback_log_key: Optional[Dict[str, tuple]] = None


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
    if _last_fallback_log_key is None:
        _last_fallback_log_key = {}
    key = tuple(sorted((tb.hwnd, tb.screen_name) for tb in taskbars))
    if _last_fallback_log_key.get(preferred_screen_name) == key:
        return
    _last_fallback_log_key[preferred_screen_name] = key

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
        "Enumerated taskbars: %s. If the preferred monitor is connected but has no taskbar of "
        "its own, 'Float on a display without a taskbar' (free_float) keeps the widget on it, or "
        "turn on Windows' 'Show my taskbar on all displays' (Settings shows this under Preferred "
        "Monitor, rc.2 W13).",
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
        if not any(getattr(tb, "hwnd", 0) for tb in taskbars):
            # NO taskbar anywhere (only the hwnd-0 fallback, or nothing): Explorer is restarting, not
            # a taskbar-less display. Floating now would put the widget off the bar for the restart;
            # the docked path's hold keeps it hidden at its last spot instead (rc.2 W5, #440 M2).
            return None
        if _select_taskbar_for_screen(taskbars, preferred_screen_name) is not None:
            return None  # it has a taskbar of its own -> dock to it normally
        return screen    # present but taskbar-less -> free-float on it
    except Exception as e:  # noqa: BLE001 - fail-safe: never break placement over float detection
        logger.error("get_free_float_screen failed for '%s': %s",
                     stable_pseudonym(preferred_screen_name, "DISPLAY"), e, exc_info=True)
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


def is_rdp_client_covering_monitor(hwnd: int, class_name: str,
                                   monitor_rect: Optional[Tuple[int, int, int, int]]) -> bool:
    """
    True when ``hwnd`` is a full-screen Remote Desktop client window (no caption) whose rect
    contains the whole ``monitor_rect`` - the multi-monitor RDP session ("Use all my monitors"), one
    window spanning every monitor, that the window == monitor fullscreen test misses (#132). A
    windowed session (caption on, even maximized) and any other window class are never matched.
    """
    try:
        if class_name not in constants.shell.shell.RDP_CLIENT_WINDOW_CLASSES or not monitor_rect:
            return False
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        if (style & win32con.WS_CAPTION) == win32con.WS_CAPTION:
            return False
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        m_left, m_top, m_right, m_bottom = monitor_rect
        return left <= m_left and top <= m_top and right >= m_right and bottom >= m_bottom
    except Exception:
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
            # A multi-monitor RDP session is one window across all monitors - "its" monitor may be
            # another one - so it is judged against the taskbar's monitor before that check (#132).
            if class_name in constants.shell.shell.RDP_CLIENT_WINDOW_CLASSES and \
                    is_rdp_client_covering_monitor(hwnd_to_check, class_name,
                                                   win32api.GetMonitorInfo(tb_monitor).get('Monitor')):
                return True
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

def horizontal_taskbar_strip(taskbar_info: TaskbarInfo, edge: constants.TaskbarEdge,
                             dpi_scale: float) -> Tuple[float, float]:
    """The visible strip a TOP/BOTTOM taskbar occupies, as ``(top, height)`` in logical px.

    One source of truth for the widget's size (``layout._horizontal_dock_height``), its position
    (``PositionCalculator._calculate_horizontal_position``) and the drag constraint, so the three
    can't disagree about where the strip is.

    - Height: the work-area inset (``geometry()`` minus ``availableGeometry()``, #104/PR #110),
      capped by the taskbar WINDOW's own height. The cap is new in #322: a third-party tool that
      moves the taskbar, or a second appbar on the same edge, can reserve more work area than the
      taskbar covers, and sizing to the inset then hung the widget off the bar. A widget can never
      usefully be taller than the taskbar window, so the cap only ever tightens. When the work area
      isn't inset at all (auto-hide, or the Win11 26200 quirk - #135/#221) the window height is used.
    - Top: anchored to the STABLE screen edge, never ``taskbar_info.rect[1]``, which slides while an
      auto-hide taskbar animates (#135). A docked bar always covers the edge-most ``height`` px.
    """
    screen = taskbar_info.get_screen()
    full_geom = screen.geometry()
    avail_geom = screen.availableGeometry()
    if not isinstance(avail_geom, QRect):
        avail_geom = full_geom
    scale = dpi_scale if dpi_scale and dpi_scale > 0 else 1.0
    window_h = (taskbar_info.rect[3] - taskbar_info.rect[1]) / scale

    if edge == constants.TaskbarEdge.TOP:
        inset = avail_geom.top() - full_geom.top()
    else:
        inset = full_geom.bottom() - avail_geom.bottom()

    if inset > 0 and window_h > 0:
        height = min(inset, window_h)
    else:
        height = inset if inset > 0 else window_h

    top = full_geom.top() if edge == constants.TaskbarEdge.TOP else (full_geom.bottom() + 1) - height
    return top, height


def vertical_taskbar_strip(taskbar_info: TaskbarInfo, edge: constants.TaskbarEdge,
                           dpi_scale: float) -> Tuple[float, float]:
    """The visible strip a LEFT/RIGHT taskbar occupies, as ``(x_origin, thickness)`` in logical px.

    Twin of ``horizontal_taskbar_strip``: one source of truth for the side-bar widget's width
    (``WidgetLayoutManager``), its x (``PositionCalculator._calculate_vertical_position``), the drag
    constraint and ``dock_signature``, so the four can't disagree about where the bar is.

    - Thickness: the work-area inset, capped by the taskbar WINDOW's width (a second appbar on the
      same edge reserves more than the taskbar covers). No inset at all (auto-hide, #221 quirk): the
      window width.
    - x: the STABLE screen edge, never ``taskbar_info.rect[0]``, which slides while an auto-hide bar
      animates (#135).
    """
    screen = taskbar_info.get_screen()
    full_geom = screen.geometry()
    avail_geom = screen.availableGeometry()
    if not isinstance(avail_geom, QRect):
        avail_geom = full_geom
    scale = dpi_scale if dpi_scale and dpi_scale > 0 else 1.0
    rect = getattr(taskbar_info, "rect", None)
    if isinstance(rect, (tuple, list)) and len(rect) == 4 and all(isinstance(v, (int, float)) for v in rect):
        window_w = (rect[2] - rect[0]) / scale
    else:
        window_w = 0.0   # no usable window rect

    if edge == constants.TaskbarEdge.RIGHT:
        inset = full_geom.right() - avail_geom.right()
    else:
        inset = avail_geom.left() - full_geom.left()

    if inset > 0 and window_w > 0:
        thickness = min(inset, window_w)
    else:
        thickness = inset if inset > 0 else window_w

    if inset > 0 and 0 < window_w < inset:
        # Another appbar shares the edge: the reserved band is wider than the taskbar, and the
        # taskbar may not be the part touching the screen edge - take x from its window. Safe here:
        # a bar that reserves work area is not auto-hiding, so the rect doesn't slide (#135).
        x_origin = rect[0] / scale
    elif edge == constants.TaskbarEdge.RIGHT:
        x_origin = (full_geom.right() + 1) - thickness
    else:
        x_origin = full_geom.left()
    return x_origin, thickness


def rect_overlaps_y(top: float, bottom: float, widgets_rect: Optional[Tuple[int, int, int, int]],
                    dpi_scale: float) -> bool:
    """The side-bar twin of ``rect_overlaps_x``: True if ``[top, bottom]`` (logical px) overlaps the
    Widgets/weather element's y-extent. On a LEFT/RIGHT taskbar the widget and the Widgets button
    share the bar's whole width, so comparing x always "overlapped" (#200 nudge false positive)."""
    if not widgets_rect or dpi_scale <= 0:
        return False
    return top < widgets_rect[3] / dpi_scale and bottom > widgets_rect[1] / dpi_scale


def dock_signature(taskbar_info: Optional[TaskbarInfo]) -> Optional[tuple]:
    """What the docked widget's SIZE depends on: ``(edge, thickness, dpi_scale, small)``, or None
    for a fallback/unknown taskbar.

    Compared on every taskbar change so the widget is re-sized only when its dock geometry changed -
    e.g. Win11's native taskbar size switched Default (48 px) <-> Small (32 px) while running, or the
    bar moved to another edge (#322) - and not on a plain move, which only needs a reposition.

    Built from the STABLE values ``WidgetLayoutManager`` sizes from, never the window's position:
    a horizontal bar's thickness is ``horizontal_taskbar_strip`` (work-area inset, else the window
    HEIGHT), a side bar's is ``vertical_taskbar_strip`` (inset, else the window WIDTH); neither changes
    while an auto-hide bar slides (#135).
    """
    if taskbar_info is None or taskbar_info.hwnd == 0:
        return None
    edge = taskbar_info.get_edge_position()
    dpi = taskbar_info.dpi_scale if taskbar_info.dpi_scale > 0 else 1.0
    if edge in (constants.TaskbarEdge.TOP, constants.TaskbarEdge.BOTTOM):
        _, thickness = horizontal_taskbar_strip(taskbar_info, edge, dpi)
    else:
        _, thickness = vertical_taskbar_strip(taskbar_info, edge, dpi)
    return (edge, round(thickness), round(dpi, 3), is_small_taskbar(taskbar_info))
