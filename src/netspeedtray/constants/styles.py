"""
Constants defining specific UI element styles, like colors and stylesheets.
"""
from typing import Final
from .color import color

class UIStyleConstants:
    """Defines stylesheets and theme colors for the UI."""
    DARK_MODE_BG_COLOR: Final[str] = "#2B2B2B"
    DARK_MODE_TEXT_COLOR: Final[str] = color.WHITE
    LIGHT_MODE_BG_COLOR: Final[str] = "#F3F3F3"
    LIGHT_MODE_TEXT_COLOR: Final[str] = color.BLACK
    BORDER_COLOR: Final[str] = "#505050"
    HOVER_BORDER_COLOR: Final[str] = color.WHITE
    LEGEND_DARK_FRAME_COLOR: Final[str] = "#404040"
    APP_LIST_BG_DARK: Final[str] = "#1E1E1E"
    APP_LIST_BG_LIGHT: Final[str] = "#F5F5F5"
    SETTINGS_PANEL_BG_DARK: Final[str] = "#1E1E1E"
    SETTINGS_PANEL_BG_LIGHT: Final[str] = color.WHITE
    SETTINGS_PANEL_TEXT_DARK: Final[str] = color.WHITE
    SETTINGS_PANEL_TEXT_LIGHT: Final[str] = "#1F1F1F"
    GRAPH_BG_DARK: Final[str] = "#1E1E1E"
    GRAPH_BG_LIGHT: Final[str] = color.WHITE
    GRAPH_TEXT_DARK: Final[str] = color.WHITE
    GRAPH_TEXT_LIGHT: Final[str] = "#1F1F1F"
    COMBOBOX_BG_DARK: Final[str] = "#2D2D2D"
    COMBOBOX_BG_LIGHT: Final[str] = "#F3F3F3"
    COMBOBOX_BORDER_DARK: Final[str] = "#3D3D3D"
    COMBOBOX_BORDER_LIGHT: Final[str] = "#CCCCCC"

    # Added missing UI colors
    UI_MICA_LIGHT: Final[str] = "#F3F3F3"
    UI_SIDEBAR_BG: Final[str] = "#E8ECEF"
    UI_SIDEBAR_SELECTED: Final[str] = "#D1D6DB"
    UI_TEXT_COLOR: Final[str] = "#1F1F1F"
    UI_ACCENT_FALLBACK: Final[str] = "#0078D4"
    UI_BORDER_COLOR: Final[str] = "#A0A0A0"

    HAMBURGER_DARK_STYLE: Final[str] = f"QPushButton {{ border: none; font-size: 16px; color: {DARK_MODE_TEXT_COLOR}; background: transparent; }}"
    HAMBURGER_LIGHT_STYLE: Final[str] = f"QPushButton {{ border: none; font-size: 16px; color: {LIGHT_MODE_TEXT_COLOR}; background: transparent; }}"
    STATS_DARK_STYLE: Final[str] = f"color: {DARK_MODE_TEXT_COLOR}; background-color: {DARK_MODE_BG_COLOR}; padding: 2px; font-size: 14px; border: none;"
    STATS_LIGHT_STYLE: Final[str] = f"color: {LIGHT_MODE_TEXT_COLOR}; background: transparent; padding: 2px; font-size: 14px; border: none;"
    SETTINGS_STYLE: Final[str] = f"background-color: {DARK_MODE_BG_COLOR};"
    LABEL_STYLE: Final[str] = f"color: {DARK_MODE_TEXT_COLOR};"

    BUTTON_STYLE: Final[str] = """
        QPushButton {
            background-color: #0078D4; color: white; border-radius: 4px;
            padding: 5px 15px; border: 1px solid #005A9E; min-height: 20px;
        }
        QPushButton:hover { background-color: #0086F0; }
        QPushButton:pressed { background-color: #005A9E; }
        QPushButton:disabled { background-color: #505050; color: #909090; border: 1px solid #444; }
    """

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate that all style attributes are valid."""
        for attr_name in dir(self):
            if not attr_name.startswith('_') and attr_name.isupper():
                value = getattr(self, attr_name)
                # Validate colors
                if attr_name.endswith(("_COLOR", "_BG", "_DARK", "_LIGHT")):
                    if isinstance(value, str) and not value.startswith("#"):
                        raise ValueError(f"Color attribute '{attr_name}' must be a hex string.")
                # Validate stylesheets
                elif attr_name.endswith("_STYLE"):
                    if not isinstance(value, str) or not value:
                        raise ValueError(f"Stylesheet attribute '{attr_name}' must be a non-empty string.")

# Singleton instance for easy access
styles = UIStyleConstants()