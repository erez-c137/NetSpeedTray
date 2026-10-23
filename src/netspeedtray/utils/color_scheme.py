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


def resolve(config: Dict[str, Any], light: Optional[bool] = None) -> Dict[str, str]:
    """``{stem: hex}`` for every themed colour on the given taskbar (default: the current Windows mode).
    A missing or invalid value falls back to that taskbar's default - never to the other theme's value,
    and never to the inert pre-1.3 key of the same stem."""
    if light is None:
        light = shell_is_light()
    defaults = constants.config.defaults.THEMED_COLOR_DEFAULTS
    out: Dict[str, str] = {}
    for stem, (dark_default, light_default) in defaults.items():
        value = config.get(themed_key(stem, light))
        out[stem] = value if _is_hex(value) else (light_default if light else dark_default)
    return out
