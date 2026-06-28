"""
UI styling engine for NetSpeedTray using PyQt6 QSS.

This module reads raw style constants from `constants.styles` and uses them
to build dynamic stylesheets for different application components.
"""

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPixmap, QPainter
from PyQt6.QtCore import Qt
import winreg

# Import the design tokens (raw values) and other UI constants
from netspeedtray.constants import styles as style_constants
from netspeedtray.constants import ui, color as color_constants

_variable_present = None  # lazy cache: is "Segoe UI Variable" installed?


def fluent_icon(codepoint: int, size: int = 16, color: str = "#FFFFFF") -> QIcon:
    """Render a Segoe Fluent Icons glyph to a tintable QIcon — the native Win11 iconography.

    Win11 ships 'Segoe Fluent Icons'; Win10 ships 'Segoe MDL2 Assets'. Listing both lets Win11 win and
    Win10 degrade. The glyph is drawn to a transparent pixmap in ``color`` so it can be tinted per state
    (e.g. text_secondary at rest → accent when a tab/nav row is selected). Uses setPixelSize for a
    DPI-crisp icon optically matched to the label's px font. NEVER pass an emoji — only Fluent codepoints.
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    try:
        f = QFont()
        f.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
        f.setPixelSize(size)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, chr(codepoint))
    finally:
        p.end()
    return QIcon(pm)


def font(token: tuple) -> QFont:
    """
    Build a QFont for a Fluent type token — a ``(style_name, pixel_size, weight)`` tuple
    from ``constants.styles`` (e.g. ``TYPE_BODY_STRONG``).

    On Windows 11 the optical cut + weight is selected via ``setStyleName`` on the single
    "Segoe UI Variable" family (the cuts are STYLES, not family names). On Windows 10,
    where that family isn't installed, falls back to plain Segoe UI at the same px/weight.
    """
    global _variable_present
    style_name, px, weight = token
    if _variable_present is None:
        try:
            _variable_present = style_constants.FONT_FAMILY_VARIABLE in QFontDatabase.families()
        except Exception:
            _variable_present = False

    if _variable_present:
        f = QFont(style_constants.FONT_FAMILY_VARIABLE)
        f.setStyleName(style_name)       # selects the optical cut + weight variant
        f.setPixelSize(px)
        f.setWeight(weight)              # keep the weight request consistent
        return f
    f = QFont(style_constants.FONT_FALLBACK)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


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


def prefers_reduced_motion() -> bool:
    """True when the user has turned Windows animations OFF (Settings → Accessibility → Visual effects
    → 'Animation effects', i.e. SPI_GETCLIENTAREAANIMATION = FALSE). Decorative slides/fades should
    snap instead of animate when this is set. Defaults to False (animate) on any query failure."""
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

    # Input/combobox fill and the thin overlay-scrollbar handle, theme-aware.
    input_bg = "#383838" if dark_mode_active else "#FFFFFF"
    scroll_handle = "rgba(255, 255, 255, 0.22)" if dark_mode_active else "rgba(0, 0, 0, 0.22)"
    scroll_handle_hover = "rgba(255, 255, 255, 0.42)" if dark_mode_active else "rgba(0, 0, 0, 0.42)"

    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"
    
    # Radio Button Dot Color (White in dark mode, Black/White in light mode? Usually White on Accent is safe)
    dot_color = "white"

    label_style_str = f"color: {text_color}; font-size: 13px; font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;"
    
    # Unchecked border
    radio_border_dull = "#999999" if dark_mode_active else "#666666"

    return f"""
        QDialog {{
            background-color: {sidebar_bg};
            border-radius: 8px;
            font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
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
            font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
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

        /* Comboboxes + spinboxes: native-looking inputs (were bare default Qt). */
        QComboBox, QSpinBox, QDoubleSpinBox {{
            background-color: {input_bg};
            border: 1px solid {border_color};
            border-radius: 4px;
            padding: 4px 8px;
            min-height: 22px;
            color: {text_color};
        }}
        QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
            border-color: {accent_rgb};
        }}
        /* The single focus-ring style: a 2px accent border replaces the bare
           outline:none, restoring keyboard-nav visibility on the inputs. */
        QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 2px solid {accent_rgb};
            padding: 3px 7px; /* keep total size stable when the border thickens */
        }}
        QComboBox::drop-down {{
            border: none;
            width: 22px;
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

        /* Thin, rounded, subtle overlay scrollbars (were fat default Qt bars). */
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {scroll_handle};
            border-radius: 5px;
            min-height: 28px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {scroll_handle_hover};
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
            margin: 0px;
        }}
        QScrollBar::handle:horizontal {{
            background: {scroll_handle};
            border-radius: 5px;
            min-width: 28px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {scroll_handle_hover};
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            width: 0px;
            height: 0px;
        }}
        QScrollBar::add-page, QScrollBar::sub-page {{
            background: transparent;
        }}
    """


def collapsible_section_style() -> str:
    """Style for CollapsibleSection header widget."""
    dark_mode_active = is_dark_mode()
    text_color = style_constants.DARK_MODE_TEXT_COLOR if dark_mode_active else style_constants.LIGHT_MODE_TEXT_COLOR
    hover_bg = "rgba(255, 255, 255, 0.05)" if dark_mode_active else "rgba(0, 0, 0, 0.03)"

    return f"""
        QWidget#collapsibleHeader {{
            background-color: transparent;
            border: none;
            padding: 4px 0px;
        }}
        QWidget#collapsibleHeader:hover {{
            background-color: {hover_bg};
            border-radius: 6px;
        }}
        QWidget#collapsibleHeader QLabel {{
            color: {text_color};
            font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
            background-color: transparent;
        }}
        QWidget#collapsibleHeader QLabel#sectionTitle {{
            font-size: 14px;
            font-weight: 600;
        }}
        QWidget#collapsibleHeader QLabel#sectionChevron {{
            font-size: 12px;
        }}
    """


def sidebar_style() -> str:
    """Style for the sidebar (QListWidget) in the main SettingsDialog."""
    dark_mode_active = is_dark_mode()
    
    sidebar_bg = style_constants.DIALOG_SIDEBAR_BG_DARK if dark_mode_active else style_constants.DIALOG_SIDEBAR_BG_LIGHT
    text_color = style_constants.DARK_MODE_TEXT_COLOR if dark_mode_active else style_constants.LIGHT_MODE_TEXT_COLOR
    # Win11 NavigationView selection: a subtle fill + a short accent indicator bar, NOT a saturated
    # full-bleed accent block; hover is the same subtle fill (not an opaque grey).
    subtle_fill = style_constants.SUBTLE_FILL_DARK if dark_mode_active else style_constants.SUBTLE_FILL_LIGHT

    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"

    return f"""
        QListWidget {{
            background-color: {sidebar_bg};
            border: none;
            font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
            font-size: 13px;
            padding: 8px 0;
            margin: 0px;
            outline: none;
        }}
        QListWidget::item {{
            padding: 8px 12px;
            color: {text_color};
            border: none;
            border-left: 3px solid transparent;   /* reserve the indicator width — no shift on select */
            border-radius: 4px;
            margin: 2px 6px;
            outline: none;
        }}
        QListWidget::item:selected {{
            background-color: {subtle_fill};
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
            font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
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
    return """
        QLabel {
            background-color: rgba(0, 0, 0, 0.6);
            color: rgba(255, 255, 255, 0.9);
            border-radius: 12px;
            padding: 4px 12px;
            font-size: 11px;
            font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
            font-weight: 500;
        }
    """


def graph_overlay_style() -> str:
    """
    Returns a stylesheet for the overlay elements on the graph (hamburger button).
    """
    return """
        QPushButton {
            background-color: rgba(40, 40, 40, 0.85);
            color: white;
            padding: 2px;
            font-size: 13px;
            font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
            border-radius: 4px;
            border: none;
        }
        QPushButton:hover {
            background-color: rgba(55, 55, 55, 0.9);
        }
        QPushButton:pressed {
            background-color: rgba(25, 25, 25, 0.95);
        }
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
    return """
        QLabel {
            color: #888;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
            background-color: transparent;
        }
    """


def graph_stats_value_style() -> str:
    """Style for the large monospace value in a stat card."""
    return """
        QLabel {
            color: white;
            font-size: 14px;
            font-weight: 600;
            font-family: 'Cascadia Mono', 'Consolas', 'SF Mono', monospace;
            background-color: transparent;
        }
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
            font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
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

    timeline_pills_style() is hardcoded for the graph window's dark canvas (light-grey glyphs on a
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
            font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
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
        # Light mode: Darker grey border
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
            font-family: "Segoe UI Variable", "Segoe UI", sans-serif; font-size: 13px;
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



