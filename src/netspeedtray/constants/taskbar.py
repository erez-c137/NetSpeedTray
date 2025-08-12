"""
Constants for taskbar interaction and widget positioning.
"""
from enum import Enum
from typing import Final

class TaskbarEdge(Enum):
    """Enum for taskbar edge positions relative to the screen."""
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    UNKNOWN = "unknown"

class TaskbarConstants:
    """Constants for taskbar-related calculations."""
    DEFAULT_HEIGHT: Final[int] = 40
    MIN_VISIBLE_SIZE: Final[int] = 10
    PADDING: Final[int] = 4
    DPI_DEFAULT: Final[float] = 1.0

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.DEFAULT_HEIGHT <= 0: raise ValueError("DEFAULT_HEIGHT must be positive")
        if self.MIN_VISIBLE_SIZE <= 0: raise ValueError("MIN_VISIBLE_SIZE must be positive")
        if self.PADDING < 0: raise ValueError("PADDING must be non-negative")
        if self.DPI_DEFAULT <= 0: raise ValueError("DPI_DEFAULT must be positive")

class PositionConstants:
    """Constants for widget positioning calculations."""
    FALLBACK_PADDING: Final[int] = 32
    FALLBACK_TASKBAR_HEIGHT: Final[int] = TaskbarConstants.DEFAULT_HEIGHT
    SCREEN_EDGE_MARGIN: Final[int] = 5
    DRAG_LOG_INTERVAL_SECONDS: Final[float] = 1.0

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.FALLBACK_PADDING < 0: raise ValueError("FALLBACK_PADDING must be non-negative")
        if self.FALLBACK_TASKBAR_HEIGHT <= 0: raise ValueError("FALLBACK_TASKBAR_HEIGHT must be positive")
        if self.SCREEN_EDGE_MARGIN < 0: raise ValueError("SCREEN_EDGE_MARGIN must be non-negative")
        if self.DRAG_LOG_INTERVAL_SECONDS <= 0: raise ValueError("DRAG_LOG_INTERVAL_SECONDS must be positive")

class TaskbarAndPositionConstants:
    """Container for taskbar and positioning constant groups."""
    def __init__(self) -> None:
        self.taskbar = TaskbarConstants()
        self.position = PositionConstants()
        self.edge = TaskbarEdge # Expose the Enum directly

# Singleton instance for easy access
taskbar = TaskbarAndPositionConstants()