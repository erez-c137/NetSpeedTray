"""
Timer management module for NetSpeedTray.

This module defines the `SpeedTimerManager` class, which manages the timer for periodic
network speed updates in the NetSpeedTray application. Key responsibilities include:
- Creating and configuring a repeating QTimer for speed updates.
- Starting, stopping, and updating the timer's interval based on configuration.
- Emitting the `stats_updated` signal to trigger speed updates in the application.
- Supporting connection and disconnection of slots to the timer's timeout signal.

The class uses constants from `constants.timers` for interval constraints and
`constants.config.defaults` for the default update rate. It relies on `timer_utils.py` for
low-level timer creation, interval calculation, and cleanup operations.

For generic timer utilities (e.g., creating timers for other purposes), use the functions
in `timer_utils.py`.
"""

import logging
from typing import Dict, Any, Optional, Callable

from PyQt6.QtCore import QTimer, QObject, pyqtSignal

from netspeedtray import constants
from netspeedtray.utils.timer_utils import calculate_timer_interval, create_timer, cleanup_timer  # Import utilities


class SpeedTimerManager(QObject):
    """
    Manages the timer for periodic network speed updates in NetSpeedTray.

    Responsibilities include:
    - Initializing a repeating QTimer for speed updates with a configurable interval.
    - Providing methods to start, stop, and update the timer's interval.
    - Emitting the `stats_updated` signal to trigger speed updates in the application.
    - Supporting connection and disconnection of slots to the timer's timeout signal.

    The timer interval is constrained by `MINIMUM_INTERVAL_MS` (500ms) and
    `constants.timers.MAXIMUM_UPDATE_RATE_SECONDS`, and the default update rate is sourced
    from `constants.config.defaults.DEFAULT_UPDATE_RATE`.

    Signals:
        stats_updated: Emitted periodically based on the speed timer interval to trigger
            speed updates.
    """
    stats_updated = pyqtSignal()
    MINIMUM_INTERVAL_MS = 500  # Enforce 500ms minimum interval


    def __init__(self, config: Dict[str, Any], parent: Optional[QObject] = None) -> None:
        """
        Initialize the SpeedTimerManager with the given configuration.

        Sets up the speed timer with an interval derived from the `update_rate` in the config.
        The timer is not started automatically; use `start_timer()` to begin updates.

        Args:
            config: The application configuration dictionary containing the `update_rate` (in seconds).
            parent: The parent QObject, typically the main application widget (e.g., NetworkSpeedWidget).

        Raises:
            ValueError: If the configuration provides an invalid update rate.
            RuntimeError: If timer creation fails due to Qt or system issues.
        """
        super().__init__(parent)
        self.logger = logging.getLogger(f"NetSpeedTray.{self.__class__.__name__}")
        self.config = config
        self.timers: Dict[str, QTimer] = {}

        # --- Initialize the Speed Timer ---
        self.logger.debug("Initializing speed timer...")
        try:
            self._init_timers()
            self.logger.info("SpeedTimerManager initialized successfully")
        except Exception as e:
            self.logger.critical("Failed to initialize SpeedTimerManager: %s", e, exc_info=True)
            raise


    def connect_timer(self, slot: Callable) -> bool:
        """
        Connects a slot function to the speed timer's timeout signal.

        Args:
            slot: The callable function or method to connect.

        Returns:
            bool: True if connection was successful or slot was already connected, False otherwise.
        """
        try:
            self.timers["speed"].timeout.connect(slot)
            self.logger.debug("Connected slot %s to speed timer", getattr(slot, '__name__', repr(slot)))
            return True
        except Exception as e:
            self.logger.error("Error connecting slot %s to speed timer: %s", getattr(slot, '__name__', repr(slot)), e)
            return False


    def disconnect_timer(self, slot: Callable) -> bool:
        """
        Disconnects a slot function from the speed timer's timeout signal.

        Args:
            slot: The callable function or method to disconnect.

        Returns:
            bool: True if disconnection seemed successful, False otherwise.
        """
        try:
            self.timers["speed"].timeout.disconnect(slot)
            self.logger.debug("Disconnected slot %s from speed timer", getattr(slot, '__name__', repr(slot)))
            return True
        except TypeError:
            self.logger.warning("Slot %s was likely not connected to speed timer, cannot disconnect.", getattr(slot, '__name__', repr(slot)))
            return True
        except Exception as e:
            self.logger.error("Error disconnecting slot %s from speed timer: %s", getattr(slot, '__name__', repr(slot)), e)
            return False


    def start_timer(self) -> None:
        """
        Starts the speed timer if it exists and is not already active.
        """
        if "speed" in self.timers and not self.timers["speed"].isActive():
            self.timers["speed"].start()
            self.logger.debug("Started speed timer")


    def stop_timer(self) -> None:
        """
        Stops the speed timer if it exists and is active.
        """
        if "speed" in self.timers and self.timers["speed"].isActive():
            self.timers["speed"].stop()
            self.logger.debug("Stopped speed timer")


    def update_interval(self, interval_ms: int) -> None:
        """
        Updates the interval of the speed timer, ensuring it meets the minimum.

        Args:
            interval_ms: The desired interval in milliseconds.
        """
        if "speed" not in self.timers:
            self.logger.warning("Cannot update interval: 'speed' timer has been cleaned up.")
            return

        actual_interval = max(interval_ms, self.MINIMUM_INTERVAL_MS)
        if actual_interval != interval_ms:
            self.logger.warning(
                "Requested interval %dms for speed timer is below minimum (%dms). Using minimum.",
                interval_ms, self.MINIMUM_INTERVAL_MS
            )
        if self.timers["speed"].interval() != actual_interval:
            was_active = self.timers["speed"].isActive()
            if was_active:
                self.timers["speed"].stop()
            self.timers["speed"].setInterval(actual_interval)
            if was_active:
                self.timers["speed"].start()
            self.logger.debug("Updated speed timer interval to %dms", actual_interval)


    def update_speed_rate(self, update_rate: float) -> None:
        """
        Updates the speed timer's interval based on the desired rate in seconds.

        Args:
            update_rate: The desired update rate in seconds (0 for smart mode).

        Raises:
            ValueError: If the update rate is negative or exceeds the maximum allowed value.
        """
        self.logger.debug("Updating speed timer rate to %.2f seconds", update_rate)
        # Validate update_rate
        if update_rate < 0:
            self.logger.error("Update rate cannot be negative: %.2f", update_rate)
            raise ValueError(f"Update rate cannot be negative: {update_rate}")
        max_update_rate = constants.timers.MAXIMUM_UPDATE_RATE_SECONDS
        if update_rate > max_update_rate and update_rate != 0:  # Allow 0 for smart mode
            self.logger.warning(
                "Update rate %.2fs exceeds maximum allowed (%.2fs). Clamping to maximum.",
                update_rate, max_update_rate
            )
            update_rate = max_update_rate

        try:
            interval_ms = calculate_timer_interval(update_rate)  # Use timer_utils
            self.update_interval(interval_ms)
            self.logger.debug("Speed timer interval updated via rate %.2fs to %dms", update_rate, interval_ms)
        except ValueError as e:
            self.logger.error("Invalid update rate provided for speed timer: %s", e)
            raise
        except Exception as e:
            self.logger.error("Unexpected error updating speed timer rate: %s", e, exc_info=True)
            raise


    def _init_timers(self) -> None:
        """
        Initializes the speed timer with the configured interval.

        Ensures the interval is at least MINIMUM_INTERVAL_MS (500ms) for performance.

        Raises:
            ValueError: If the configuration provides an invalid update rate.
            RuntimeError: If timer creation fails due to Qt or system issues.
        """
        try:
            initial_speed_rate = self.config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE)
            speed_interval = calculate_timer_interval(initial_speed_rate)
            # Enforce minimum interval
            speed_interval = max(speed_interval, self.MINIMUM_INTERVAL_MS)
            self.timers["speed"] = create_timer(self, self.stats_updated.emit, speed_interval)
            self.logger.debug("Successfully initialized speed timer with interval %dms", speed_interval)
        except ValueError as e:
            self.logger.error("Invalid update rate in configuration: %s", e)
            raise ValueError(f"Failed to initialize speed timer due to invalid update rate: {e}") from e
        except Exception as e:
            self.logger.error("Failed to initialize speed timer: %s", e, exc_info=True)
            raise RuntimeError(f"Failed to initialize speed timer: {e}") from e


    def cleanup(self) -> None:
        """
        Stops and cleans up the speed timer gracefully.
        """
        self.logger.debug("Cleaning up speed timer...")
        if "speed" in self.timers:
            cleanup_timer(self.timers["speed"])
            del self.timers["speed"]
        self.logger.info("Speed timer cleanup completed")