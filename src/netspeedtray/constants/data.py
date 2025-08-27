"""
Constants related to historical data management and representation.
"""

from typing import Final, Dict, List, Optional

class LegendPositionConstants:
    """Constants defining available positions for the graph legend."""
    # Use i18n keys, not display strings
    OFF: Final[str] = "LEGEND_POSITION_OFF"
    LEFT: Final[str] = "LEGEND_POSITION_LEFT"
    CENTER: Final[str] = "LEGEND_POSITION_CENTER"
    RIGHT: Final[str] = "LEGEND_POSITION_RIGHT"

    LEGEND_LOC_MAP: Final[Dict[str, Optional[str]]] = {
        OFF: None,
        LEFT: "upper left",
        CENTER: "upper center",
        RIGHT: "upper right",
    }
    # This list now contains the keys for your UI code to use
    UI_OPTIONS: Final[List[str]] = [OFF, LEFT, CENTER, RIGHT]
    DEFAULT_LEGEND_POSITION: Final[str] = OFF

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.LEGEND_LOC_MAP:
            raise ValueError("LEGEND_LOC_MAP must not be empty")
        if set(self.UI_OPTIONS) != set(self.LEGEND_LOC_MAP.keys()):
            raise ValueError("UI_OPTIONS must exactly match LEGEND_LOC_MAP keys")
        if self.DEFAULT_LEGEND_POSITION not in self.UI_OPTIONS:
            raise ValueError(f"DEFAULT_LEGEND_POSITION '{self.DEFAULT_LEGEND_POSITION}' must be one of {self.UI_OPTIONS}")

class DataRetentionConstants:
    """Constants for managing the retention period of stored historical data."""
    MAX_RETENTION_DAYS: Final[int] = 365  # 1 year
    DAYS_MAP: Final[Dict[int, int]] = {
        0: 1, 1: 7, 2: 14, 3: 30, 4: 90, 5: 180, 6: 365,
    }

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.DAYS_MAP, dict) or not self.DAYS_MAP:
            raise ValueError("DAYS_MAP must be a non-empty dictionary")
        if sorted(self.DAYS_MAP.keys()) != list(range(len(self.DAYS_MAP))):
            raise ValueError("DAYS_MAP keys must be sequential integers starting from 0")
        for days_value in self.DAYS_MAP.values():
            if not isinstance(days_value, int) or days_value <= 0 or days_value > self.MAX_RETENTION_DAYS:
                raise ValueError(f"Invalid DAYS_MAP value: {days_value}")

class HistoryPeriodConstants:
    """Constants for history periods in graphs."""
    # Use i18n keys, not display strings
    PERIOD_MAP: Final[Dict[int, str]] = {
        0: "TIMELINE_SYSTEM_UPTIME", 1: "TIMELINE_SESSION", 2: "TIMELINE_3_HOURS",
        3: "TIMELINE_6_HOURS", 4: "TIMELINE_12_HOURS", 5: "TIMELINE_24_HOURS",
        6: "TIMELINE_WEEK", 7: "TIMELINE_MONTH", 8: "TIMELINE_ALL",
    }
    DEFAULT_PERIOD: Final[str] = PERIOD_MAP[0]
    # The logic in CUTOFF_DAYS must also use the i18n keys
    CUTOFF_DAYS: Final[Dict[str, float]] = {
        "TIMELINE_3_HOURS": 3 / 24, "TIMELINE_6_HOURS": 6 / 24, "TIMELINE_12_HOURS": 12 / 24,
        "TIMELINE_24_HOURS": 1, "TIMELINE_WEEK": 7, "TIMELINE_MONTH": 30, "TIMELINE_ALL": 365 * 10,
    }

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.PERIOD_MAP:
            raise ValueError("PERIOD_MAP must not be empty")
        if self.DEFAULT_PERIOD not in self.PERIOD_MAP.values():
            raise ValueError("DEFAULT_PERIOD must be a value in PERIOD_MAP")
        expected_cutoff_keys = set(self.PERIOD_MAP.values()) - {"TIMELINE_SYSTEM_UPTIME", "TIMELINE_SESSION"}
        if set(self.CUTOFF_DAYS.keys()) != expected_cutoff_keys:
            raise ValueError("CUTOFF_DAYS keys do not match filterable periods in PERIOD_MAP")

class DataConstants:
    """Container for data-related constant groups."""
    def __init__(self) -> None:
        self.legend_position = LegendPositionConstants()
        self.retention = DataRetentionConstants()
        self.history_period = HistoryPeriodConstants()

# Singleton instance for easy access
data = DataConstants()