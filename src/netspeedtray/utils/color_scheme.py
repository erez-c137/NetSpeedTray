"""
The widget's colour scheme (2.2 rc.2; #117, #332, #9): every foreground colour it paints has a
dark-taskbar and a light-taskbar value, stored as ``<stem>_dark`` / ``<stem>_light``
(``constants.config.defaults.THEMED_COLOR_STEMS``). This module is the one place that picks between
them, for the current WINDOWS mode - SystemUsesLightTheme, the theme the taskbar is painted in, which
is not the app mode Qt follows.

The theme never writes the config: before 2.2 rc.2 an "automatic" text colour was overwritten in the
file at startup and in memory on every flip, and the Settings dialog needed ~60 lines of workarounds to
keep its preview and Cancel from pinning a stale one (#405 S3/S5). Now the config holds both colours
and ``RenderConfig.from_dict`` resolves them, so a theme flip is one ``refresh_shell_theme()`` and a
renderer rebuild.
"""
from typing import Any, Dict, Optional

from netspeedtray import constants
from netspeedtray.utils.styles import read_system_uses_light_theme

# The cached Windows-mode flag; None = not read yet (read lazily on first use). Cached because
# RenderConfig.from_dict runs on every layout / settings tick and should not hit the registry each time.
# WidgetThemeManager.on_theme_changed refreshes it when the system event handler reports a flip.
_shell_light: Optional[bool] = None


def refresh_shell_theme() -> bool:
    """Re-read the Windows mode. Unreadable (no key, no value) counts as dark - the Windows default and
    the pre-2.2 behaviour. Returns the new flag."""
    global _shell_light
    _shell_light = read_system_uses_light_theme() == 1
    return _shell_light


def shell_is_light() -> bool:
    """True while the taskbar is light. Cached; reads on first use."""
    if _shell_light is None:
        return refresh_shell_theme()
    return _shell_light


def themed_key(stem: str, light: bool) -> str:
    """The config key holding ``stem``'s colour for a light (True) or dark (False) taskbar."""
    return f"{stem}_{'light' if light else 'dark'}"


def _is_hex(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 7 and value.startswith("#")
            and all(c in "0123456789abcdefABCDEF" for c in value[1:]))


def _relative_luminance(rgb) -> float:
    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgb(hex_code: str):
    return tuple(int(hex_code[i:i + 2], 16) for i in (1, 3, 5))


def backdrop_is_light(config: Dict[str, Any], shell_light: Optional[bool] = None) -> bool:
    """Which half of the scheme the widget's text sits on: the widget's own background colour,
    composited at its opacity over the taskbar (the mode's reference colour), as DWM shows it.

    At 0 % opacity this is the taskbar's Windows mode exactly - every default config. A mostly opaque
    background decides by itself: an opaque #EEEEEE background on a dark taskbar used to get the
    dark-taskbar (white) text, invisible, and an opaque #202020 one on a light taskbar black text.
    Light when black text out-contrasts white on that backdrop (luminance above
    BACKDROP_LUMINANCE_SPLIT, the WCAG crossover)."""
    if shell_light is None:
        shell_light = shell_is_light()
    c = constants.color
    taskbar = c.TASKBAR_REFERENCE_LIGHT if shell_light else c.TASKBAR_REFERENCE_DARK
    try:
        alpha = max(0.0, min(1.0, float(config.get("background_opacity", 0) or 0) / 100.0))
    except (TypeError, ValueError):
        alpha = 0.0
    bg = config.get("background_color")
    if alpha <= 0.0 or not _is_hex(bg):
        return bool(shell_light)
    seen = tuple(a * alpha + t * (1.0 - alpha) for a, t in zip(_rgb(bg), _rgb(taskbar)))
    return _relative_luminance(seen) > constants.renderer.BACKDROP_LUMINANCE_SPLIT


def resolve(config: Dict[str, Any], light: Optional[bool] = None) -> Dict[str, str]:
    """``{stem: hex}`` for every themed colour on the given backdrop (default: ``backdrop_is_light`` -
    the current Windows mode, unless the widget's own background is opaque enough to decide). A missing
    or invalid value falls back to that half's default - never to the other half's value, and never to
    the inert pre-1.3 key of the same stem."""
    if light is None:
        light = backdrop_is_light(config)
    defaults = constants.config.defaults.THEMED_COLOR_DEFAULTS
    out: Dict[str, str] = {}
    for stem, (dark_default, light_default) in defaults.items():
        value = config.get(themed_key(stem, light))
        out[stem] = value if _is_hex(value) else (light_default if light else dark_default)
    return out
