"""
Constants related to font selection, size, and weight used in the application.
"""
from typing import Final, Dict, List

class FontConstants:
    """Defines font weights, sizes, and fallback options."""
    FONT_SIZE_MIN: Final[int] = 7
    FONT_SIZE_MAX: Final[int] = 11
    
    WEIGHT_THIN: Final[int] = 100
    WEIGHT_EXTRALIGHT: Final[int] = 200
    WEIGHT_LIGHT: Final[int] = 300
    WEIGHT_NORMAL: Final[int] = 400
    WEIGHT_MEDIUM: Final[int] = 500
    WEIGHT_DEMIBOLD: Final[int] = 600
    WEIGHT_BOLD: Final[int] = 700
    WEIGHT_EXTRABOLD: Final[int] = 800
    WEIGHT_BLACK: Final[int] = 900
    
    FALLBACK_WEIGHTS: Final[List[int]] = [
        WEIGHT_LIGHT, WEIGHT_NORMAL, WEIGHT_DEMIBOLD, WEIGHT_BOLD
    ]

    DEFAULT_FONT: Final[str] = 'Segoe UI'
    NOTE_FONT_SIZE: Final[int] = 8
    FONT_WEIGHT_MIN_STANDARD: Final[int] = WEIGHT_LIGHT
    FONT_WEIGHT_MAX_STANDARD: Final[int] = WEIGHT_BLACK
    FONT_WEIGHT_STEP_STANDARD: Final[int] = 100
    FONT_WEIGHT_SLIDER_MIN_WIDTH: Final[int] = 120
    FONT_WEIGHT_LABEL_PADDING: Final[int] = 20

    # This map is now internal and points to the i18n keys.
    WEIGHT_MAP: Final[Dict[int, str]] = {
        WEIGHT_THIN: "FONT_WEIGHT_THIN", WEIGHT_EXTRALIGHT: "FONT_WEIGHT_EXTRALIGHT",
        WEIGHT_LIGHT: "FONT_WEIGHT_LIGHT", WEIGHT_NORMAL: "FONT_WEIGHT_NORMAL",
        WEIGHT_MEDIUM: "FONT_WEIGHT_MEDIUM", WEIGHT_DEMIBOLD: "FONT_WEIGHT_DEMIBOLD",
        WEIGHT_BOLD: "FONT_WEIGHT_BOLD", WEIGHT_EXTRABOLD: "FONT_WEIGHT_EXTRABOLD",
        WEIGHT_BLACK: "FONT_WEIGHT_BLACK"
    }
    
    # This map can be removed as WEIGHT_MAP now serves its purpose.
    # Keeping it for now if other code relies on it, but it's now redundant.
    WEIGHT_NUM_TO_KEY: Final[Dict[int, str]] = WEIGHT_MAP

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.FONT_SIZE_MAX < self.FONT_SIZE_MIN:
            raise ValueError("FONT_SIZE_MAX must be >= FONT_SIZE_MIN")
        if set(self.WEIGHT_MAP.keys()) != set(self.WEIGHT_NUM_TO_KEY.keys()):
             raise ValueError("Keys in WEIGHT_MAP and WEIGHT_NUM_TO_KEY must match")

# Singleton instance for easy access
fonts = FontConstants()