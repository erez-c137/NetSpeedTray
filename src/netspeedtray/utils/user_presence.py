"""
Is someone at the screen to see a passing notice? (#508 review)

Used before an unrequested, auto-dismissing notice (the automatic update offer): shown while the
session is locked, a screen saver runs, or the user is away, it would time out unseen - and the check
that found it is not due again for a day. Windows' own notification gate,
``SHQueryUserNotificationState``, also covers what the widget's taskbar-based fullscreen check cannot
see: exclusive (D3D) fullscreen, presentation mode, a fullscreen app on another monitor.

Both probes fail open (``None``): a notice that may go unseen is better than one that never shows.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

# SHQueryUserNotificationState results (shellapi.h).
QUNS_NOT_PRESENT = 1              # screen saver, locked, or an inactive Fast User Switching session
QUNS_BUSY = 2                     # a fullscreen app, or Presentation Settings applied
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6               # first hour after a new install/upgrade (toasts only)
QUNS_APP = 7                      # an immersive (Store) app is in front

BLOCKING_STATES = frozenset({QUNS_NOT_PRESENT, QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN,
                             QUNS_PRESENTATION_MODE, QUNS_APP})


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def notification_state() -> Optional[int]:
    """The shell's QUERY_USER_NOTIFICATION_STATE, or None when it cannot be read."""
    try:
        state = ctypes.c_int(0)
        hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if hr != 0:
            return None
        return int(state.value)
    except Exception:
        return None


def idle_seconds() -> Optional[float]:
    """Seconds since the session's last keyboard/mouse input, or None when it cannot be read.
    Both ticks are 32-bit and wrap every 49.7 days, so the difference is taken modulo 2**32."""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((now - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def notice_would_go_unseen(max_idle_s: float) -> bool:
    """True while an unrequested notice should wait: the shell says not now (locked, screen saver,
    fullscreen, presentation, immersive app) or nobody has touched input for ``max_idle_s``."""
    if notification_state() in BLOCKING_STATES:
        return True
    idle = idle_seconds()
    return idle is not None and idle >= max_idle_s
