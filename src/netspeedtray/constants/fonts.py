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

    # This map points numeric font weights to their corresponding i18n translation keys.
    WEIGHT_MAP: Final[Dict[int, str]] = {
        WEIGHT_THIN: "FONT_WEIGHT_THIN", WEIGHT_EXTRALIGHT: "FONT_WEIGHT_EXTRALIGHT",
        WEIGHT_LIGHT: "FONT_WEIGHT_LIGHT", WEIGHT_NORMAL: "FONT_WEIGHT_NORMAL",
        WEIGHT_MEDIUM: "FONT_WEIGHT_MEDIUM", WEIGHT_DEMIBOLD: "FONT_WEIGHT_DEMIBOLD",
        WEIGHT_BOLD: "FONT_WEIGHT_BOLD", WEIGHT_EXTRABOLD: "FONT_WEIGHT_EXTRABOLD",
        WEIGHT_BLACK: "FONT_WEIGHT_BLACK"
    }
    
    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.FONT_SIZE_MAX < self.FONT_SIZE_MIN:
            raise ValueError("FONT_SIZE_MAX must be >= FONT_SIZE_MIN")

# Singleton instance for easy access
fonts = FontConstants()