"""
What the taskbar really looks like beyond Light/Dark (2.2 rc.2 legibility package "L", items 4a and 5a).

The colour scheme (utils/color_scheme) assumes a Windows 11 taskbar painted #202020 (dark) or #EEEEEE
(light). Two Windows settings break that assumption:

- **High Contrast** (Settings > Accessibility > Contrast themes; SPI_GETHIGHCONTRAST). The taskbar is
  painted in the theme's own palette, and an app is expected to draw in the system colours. The widget
  then uses the system text colour (COLOR_WINDOWTEXT), hides the mini graph and turns colour coding off -
  at paint time only (RenderConfig), so the config is untouched and everything comes back when the
  contrast theme is turned off.
- **"Show accent color on Start and taskbar"** (ColorPrevalence = 1). The bar is a dark shade of the accent
  colour, read from the accent palette Windows generates (AccentPalette, the shade at
  ACCENT_TASKBAR_PALETTE_INDEX - measured on the bar in the VM). Windows 11 paints it in the LIGHT Windows
  mode too (verified on build 26300, with white clock text), so the widget then picks its text half from
  the bar, not from the mode. A band colour or graph line of the same hue can fall to ~1.5:1 there, so
  any pen under ACCENT_PEN_MIN_CONTRAST against the real bar is swapped for the default text colour.

Everything here is read-only and best-effort: any failure reads as "not on" (the pre-2.2 behaviour).
"""
from __future__ import annotations

import ctypes
import logging
import sys
import winreg
from dataclasses import dataclass
from typing import Optional

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.TaskbarAppearance")

_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_ACCENT_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent"

_SPI_GETHIGHCONTRAST = 0x0042
_HCF_HIGHCONTRASTON = 0x00000001
_COLOR_WINDOW = 5
_COLOR_WINDOWTEXT = 8


@dataclass(frozen=True)
class TaskbarAppearance:
    """A snapshot of the two settings above. ``accent_on`` with ``accent_bar`` None means the accent bar
    is on but its colour could not be read (the widget then falls back to colour coding off, 5b)."""
    high_contrast: bool = False
    hc_text: Optional[str] = None          # COLOR_WINDOWTEXT as #RRGGBB, in High Contrast only
    hc_background: Optional[str] = None    # COLOR_WINDOW as #RRGGBB, in High Contrast only
    accent_on: bool = False
    accent_bar: Optional[str] = None       # the accent taskbar's colour as #RRGGBB


PLAIN = TaskbarAppearance()


class _HIGHCONTRASTW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint32), ("lpszDefaultScheme", ctypes.c_wchar_p)]


def _colorref_hex(value: int) -> str:
    """A GDI COLORREF (0x00BBGGRR) as #RRGGBB."""
    r, g, b = value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


def read_high_contrast() -> tuple:
    """(on, text_hex, background_hex). Off / unreadable -> (False, None, None)."""
    if sys.platform != "win32":
        return False, None, None
    try:
        hc = _HIGHCONTRASTW()
        hc.cbSize = ctypes.sizeof(_HIGHCONTRASTW)
        if not ctypes.windll.user32.SystemParametersInfoW(_SPI_GETHIGHCONTRAST, hc.cbSize, ctypes.byref(hc), 0):
            return False, None, None
        if not (hc.dwFlags & _HCF_HIGHCONTRASTON):
            return False, None, None
        get = ctypes.windll.user32.GetSysColor
        get.restype = ctypes.c_uint32
        return True, _colorref_hex(get(_COLOR_WINDOWTEXT)), _colorref_hex(get(_COLOR_WINDOW))
    except Exception as e:   # pragma: no cover - defensive; a failed read is "not on"
        logger.debug("High Contrast read failed: %s", e)
        return False, None, None


def _read_dword(path: str, name: str) -> Optional[int]:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        try:
            value, _ = winreg.QueryValueEx(key, name)
        finally:
            winreg.CloseKey(key)
        return int(value)
    except (OSError, ValueError, TypeError):
        return None


def read_accent_palette() -> Optional[list]:
    """The 8 colours of AccentPalette (#RRGGBB, lightest to darkest, then a complement), or None."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ACCENT_KEY)
        try:
            value, kind = winreg.QueryValueEx(key, "AccentPalette")
        finally:
            winreg.CloseKey(key)
    except OSError:
        return None
    if kind != winreg.REG_BINARY or not isinstance(value, (bytes, bytearray)) or len(value) < 32:
        return None
    return [f"#{value[i]:02X}{value[i + 1]:02X}{value[i + 2]:02X}" for i in range(0, 32, 4)]


# Windows 11's first build. Windows 10 greys the accent-taskbar option out in the light mode (the value
# can stay 1 from the dark mode), so there a light shell reads as off; Windows 11 shows the accent bar in
# both modes.
_WIN11_BUILD = 22000


def _windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0


def read_accent_taskbar(shell_light: bool) -> tuple:
    """(on, bar_hex): "Show accent color on Start and taskbar" is on, and the shade the bar is painted."""
    if _read_dword(_PERSONALIZE_KEY, "ColorPrevalence") != 1:
        return False, None
    if shell_light and _windows_build() < _WIN11_BUILD:
        return False, None
    palette = read_accent_palette()
    idx = constants.taskbar.taskbar.ACCENT_TASKBAR_PALETTE_INDEX
    if not palette or not (0 <= idx < len(palette)):
        return True, None
    return True, palette[idx]


def read_taskbar_appearance(shell_light: bool) -> TaskbarAppearance:
    """One snapshot of both settings (a few registry / SystemParametersInfo reads; cached by
    utils/color_scheme and refreshed on a theme-change broadcast)."""
    hc_on, hc_text, hc_back = read_high_contrast()
    if hc_on:
        return TaskbarAppearance(high_contrast=True, hc_text=hc_text, hc_background=hc_back)
    accent_on, bar = read_accent_taskbar(shell_light)
    return TaskbarAppearance(accent_on=accent_on, accent_bar=bar)
