"""
Constants specific to the network speed history graph window.
"""
from typing import Final, Tuple
from .app import app
from .color import color

class GraphConstants:
    """Defines constants for the history graph window."""
    # --- Sizing and Layout ---
    FIGURE_SIZE: Final[Tuple[float, float]] = (8, 6)
    GRAPH_WIDGET_WIDTH: Final[int] = 802
    GRAPH_WIDGET_HEIGHT: Final[int] = 602
    HAMBURGER_ICON_SIZE: Final[int] = 24
    HAMBURGER_ICON_OFFSET_X: Final[int] = 5
    
    # --- Timers and Performance ---
    REALTIME_UPDATE_INTERVAL_MS: Final[int] = 1000
    GRAPH_UPDATE_THROTTLE_MS: Final[int] = 200
    MAX_DATA_POINTS: Final[int] = 500
    STATS_UPDATE_INTERVAL: Final[float] = 1.0

    # --- Plotting and Theming ---
    MINIMUM_Y_AXIS_MBPS: Final[float] = 0.1 # Represents 100 Kbps
    # Use the master color palette as the single source of truth
    UPLOAD_LINE_COLOR: Final[str] = color.UPLOAD_LINE_COLOR
    DOWNLOAD_LINE_COLOR: Final[str] = color.DOWNLOAD_LINE_COLOR
    LINE_WIDTH: Final[float] = 1.5
    GRID_ALPHA: Final[float] = 0.5
    GRID_LINESTYLE: Final[str] = ":"
    ERROR_MESSAGE_FONTSIZE: Final[int] = 12

    # --- Text and Labels ---
    WINDOW_TITLE: Final[str] = f"{app.APP_NAME} - Network Speed Graph"
    INITIAL_STATS_TEXT: Final[str] = "..." # Placeholder text for stats bar

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.WINDOW_TITLE:
            raise ValueError("WINDOW_TITLE must not be empty")

# Singleton instance for easy access
graph = GraphConstants()