"""
Constants for timer intervals used within the application.
"""

from typing import Final

class TimerConstants:
    """Defines all timer intervals used for updates and checks."""
    MINIMUM_INTERVAL_MS: Final[int] = 100
    CSV_FLUSH_INTERVAL_MS: Final[int] = 5000
    POSITION_CHECK_INTERVAL_MS: Final[int] = 500
    VISIBILITY_CHECK_INTERVAL_MS: Final[int] = 500
    MAXIMUM_UPDATE_RATE_SECONDS: Final[float] = 10.0
    # The one-time "history upgraded" flyout reads three sentences - twice the data-cap alert's 15 s.
    HISTORY_UPGRADE_NOTICE_MS: Final[int] = 30000

    # Monitor Overview: how often the selected window's DB figures (sparklines, avg/peak, drops) are
    # re-read, by the window's length. A reload costs 0.4-1.1 s of reader-thread CPU on a big database,
    # and one sparkline point of a 24 h window is ~13 minutes - re-reading it every 6 s bought nothing.
    # Session and windows up to OVERVIEW_SHORT_WINDOW_SECONDS keep the 6 s cadence; up to
    # OVERVIEW_DAY_WINDOW_SECONDS reload every 30 s; longer ones (Week, Month, All) every 2 minutes. A
    # range change, showing the Overview and "Delete all history" reload at once whatever the cadence.
    OVERVIEW_SHORT_RELOAD_MS: Final[int] = 6000
    OVERVIEW_DAY_RELOAD_MS: Final[int] = 30000
    OVERVIEW_LONG_RELOAD_MS: Final[int] = 120000
    OVERVIEW_SHORT_WINDOW_SECONDS: Final[int] = 4 * 3600
    OVERVIEW_DAY_WINDOW_SECONDS: Final[int] = 2 * 86400
    # A reload whose database has not changed since the last read (PRAGMA data_version) reuses that
    # read - but never one older than this, so the window's edge still moves on a file nothing writes
    # (a read-only session while a v8 upgrade is deferred).
    OVERVIEW_REUSE_MAX_AGE_MS: Final[int] = 300000
    # Sparkline points per Overview card, spread over the WHOLE window (integer-binned in SQL). At most
    # this + 2 bins can be touched, which must stay within the tiles' 120-point cap (tiles._SPARK_POINTS).
    OVERVIEW_SPARK_BINS: Final[int] = 118

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate the timer constants to ensure they are positive."""
        for attr_name in dir(self):
            if attr_name.endswith("_MS") or attr_name.endswith("_SECONDS"):
                value = getattr(self, attr_name)
                if not isinstance(value, (int, float)) or value <= 0:
                    raise ValueError(f"{attr_name} must be a positive number.")
        if self.MAXIMUM_UPDATE_RATE_SECONDS * 1000 < self.MINIMUM_INTERVAL_MS:
            raise ValueError("MAXIMUM_UPDATE_RATE_SECONDS must allow intervals >= MINIMUM_INTERVAL_MS")
        if not (self.OVERVIEW_SHORT_RELOAD_MS <= self.OVERVIEW_DAY_RELOAD_MS <= self.OVERVIEW_LONG_RELOAD_MS
                <= self.OVERVIEW_REUSE_MAX_AGE_MS):
            raise ValueError("Overview reload intervals must grow with the window and stay within the reuse age")
        if not self.OVERVIEW_SHORT_WINDOW_SECONDS < self.OVERVIEW_DAY_WINDOW_SECONDS:
            raise ValueError("OVERVIEW_SHORT_WINDOW_SECONDS must be below OVERVIEW_DAY_WINDOW_SECONDS")
        if not isinstance(self.OVERVIEW_SPARK_BINS, int) or self.OVERVIEW_SPARK_BINS < 2:
            raise ValueError("OVERVIEW_SPARK_BINS must be an integer >= 2")

# Singleton instance for easy access
timers = TimerConstants()