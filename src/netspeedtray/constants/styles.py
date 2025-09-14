"""
Constants defining specific UI element styles, like colors and stylesheets.

This file serves as the "design token" repository for the application. It should
only contain raw, static values (e.g., hex color codes). The construction of
actual QSS stylesheets from these tokens is handled by functions in `utils/styles.py`.
"""
from typing import Final
from .color import color

class UIStyleConstants:
    """Defines theme colors and other style constants for the UI."""

    # --- Theme Agnostic ---
    # Used when a value is the same in both light and dark mode.
    UI_ACCENT_FALLBACK: Final[str] = "#0078D4"
    BORDER_COLOR: Final[str] = "#505050"

    # --- Light Mode ---
    LIGHT_MODE_TEXT_COLOR: Final[str] = color.BLACK
    DIALOG_SIDEBAR_BG_LIGHT: Final[str] = "#f3f3f3"
    DIALOG_CONTENT_BG_LIGHT: Final[str] = "#ffffff"
    DIALOG_SECTION_BG_LIGHT: Final[str] = "#F0F0F0"
    GRAPH_BG_LIGHT: Final[str] = color.WHITE
    GRID_COLOR_LIGHT: Final[str] = '#CCCCCC'
    COMBOBOX_BG_LIGHT: Final[str] = "#f9f9f9"
    COMBOBOX_BORDER_LIGHT: Final[str] = "#cccccc"

    # --- Dark Mode ---
    DARK_MODE_TEXT_COLOR: Final[str] = color.WHITE
    DIALOG_SIDEBAR_BG_DARK: Final[str] = '#202020'
    DIALOG_CONTENT_BG_DARK: Final[str] = '#2d2d2d'
    DIALOG_SECTION_BG_DARK: Final[str] = '#202020'
    GRAPH_BG_DARK: Final[str] = "#1E1E1E"
    GRID_COLOR_DARK: Final[str] = '#444444'
    COMBOBOX_BG_DARK: Final[str] = "#3c3c3c"
    COMBOBOX_BORDER_DARK: Final[str] = "#555555"

    # --- Component Specific ---
    # Used for elements that have unique colors not tied to the main theme.
    SETTINGS_PANEL_TEXT_DARK: Final[str] = color.WHITE
    SETTINGS_PANEL_TEXT_LIGHT: Final[str] = "#1F1F1F"
    SUBTLE_TEXT_COLOR_LIGHT: Final[str] = "#595959"
    SUBTLE_TEXT_COLOR_DARK: Final[str] = "#808080"

    def __init__(self) -> None:
        """
        This class is intended for holding constants and should not be instantiated
        with instance-specific logic. The __init__ is kept minimal.
        """
        pass

# Singleton instance for easy access throughout the application
styles = UIStyleConstants()