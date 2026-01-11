"""
Constants defining default margins and spacing for UI layouts.
"""
from typing import Final

class LayoutConstants:
    """Defines default margins and spacing for Qt layouts."""
    # --- General Purpose Spacing ---
    HORIZONTAL_SPACING_SMALL: Final[int] = 5
    HORIZONTAL_SPACING_MEDIUM: Final[int] = 8
    VERTICAL_SPACING: Final[int] = 5
    SPACING: Final[int] = 8
    DEFAULT_PADDING: Final[int] = 4

    # --- General Dialog Layout ---
    MAIN_MARGIN: Final[int] = 16
    MAIN_SPACING: Final[int] = 12
    GROUP_BOX_SPACING: Final[int] = 12
    SIDEBAR_WIDTH: Final[int] = 200

    # --- Interface Page Specific ---
    INTERFACE_SCROLL_MAX_ITEMS: Final[int] = 7
    INTERFACE_SCROLL_MIN_HEIGHT: Final[int] = 80
    
    # --- Main Widget Specific ---
    WIDGET_DEFAULT_RIGHT_PADDING_PX: Final[int] = 10
    SMALL_TASKBAR_HEIGHT_THRESHOLD: Final[int] = 34
    HORIZONTAL_LAYOUT_SEPARATOR: Final[str] = " | "
    MINI_GRAPH_HORIZONTAL_WIDTH: Final[int] = 40

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate that all layout constants are valid non-negative numbers or strings."""
        for attr_name in dir(self):
            if not attr_name.startswith('_') and attr_name.isupper():
                value = getattr(self, attr_name)
                if isinstance(value, int) and value < 0:
                    raise ValueError(f"{attr_name} must be a non-negative integer.")
                elif not isinstance(value, (int, str)):
                    raise ValueError(f"{attr_name} must be an integer or a string.")

# Singleton instance for easy access
layout = LayoutConstants()