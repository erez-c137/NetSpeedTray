"""
Constants defining specific UI element styles, like colors and stylesheets.

This file serves as the "design token" repository for the application. It should
only contain raw, static values (e.g., hex color codes). The construction of
actual QSS stylesheets from these tokens is handled by functions in `utils/styles.py`.
"""
from typing import Final
from netspeedtray.constants.color import color

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
    GRID_COLOR_LIGHT: Final[str] = '#B0B0B0'  # Darkened for better visibility on white
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

    # --- Design system: Fluent type ramp ------------------------------------
    # The Segoe UI Variable optical axis only engages when the family name selects
    # the cut ("...Display"/"Text"/"Small"); a generic "Segoe UI Variable" falls
    # back to flat Text metrics. utils/styles.font() builds a QFont per tier from
    # these (family, pixel_size, weight) tuples and falls back to plain Segoe UI
    # when the optical cut isn't installed (pre-Win11).
    FONT_FAMILY_DISPLAY: Final[str] = "Segoe UI Variable Display"
    FONT_FAMILY_TEXT: Final[str] = "Segoe UI Variable Text"
    FONT_FAMILY_SMALL: Final[str] = "Segoe UI Variable Small"
    FONT_FALLBACK: Final[str] = "Segoe UI"

    TYPE_CAPTION: Final[tuple] = (FONT_FAMILY_SMALL, 12, 400)      # hints, notes
    TYPE_BODY: Final[tuple] = (FONT_FAMILY_TEXT, 14, 400)         # control text
    TYPE_BODY_STRONG: Final[tuple] = (FONT_FAMILY_TEXT, 14, 600)  # card titles
    TYPE_SUBTITLE: Final[tuple] = (FONT_FAMILY_DISPLAY, 16, 600)  # section headers
    TYPE_TITLE: Final[tuple] = (FONT_FAMILY_DISPLAY, 20, 600)     # page / preview title

    # --- Design system: semantic surface tokens (Fluent cards) --------------
    CARD_BG_LIGHT: Final[str] = "#FBFBFB"
    CARD_BG_DARK: Final[str] = "#2D2D2D"
    CARD_STROKE_LIGHT: Final[str] = "#E5E5E5"
    CARD_STROKE_DARK: Final[str] = "#3A3A3A"
    SUBTLE_FILL_LIGHT: Final[str] = "rgba(0, 0, 0, 0.04)"   # hover overlay
    SUBTLE_FILL_DARK: Final[str] = "rgba(255, 255, 255, 0.06)"

    # --- Design system: spacing scale (4px grid), radii, layout -------------
    SPACE_XS: Final[int] = 4
    SPACE_S: Final[int] = 8
    SPACE_M: Final[int] = 12
    SPACE_L: Final[int] = 20
    SPACE_XL: Final[int] = 30
    RADIUS_CARD: Final[int] = 8
    RADIUS_CONTROL: Final[int] = 4
    CONTENT_MAX_WIDTH: Final[int] = 640

    def __init__(self) -> None:
        """
        This class is intended for holding constants and should not be instantiated
        with instance-specific logic. The __init__ is kept minimal.
        """
        pass

# Singleton instance for easy access throughout the application
styles = UIStyleConstants()