"""
Constants defining default margins and spacing for UI layouts.
"""
from typing import Final

class LayoutConstants:
    """Defines default margins and spacing for Qt layouts."""
    HORIZONTAL_SPACING_SMALL: Final[int] = 5
    HORIZONTAL_SPACING_MEDIUM: Final[int] = 8
    VERTICAL_SPACING: Final[int] = 5
    MARGIN: Final[int] = 10
    WIDGET_DEFAULT_RIGHT_PADDING_PX: Final[int] = 10
    
    SPACING: Final[int] = 8
    DEFAULT_PADDING: Final[int] = 4

    # Constants for Adaptive Layout
    # A standard taskbar is ~40px high. Small is ~32px. This is a safe threshold.
    SMALL_TASKBAR_HEIGHT_THRESHOLD: Final[int] = 34
    # Padding between the upload and download sections in horizontal mode.
    HORIZONTAL_LAYOUT_SEPARATOR: Final[str] = " | "
    MINI_GRAPH_HORIZONTAL_WIDTH: Final[int] = 40

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for attr_name in dir(self):
            if not attr_name.startswith('_') and attr_name.isupper():
                value = getattr(self, attr_name)
                if not isinstance(value, (int, str)) or value < 0 if isinstance(value, int) else False:
                    raise ValueError(f"{attr_name} must be a non-negative integer or a string.")

# Singleton instance for easy access
layout = LayoutConstants()