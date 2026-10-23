"""
Constants specific to the network speed history graph window.
"""
from typing import Dict, Final, Tuple
from netspeedtray.constants.app import app
from netspeedtray.constants.color import color

class GraphConstants:
    """Defines constants for the history graph window."""
    # --- Sizing and Layout ---
    FIGURE_SIZE: Final[Tuple[float, float]] = (8, 6)
    GRAPH_WIDGET_WIDTH: Final[int] = 802
    GRAPH_WIDGET_HEIGHT: Final[int] = 602
    HAMBURGER_ICON_SIZE: Final[int] = 24
    HAMBURGER_ICON_OFFSET_X: Final[int] = 20
    
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
    CPU_LINE_COLOR: Final[str] = "#00BCD4" # Cyan
    GPU_LINE_COLOR: Final[str] = "#FF9800" # Orange
    LINE_WIDTH: Final[float] = 1.5
    GRID_ALPHA: Final[float] = 0.5
    GRID_LINESTYLE: Final[str] = ":"
    ERROR_MESSAGE_FONTSIZE: Final[int] = 12
    # Value (y) axis labels show as many decimals as the tick step needs (0.5 -> 1, 0.02 -> 2), up to
    # this many, with trailing zeros dropped ("1.5", "2", never "1.50" or "1" for 1.5).
    VALUE_AXIS_MAX_DECIMALS: Final[int] = 3

    # --- Percentile-clipped speed axis on long windows (views/graph/value_axis.py) ---
    # A month of ordinary traffic plus two speed tests used to scale to the speed tests, squashing the
    # month into ~3% of the height. On these windows the speed axis scales to the 99th percentile of
    # the plotted points instead, but only when the max exceeds it by TRIGGER x - otherwise the max
    # still sets the axis. Short and live windows always scale to the max. % axes (0-100) never clip.
    VALUE_AXIS_CLIP_PERIODS: Final[Tuple[str, ...]] = ("TIMELINE_WEEK", "TIMELINE_MONTH", "TIMELINE_ALL")
    VALUE_AXIS_CLIP_PERCENTILE: Final[float] = 99.0
    VALUE_AXIS_CLIP_TRIGGER: Final[float] = 3.0     # clip only if max > percentile x this
    VALUE_AXIS_CLIP_HEADROOM: Final[float] = 1.25   # axis scales to percentile x this (then nice-rounds up)
    VALUE_AXIS_CLIP_MIN_POINTS: Final[int] = 100    # below this a 99th percentile is just the max
    # Points above the clipped top get one caret per run on the top edge, and the axis states the peak.
    VALUE_AXIS_CLIP_MARKER: Final[str] = "^"
    VALUE_AXIS_CLIP_MARKER_SIZE: Final[float] = 22.0   # scatter area, points^2
    VALUE_AXIS_CLIP_PEAK_FONTSIZE: Final[float] = 8.0
    VALUE_AXIS_CLIP_PEAK_ALPHA: Final[float] = 0.75

    # --- Speed axis unit (rc.2 S2) ---
    # The network axes are plotted in Mbps internally (sticky limits, the 0.1 floor and the clip all
    # work in Mbps); the TICKS and the axis label follow the user's unit_type with one unit for the
    # whole axis, chosen from its top like the widget picks a unit from a value. Never the base unit
    # (bps / B/s): the 0.1 Mbps floor is already 100 Kbps. tier: 1 kilo, 2 mega, 3 giga.
    VALUE_AXIS_MIN_UNIT_TIER: Final[int] = 1
    VALUE_AXIS_LOCATOR_BINS: Final[int] = 5
    VALUE_AXIS_LOCATOR_STEPS: Final[Tuple[float, ...]] = (1, 2, 2.5, 5, 10)

    # --- What the network line is, per stored resolution (rc.2 S3) ---
    # Past 6 h the graph reads the roll-up tiers, and each point is the PEAK of its bucket, while the
    # header shows the window's volume. A caption names the statistic so the two do not read as a
    # contradiction. Raw resolution (<= 6 h, and the live Session) is the measured per-second rate:
    # no caption. Keys are i18n string names.
    NET_GRAPH_STAT_CAPTION_KEYS: Final[Dict[str, str]] = {
        "minute": "GRAPH_CAPTION_PEAK_PER_MINUTE",
        "hour": "GRAPH_CAPTION_PEAK_PER_HOUR",
        "day": "GRAPH_CAPTION_PEAK_PER_DAY",
    }
    NET_GRAPH_CAPTION_FONTSIZE: Final[float] = 8.0
    NET_GRAPH_CAPTION_ALPHA: Final[float] = 0.75

    # --- Unmeasured time (rc.2 S4) ---
    # Seconds one plotted point stands for at each resolution the history reader bins to. The renderer
    # fills MEASURED silence (the app ran, nothing was stored: v8 writes no all-zero rows) with zeros
    # one bin away from the neighbouring points, and breaks the line across UNMEASURED time (app off,
    # PC asleep, before the first recorded session) instead of drawing it as zero.
    RESOLUTION_BIN_SECONDS: Final[Dict[str, int]] = {"raw": 1, "minute": 60, "hour": 3600, "day": 86400}

    # --- Time (x) axis ticks (views/graph/time_axis.py) ---
    # The tick count follows the axis width: one label per this many points, clamped to [MIN, MAX].
    TIME_AXIS_LABEL_SPACING_PT: Final[float] = 60.0
    TIME_AXIS_MIN_TICKS: Final[int] = 4
    TIME_AXIS_MAX_TICKS: Final[int] = 8
    # Candidate tick steps, finest first: (unit, count). Every second/minute/hour/month count divides
    # its parent unit evenly, so ticks land on round clock times ("18:15", never "18:13:20").
    TIME_AXIS_STEPS: Final[Tuple[Tuple[str, int], ...]] = (
        ("second", 1), ("second", 2), ("second", 5), ("second", 10), ("second", 15), ("second", 30),
        ("minute", 1), ("minute", 2), ("minute", 5), ("minute", 10), ("minute", 15), ("minute", 30),
        ("hour", 1), ("hour", 2), ("hour", 3), ("hour", 4), ("hour", 6), ("hour", 12),
        ("day", 1), ("day", 2), ("day", 3), ("day", 4), ("week", 1), ("week", 2),
        ("month", 1), ("month", 2), ("month", 3), ("month", 6),
        ("year", 1), ("year", 2), ("year", 5), ("year", 10), ("year", 25), ("year", 50),
    )
    # Label formats, chosen from the tick step (and the span) so two ticks never print the same text.
    TIME_AXIS_FMT_SECONDS: Final[str] = "%H:%M:%S"     # sub-minute steps
    TIME_AXIS_FMT_MINUTES: Final[str] = "%H:%M"        # minute/hour steps within one day
    TIME_AXIS_FMT_HOURS_MULTIDAY: Final[str] = "%a %H:%M"  # hour steps on a span of a day or more
    TIME_AXIS_FMT_DAYS_SHORT: Final[str] = "%a %d"     # day/week steps on a span under four weeks
    TIME_AXIS_FMT_DAYS: Final[str] = "%b %d"           # day/week steps within a year
    TIME_AXIS_FMT_DAYS_LONG: Final[str] = "%Y-%m-%d"   # day/week steps on a span over a year
    TIME_AXIS_FMT_MONTHS: Final[str] = "%b %Y"
    TIME_AXIS_FMT_YEARS: Final[str] = "%Y"

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