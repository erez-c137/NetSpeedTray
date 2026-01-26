"""
Timeouts and Intervals Constants Module.

This module defines constant values for various timeouts, intervals, and sleep durations
used throughout the application to avoid magic numbers.
"""

from typing import Final


class TimeoutConstants:
    """Defines all timeout values used across the application."""
    # Database related timeouts (seconds)
    DB_FLUSH_BATCH_SYNC_SLEEP: Final[float] = 0.1
    DB_BUSY_TIMEOUT_MS: Final[int] = 250

    # System Event related intervals (milliseconds)
    TASKBAR_VALIDITY_CHECK_INTERVAL_MS: Final[int] = 3000

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate that all timeouts are positive."""
        for attr_name in dir(self):
            if attr_name.endswith("_MS") or attr_name.endswith("_SLEEP"):
                value = getattr(self, attr_name)
                if not isinstance(value, (int, float)) or value < 0:
                    raise ValueError(f"{attr_name} must be a non-negative number.")


# Singleton instance for easy access
timeouts = TimeoutConstants()
