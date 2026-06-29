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
    # Light-mode counterpart: a soft grey base so the near-white #FBFBFB cards float (was #ffffff, which
    # made the cards read as slightly darker insets on white instead of floating panels).
    DIALOG_CONTENT_BG_LIGHT: Final[str] = "#f3f3f3"
    DIALOG_SECTION_BG_LIGHT: Final[str] = "#F0F0F0"
    GRAPH_BG_LIGHT: Final[str] = color.WHITE
    GRID_COLOR_LIGHT: Final[str] = '#B0B0B0'  # Darkened for better visibility on white
    COMBOBOX_BG_LIGHT: Final[str] = "#f9f9f9"
    COMBOBOX_BORDER_LIGHT: Final[str] = "#cccccc"

    # --- Dark Mode ---
    DARK_MODE_TEXT_COLOR: Final[str] = color.WHITE
    DIALOG_SIDEBAR_BG_DARK: Final[str] = '#202020'
    # The content area sits at the window base so the lighter #2D2D2D cards FLOAT on it (Win11 cards-on-
    # Mica). Was #2d2d2d — identical to the card fill — so cards had no tonal pop, only their stroke.
    DIALOG_CONTENT_BG_DARK: Final[str] = '#202020'
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
    # Lifted from #808080 (≈3.48:1 on the dark card — fails WCAG AA for the 12/14px caption text used
    # app-wide) to #A6A6A6 (≈5.7:1), per the 2.0 a11y audit. Still clearly "subtle" vs primary text.
    SUBTLE_TEXT_COLOR_DARK: Final[str] = "#A6A6A6"

    # --- Design system: Fluent type ramp ------------------------------------
    # "Segoe UI Variable" is ONE family; the optical cuts (Small/Text/Display) and
    # weights are STYLES of it, selected via QFont.setStyleName — NOT family names.
    # (QFont("Segoe UI Variable Text") does not match and silently downgrades to a
    # fallback face.) utils/styles.font() applies the style name on Win11 and falls
    # back to plain Segoe UI at the px/weight on Win10.
    FONT_FAMILY_VARIABLE: Final[str] = "Segoe UI Variable"
    FONT_FALLBACK: Final[str] = "Segoe UI"

    # (style_name, pixel_size, weight)
    TYPE_CAPTION: Final[tuple] = ("Small", 12, 400)               # hints, notes
    TYPE_BODY: Final[tuple] = ("Text", 14, 400)                   # control text
    TYPE_BODY_STRONG: Final[tuple] = ("Text Semibold", 14, 600)   # card titles
    TYPE_SUBTITLE: Final[tuple] = ("Display Semibold", 16, 600)   # section headers
    TYPE_TITLE: Final[tuple] = ("Display Semibold", 20, 600)      # page / preview title

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