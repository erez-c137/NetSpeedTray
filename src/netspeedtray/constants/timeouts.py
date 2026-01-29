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
    STATE_WATCHER_INTERVAL_MS: Final[int] = 1000
    TASKBAR_RESTART_RECOVERY_DELAY_MS: Final[int] = 1000
    TASKBAR_RESTART_RETRIES: Final[int] = 5

    # Widget / UI Delays (milliseconds)
    WIDGET_INIT_DELAY_MS: Final[int] = 500
    GRAPH_CLOSE_REFRESH_DELAY_MS: Final[int] = 300
    
    # Thread / App Lifecycle (milliseconds/seconds)
    APP_CLOSE_WAIT_MS: Final[int] = 2000
    MONITOR_THREAD_STOP_WAIT_MS: Final[int] = 1000
    DB_INITIALIZATION_RETRY_DELAY_SEC: Final[float] = 2.0

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
