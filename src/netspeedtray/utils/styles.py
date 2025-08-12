"""
UI styling definitions for NetSpeedTray using PyQt6 QSS.
"""

from PyQt6.QtGui import QColor
import winreg
from typing import Dict
from netspeedtray import constants


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
        return QColor(constants.styles.UI_ACCENT_FALLBACK)

def dialog_style() -> str:
    """Style for the main SettingsDialog."""
    dark_mode_active = is_dark_mode()
    sidebar_bg = constants.styles.SETTINGS_PANEL_BG_DARK if dark_mode_active else constants.styles.SETTINGS_PANEL_BG_LIGHT
    # DEFINE THE MISSING VARIABLES HERE
    content_bg = constants.styles.SETTINGS_PANEL_BG_DARK if dark_mode_active else constants.styles.SETTINGS_PANEL_BG_LIGHT
    section_bg = "#2D2D2D" if dark_mode_active else "#F0F0F0"
    text_color = constants.styles.SETTINGS_PANEL_TEXT_DARK if dark_mode_active else constants.styles.SETTINGS_PANEL_TEXT_LIGHT
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
            border: 1px solid {"#404040" if dark_mode_active else "#D0D0D0"};
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
        QPushButton {{
            font-size: 13px;
            padding: 5px 15px;
            border-radius: 4px;
            color: {text_color};
            background-color: {"#454545" if dark_mode_active else "#E0E0E0"};
            border: 1px solid {"#505050" if dark_mode_active else "#C0C0C0"};
            outline: none;
        }}
        QPushButton:hover {{
            background-color: {"#505050" if dark_mode_active else "#D0D0D0"};
        }}
        QPushButton:pressed {{
            background-color: {"#353535" if dark_mode_active else "#B8B8B8"};
        }}
        QCheckBox::indicator {{
            border-radius: {constants.ui.visuals.TOGGLE_TRACK_HEIGHT // 2}px;
        }}
        QWidget:focus {{ outline: none; }}
        QStackedWidget:focus {{ outline: none; }}
        QStackedWidget::widget:focus {{ outline: none; }}
    """

def sidebar_style() -> str:
    """Style for the sidebar (QListWidget)."""
    dark_mode_active = is_dark_mode()
    
    sidebar_bg = constants.styles.SETTINGS_PANEL_BG_DARK if dark_mode_active else constants.styles.UI_SIDEBAR_BG
    selected_bg = "#3A3A3A" if dark_mode_active else constants.styles.UI_SIDEBAR_SELECTED
    hover_bg = "#4A4A4A" if dark_mode_active else "#E0E0E0"
    text_color = constants.styles.SETTINGS_PANEL_TEXT_DARK if dark_mode_active else constants.styles.UI_TEXT_COLOR

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
            color: white;
            border-radius: 4px; 
            outline: none;
        }}
        QListWidget::item:hover:!selected {{
            background-color: {hover_bg};
            border-radius: 4px;
        }}
        QListWidget:focus {{ outline: none; }}
        QListWidget::item:focus {{ outline: none; border: none; }}
    """


def toggle_style(total_track_width: int, total_track_height: int) -> str:
    """
    Style for the QCheckBox acting as the track in Win11Toggle.
    The provided dimensions are treated as the total outer dimensions including border.
    """
    dark_mode_active = is_dark_mode()
    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"
    
    track_off_bg = getattr(constants.styles, 'COMBOBOX_BORDER_DARK', '#333333') if dark_mode_active else getattr(constants.styles, 'COMBOBOX_BORDER_LIGHT', '#CCCCCC')
    track_on_bg = accent_rgb
    track_off_hover_bg = QColor(track_off_bg).lighter(120).name() if dark_mode_active else QColor(track_off_bg).darker(110).name()
    track_on_hover_bg = accent_qcolor.darker(115).name()
    checkbox_text_color = constants.styles.SETTINGS_PANEL_TEXT_DARK if dark_mode_active else constants.styles.SETTINGS_PANEL_TEXT_LIGHT

    track_border_width = 1  # Define the border width for the track indicator
    
    # Calculate content width/height for the QCheckBox::indicator
    # QSS 'width' and 'height' properties apply to the content box.
    indicator_content_width = total_track_width - (2 * track_border_width)
    indicator_content_height = total_track_height - (2 * track_border_width)

    # Ensure content dimensions are not negative
    indicator_content_width = max(0, indicator_content_width)
    indicator_content_height = max(0, indicator_content_height)

    track_border_color_off = "#505050" if dark_mode_active else "#B0B0B0"
    track_border_color_on = accent_qcolor.darker(120).name()
    track_border_color_on_hover = accent_qcolor.darker(130).name()

    return f"""
        QCheckBox {{
            background-color: transparent;
            border: none;
            padding: 0px;
            margin: 0px;
            spacing: 0px;
            color: {checkbox_text_color};
        }}
        QCheckBox::indicator {{
            width: {indicator_content_width}px;
            height: {indicator_content_height}px;
            background-color: {track_off_bg};
            border-radius: {total_track_height // 2}px; /* Radius based on total outer height */
            border: {track_border_width}px solid {track_border_color_off};
        }}
        QCheckBox::indicator:checked {{
            background-color: {track_on_bg};
            border: {track_border_width}px solid {track_border_color_on};
        }}
        QCheckBox::indicator:unchecked:hover {{
            background-color: {track_off_hover_bg};
            /* border-color: {track_border_color_off}; /* Already set */
        }}
        QCheckBox::indicator:checked:hover {{
            background-color: {track_on_hover_bg};
            border: {track_border_width}px solid {track_border_color_on_hover};
        }}
    """

def slider_style() -> str:
    """Style for the internal QSlider in Win11Slider, using the user's accent color."""
    dark_mode_active = is_dark_mode()
    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"
    
    groove_bg_inactive = getattr(constants.styles, 'COMBOBOX_BORDER_DARK', '#333333') if dark_mode_active else "#E0E0E0"
    handle_bg = accent_rgb
    handle_hover_bg = accent_qcolor.lighter(115).name()
    handle_pressed_bg = accent_qcolor.darker(115).name()
    
    handle_border_color_str = QColor(handle_bg).lighter(130).name() if not dark_mode_active else QColor(handle_bg).darker(130).name()
    handle_hover_border_color_str = QColor(handle_hover_bg).lighter(130).name() if not dark_mode_active else QColor(handle_hover_bg).darker(130).name()
    handle_pressed_border_color_str = QColor(handle_pressed_bg).darker(130).name()


    return f"""
        QSlider::groove:horizontal {{
            background: {groove_bg_inactive};
            height: 4px;
            border-radius: 2px;
            margin: 8px 0;
        }}
        QSlider::sub-page:horizontal {{
            background: {accent_rgb};
            height: 4px;
            border-radius: 2px;
            margin: 8px 0;
        }}
        QSlider::add-page:horizontal {{
            background: {groove_bg_inactive};
            height: 4px;
            border-radius: 2px;
            margin: 8px 0;
        }}
        QSlider::handle:horizontal {{
            background: {handle_bg};
            border: 2px solid {handle_border_color_str};
            width: 16px; 
            height: 16px;
            margin: -8px 0;
            border-radius: 8px;
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
        text_color = constants.color.WHITE
        hover_bg_color = accent_qcolor.lighter(110).name()
        pressed_bg_color = accent_qcolor.darker(110).name()
        border_color = accent_qcolor.darker(120).name()
    else: 
        bg_color = "#4D4D4D" if dark_mode_active else "#E1E1E1"
        text_color = constants.color.WHITE if dark_mode_active else constants.color.BLACK
        hover_bg_color = "#5A5A5A" if dark_mode_active else "#D0D0D0"
        pressed_bg_color = "#404040" if dark_mode_active else "#B8B8B8"
        border_color = "#606060" if dark_mode_active else "#ADADAD"

    return f"""
        QPushButton {{
            background-color: {bg_color};
            color: {text_color};
            border: 1px solid {border_color};
            padding: 5px 15px;
            border-radius: 4px;
            font-family: "Segoe UI Variable";
            font-size: 13px;
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
        color_hex = constants.config.defaults.DEFAULT_COLOR

    border_c = constants.styles.UI_BORDER_COLOR
    button_width = constants.ui.dialogs.COLOR_BUTTON_WIDTH
    button_height = constants.ui.dialogs.COLOR_BUTTON_HEIGHT

    return (
        f"QPushButton {{ background-color: {color_hex}; border: 1px solid {border_c}; "
        f"min-width: {button_width}px; max-width: {button_width}px; "
        f"min-height: {button_height}px; max-height: {button_height}px; "
        f"border-radius: 4px; }}"
    )

def always_dark_panel_style() -> Dict[str, str]:
    panel_background_color = constants.styles.GRAPH_BG_DARK
    groupbox_title_text_color = constants.styles.SETTINGS_PANEL_TEXT_DARK
    panel_border_color = constants.styles.BORDER_COLOR
    
    combo_bg_dark_panel = constants.styles.COMBOBOX_BG_DARK
    combo_border_dark_panel = constants.styles.COMBOBOX_BORDER_DARK
    
    accent_qcolor = get_accent_color()
    accent_rgb = f"rgb({accent_qcolor.red()}, {accent_qcolor.green()}, {accent_qcolor.blue()})"

    styles = {
        "QWidget_Container": "background-color: transparent; border: none; outline: none;",
        "QGroupBox_PanelDark": f"""
            QGroupBox {{
                font-size: 14px;
                font-weight: 600;
                color: {groupbox_title_text_color}; 
                background-color: {panel_background_color};
                border: 1px solid {panel_border_color}; 
                border-radius: 6px;
                padding: 10px; 
                padding-top: 20px; 
                margin-top: 8px; 
                outline: none; 
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0px 5px 2px 5px;
                left: 10px;
                color: {groupbox_title_text_color};
            }}
            /* REMOVED: QGroupBox QLabel sub-rule from here */
            QGroupBox > QWidget:focus, QGroupBox QWidget:focus {{ outline: none; }}
            QGroupBox QComboBox:focus {{ outline: none; }}
            QGroupBox QSlider:focus {{ outline: none; }}
            QGroupBox Win11Toggle:focus {{ outline: none; }}
            QGroupBox Win11Slider:focus {{ outline: none; }}
            QGroupBox Win11Toggle QCheckBox:focus {{ outline: none; }}
            QGroupBox Win11Slider QSlider:focus {{ outline: none; }}
        """,
        # This QLabel_PanelDark will be used explicitly in graph.py now
        "QLabel_PanelDark": f"color: {constants.styles.SETTINGS_PANEL_TEXT_DARK}; background-color: transparent; border: none; outline: none; font-size: 13px; font-family: 'Segoe UI Variable';",
        "QComboBox_PanelDark": f"""
            QComboBox {{ 
                background-color: {combo_bg_dark_panel}; 
                color: {constants.styles.SETTINGS_PANEL_TEXT_DARK}; 
                border: 1px solid {combo_border_dark_panel}; 
                padding: 4px 8px 4px 8px; 
                border-radius: 4px;
                min-width: 90px;
                outline: none; 
            }}
            QComboBox:focus {{ /* border: 1px solid {accent_rgb}; */ }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox::down-arrow {{ }}
            QComboBox QAbstractItemView {{
                background-color: {combo_bg_dark_panel}; 
                color: {constants.styles.SETTINGS_PANEL_TEXT_DARK}; 
                selection-background-color: {accent_rgb};
                selection-color: {constants.color.WHITE}; 
                border: 1px solid {combo_border_dark_panel};
                border-radius: 4px; 
                outline: none;
            }}
        """,
    }
    return styles