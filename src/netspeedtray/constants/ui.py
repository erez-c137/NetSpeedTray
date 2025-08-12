"""
General and component-specific UI constants.
"""
from typing import Final, Tuple

class UIConstants:
    """General UI constants for elements like menus."""
    MENU_PADDING_ABOVE: Final[int] = 5
    MENU_PADDING_BELOW: Final[int] = 5
    ESTIMATED_MENU_WIDTH: Final[int] = 150

class ComponentVisualConstants:
    """Constants defining visual dimensions for custom UI components."""
    TOGGLE_TRACK_WIDTH: Final[int] = 40
    TOGGLE_TRACK_HEIGHT: Final[int] = 20
    TOGGLE_THUMB_DIAMETER: Final[int] = 14

class DialogConstants:
    """Constants for dialog sizing and behavior."""
    MINIMUM_DIALOG_WIDTH: Final[int] = 300
    MINIMUM_DIALOG_HEIGHT: Final[int] = 200
    MAXIMUM_DIALOG_WIDTH: Final[int] = 350
    GAP_ABOVE_WIDGET: Final[int] = 40
    THROTTLE_INTERVAL_MS: Final[int] = 250
    COLOR_BUTTON_WIDTH: Final[int] = 40
    COLOR_BUTTON_HEIGHT: Final[int] = 18

class SliderConstants:
    """Constants for configuring QSlider widgets."""
    SPEED_THRESHOLD_MIN_HIGH: Final[int] = 1 * 10
    SPEED_THRESHOLD_MAX_HIGH: Final[int] = 10000 * 10
    SPEED_THRESHOLD_MIN_LOW: Final[int] = 0 * 10
    SPEED_THRESHOLD_MAX_LOW: Final[int] = 9999 * 10
    OPACITY_MIN: Final[int] = 10
    OPACITY_MAX: Final[int] = 100
    SLIDER_STYLE: str = "" # Style is now handled by theme, but keep attr

class HistoryConstants:
    """Constants for managing historical data ranges."""
    HISTORY_MINUTES_RANGE: Final[Tuple[int, int]] = (1, 120)

class InterfaceGroupConstants:
    """Constants for the Network Interfaces group in settings."""
    MAX_VISIBLE_INTERFACES: Final[int] = 10
    SCROLL_MIN_HEIGHT: Final[int] = 80
    SCROLL_MAX_HEIGHT_EMPTY: Final[int] = 60

class UIGroup:
    """Container for various UI-related constant groups."""
    def __init__(self) -> None:
        self.general = UIConstants()
        self.visuals = ComponentVisualConstants()
        self.dialogs = DialogConstants()
        self.sliders = SliderConstants()
        self.history = HistoryConstants()
        self.interfaces = InterfaceGroupConstants()

# Singleton instance for easy access
ui = UIGroup()