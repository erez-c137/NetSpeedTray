"""
Windows 11 window chrome via DWM (Desktop Window Manager).

Applies the native Win11 finishing touches that Qt does not expose on a top-level
window: a dark title bar that follows the OS theme, rounded window corners, and
(optionally) the Mica system backdrop.

Everything here is best-effort and fail-safe. On Windows 10, on older Windows 11
builds, or on any error, the calls become silent no-ops — so callers never have to
guard by OS version. `hwnd` is the window's native handle, i.e. ``int(widget.winId())``.

Mica is intentionally OFF by default: it only reads through a window whose base has
been made translucent (``WA_TranslucentBackground`` + a semi-opaque palette), and a
half-applied backdrop looks *more* broken than none. Enable it only once the caller's
surface has been reworked for it.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Optional

logger = logging.getLogger("NetSpeedTray.dwm")

# --- DWM attribute IDs (dwmapi.h) ---------------------------------------------
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20         # BOOL; build >= 18985
_DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19  # the same flag on 20H1 (1903/1909)
_DWMWA_WINDOW_CORNER_PREFERENCE = 33        # int;  Win11 build >= 22000
_DWMWA_SYSTEMBACKDROP_TYPE = 38             # int;  Win11 22H2 build >= 22621

# DWM_WINDOW_CORNER_PREFERENCE values
_DWMWCP_ROUND = 2

# DWM_SYSTEMBACKDROP_TYPE values
_DWMSBT_NONE = 1
_DWMSBT_MAINWINDOW = 2  # Mica

_S_OK = 0

# Minimum Windows builds that introduced each feature.
_BUILD_WIN11 = 22000
_BUILD_MICA = 22621


def _build() -> int:
    """Current Windows build number, or 0 if it can't be determined."""
    try:
        return sys.getwindowsversion().build  # type: ignore[attr-defined]
    except Exception:
        return 0


def _set_attr(hwnd: int, attr: int, value: int) -> bool:
    """``DwmSetWindowAttribute(hwnd, attr, &c_int(value), 4)``; True on S_OK."""
    try:
        dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False
    val = ctypes.c_int(value)
    try:
        hr = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(attr),
            ctypes.byref(val),
            ctypes.sizeof(val),
        )
        return hr == _S_OK
    except Exception:
        return False


def set_dark_titlebar(hwnd: int, enabled: bool) -> bool:
    """Make the window's title bar dark (``enabled``) or light. Returns success."""
    flag = 1 if enabled else 0
    if _set_attr(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, flag):
        return True
    # 20H1 builds used a different attribute id for the same thing.
    return _set_attr(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY, flag)


def set_rounded_corners(hwnd: int) -> bool:
    """Round the window corners (Win11 only). No-op / False on Win10."""
    if _build() < _BUILD_WIN11:
        return False
    return _set_attr(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)


def supports_mica() -> bool:
    """True when the OS build supports the Mica system backdrop (22H2+)."""
    return _build() >= _BUILD_MICA


def set_mica(hwnd: int, enabled: bool = True) -> bool:
    """
    Toggle the Mica backdrop. Returns False on unsupported builds. The caller MUST
    have made the window base translucent first, or Mica won't show through.
    """
    if not supports_mica():
        return False
    return _set_attr(
        hwnd, _DWMWA_SYSTEMBACKDROP_TYPE,
        _DWMSBT_MAINWINDOW if enabled else _DWMSBT_NONE,
    )


def apply_win11_chrome(hwnd: Optional[int], *, dark: bool, mica: bool = False) -> None:
    """
    Apply the native Win11 chrome to a top-level window. Best-effort + fail-safe:
    each piece is independent, and unsupported pieces are silent no-ops.

    - dark title bar follows ``dark`` (pass ``is_dark_mode()``)
    - rounded corners on Win11
    - Mica only when ``mica=True`` AND the window base is already translucent
      (off by default; see the module docstring).
    """
    if not hwnd:
        return
    try:
        handle = int(hwnd)
    except (TypeError, ValueError):
        return
    set_dark_titlebar(handle, dark)
    set_rounded_corners(handle)
    if mica:
        set_mica(handle, True)
