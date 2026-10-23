"""
Defines a common named color palette used throughout the application.
"""
from typing import Final

class ColorConstants:
    """Defines a static palette of named colors."""
    WHITE: Final[str] = "#FFFFFF"
    BLACK: Final[str] = "#000000"
    GREEN: Final[str] = "#00FF00"
    ORANGE: Final[str] = "#FFA500"
    BLUE: Final[str] = "#0000FF"
    RED: Final[str] = "#FF0000"
    
    # Contrast figures below: WCAG 2.x relative luminance against the four dark taskbars (#000000,
    # #1C1C1C, #202020, #2B2B2B) or the four light ones (#E8E8E8, #EEEEEE, #F3F3F3, #FFFFFF), worst-best.
    # The widget's text is ~9 pt, so text colours must clear the 4.5:1 body-text bar (not the 3:1
    # large-text one); graph lines are non-text graphics, 3:1. test_color_scheme computes all of it.

    # --- Speed bands (2.2 rc.2 legibility package "L", item 2) ---
    # Chosen by the UI/UX review so the three bands stay apart for everyone: pairwise CIEDE2000 >= 14 with
    # normal vision AND simulated deuteranopia / protanopia (Machado 2009, severity 1), every band >= 4.5:1
    # on #202020/#2B2B2B (dark) or #EEEEEE/#E8E8E8 (light), and bands separated by lightness as well as hue
    # (the old light trio all became one olive under deuteranopia; dark Medium and High merged for
    # red-green deficiency). test_color_scheme.test_band_palette_* pins every number.
    # Dark: Low keeps the orange; Medium a pale yellow (15.4:1); High a pale mint (12.1:1) - far enough
    # from the graph's download green that a coded number no longer melts into the line under it.
    AMBER: Final[str] = "#FFFF80"                # dark Medium (the name is historical: #FFE14D before L)
    HIGH_SPEED_DARK: Final[str] = "#8CF2C3"
    # Light: three deep tones apart in hue AND lightness: brick 11.7:1, brown 6.5:1, teal 7.7:1.
    HIGH_SPEED_LIGHT: Final[str] = "#145252"
    MEDIUM_SPEED_LIGHT: Final[str] = "#764B0A"
    LOW_SPEED_LIGHT: Final[str] = "#57190F"
    # The pre-L dark High default (shipped since 1.x). A config whose dark High is exactly this is on the
    # factory value, and the 1.4 migration moves it to HIGH_SPEED_DARK (utils/config._migrate_to_1_4).
    LEGACY_HIGH_SPEED_DARK: Final[str] = GREEN

    # Light-taskbar text (7a): Windows 11's own near-black (the clock's #1B1B1B), not pure black.
    TEXT_LIGHT: Final[str] = "#1B1B1B"
    # The custom up-arrow colour's light default keeps the pre-L dark green (custom arrow colours are
    # opt-in, #168; the band change is not theirs).
    ARROW_UP_LIGHT: Final[str] = "#007A00"

    # The widget mini graph's upload line (7d): a muted steel blue instead of the bright #4287F5, which
    # the reviews found the most distracting element of the split view. Still >= 3:1 (graphics) on
    # #202020 (4.4:1) and #EEEEEE (3.5:1). The Monitor window keeps UPLOAD_LINE_COLOR.
    WIDGET_UPLOAD_LINE_DARK: Final[str] = "#6F86A6"
    WIDGET_UPLOAD_LINE_LIGHT: Final[str] = "#6A7FA3"
    UPLOAD_LINE_LIGHT: Final[str] = WIDGET_UPLOAD_LINE_LIGHT
    DOWNLOAD_LINE_LIGHT: Final[str] = "#1A7F4E"  # 4.09-5.01:1 (#42B883 is 2.04:1)

    # The taskbar each Windows mode paints (Windows 11 solid taskbars), used to composite a partly
    # transparent widget background over it when choosing the dark / light half (utils/color_scheme).
    TASKBAR_REFERENCE_DARK: Final[str] = "#202020"
    TASKBAR_REFERENCE_LIGHT: Final[str] = "#EEEEEE"

    # The Settings preview strip's taskbar-like backdrop, per taskbar theme (dialog.py).
    PREVIEW_STRIP_DARK: Final[str] = "#2B2B2B"
    PREVIEW_STRIP_LIGHT: Final[str] = "#E8E8E8"

    # Graph Line Colors
    UPLOAD_LINE_COLOR: Final[str] = "#4287F5"   # Replaces SOFT_BLUE
    DOWNLOAD_LINE_COLOR: Final[str] = "#42B883" # Replaces SOFT_GREEN

    # App Usage / Progress Bar Colors
    APP_USAGE_PROGRESS_CHUNK: Final[str] = DOWNLOAD_LINE_COLOR
    APP_USAGE_PROGRESS_BG_DARK: Final[str] = "#333333"
    APP_USAGE_PROGRESS_BG_LIGHT: Final[str] = "#E0E0E0"
    
    # UI Text Colors
    SUBTLE_TEXT_COLOR_LIGHT: Final[str] = "#595959"
    SUBTLE_TEXT_COLOR_DARK: Final[str] = "#808080"

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for attr_name in dir(self):
            if not attr_name.startswith('_') and attr_name.isupper():
                value = getattr(self, attr_name)
                if not (isinstance(value, str) and value.startswith("#") and len(value) == 7):
                    raise ValueError(f"Color '{attr_name}' must be a 7-character hex string.")

# Singleton instance for easy access
color = ColorConstants()