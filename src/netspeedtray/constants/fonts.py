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

    # 7a (2.2 rc.2 "L"): the taskbar clock's face. "Segoe UI Variable Text" is the Text optical size of
    # the variable font Windows 11 ships (Qt lists the family as "Segoe UI Variable", with an opsz axis);
    # utils/widget_renderer.make_widget_font maps the name to that family + VARIABLE_OPSZ, and falls back
    # to FALLBACK_FONT where the variable font is missing (Windows 10).
    DEFAULT_FONT: Final[str] = 'Segoe UI Variable Text'
    FALLBACK_FONT: Final[str] = 'Segoe UI'
    LEGACY_DEFAULT_FONT: Final[str] = 'Segoe UI'     # the default up to config schema 1.3
    VARIABLE_FAMILY: Final[str] = 'Segoe UI Variable'
    # Named optical sizes of Segoe UI Variable -> its opsz axis value (Microsoft's named instances).
    VARIABLE_OPSZ: Final[Dict[str, float]] = {
        'Segoe UI Variable Small': 8.0, 'Segoe UI Variable Text': 10.5, 'Segoe UI Variable Display': 36.0,
    }
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