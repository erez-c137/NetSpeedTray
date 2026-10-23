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
    # large-text one); graph lines are non-text graphics, 3:1. test_color_scheme computes all of it,
    # plus the CIEDE2000 distance that keeps Medium apart from Low and High (>= 15).

    # The Medium speed band's dark-taskbar default (#9): a light amber, 10.9-16.1:1. CIEDE2000 19.6 from
    # the Low orange #FFA500 and 29.0 from the High green (the first pick, #FFBF00, was only 9.3 from the orange).
    AMBER: Final[str] = "#FFE14D"

    # Light-taskbar defaults of the widget's themed colours (the colour scheme, #332). The dark-taskbar
    # defaults are the colours above, unchanged since 2.0 - on a light taskbar most of them could not be
    # read (green #00FF00 is 1.2:1 on #EEEEEE, the white down arrow 1.2:1). Each one below keeps its dark
    # twin's hue family.
    HIGH_SPEED_LIGHT: Final[str] = "#007A00"     # 4.53-5.55:1
    MEDIUM_SPEED_LIGHT: Final[str] = "#7A6500"   # dark amber, 4.65-5.69:1; dE 24.0 from Low, 25.0 from High
    LOW_SPEED_LIGHT: Final[str] = "#A84600"      # dark orange, 4.84-5.93:1
    UPLOAD_LINE_LIGHT: Final[str] = "#2F6FDB"    # 3.88-4.75:1 (#4287F5 is 2.85:1 on #E8E8E8)
    DOWNLOAD_LINE_LIGHT: Final[str] = "#1A7F4E"  # 4.09-5.01:1 (#42B883 is 2.04:1)

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