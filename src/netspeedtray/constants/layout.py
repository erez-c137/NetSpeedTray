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
    
    # ADDED missing constants
    SPACING: Final[int] = 8
    DEFAULT_PADDING: Final[int] = 4

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for attr_name in dir(self):
            if not attr_name.startswith('_') and attr_name.isupper():
                value = getattr(self, attr_name)
                if not isinstance(value, int) or value < 0:
                    raise ValueError(f"{attr_name} must be a non-negative integer.")

# Singleton instance for easy access
layout = LayoutConstants()