"""
Constants for timer intervals used within the application.
"""

from typing import Final

class TimerConstants:
    """Defines all timer intervals used for updates and checks."""
    SMART_MODE_INTERVAL_MS: Final[int] = 2000
    MINIMUM_INTERVAL_MS: Final[int] = 100
    CSV_FLUSH_INTERVAL_MS: Final[int] = 5000
    POSITION_CHECK_INTERVAL_MS: Final[int] = 500
    VISIBILITY_CHECK_INTERVAL_MS: Final[int] = 500
    MAXIMUM_UPDATE_RATE_SECONDS: Final[float] = 10.0

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