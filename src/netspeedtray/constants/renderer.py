"""
Constants influencing the rendering of the network speed widget.
"""
from typing import Final

class RendererConstants:
    """Defines constants for rendering the widget display."""
    MIN_GRAPH_POINTS: Final[int] = 2
    MIN_SPEED_FOR_COLOR: Final[float] = 0.01
    LINE_WIDTH: Final[int] = 1
    TEXT_MARGIN: Final[int] = 2
    GRAPH_MARGIN: Final[int] = 1
    GRAPH_HEIGHT_PERCENTAGE: Final[float] = 0.8
    GRAPH_LEFT_PADDING: Final[int] = 2
    GRAPH_RIGHT_PADDING: Final[int] = 2
    GRAPH_BOTTOM_PADDING: Final[int] = 1    
    DEFAULT_ARROW_WIDTH: Final[int] = 5
    VALUE_UNIT_GAP: Final[int] = 5
    ARROW_NUMBER_GAP: Final[int] = 5
    MIN_Y_SCALE: Final[int] = 62500  # 500 Kbps in bytes/sec
    MIN_SPEED_THRESHOLD: Final[int] = 10000
    GRAPH_Y_AXIS_PADDING_FACTOR: Final[float] = 1.15 # Add 15% headroom

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.MIN_GRAPH_POINTS < 2:
            raise ValueError("MIN_GRAPH_POINTS must be at least 2")
        if not (0.0 < self.GRAPH_HEIGHT_PERCENTAGE <= 1.0):
            raise ValueError("GRAPH_HEIGHT_PERCENTAGE must be between 0 and 1")
        if self.GRAPH_Y_AXIS_PADDING_FACTOR < 1.0:
            raise ValueError("GRAPH_Y_AXIS_PADDING_FACTOR must be >= 1.0")

# Singleton instance for easy access
renderer = RendererConstants()