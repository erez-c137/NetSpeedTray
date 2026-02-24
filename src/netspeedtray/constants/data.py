"""
Constants related to historical data management and representation.
"""

from datetime import datetime, timedelta
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
    # Optimized 6-pill design: SESS, BOOT, 24H, WEEK, MONTH, ALL
    PERIOD_MAP: Final[Dict[int, str]] = {
        0: "TIMELINE_SESSION", 
        1: "TIMELINE_SYSTEM_UPTIME", 
        2: "TIMELINE_24_HOURS",
        3: "TIMELINE_WEEK", 
        4: "TIMELINE_MONTH", 
        5: "TIMELINE_ALL",
    }
    DEFAULT_PERIOD: Final[str] = PERIOD_MAP[2]  # "TIMELINE_24_HOURS" - most universally useful

    # Aggregation thresholds (seconds) for choosing DB source and plotting resolution
    # OPTIMIZED for Performance:
    # 24H view (86400s) -> Must use Minute (1440 pts), NOT Raw (86400 pts).
    # Week view (604800s) -> Must use Hour (168 pts), NOT Minute (10080 pts).
    
    RES_RAW_THRESHOLD: Final[int] = 6 * 3600       # Use Raw for < 6 hours (~21k pts max)
    RES_MINUTE_THRESHOLD: Final[int] = 3 * 86400   # Use Minute for < 3 days (~4.3k pts max)
    RES_HOUR_THRESHOLD: Final[int] = 90 * 86400    # Use Hour for < 90 days (Month=720 pts, Week=168 pts)
    
    # Plotting resolution thresholds (seconds) - unused by current logic but kept for reference
    PLOT_MINUTE_THRESHOLD: Final[int] = 30 * 86400 # Use minute bins for < 30 days
    PLOT_HOURLY_THRESHOLD: Final[int] = 90 * 86400 # Use hourly bins for < 90 days
    
    # Mapping of period keys to their duration in days for standard ranges
    CUTOFF_DAYS: Final[Dict[str, float]] = {
        "TIMELINE_24_HOURS": 1, 
        "TIMELINE_WEEK": 7, 
        "TIMELINE_MONTH": 30, 
        "TIMELINE_ALL": 365 * 10,
    }

    @staticmethod
    def get_start_time(period_key: str, now: datetime, session_start: Optional[datetime] = None, 
                       boot_time: Optional[datetime] = None, earliest_db: Optional[datetime] = None) -> Optional[datetime]:
        """Centralized calculation of start_time for any given period."""
        if period_key == "TIMELINE_SESSION":
            return session_start
        elif period_key == "TIMELINE_SYSTEM_UPTIME":
            if earliest_db and boot_time:
                return max(boot_time, earliest_db)
            return boot_time
        elif period_key in HistoryPeriodConstants.CUTOFF_DAYS:
            if period_key == "TIMELINE_ALL":
                # Ensure we have a start time for 'ALL' to calculate duration correctly
                if earliest_db:
                     return earliest_db
                return now - timedelta(days=365*10) 
            return now - timedelta(days=HistoryPeriodConstants.CUTOFF_DAYS[period_key])
        return None

    @staticmethod
    def get_target_resolution(start_time: Optional[datetime], end_time: datetime) -> str:
        """Determines the data resolution (raw, minute, hour, day) for a given time range."""
        if not start_time or not end_time:
            # If no start time, assume infinite duration -> cheapest resolution
            return 'day' 
        
        duration = (end_time - start_time).total_seconds()
        
        # Guard: If duration is huge (e.g. ALL view with 1 year data), use appropriate
        if duration <= HistoryPeriodConstants.RES_RAW_THRESHOLD:
            return 'raw'
        elif duration <= HistoryPeriodConstants.RES_MINUTE_THRESHOLD:
            return 'minute'
        elif duration <= HistoryPeriodConstants.RES_HOUR_THRESHOLD:
            return 'hour'
        else:
            return 'day'

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
    # The filename for the SQLite database that stores speed history.
    DB_FILENAME: Final[str] = "speed_history.db"
    
    # Modern Schema (v2+)
    SPEED_TABLE_RAW: Final[str] = "speed_history_raw"
    SPEED_TABLE_MINUTE: Final[str] = "speed_history_minute"
    SPEED_TABLE_HOUR: Final[str] = "speed_history_hour"
    
    # Hardware Stats Schema (v5+)
    HARDWARE_STATS_TABLE_RAW: Final[str] = "hardware_stats_raw"
    HARDWARE_STATS_TABLE_MINUTE: Final[str] = "hardware_stats_minute"
    
    # Legacy Schema (v1) - To be removed after full transition
    SPEED_TABLE: Final[str] = "speed_history"
    AGGREGATED_TABLE: Final[str] = "speed_history_aggregated"
    
    BANDWIDTH_TABLE: Final[str] = "bandwidth_history"
    APP_BANDWIDTH_TABLE: Final[str] = "app_bandwidth"
    
    AGGREGATION_CUTOFF_DAYS: Final[int] = 2  # Days before data is aggregated
    
    def __init__(self) -> None:
        self.legend_position = LegendPositionConstants()
        self.retention = DataRetentionConstants()
        self.history_period = HistoryPeriodConstants()
        self.validate()

    def validate(self) -> None:
        if not self.DB_FILENAME or not self.DB_FILENAME.endswith(".db"):
            raise ValueError("DB_FILENAME must be a valid .db filename")

# Singleton instance for easy access
data = DataConstants()