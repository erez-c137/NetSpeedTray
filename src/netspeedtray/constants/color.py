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