"""
UI styling engine for NetSpeedTray using PyQt6 QSS.

This module reads raw style constants from `constants.styles` and uses them
to build dynamic stylesheets for different application components.
"""

from PyQt6.QtGui import QColor
import winreg

# Import the design tokens (raw values) and other UI constants
from netspeedtray.constants import styles as style_constants
from netspeedtray.constants import ui, color as color_constants


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
        return QColor(style_constants.styles.UI_ACCENT_FALLBACK)


def dialog_style() -> str:
    """Style for the main SettingsDialog."""
    dark_mode_active = is_dark_mode()
    
    sidebar_bg = style_constants.DIALOG_SIDEBAR_BG_DARK if dark_mode_active else style_constants.DIALOG_SIDEBAR_BG_LIGHT
    content_bg = style_constants.DIALOG_CONTENT_BG_DARK if dark_mode_active else style_constants.DIALOG_CONTENT_BG_LIGHT
    section_bg = style_constants.DIALOG_SECTION_BG_DARK if dark_mode_active else style_constants.DIALOG_SECTION_BG_LIGHT
    text_color = style_constants.DARK_MODE_TEXT_COLOR if dark_mode_active else style_constants.LIGHT_MODE_TEXT_COLOR
    border_color = "#404040" if dark_mode_active else "#D0D0D0"

    label_style_str = f"color: {text_color}; font-size: 13px; font-family: 'Segoe UI Variable';"

    return f"""
        QDialog {{
            background-color: {sidebar_bg};
            border-radius: 8px;
            font-family: "Segoe UI Variable";
        }}
        QWidget#contentWidget {{
            background-color: {content_bg}; 
        }}
        QLabel {{
            {label_style_str}
            background-color: transparent;
        }}
        QGroupBox {{
            font-size: 14px;
            font-weight: 600;
            margin-top: 12px;
            color: {text_color};
            background-color: {section_bg};
            border: 1px solid {border_color};
            border-radius: 6px;
            padding: 10px; 
            padding-top: 20px;
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
    """


def sidebar_style() -> str:
    """Style for the sidebar (QListWidget) in the main SettingsDialog."""
    dark_mode_active = is_dark_mode()
    
    sidebar_bg = style_constants.DIALOG_SIDEBAR_BG_DARK if dark_mode_active else style_constants.DIALOG_SIDEBAR_BG_LIGHT
    text_color = style_constants.DARK_MODE_TEXT_COLOR if dark_mode_active else style_constants.LIGHT_MODE_TEXT_COLOR
    hover_bg = "#4A4A4A" if dark_mode_active else "#E0E0E0"

    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"

    return f"""
        QListWidget {{
            background-color: {sidebar_bg};
            border: none;
            font-family: "Segoe UI Variable";
            font-size: 13px;
            padding: 8px 0;
            margin: 0px;
            outline: none;
        }}
        QListWidget::item {{
            padding: 8px 12px;
            color: {text_color};
            border: none;
            border-radius: 4px;
            margin: 2px 6px;
            outline: none;
        }}
        QListWidget::item:selected {{
            background-color: {accent_rgb};
            color: {color_constants.WHITE};
            border-radius: 4px; 
            outline: none;
        }}
        QListWidget::item:hover:!selected {{
            background-color: {hover_bg};
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
            font-family: 'Segoe UI Variable';
            font-size: 15px;
            font-weight: 600;
            color: {style_constants.DARK_MODE_TEXT_COLOR};
            padding: 5px 0 10px 0;
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


def graph_overlay_style() -> str:
    """
    Returns a stylesheet for the overlay elements on the graph (stats bar, hamburger).
    """
    return """
        QLabel, QPushButton {
            background-color: rgba(40, 40, 40, 0.85);
            color: white;
            padding: 4px;
            font-size: 13px;
            font-family: 'Segoe UI Variable';
            border-radius: 4px;
            border: none;
        }
        QPushButton {
            padding: 2px;
        }
        QPushButton:hover {
            background-color: rgba(55, 55, 55, 0.9);
        }
        QPushButton:pressed {
            background-color: rgba(25, 25, 25, 0.95);
        }
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
    handle_hover_bg = accent_qcolor.lighter(115).name()
    handle_pressed_bg = accent_qcolor.darker(115).name()
    
    handle_border_color_str = QColor(handle_bg).lighter(130).name() if not dark_mode_active else QColor(handle_bg).darker(130).name()
    handle_hover_border_color_str = QColor(handle_hover_bg).lighter(130).name() if not dark_mode_active else QColor(handle_hover_bg).darker(130).name()
    handle_pressed_border_color_str = QColor(handle_pressed_bg).darker(130).name()

    return f"""
        QSlider::groove:horizontal {{
            background: {groove_bg_inactive};
            height: 4px; border-radius: 2px; margin: 8px 0;
        }}
        QSlider::sub-page:horizontal {{
            background: {accent_rgb};
            height: 4px; border-radius: 2px; margin: 8px 0;
        }}
        QSlider::add-page:horizontal {{
            background: {groove_bg_inactive};
            height: 4px; border-radius: 2px; margin: 8px 0;
        }}
        QSlider::handle:horizontal {{
            background: {handle_bg};
            border: 2px solid {handle_border_color_str};
            width: 16px; height: 16px;
            margin: -8px 0; border-radius: 8px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {handle_hover_bg};
            border: 2px solid {handle_hover_border_color_str};
        }}
        QSlider::handle:horizontal:pressed {{
            background: {handle_pressed_bg};
            border: 2px solid {handle_pressed_border_color_str};
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
            font-family: "Segoe UI Variable"; font-size: 13px;
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