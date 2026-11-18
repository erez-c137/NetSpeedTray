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
    # re-read, by the window's length. A full read costs 0.1-0.4 s of reader-thread CPU on a big
    # database, and one sparkline point of a 24 h window is ~13 minutes - re-reading it every 6 s bought
    # nothing. The Overview still asks every OVERVIEW_SHORT_RELOAD_MS (the session's down/up totals are
    # read fresh each time - a few ms); the window part is reused until its interval has passed AND
    # the database changed. Session and windows up to OVERVIEW_SHORT_WINDOW_SECONDS: 6 s; up to
    # OVERVIEW_DAY_WINDOW_SECONDS: 30 s; longer (Week, Month, All): 90 s - below WidgetState's 120 s
    # idle limit for a read connection. A range change, showing / activating the Monitor and "Delete
    # all history" re-read at once.
    OVERVIEW_SHORT_RELOAD_MS: Final[int] = 6000
    OVERVIEW_DAY_RELOAD_MS: Final[int] = 30000
    OVERVIEW_LONG_RELOAD_MS: Final[int] = 90000
    OVERVIEW_SHORT_WINDOW_SECONDS: Final[int] = 4 * 3600
    OVERVIEW_DAY_WINDOW_SECONDS: Final[int] = 2 * 86400
    # A timer tick lands a little before or after its interval: a read younger than the interval minus
    # this slack is still current.
    OVERVIEW_REUSE_SLACK_MS: Final[int] = 1000
    # A read whose database has not changed since (PRAGMA data_version) is reused past its interval -
    # but never once older than this, so the window's edge still moves on a file nothing writes (a
    # read-only session while a v8 upgrade is deferred).
    OVERVIEW_REUSE_MAX_AGE_MS: Final[int] = 300000
    # The internet connection dropped or came back: re-read the window once this long after, so the
    # history batch holding it (WidgetState persists every 10 s) is committed and the drop is counted.
    OVERVIEW_LINK_CHANGE_RELOAD_MS: Final[int] = 11000
    # A live peak stays on the card until a read made this long after it lands - by then the sample
    # is in the database (10 s persist cadence + slack), so the window's own peak includes it.
    OVERVIEW_LIVE_PEAK_HOLD_MS: Final[int] = 12000
    # Session sparkline: the in-memory buffer (WidgetState's graph deques) covers the session when its
    # oldest sample is at most this long after the session started; otherwise the session window is
    # read from the database like any other.
    OVERVIEW_SESSION_BUFFER_SLACK_SECONDS: Final[int] = 60
    # Activating / restoring the Monitor re-asks when the last request is older than this.
    OVERVIEW_ACTIVATE_RELOAD_MS: Final[int] = 6000
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