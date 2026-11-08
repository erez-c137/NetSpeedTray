"""
UI styling engine for NetSpeedTray using PyQt6 QSS.

This module reads raw style constants from `constants.styles` and uses them
to build dynamic stylesheets for different application components.
"""

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPixmap, QPainter
from PyQt6.QtCore import Qt, QSize
import os
import hashlib
import winreg
from typing import Optional

# Import the design tokens (raw values) and other UI constants
from netspeedtray.constants import styles as style_constants
from netspeedtray.constants import ui, color as color_constants

_variable_present = None  # lazy cache: is "Segoe UI Variable" installed?


_icon_family: Optional[str] = None   # lazy cache: the installed Fluent icon family


def icon_font_family() -> str:
    """The Fluent icon font: 'Segoe Fluent Icons' (Win11), else 'Segoe MDL2 Assets' (Win10). Picked from
    ``QFontDatabase.families()`` - names only, no styles loaded - rather than handed to Qt as a fallback
    list: a QFont with more than one family makes Qt load the styles of every installed font family on
    its first use (~38 MB, process-wide, never freed)."""
    global _icon_family
    if _icon_family is None:
        try:
            installed = set(QFontDatabase.families())
        except Exception:
            installed = set()
        _icon_family = next((fam for fam in style_constants.ICON_FONT_FAMILIES if fam in installed),
                            style_constants.ICON_FONT_FAMILIES[-1])
    return _icon_family


def fluent_icon(codepoint: int, size: int = 16, color: str = "#FFFFFF") -> QIcon:
    """Render a Segoe Fluent Icons glyph to a tintable QIcon - the native Win11 iconography.

    Win11 ships 'Segoe Fluent Icons'; Win10 ships 'Segoe MDL2 Assets' (icon_font_family picks the one
    installed). The glyph is drawn to a transparent pixmap in ``color`` so it can be tinted per state
    (e.g. text_secondary at rest → accent when a tab/nav row is selected). Uses setPixelSize for a
    DPI-crisp icon optically matched to the label's px font. NEVER pass an emoji - only Fluent codepoints.
    No font merging: both icon fonts carry every codepoint the app uses, and a Fluent codepoint has no
    meaningful fallback glyph - searching for one would load every installed family.
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    try:
        f = QFont(icon_font_family())
        f.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        f.setPixelSize(size)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, chr(codepoint))
    finally:
        p.end()
    return QIcon(pm)


def combo_chevron_url(color_hex: str) -> str:
    """Render the Fluent ChevronDown to a small cached PNG and return a QSS-friendly (forward-slash)
    path for ``QComboBox::down-arrow {{ image: url(...) }}``.

    Qt drops a combo's native arrow as soon as ``::drop-down`` is QSS-styled, which left every styled
    dropdown looking like a bare box. Supplying our own themed glyph restores the affordance with the
    native Win11 chevron. Rendered at 2× then scaled by QSS for crispness; cached per color so it's
    written once. Degrades to no-arrow (empty string) if the app-data dir can't be written.
    """
    try:
        from netspeedtray.utils.helpers import get_app_data_path
        key = hashlib.md5(color_hex.encode("utf-8")).hexdigest()[:8]
        cache_dir = os.path.join(get_app_data_path(), "cache")
        os.makedirs(cache_dir, exist_ok=True)
        # Render at the on-screen size (QSS clips rather than scales a ::down-arrow image, so a larger
        # source would show only a cropped fragment - the "little square" bug).
        path = os.path.join(cache_dir, f"chevron_{key}_v2.png")
        if not os.path.exists(path):
            fluent_icon(0xE70D, 14, color_hex).pixmap(QSize(14, 14)).save(path, "PNG")
        return path.replace("\\", "/")
    except Exception:
        return ""


# A dropdown QToolButton's right padding: its text padding + room for the chevron (menu_chevron_qss).
MENU_CHEVRON_PADDING = 32


def menu_chevron_qss(selector: str = "QToolButton") -> str:
    """QSS drawing a dropdown button's chevron as its menu indicator - the Fluent ChevronDown image the
    combos use (combo_chevron_url) - in the button's right padding (give it MENU_CHEVRON_PADDING).
    Not a "\u25be" in the label: no UI face has that glyph, so Qt searched every installed family for
    it (the Monitor's first open loaded ~850 font files)."""
    url = combo_chevron_url("#C8C8C8" if is_dark_mode() else "#505050")
    if not url:
        return f" {selector}::menu-indicator {{ image: none; width: 0; }}"
    return (f" {selector}::menu-indicator {{ image: url(\"{url}\"); width: 14px; height: 14px;"
            f" subcontrol-origin: border; subcontrol-position: center right; right: 10px; }}")


def spin_arrow_url(up: bool, color_hex: str) -> str:
    """Up/down chevron for a QSpinBox's stepper buttons, cached like ``combo_chevron_url``.

    Qt drops the native stepper arrows the moment ``::up-button``/``::down-button`` are QSS-styled.
    We supply our own themed chevrons AND (in ``dialog_style``) give the buttons an explicit width,
    which stops the styled edit field from overlapping the up-button and swallowing its clicks - the
    root of #169 ("down arrow works, up arrow doesn't"), since the native side-by-side layout puts the
    up-button under the edit field's right edge. Degrades to no image (empty string) on failure.
    """
    try:
        from netspeedtray.utils.helpers import get_app_data_path
        codepoint = 0xE70E if up else 0xE70D  # Fluent ChevronUp / ChevronDown
        key = hashlib.md5(f"{codepoint}_{color_hex}".encode("utf-8")).hexdigest()[:8]
        cache_dir = os.path.join(get_app_data_path(), "cache")
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, f"spin_{key}_v1.png")
        if not os.path.exists(path):
            fluent_icon(codepoint, 10, color_hex).pixmap(QSize(10, 10)).save(path, "PNG")
        return path.replace("\\", "/")
    except Exception:
        return ""


def _is_rtl() -> bool:
    """True when the app is running in a right-to-left layout (Hebrew). Reads the
    QApplication layout direction the app already sets from i18n.is_rtl(), so it stays
    decoupled from the i18n module (no circular import) and needs no locale argument."""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        return bool(app is not None and app.layoutDirection() == Qt.LayoutDirection.RightToLeft)
    except Exception:
        return False


def _variable_installed() -> bool:
    global _variable_present
    if _variable_present is None:
        try:
            _variable_present = style_constants.FONT_FAMILY_VARIABLE in QFontDatabase.families()
        except Exception:
            _variable_present = False
    return _variable_present


# --------------------------------------------------------------------------- the UI's one face (rc.7)
#
# The app's UI text - menus, the hover card, flyouts, Settings, Monitor - is drawn in ONE face per UI
# language, never a family list: a QFont (or a QSS font-family) that lists more than one family, a
# family name Qt can't match, or a glyph the face lacks makes Qt resolve its whole fallback chain,
# which loads the styles of EVERY installed font family (~38 MB, ~850 mapped font files on a typical
# PC, process-wide, never freed). So the face must cover the language's own text and Latin:
#   - Segoe UI Variable for the Latin, Cyrillic and Greek UIs (Windows 10: Segoe UI);
#   - Segoe UI for a right-to-left UI (#194: Segoe UI Variable has no Hebrew letters);
#   - the language's Windows UI face for ja/ko/zh/bn (constants.fonts.SCRIPT_FAMILIES' first installed
#     face: Yu Gothic UI, Malgun Gothic, Microsoft YaHei UI / JhengHei UI, Nirmala UI).
# The widget keeps its own (configurable) face and draws its own strings run by run (utils/font_runs).

_ui_language = ""
_ui_family: Optional[str] = None


def set_ui_language(language: Optional[str]) -> None:
    """The UI language, which picks the UI face (call once at startup - a language change restarts)."""
    global _ui_language, _ui_family
    language = str(language or "")
    if language != _ui_language:
        _ui_language = language
        _ui_family = None


def ui_family() -> str:
    """The ONE family the app's UI text is drawn in, for the UI language (see above)."""
    global _ui_family
    if _is_rtl():
        return style_constants.FONT_FALLBACK
    if _ui_family is None:
        from netspeedtray.constants import fonts as font_constants
        from netspeedtray.utils.font_runs import resolve_family
        family = None
        for candidate in font_constants.SCRIPT_FAMILIES.get(_ui_language, ()):
            family = resolve_family(candidate)   # the name Qt lists (a localized one on a CJK Windows)
            if family:
                break
        if not family:
            from netspeedtray.constants.i18n import I18nStrings
            if _ui_language in I18nStrings.RTL_LANGUAGES:
                family = style_constants.FONT_FALLBACK
            elif _variable_installed():
                family = style_constants.FONT_FAMILY_VARIABLE
            else:
                family = style_constants.FONT_FALLBACK
        _ui_family = family
    return _ui_family


def ui_family_css() -> str:
    """``font-family: '<the UI face>';`` for a stylesheet - one family, never a list (see above)."""
    return "font-family: '%s';" % ui_family().replace("'", "")


_mono_family: Optional[str] = None


def mono_family() -> str:
    """The one installed monospace face: Cascadia Mono, else Consolas, else Courier New."""
    global _mono_family
    if _mono_family is None:
        from netspeedtray.constants import fonts as font_constants
        from netspeedtray.utils.font_runs import resolve_family
        _mono_family = next((resolve_family(f) for f in font_constants.MONO_FAMILIES if resolve_family(f)),
                            font_constants.MONO_FAMILIES[-1])
    return _mono_family


def _set_optical_size(f: QFont, style_name: str) -> None:
    """Segoe UI Variable's optical size for a type-ramp style ("Small", "Text Semibold", "Display"...),
    set on the opsz axis explicitly: a font that leaves the axis unset inherits the APP font's (Text),
    so a caption would silently draw in the Text cut."""
    from netspeedtray.constants import fonts as font_constants
    opsz = font_constants.UI_OPSZ.get(str(style_name or "Text").split(" ")[0], font_constants.UI_OPSZ["Text"])
    try:
        f.setVariableAxis(QFont.Tag.fromString("opsz"), float(opsz))
    except Exception:   # Qt < 6.7: the style name alone selects the cut
        pass


def ui_font(point_size: Optional[float] = None, pixel_size: Optional[int] = None,
            weight: Optional[int] = None, optical: str = "Text") -> QFont:
    """A QFont in the UI face (``ui_family``); in Segoe UI Variable at the ``optical`` size."""
    family = ui_family()
    f = QFont(family)
    if pixel_size:
        f.setPixelSize(int(pixel_size))
    elif point_size:
        f.setPointSizeF(float(point_size))
    if weight:
        f.setWeight(QFont.Weight(int(weight)))
    if family == style_constants.FONT_FAMILY_VARIABLE:
        _set_optical_size(f, optical)
    return f


# A run in another face starts with this invisible Latin letter. Qt gives a script-neutral character
# (an arrow, a space) the script of its neighbours, and a face that doesn't support that script's
# writing system is passed over for the whole run - Qt then searches every installed family: "→"
# between Bengali words, drawn in Segoe UI Variable, is a BENGALI arrow to Qt and loaded ~850 font
# files. After a Latin letter the run is Latin, which Segoe UI Variable supports. Screen readers get
# the plain text (set_ui_text sets the accessible name).
_LATIN_ANCHOR = "<span style=\"font-size:1px; color:transparent;\">l</span>"


def _face_runs(text: str, escape: bool) -> str:
    import html
    from netspeedtray.utils.font_runs import family_covers
    base = ui_family()
    out, run, run_face = [], "", None

    def flush():
        if run:
            esc = html.escape(run, quote=False) if escape else run
            out.append(esc if run_face is None else
                       "<span style=\"font-family:'%s';\">%s%s</span>" % (run_face, _LATIN_ANCHOR, esc))

    for ch in text:
        face = None
        if not ch.isspace() and not family_covers(base, ch):
            face = next((f for f in (style_constants.FONT_FAMILY_VARIABLE, style_constants.FONT_FALLBACK)
                         if f != base and family_covers(f, ch)), None)
        if face != run_face and not ch.isspace():
            flush()
            run, run_face = "", face
        run += ch
    flush()
    return "".join(out)


def ui_html(text: str) -> str:
    """Plain ``text`` as rich text in which every run of characters the UI face lacks - the ↑ ↓ →
    arrows in Nirmala UI (Bengali) - is a span in the first of Segoe UI Variable / Segoe UI that has
    them: one known face, so Qt never searches its fallback chain for them (which loads every installed
    family). A character neither has stays in the UI face (Qt's own fallback, as before). Spaces and
    line breaks are kept as in plain text (white-space: pre-wrap)."""
    return "<span style=\"white-space:pre-wrap;\">%s</span>" % _face_runs(text, escape=True)


def rich_plain_text(markup: str) -> str:
    """The words of rich text (a label's accessible name when ui_rich added anchor letters)."""
    from PyQt6.QtGui import QTextDocumentFragment
    return QTextDocumentFragment.fromHtml(markup).toPlainText()


def ui_rich(markup: str) -> str:
    """``ui_html`` for text that already is rich text: only the text between tags gets the spans."""
    import re
    if not needs_ui_html(re.sub(r"<[^>]*>", "", markup)):
        return markup
    return "".join(part if part.startswith("<") else _face_runs(part, escape=False)
                   for part in re.split(r"(<[^>]*>)", markup))


def needs_ui_html(text: str) -> bool:
    """The UI face lacks a (non-space) character of ``text`` (False for a face we can't see - the
    offscreen test platform: drawn as before)."""
    from netspeedtray.utils.font_runs import family_covers, resolve_family
    base = ui_family()
    return bool(text) and resolve_family(base) is not None and not family_covers(base, text)


def face_for_text(text: str, language: Optional[str] = None) -> Optional[str]:
    """None when the UI face draws all of ``text``; else the first known face that does - ``language``'s
    own UI face first (a language's name in its own script: "日本語", "বাংলা"), then every UI language's
    face, Segoe UI Variable and Segoe UI. None too when no known face has it all (Qt's fallback, as
    before) or the face can't be seen (offscreen tests)."""
    from netspeedtray.constants import fonts as font_constants
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.utils.font_runs import family_covers, resolve_family
    if not text or resolve_family(ui_family()) is None or family_covers(ui_family(), text):
        return None
    own = list(font_constants.SCRIPT_FAMILIES.get(str(language or ""), ()))
    if language in I18nStrings.RTL_LANGUAGES:
        own.append(style_constants.FONT_FALLBACK)
    others = [f for fams in font_constants.SCRIPT_FAMILIES.values() for f in fams]
    for candidate in own + [style_constants.FONT_FAMILY_VARIABLE, style_constants.FONT_FALLBACK] + others:
        if family_covers(candidate, text):
            return resolve_family(candidate)
    return None


def symbol_face(text: str) -> Optional[str]:
    """The ONE known face the widget draws ``text`` (symbols: the ▴ ▾ arrow presets) in when the UI face
    lacks it (utils/font_runs' choice, so a preview looks like the taskbar) - None when the UI face has it
    all, or no single known face does."""
    from netspeedtray.utils import font_runs
    if not needs_ui_html(text):
        return None
    if font_runs.family_covers(style_constants.FONT_FAMILY_VARIABLE, text):
        return font_runs.resolve_family(style_constants.FONT_FAMILY_VARIABLE)   # the widget's face, loaded
    runs = font_runs.text_runs(QFont(ui_family()), text)
    if len(runs) == 1 and runs[0][0] not in (None, font_runs.QT_PATH):
        return runs[0][0]
    return None


def ui_label(text: str, parent=None):
    """A QLabel showing ``text`` through set_ui_text."""
    from PyQt6.QtWidgets import QLabel
    label = QLabel(parent)
    set_ui_text(label, text)
    return label


def set_ui_text(label, text: str) -> None:
    """``label.setText(text)``, as rich text (``ui_html``) only when the UI face lacks a character of it;
    otherwise plain text exactly as before."""
    text = str(text)
    if needs_ui_html(text):
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(ui_html(text))
        label.setAccessibleName(text)   # the words, without the run markup's anchor letter
        label.setProperty("nst_ui_html", True)
    else:
        if label.property("nst_ui_html"):
            label.setAccessibleName("")   # back to the label's own text
            label.setProperty("nst_ui_html", False)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setText(text)


def apply_ui_language(app, language: Optional[str]) -> None:
    """Startup: pick the UI face for ``language`` and make it the application font (every widget
    without its own font - the tray menu, tooltips, message boxes - and every per-class font Qt took
    from the system, which QApplication.setFont replaces), at the system's UI point size. Segoe UI
    Variable gets its Text optical size, Windows' cut for ~9 pt UI text and the closest to the Segoe
    UI it replaces. Also registers the script fallbacks (Bengali)."""
    set_ui_language(language)
    apply_script_font_fallbacks(language)
    try:
        size = app.font().pointSizeF()
    except Exception:
        size = -1.0
    app.setFont(ui_font(point_size=size if size and size > 0 else 9.0))


def font(token: tuple) -> QFont:
    """
    Build a QFont for a Fluent type token - a ``(style_name, pixel_size, weight)`` tuple
    from ``constants.styles`` (e.g. ``TYPE_BODY_STRONG``), in the UI face (``ui_family``).

    In Segoe UI Variable the optical cut + weight is selected via ``setStyleName`` (the cuts are
    STYLES, not family names), and the cut's opsz axis value is set too (``_set_optical_size``).
    Any other UI face (Segoe UI on Windows 10 or in a right-to-left UI - #194: Segoe UI Variable has
    no Hebrew -, or the CJK/Bengali UI face) is the plain face at the same px/weight.
    """
    style_name, px, weight = token
    family = ui_family()
    f = QFont(family)
    if family == style_constants.FONT_FAMILY_VARIABLE:
        f.setStyleName(style_name)       # selects the optical cut + weight variant
        _set_optical_size(f, style_name)
    f.setPixelSize(px)
    f.setWeight(weight)                  # keep the weight request consistent
    return f


# UI languages whose script the Segoe families lack, and the Windows font that has it (#457). Qt
# already falls back per run of that script, but a script-neutral mark after Latin text - the
# Bengali danda "।" after ")" or "</b>" - is resolved from the Latin side and lands on Fixedsys, a
# bitmap tick. Registering the script's font as Qt's application fallback for the script AND for
# script-neutral (Common) text fixes that. A fallback is consulted only for glyphs a face lacks, so
# Latin, arrows, CJK, Hebrew and emoji render exactly as before.
#
# Not a substitute of the Segoe families (rc.4 - 2.2.0): a substitute makes every Segoe font resolve
# its fallback chain on its first measurement, which loads the styles of every installed font family
# (~38 MB, never freed) at startup. A fallback family is looked up only when a glyph is missing.
SCRIPT_FONT_FALLBACKS = {"bn_BD": ("Nirmala UI",)}
_SCRIPT_FALLBACK_SCRIPTS = {"bn_BD": ("Script_Bengali", "Script_Common")}


def apply_script_font_fallbacks(language: Optional[str]) -> list:
    """Register SCRIPT_FONT_FALLBACKS[language] as Qt application fallback families for the language's
    script and for script-neutral text. Call once at startup (a language change needs a restart).
    Returns the scripts changed, as QChar.Script names ([] = no-op)."""
    from PyQt6.QtCore import QChar
    language = str(language or "")
    families = SCRIPT_FONT_FALLBACKS.get(language)
    if not families:
        return []
    changed = []
    for name in _SCRIPT_FALLBACK_SCRIPTS.get(language, ()):
        script = getattr(QChar.Script, name)
        present = [x.lower() for x in QFontDatabase.applicationFallbackFontFamilies(script)]
        for family in families:
            if family.lower() not in present:
                QFontDatabase.addApplicationFallbackFontFamily(script, family)
        changed.append(name)
    return changed


def semantic_colors() -> dict:
    """
    Theme-resolved semantic surface tokens for the *current* OS theme. One source of
    truth for the design-system primitives (SettingCard/SettingExpander/etc.) so they
    never hardcode hexes. Values are CSS-ready strings.
    """
    dark = is_dark_mode()
    sc = style_constants
    return {
        "card_bg": sc.CARD_BG_DARK if dark else sc.CARD_BG_LIGHT,
        "card_stroke": sc.CARD_STROKE_DARK if dark else sc.CARD_STROKE_LIGHT,
        "subtle_fill": sc.SUBTLE_FILL_DARK if dark else sc.SUBTLE_FILL_LIGHT,
        "text_primary": sc.DARK_MODE_TEXT_COLOR if dark else sc.LIGHT_MODE_TEXT_COLOR,
        "text_secondary": sc.SUBTLE_TEXT_COLOR_DARK if dark else sc.SUBTLE_TEXT_COLOR_LIGHT,
        "accent": get_accent_color().name(),
    }


# The app's own "Reduce motion" setting (config `reduce_motion`), pushed in by set_app_reduce_motion()
# whenever config is applied. ON forces reduced motion; OFF defers to the Windows setting.
_app_reduce_motion = False


def set_app_reduce_motion(enabled: bool) -> None:
    """Apply the app's "Reduce motion" setting to every animation gated by prefers_reduced_motion()."""
    global _app_reduce_motion
    _app_reduce_motion = bool(enabled)


def prefers_reduced_motion() -> bool:
    """True when the app's "Reduce motion" setting is on, or the user has turned Windows animations
    OFF (Settings → Accessibility → Visual effects → 'Animation effects', i.e.
    SPI_GETCLIENTAREAANIMATION = FALSE). Decorative slides/fades should snap instead of animate when
    this is set. The Windows query defaults to False (animate) on any failure."""
    if _app_reduce_motion:
        return True
    try:
        import ctypes
        SPI_GETCLIENTAREAANIMATION = 0x1042
        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
        if ok:
            return enabled.value == 0
    except Exception:
        pass
    return False


def is_dark_mode() -> bool:
    """Check if Windows is in dark mode."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except Exception:
        return False


_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def read_system_uses_light_theme() -> Optional[int]:
    """The WINDOWS-mode (taskbar) theme flag, SystemUsesLightTheme: 1 light, 0 dark, None unreadable.
    This, not the app mode Qt follows, is what the taskbar the widget sits on is painted in."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY)
        try:
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
        finally:
            winreg.CloseKey(key)
        return int(value)
    except (OSError, ValueError, TypeError):
        return None


def get_accent_color() -> QColor:
    """Retrieve the user's Windows accent color as a QColor."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM")
        value, _ = winreg.QueryValueEx(key, "ColorizationColor")
        winreg.CloseKey(key)
        argb = int(value)
        red = (argb >> 16) & 0xFF
        green = (argb >> 8) & 0xFF
        blue = argb & 0xFF
        return QColor(red, green, blue)
    except Exception:
        return QColor(style_constants.UI_ACCENT_FALLBACK)


def dialog_style() -> str:
    """Style for the main SettingsDialog."""
    dark_mode_active = is_dark_mode()
    
    sidebar_bg = style_constants.DIALOG_SIDEBAR_BG_DARK if dark_mode_active else style_constants.DIALOG_SIDEBAR_BG_LIGHT
    content_bg = style_constants.DIALOG_CONTENT_BG_DARK if dark_mode_active else style_constants.DIALOG_CONTENT_BG_LIGHT
    section_bg = style_constants.DIALOG_SECTION_BG_DARK if dark_mode_active else style_constants.DIALOG_SECTION_BG_LIGHT
    text_color = style_constants.DARK_MODE_TEXT_COLOR if dark_mode_active else style_constants.LIGHT_MODE_TEXT_COLOR
    border_color = "#404040" if dark_mode_active else "#D0D0D0"

    # Input/combobox fill, theme-aware.
    input_bg = "#383838" if dark_mode_active else "#FFFFFF"
    # A themed dropdown chevron (Qt drops the native arrow once ::drop-down is styled).
    chevron_url = combo_chevron_url("#C8C8C8" if dark_mode_active else "#505050")
    # Themed up/down chevrons for spinbox steppers (see spin_arrow_url / #169).
    _spin_glyph = "#C8C8C8" if dark_mode_active else "#505050"
    spin_up_url = spin_arrow_url(True, _spin_glyph)
    spin_down_url = spin_arrow_url(False, _spin_glyph)

    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"
    
    # Radio Button Dot Color (White in dark mode, Black/White in light mode? Usually White on Accent is safe)
    dot_color = "white"

    label_style_str = f"color: {text_color}; font-size: 13px; {ui_family_css()}"
    
    # Unchecked border
    radio_border_dull = "#999999" if dark_mode_active else "#666666"

    return f"""
        QDialog {{
            background-color: {sidebar_bg};
            border-radius: 8px;
            {ui_family_css()}
        }}
        QWidget#contentWidget {{
            background-color: {content_bg}; 
        }}
        QLabel {{
            {label_style_str}
            background-color: transparent;
        }}
        QRadioButton, QCheckBox {{
            color: {text_color};
            {ui_family_css()}
            font-size: 13px;
            background-color: transparent;
            outline: none;
            spacing: 8px;
        }}
        /* Robust Custom Radio Button Styling */
        QRadioButton::indicator {{
            width: 14px;
            height: 14px;
            border-radius: 8px;
            border: 1px solid {radio_border_dull};
            background-color: transparent;
        }}
        QRadioButton::indicator:checked {{
            background-color: {dot_color};
            border: 5px solid {accent_rgb};
            width: 6px;
            height: 6px;
            border-radius: 8px;
        }}
        QRadioButton::indicator:hover {{
            border-color: {accent_rgb};
        }}
        
        QGroupBox {{
            font-size: 14px;
            font-weight: 600;
            /* margin-top must exceed the title's ascent + padding, otherwise
             * the top of the bold title gets clipped (#149). 14px font ~
             * 18-19px box height with bold weight; use 22px to be safe. */
            margin-top: 22px;
            color: {text_color};
            background-color: {section_bg};
            border: none;
            border-radius: 8px; /* Native Win11 radius */
            padding: 12px;
            padding-top: 24px; /* Space for title */
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0px 5px 2px 5px;
            left: 10px;
            color: {text_color};
        }}
        QWidget:focus, QStackedWidget:focus, QStackedWidget::widget:focus {{
            outline: none;
        }}

        /* Comboboxes + spinboxes: native-looking inputs (were bare default Qt). A QLineEdit opts in
           with the fieldStyle="input" property (the metrics URL beside the port spin box). */
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit[fieldStyle="input"] {{
            background-color: {input_bg};
            border: 1px solid {border_color};
            border-radius: 4px;
            padding: 4px 8px;
            min-height: 22px;
            color: {text_color};
        }}
        QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit[fieldStyle="input"]:hover {{
            border-color: {accent_rgb};
        }}
        /* The single focus-ring style: a 2px accent border replaces the bare
           outline:none, restoring keyboard-nav visibility on the inputs. */
        QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit[fieldStyle="input"]:focus {{
            border: 2px solid {accent_rgb};
            padding: 3px 7px; /* keep total size stable when the border thickens */
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
            subcontrol-origin: padding;
            subcontrol-position: center right;
        }}
        QComboBox::down-arrow {{
            image: url("{chevron_url}");
        }}
        /* Explicit spinbox stepper geometry. Without this Qt lays the native up/down
           buttons under the styled edit field's right edge, so the up-button's clicks
           get swallowed while the down-button (further right) still works - the #169
           "up arrow doesn't work" bug. Reserving a fixed-width, stacked pair keeps the
           edit field clear of both and restores a themed, unambiguous stepper. */
        QSpinBox::up-button, QDoubleSpinBox::up-button {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 20px;
            border-left: 1px solid {border_color};
            border-top-right-radius: 4px;
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 20px;
            border-left: 1px solid {border_color};
            border-bottom-right-radius: 4px;
        }}
        QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
        QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
            background-color: {accent_rgb};
        }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
            image: url("{spin_up_url}");
            width: 10px;
            height: 10px;
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
            image: url("{spin_down_url}");
            width: 10px;
            height: 10px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {input_bg};
            color: {text_color};
            border: 1px solid {border_color};
            border-radius: 4px;
            selection-background-color: {accent_rgb};
            selection-color: white;
            outline: 0px;
            padding: 2px;
        }}

        /* Scrollbars are intentionally NOT styled here - Settings inherits the native Windows
           scrollbar, identical to the Monitor window (which never styled it). One native scrollbar
           everywhere; a custom QSS bar read as "not native / too thick". */
    """


def sidebar_style() -> str:
    """Style for the sidebar (QListWidget) in the main SettingsDialog."""
    dark_mode_active = is_dark_mode()
    
    sidebar_bg = style_constants.DIALOG_SIDEBAR_BG_DARK if dark_mode_active else style_constants.DIALOG_SIDEBAR_BG_LIGHT
    text_color = style_constants.DARK_MODE_TEXT_COLOR if dark_mode_active else style_constants.LIGHT_MODE_TEXT_COLOR
    # Win11 NavigationView selection: a subtle fill + a short accent indicator bar, NOT a saturated
    # full-bleed accent block; hover is the same subtle fill (not an opaque gray).
    subtle_fill = style_constants.SUBTLE_FILL_DARK if dark_mode_active else style_constants.SUBTLE_FILL_LIGHT
    # The selected nav row uses the SAME fill as the cards so the whole UI reads as exactly two tones:
    # a dark base (window + sidebar) and a lighter surface (cards + the selected row).
    card_bg = style_constants.CARD_BG_DARK if dark_mode_active else style_constants.CARD_BG_LIGHT

    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"

    return f"""
        QListWidget {{
            background-color: {sidebar_bg};
            border: none;
            {ui_family_css()}
            font-size: 13px;
            padding: 8px 0;
            margin: 0px;
            outline: none;
        }}
        QListWidget::item {{
            padding: 8px 12px;
            color: {text_color};
            border: none;
            border-left: 3px solid transparent;   /* reserve the indicator width - no shift on select */
            border-radius: 4px;
            margin: 2px 6px;
            outline: none;
        }}
        QListWidget::item:selected {{
            background-color: {card_bg};
            color: {text_color};
            border-left: 3px solid {accent_rgb};
            border-radius: 4px;
            outline: none;
        }}
        QListWidget::item:hover:!selected {{
            background-color: {subtle_fill};
            border-radius: 4px;
        }}
        QListWidget:focus, QListWidget::item:focus {{ 
            outline: none; 
            border: none; 
        }}
    """


def graph_settings_panel_style() -> str:
    """
    Returns a single, scoped stylesheet for the graph's settings panel.
    This panel always has a dark, semi-transparent theme.
    """
    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"
    chevron_url = combo_chevron_url("#C8C8C8")   # this panel is always dark

    # Correctly access constants directly from the 'style_constants' instance
    return f"""
        QWidget#settingsPanel {{
            background-color: rgba(32, 32, 32, 0.95); /* Dark, semi-transparent base */
            border-radius: 8px;
            border: 1px solid rgba(80, 80, 80, 0.9);
        }}
        QWidget#controlsContainer {{
            background-color: rgba(45, 45, 45, 0.95); /* Slightly lighter card for controls */
            border: 1px solid #4a4a4a;
            border-radius: 8px;
        }}
        #settingsPanel QLabel {{
            color: {style_constants.SETTINGS_PANEL_TEXT_DARK};
            background-color: transparent;
            font-size: 13px;
        }}
        #settingsPanel QLabel#settingsTitleLabel {{
            {ui_family_css()}
            font-size: 14px;
            font-weight: 600;
            color: {style_constants.DARK_MODE_TEXT_COLOR};
            padding: 2px 0 8px 0;
        }}

        #settingsPanel QComboBox {{
            background-color: {style_constants.COMBOBOX_BG_DARK};
            border: 1px solid {style_constants.COMBOBOX_BORDER_DARK};
            border-radius: 4px;
            padding: 4px 8px;
            color: {style_constants.DARK_MODE_TEXT_COLOR};
        }}
        #settingsPanel QComboBox::drop-down {{
            border: none;
            width: 24px;
            subcontrol-origin: padding;
            subcontrol-position: center right;
        }}
        #settingsPanel QComboBox::down-arrow {{
            image: url("{chevron_url}");
        }}
        #settingsPanel QComboBox QAbstractItemView {{
            color: {style_constants.DARK_MODE_TEXT_COLOR};
            background-color: {style_constants.COMBOBOX_BG_DARK};
            border: 1px solid {style_constants.COMBOBOX_BORDER_DARK};
            selection-background-color: {accent_rgb};
            outline: 0px;
        }}
    """


def graph_tooltip_style() -> str:
    """Returns the stylesheet for the graph's live data tooltip."""
    return """
        QLabel#graphTooltip {
            background-color: rgba(40, 40, 40, 230);
            color: white;
            border: 1px solid rgba(100, 100, 100, 200);
            border-radius: 4px;
            padding: 5px;
            font-size: 9pt;
        }
    """


def zoom_hint_style() -> str:
    """Returns the stylesheet for the 'Double-click to Reset' hint."""
    return f"""
        QLabel {{
            background-color: rgba(0, 0, 0, 0.6);
            color: rgba(255, 255, 255, 0.9);
            border-radius: 12px;
            padding: 4px 12px;
            font-size: 11px;
            {ui_family_css()}
            font-weight: 500;
        }}
    """


def graph_overlay_style() -> str:
    """
    Returns a stylesheet for the overlay elements on the graph (hamburger button).
    """
    return f"""
        QPushButton {{
            background-color: rgba(40, 40, 40, 0.85);
            color: white;
            padding: 2px;
            font-size: 13px;
            {ui_family_css()}
            border-radius: 4px;
            border: none;
        }}
        QPushButton:hover {{
            background-color: rgba(55, 55, 55, 0.9);
        }}
        QPushButton:pressed {{
            background-color: rgba(25, 25, 25, 0.95);
        }}
    """


def graph_stats_bar_style() -> str:
    """
    Returns a stylesheet for the graph stats bar container.
    """
    return """
        QWidget#statsBar {
            background-color: rgba(40, 40, 40, 0.85);
            border-radius: 4px;
            border: none;
        }
    """


def graph_stats_card_style() -> str:
    """Style for an individual stat card container."""
    return """
        QWidget {
            background-color: transparent;
            padding: 2px;
        }
    """


def graph_stats_title_style() -> str:
    """Style for the uppercase title of a stat card."""
    return f"""
        QLabel {{
            color: #888;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            {ui_family_css()}
            background-color: transparent;
        }}
    """


def graph_stats_value_style() -> str:
    """Style for the large monospace value in a stat card."""
    return f"""
        QLabel {{
            color: white;
            font-size: 14px;
            font-weight: 600;
            font-family: '{mono_family()}';
            background-color: transparent;
        }}
    """


def timeline_pills_style() -> str:
    """
    Returns stylesheet for the segmented timeline control (Time Pills).
    Uses the Windows accent color for the active pill.
    """
    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"
    accent_hover = accent_qcolor.lighter(110).name()
    
    return f"""
        QWidget#timelinePills {{
            background: transparent;
        }}
        QWidget#timelinePills QPushButton {{
            background: rgba(255, 255, 255, 0.05);
            color: #ccc;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-right: none; /* Join pills */
            margin: 0px;
            padding: 4px 2px;
            font-size: 10px;
            font-weight: 600;
            {ui_family_css()}
        }}
        QPushButton#pillFirst {{
            border-top-left-radius: 6px;
            border-bottom-left-radius: 6px;
        }}
        QPushButton#pillLast {{
            border-top-right-radius: 6px;
            border-bottom-right-radius: 6px;
            border-right: 1px solid rgba(255, 255, 255, 0.1);
        }}
        QPushButton:hover:!checked {{
            background: rgba(255, 255, 255, 0.15);
            color: white;
        }}
        QWidget#timelinePills QPushButton:checked {{
            background-color: {accent_rgb} !important;
            color: white !important;
            border: 1px solid {accent_rgb} !important;
            font-weight: bold;
        }}
        QWidget#timelinePills QPushButton#pillLast:checked {{
            border-right: 1px solid {accent_rgb} !important;
        }}
    """


def segmented_pills_style(dark: bool) -> str:
    """Theme-aware variant of timeline_pills_style() for the Monitor's period control.

    timeline_pills_style() is hardcoded for the graph window's dark canvas (light-gray glyphs on a
    translucent-white fill); on the Monitor's theme-aware surface that's invisible in light mode. This
    keeps the exact geometry + accent checked-state but flips the unchecked fill/text/border by theme.
    """
    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"

    if dark:
        text, fill, border, hover_fill, hover_text = "#ccc", "rgba(255,255,255,0.05)", \
            "rgba(255,255,255,0.10)", "rgba(255,255,255,0.15)", "white"
    else:
        text, fill, border, hover_fill, hover_text = "#444", "rgba(0,0,0,0.04)", \
            "rgba(0,0,0,0.12)", "rgba(0,0,0,0.08)", "#111"

    return f"""
        QWidget#timelinePills {{
            background: transparent;
        }}
        QWidget#timelinePills QPushButton {{
            background: {fill};
            color: {text};
            border: 1px solid {border};
            border-right: none;
            margin: 0px;
            padding: 4px 2px;
            font-size: 10px;
            font-weight: 600;
            {ui_family_css()}
        }}
        QPushButton#pillFirst {{
            border-top-left-radius: 6px;
            border-bottom-left-radius: 6px;
        }}
        QPushButton#pillLast {{
            border-top-right-radius: 6px;
            border-bottom-right-radius: 6px;
            border-right: 1px solid {border};
        }}
        QPushButton:hover:!checked {{
            background: {hover_fill};
            color: {hover_text};
        }}
        QWidget#timelinePills QPushButton:checked {{
            background-color: {accent_rgb} !important;
            color: white !important;
            border: 1px solid {accent_rgb} !important;
            font-weight: bold;
        }}
        QWidget#timelinePills QPushButton#pillLast:checked {{
            border-right: 1px solid {accent_rgb} !important;
        }}
    """



def toggle_style(total_track_width: int, total_track_height: int) -> str:
    """
    Style for the QCheckBox acting as the track in Win11Toggle.
    """
    dark_mode_active = is_dark_mode()
    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"
    
    # Using direct constants instead of getattr for clarity and performance
    # ...
    track_off_bg = style_constants.COMBOBOX_BORDER_DARK if dark_mode_active else style_constants.COMBOBOX_BORDER_LIGHT
    track_on_bg = accent_rgb
    track_off_hover_bg = QColor(track_off_bg).lighter(120).name() if dark_mode_active else QColor(track_off_bg).darker(110).name()
    track_on_hover_bg = accent_qcolor.darker(115).name()
    checkbox_text_color = style_constants.DARK_MODE_TEXT_COLOR if dark_mode_active else style_constants.LIGHT_MODE_TEXT_COLOR

    track_border_width = 1
    indicator_content_width = max(0, total_track_width - (2 * track_border_width))
    indicator_content_height = max(0, total_track_height - (2 * track_border_width))
    track_border_color_off = "#505050" if dark_mode_active else "#B0B0B0"
    track_border_color_on = accent_qcolor.darker(120).name()
    track_border_color_on_hover = accent_qcolor.darker(130).name()

    return f"""
        QCheckBox {{
            color: {checkbox_text_color};
            background-color: transparent;
            border: none;
            padding: 0px; margin: 0px; spacing: 0px;
        }}
        QCheckBox::indicator {{
            width: {indicator_content_width}px;
            height: {indicator_content_height}px;
            background-color: {track_off_bg};
            border-radius: {total_track_height // 2}px;
            border: {track_border_width}px solid {track_border_color_off};
        }}
        QCheckBox::indicator:checked {{
            background-color: {track_on_bg};
            border: {track_border_width}px solid {track_border_color_on};
        }}
        QCheckBox::indicator:unchecked:hover {{
            background-color: {track_off_hover_bg};
        }}
        QCheckBox::indicator:checked:hover {{
            background-color: {track_on_hover_bg};
            border: {track_border_width}px solid {track_border_color_on_hover};
        }}
    """


def slider_style() -> str:
    """Style for the internal QSlider in Win11Slider."""
    dark_mode_active = is_dark_mode()
    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"
    
    groove_bg_inactive = style_constants.COMBOBOX_BORDER_DARK if dark_mode_active else "#E0E0E0"
    handle_bg = accent_rgb
    
    # Refined Interaction Colors
    handle_hover_bg = accent_qcolor.lighter(110).name()
    handle_pressed_bg = accent_qcolor.darker(110).name()
    
    # Border Logic: Win11 usually has a subtle contrast border, not a thick one.
    if dark_mode_active:
        # Dark mode: Lighter border for visibility against dark bg, or semi-transparent
        handle_border_color_str = "#454545" 
        handle_hover_border_color_str = "#606060"
        handle_pressed_border_color_str = "#303030"
    else:
        # Light mode: Darker gray border
        handle_border_color_str = "#cccccc"
        handle_hover_border_color_str = "#bbbbbb"
        handle_pressed_border_color_str = "#999999"

    # Dimensions (Native Win11 feel)
    groove_h = 4
    handle_size = 18 # 20px is standard, 18px feels tighter for tray apps
    # Margins are critical to prevent "cut off" and center the handle
    # Formula: (GrooveHeight + 2*GrooveMargin) must >= HandleHeight
    # We want Handle centered on Groove.
    # Groove Margin = (TotalHeight - GrooveHeight) / 2
    # Handle Margin = negative offset to pull it up? 
    # QSS Logic: Handle is placed relative to content rect of groove.
    # To center handle (18px) on groove (4px):
    # Handle needs to overhang groove by (18-4)/2 = 7px.
    # So handle margin should be -7px.
    margin_offset = (handle_size - groove_h) // 2 # 7px
    
    # Total widget height must accommodate the handle
    total_height = handle_size + 2 # +2 for buffer/border
    
    return f"""
        QSlider {{
            min-height: {total_height}px;
            max-height: {total_height}px;
            background: transparent;
        }}
        QSlider::groove:horizontal {{
            background: {groove_bg_inactive};
            height: {groove_h}px; 
            border-radius: {groove_h // 2}px; 
            margin: {margin_offset}px 0; /* Pushes groove down to center it */
        }}
        QSlider::sub-page:horizontal {{
            background: {accent_rgb};
            height: {groove_h}px; 
            border-radius: {groove_h // 2}px; 
            margin: {margin_offset}px 0;
        }}
        QSlider::add-page:horizontal {{
            background: {groove_bg_inactive};
            height: {groove_h}px; 
            border-radius: {groove_h // 2}px; 
            margin: {margin_offset}px 0;
        }}
        QSlider::handle:horizontal {{
            background: {handle_bg};
            border: 1px solid {handle_border_color_str}; /* 1px is cleaner */
            width: {handle_size}px; height: {handle_size}px;
            margin: -{margin_offset}px 0; /* Pulls handle up to center align */
            border-radius: {handle_size // 2}px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {handle_hover_bg};
            border: 1px solid {handle_hover_border_color_str};
            /* Scale effect simulated by color change, actual scaling hard in pure QSS without image */
        }}
        QSlider::handle:horizontal:pressed {{
            background: {handle_pressed_bg};
            border: 1px solid {handle_pressed_border_color_str};
            /* Win11 "shrink" effect: simulated by making border thicker/matching bg? 
               Or just darker color is enough for feedback. */
        }}
    """


def button_style(accent: bool = False) -> str:
    """Generic button style, can be for standard or accent buttons."""
    dark_mode_active = is_dark_mode()
    bg_color, text_color, hover_bg_color, pressed_bg_color, border_color = "", "", "", "", ""

    if accent:
        accent_qcolor = get_accent_color()
        bg_color = accent_qcolor.name()
        # CORRECTED ACCESS:
        text_color = color_constants.WHITE
        hover_bg_color = accent_qcolor.lighter(110).name()
        pressed_bg_color = accent_qcolor.darker(110).name()
        border_color = accent_qcolor.darker(120).name()
    else: 
        bg_color = "#4D4D4D" if dark_mode_active else "#E1E1E1"
        # CORRECTED ACCESS:
        text_color = color_constants.WHITE if dark_mode_active else color_constants.BLACK
        hover_bg_color = "#5A5A5A" if dark_mode_active else "#D0D0D0"
        pressed_bg_color = "#404040" if dark_mode_active else "#B8B8B8"
        border_color = "#606060" if dark_mode_active else "#ADADAD"

    return f"""
        QPushButton {{
            background-color: {bg_color};
            color: {text_color};
            border: 1px solid {border_color};
            padding: 5px 15px; border-radius: 4px;
            {ui_family_css()} font-size: 13px;
            min-height: 22px; 
        }}
        QPushButton:hover {{
            background-color: {hover_bg_color};
            border-color: {QColor(border_color).lighter(120).name() if dark_mode_active else QColor(border_color).darker(120).name()};
        }}
        QPushButton:pressed {{
            background-color: {pressed_bg_color};
        }}
        QPushButton:disabled {{
            background-color: {"#3C3C3C" if dark_mode_active else "#F0F0F0"};
            color: {"#707070" if dark_mode_active else "#A0A0A0"};
            border-color: {"#505050" if dark_mode_active else "#D0D0D0"};
        }}
    """


def color_button_style(color_hex: str) -> str:
    """Style for color picker preview buttons."""
    if not (isinstance(color_hex, str) and color_hex.startswith("#") and len(color_hex) == 7):
        # Fallback to a default if the input is invalid
        color_hex = color_constants.BLACK 

    border_c = style_constants.BORDER_COLOR
    button_width = ui.dialogs.COLOR_BUTTON_WIDTH
    button_height = ui.dialogs.COLOR_BUTTON_HEIGHT

    return (
        f"QPushButton {{ background-color: {color_hex}; border: 1px solid {border_c}; "
        f"min-width: {button_width}px; max-width: {button_width}px; "
        f"min-height: {button_height}px; max-height: {button_height}px; "
        f"border-radius: 4px; }}"
    )



