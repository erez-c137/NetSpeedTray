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
    # Settings' tint on the control a NoticeLink / deep link landed on (2.2.2 D4): kept until the user's
    # first click, key or scroll, at most this long after the dialog first paints it, then faded out.
    SETTINGS_ANCHOR_HIGHLIGHT_MS: Final[int] = 5000
    SETTINGS_ANCHOR_FADE_MS: Final[int] = 300

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

# Singleton instance for easy access
timers = TimerConstants()