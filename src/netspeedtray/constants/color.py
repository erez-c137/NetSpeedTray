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
    
    # The Medium speed band's dark-taskbar default (#9): amber, between the High green and the Low
    # orange. 9.9:1 on the #202020 Windows 11 dark taskbar (8.6:1 on #2B2B2B, the lightest dark one).
    AMBER: Final[str] = "#FFBF00"

    # Light-taskbar defaults of the widget's themed colours (the colour scheme, #332). The dark-taskbar
    # defaults are the colours above, unchanged since 2.0 - on a light taskbar most of them could not be
    # read (green #00FF00 is 1.2:1 on #EEEEEE, the white down arrow 1.2:1). Each one below keeps its dark
    # twin's hue and is at least 3:1 (WCAG non-text / large-text) against every light taskbar measured -
    # #E8E8E8, #EEEEEE, #F3F3F3 and #FFFFFF; the text ones reach 4.1:1 or more. WCAG 2.x relative
    # luminance, checked by test_color_scheme.test_light_defaults_read_on_a_light_taskbar.
    HIGH_SPEED_LIGHT: Final[str] = "#007A00"     # 4.5-5.6:1
    MEDIUM_SPEED_LIGHT: Final[str] = "#8C6A00"   # dark amber, 4.1-5.0:1
    LOW_SPEED_LIGHT: Final[str] = "#B04A00"      # dark orange, 4.5-5.5:1
    UPLOAD_LINE_LIGHT: Final[str] = "#2F6FDB"    # 3.9-4.8:1 (#4287F5 is 2.9:1 on #E8E8E8)
    DOWNLOAD_LINE_LIGHT: Final[str] = "#1A7F4E"  # 4.1-5.0:1 (#42B883 is 2.0:1)

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