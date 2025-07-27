"""
Manages the in-memory network speed history for the NetworkSpeedWidget.

This module defines the `SpeedHistory` class, responsible for tracking network
speed data points over time in a capped deque.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional

from ..constants.constants import ConfigConstants


logger = logging.getLogger("NetSpeedTray.SpeedHistory")


@dataclass(slots=True)
class SpeedData:
    """
    Represents network speed data at a specific timestamp.

    Attributes:
        upload: Upload speed in bytes/sec.
        download: Download speed in bytes/sec.
        timestamp: Python datetime object when the speeds were recorded.
    """
    upload: float
    download: float
    timestamp: datetime


class SpeedHistory:
    """
    Manages the in-memory network speed history for the application.

    Maintains a capped deque (`speed_history`) of recent SpeedData points for quick access.
    Persistent storage is handled by `WidgetState` in a SQLite database.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initializes the SpeedHistory.

        Args:
            config: The application configuration dictionary, used to determine
                    the maximum history length.
        """
        self.logger = logging.getLogger(f"NetSpeedTray.{self.__class__.__name__}")
        self.config = config.copy()

        # --- Configuration Dependent State ---
        self.max_history_points: int = self._get_max_history_points()

        # --- Core Data Structure ---
        self.speed_history: Deque[SpeedData] = deque(maxlen=self.max_history_points)

        self.logger.debug("SpeedHistory initialized (Max History Points: %d)", self.max_history_points)

    def _get_max_history_points(self) -> int:
        """
        Calculates the maximum number of history points based on config.

        Returns:
            int: The maximum number of history points, constrained by a hard maximum.
        """
        try:
            history_minutes = self.config.get("history_minutes", ConfigConstants.DEFAULT_HISTORY_MINUTES)
            update_rate_sec = self.config.get("update_rate", ConfigConstants.DEFAULT_UPDATE_RATE)

            if update_rate_sec <= 0:
                self.logger.warning("Invalid update_rate (<=0) in config, using %.2fs for history calculation.", ConfigConstants.DEFAULT_UPDATE_RATE)
                update_rate_sec = ConfigConstants.DEFAULT_UPDATE_RATE

            # Calculate points: (history_minutes * 60 seconds/minute) / update_rate_sec
            calculated_points = int(round((history_minutes * 60) / update_rate_sec))
            # Ensure a minimum number of points
            min_points = getattr(ConfigConstants, "MINIMUM_HISTORY_POINTS", 10)
            max_points = max(min_points, calculated_points)

            # Apply a hard maximum
            max_limit = getattr(ConfigConstants, "MAXIMUM_HISTORY_POINTS", 5000)
            if max_points > max_limit:
                self.logger.warning(
                    "Calculated history points (%d) exceeds maximum (%d). Clamping to maximum.",
                    max_points, max_limit
                )
                max_points = max_limit

            self.logger.debug("Calculated max history points: %d (from %d mins, %.2fs rate)",
                             max_points, history_minutes, update_rate_sec)
            return max_points

        except Exception as e:
            self.logger.error("Error calculating max history points from config: %s. Using default.", e)
            return getattr(ConfigConstants, "DEFAULT_HISTORY_POINTS", 1800)

    def apply_config(self, config: Dict[str, Any]) -> None:
        """
        Applies updated configuration, primarily adjusting history length.

        Args:
            config: The new configuration dictionary.
        """
        self.logger.debug("Applying new configuration to SpeedHistory...")
        self.config = config.copy()
        new_max_points = self._get_max_history_points()

        if new_max_points != self.max_history_points:
            self.max_history_points = new_max_points
            # Recreate deque with new maxlen, preserving existing data
            self.speed_history = deque(self.speed_history, maxlen=self.max_history_points)
            self.logger.info("Speed history capacity updated to %d points.", self.max_history_points)
        else:
            self.logger.debug("Max history points unchanged (%d).", self.max_history_points)

    def update_speed_history(self, upload: float, download: float) -> None:
        """
        Adds a new network speed data point to the history deque.

        Args:
            upload: Current upload speed in bytes/sec.
            download: Current download speed in bytes/sec.
        """
        try:
            now = datetime.now()
            speed_data = SpeedData(upload=round(upload, 2), download=round(download, 2), timestamp=now)
            self.speed_history.append(speed_data)
        except Exception as e:
            self.logger.error(f"Error updating speed history: {e}", exc_info=True)

    def get_speed_history(self, start_time: Optional[datetime] = None) -> List[SpeedData]:
        """
        Retrieves the current in-memory speed history as a list.

        Args:
            start_time: If specified, only include data after this timestamp.

        Returns:
            List[SpeedData]: A list of SpeedData objects, sorted by timestamp.
        """
        history = list(self.speed_history)
        if start_time:
            history = [speed_data for speed_data in history if speed_data.timestamp >= start_time]
        return history