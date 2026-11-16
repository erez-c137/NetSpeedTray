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

Since the legibility package ("L", 2.2 rc.2) the theme is more than Light/Dark: High Contrast and an
accent-coloured taskbar (utils/taskbar_appearance) are read alongside it, and ``paint_colors`` turns them
into what the widget paints - never into the config.
"""
from typing import Any, Dict, Optional, Tuple

from netspeedtray import constants
from netspeedtray.utils.styles import read_system_uses_light_theme
from netspeedtray.utils.taskbar_appearance import TaskbarAppearance, read_taskbar_appearance

# The cached Windows-mode flag; None = not read yet (read lazily on first use). Cached because
# RenderConfig.from_dict runs on every layout / settings tick and should not hit the registry each time.
# WidgetThemeManager.on_theme_changed refreshes it when the system event handler reports a flip.
_shell_light: Optional[bool] = None
# The cached High Contrast / accent-taskbar snapshot, same lifecycle as _shell_light.
_appearance: Optional[TaskbarAppearance] = None


def refresh_shell_theme() -> bool:
    """Re-read the Windows mode - and the High Contrast / accent-taskbar state that depends on it.
    Unreadable (no key, no value) counts as dark - the Windows default and the pre-2.2 behaviour.
    Returns the new flag."""
    global _shell_light, _appearance
    _shell_light = read_system_uses_light_theme() == 1
    _appearance = read_taskbar_appearance(_shell_light)
    return _shell_light


def appearance() -> TaskbarAppearance:
    """The cached High Contrast / accent-taskbar snapshot (read on first use)."""
    global _appearance
    if _appearance is None:
        _appearance = read_taskbar_appearance(shell_is_light())
    return _appearance


def taskbar_color(shell_light: Optional[bool] = None) -> str:
    """The colour the taskbar is really painted in: the accent bar's shade when "Show accent color on
    Start and taskbar" is on (and readable), else the Windows mode's reference colour."""
    if shell_light is None:
        shell_light = shell_is_light()
    app = appearance()
    if app.accent_on and app.accent_bar:
        return app.accent_bar
    c = constants.color
    return c.TASKBAR_REFERENCE_LIGHT if shell_light else c.TASKBAR_REFERENCE_DARK


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
    taskbar = taskbar_color(shell_light)
    try:
        alpha = max(0.0, min(1.0, float(config.get("background_opacity", 0) or 0) / 100.0))
    except (TypeError, ValueError):
        alpha = 0.0
    bg = config.get("background_color")
    accent = taskbar not in (constants.color.TASKBAR_REFERENCE_DARK, constants.color.TASKBAR_REFERENCE_LIGHT)
    if alpha <= 0.0 or not _is_hex(bg):
        # An accent-coloured bar is a dark shade in either Windows mode (Windows then draws white clock
        # text on it even in light mode): judge it by its own luminance, like an opaque background.
        if accent:
            return _relative_luminance(_rgb(taskbar)) > constants.renderer.BACKDROP_LUMINANCE_SPLIT
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


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio of two #RRGGBB colours."""
    la, lb = _relative_luminance(_rgb(a)), _relative_luminance(_rgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def seen_backdrop(config: Dict[str, Any], shell_light: Optional[bool] = None) -> str:
    """What the widget's text really sits on, as #RRGGBB: its own background composited at its opacity
    over the real taskbar colour (taskbar_color)."""
    if shell_light is None:
        shell_light = shell_is_light()
    bar = taskbar_color(shell_light)
    try:
        alpha = max(0.0, min(1.0, float(config.get("background_opacity", 0) or 0) / 100.0))
    except (TypeError, ValueError):
        alpha = 0.0
    bg = config.get("background_color")
    if alpha <= 0.0 or not _is_hex(bg):
        return bar
    seen = tuple(round(a * alpha + t * (1.0 - alpha)) for a, t in zip(_rgb(bg), _rgb(bar)))
    return "#%02X%02X%02X" % seen


def legible_on(hex_code: str, backdrop: str, fallback: str,
               floor: Optional[float] = None) -> str:
    """``hex_code`` if it reaches ``floor`` (ACCENT_PEN_MIN_CONTRAST) against ``backdrop``, else
    ``fallback`` - the default text colour."""
    floor = constants.renderer.ACCENT_PEN_MIN_CONTRAST if floor is None else floor
    if not _is_hex(hex_code) or not _is_hex(backdrop):
        return hex_code
    return hex_code if contrast_ratio(hex_code, backdrop) >= floor else fallback


def default_text_on(config: Dict[str, Any], colors: Dict[str, str], light: bool, backdrop: str) -> str:
    """The "default text colour" a too-faint pen falls back to on ``backdrop``: the user's own text colour
    when it reads there (4.5:1), else the better of that half's factory text and its opposite."""
    own = colors.get("default_color")
    if _is_hex(own) and contrast_ratio(own, backdrop) >= 4.5:
        return own
    theme_colors = constants.config.defaults.THEMED_COLOR_DEFAULTS   # {stem: (dark, light)}
    dark_default, light_default = theme_colors["default_color"]
    candidates = [light_default if light else dark_default, dark_default if light else light_default]
    return max(candidates, key=lambda c: contrast_ratio(c, backdrop))


class PaintColors(Dict[str, Any]):
    """``paint_colors`` result: the themed colours to paint (a ``{stem: hex}`` dict) plus flags."""
    high_contrast: bool = False
    coding_off: bool = False
    backdrop: Optional[str] = None      # set on an accent bar: pens are judged against it


def paint_colors(config: Dict[str, Any], light: Optional[bool] = None) -> PaintColors:
    """The colours the widget PAINTS with (RenderConfig.from_dict): ``resolve`` plus the system's say.

    - High Contrast (4a): every themed pen is the system text colour (COLOR_WINDOWTEXT), coding is off;
      the caller also hides the graph and the widget background.
    - Accent taskbar (5a): any pen under ACCENT_PEN_MIN_CONTRAST against the real bar (composited under
      the widget's background) becomes the default text colour. If the bar's shade cannot be read, colour
      coding is turned off instead (5b).
    Paint time only: the config - and so Settings - keeps the user's colours.
    """
    shell_light = shell_is_light()
    if light is None:
        light = backdrop_is_light(config, shell_light)
    colors = PaintColors(resolve(config, light))
    # 7a's light-taskbar text is Windows' near-black #1B1B1B, not pure black: on the light bar itself that
    # is 14.8:1, but over a mid-grey widget background (say 50 % black on a light bar) it is the
    # difference between 3.9 and 4.7:1. The factory value only - a chosen colour is the user's.
    theme_colors = constants.config.defaults.THEMED_COLOR_DEFAULTS
    if light and colors["default_color"].upper() == theme_colors["default_color"][1].upper():
        backdrop = seen_backdrop(config, shell_light)
        if contrast_ratio(colors["default_color"], backdrop) < 4.5:
            colors["default_color"] = max((colors["default_color"], constants.color.BLACK),
                                          key=lambda c: contrast_ratio(c, backdrop))
    app = appearance()
    if app.high_contrast:
        text = app.hc_text if _is_hex(app.hc_text) else colors["default_color"]
        for stem in list(colors):
            colors[stem] = text
        colors.high_contrast = True
        colors.coding_off = True
        return colors
    if app.accent_on:
        if not app.accent_bar:
            colors.coding_off = True
            return colors
        backdrop = seen_backdrop(config, shell_light)
        fallback = default_text_on(config, colors, light, backdrop)
        for stem in list(colors):
            colors[stem] = legible_on(colors[stem], backdrop, fallback)
        colors.backdrop = backdrop
    return colors
