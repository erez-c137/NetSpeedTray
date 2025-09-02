"""
Constants specific to the network speed history graph window.
"""
from typing import Final, Tuple
from .app import app
from .color import color

class GraphConstants:
    """Defines constants for the history graph window."""
    FIGURE_SIZE: Final[Tuple[float, float]] = (8, 6)
    GRAPH_WIDGET_WIDTH: Final[int] = 802
    GRAPH_WIDGET_HEIGHT: Final[int] = 602
    MIN_Y_AXIS_LIMIT: Final[float] = 1.0
    HAMBURGER_ICON_SIZE: Final[int] = 24
    HAMBURGER_ICON_OFFSET_X: Final[int] = 5
    HAMBURGER_ICON_OFFSET_Y: Final[int] = 5
    STATS_BAR_OFFSET_X: Final[int] = HAMBURGER_ICON_OFFSET_X
    STATS_BAR_OFFSET_Y: Final[int] = HAMBURGER_ICON_OFFSET_Y + HAMBURGER_ICON_SIZE + 5
    SETTINGS_WIDGET_WIDTH: Final[int] = 250
    SETTINGS_PANEL_Y_OFFSET: Final[int] = 10 # Vertical distance from the top edge
    REALTIME_UPDATE_INTERVAL_MS: Final[int] = 1000
    GRAPH_UPDATE_THROTTLE_MS: Final[int] = 200
    MAX_DATA_POINTS: Final[int] = 500
    UPLOAD_LINE_COLOR: Final[str] = color.SOFT_BLUE
    DOWNLOAD_LINE_COLOR: Final[str] = color.SOFT_GREEN
    LINE_WIDTH: Final[float] = 1.5
    GRID_ALPHA: Final[float] = 0.5
    GRID_LINESTYLE: Final[str] = ":"
    TICK_MIN: Final[int] = 4
    TICK_MAX: Final[int] = 10
    LABEL_FONTSIZE: Final[int] = 8
    TITLE_FONTSIZE: Final[int] = 10
    ERROR_MESSAGE_COLOR: Final[str] = color.RED
    ERROR_MESSAGE_FONTSIZE: Final[int] = 12
    STATS_UPDATE_INTERVAL: Final[float] = 1.0
    DATE_FORMAT_HOUR: Final[str] = "%H:%M"
    DATE_FORMAT_MULTIDAY: Final[str] = "%d-%m-%Y"
    DATE_FORMAT_SWITCH_HOURS_SECONDS: Final[int] = 2
    DATE_FORMAT_SWITCH_DAYS_MINUTES: Final[int] = 1
    DATE_FORMAT_SWITCH_WEEKS_DAYS: Final[int] = 7
    DATE_FORMAT_SECONDS: Final[str] = "%H:%M:%S"
    DATE_FORMAT_DAY_ABBREV_TIME: Final[str] = "%a %H:%M"
    MIN_TIME_RANGE_SECONDS: Final[int] = 300
    X_AXIS_LABEL_ROTATION: Final[int] = 45
    X_AXIS_TICKS: Final[int] = 6
    WINDOW_TITLE: Final[str] = f"{app.APP_NAME} - Network Speed Graph"

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.WINDOW_TITLE:
            raise ValueError("WINDOW_TITLE must not be empty")

# Singleton instance for easy access
graph = GraphConstants()