"""
Manages network speed and bandwidth history for the NetworkSpeedWidget.

This module defines the `WidgetState` class, which handles:
- Storing and smoothing network speeds provided by NetworkController.
- Maintaining an in-memory deque of recent speeds for the mini-graph.
- Persisting speed data (bytes/sec) to `speed_history` table in SQLite (`speed_history.db`).
- Aggregating speed data older than 7 days into `speed_history_aggregated`.
- Vacuuming the database periodically to reclaim disk space.
- Tracking bandwidth (bytes sent/received) in `bandwidth_history` table with dynamic intervals
  (per-minute for 7 days, per-hour for older data).
- Tracking per-application bandwidth in `app_bandwidth` table.
- Adapting to user-configured update rates and interface settings.

The class ensures thread-safe database operations and efficient batch writes.
"""

import logging
import os
import sqlite3
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

# --- Qt Imports ---
from PyQt6.QtCore import QObject

# --- External Imports ---
import psutil

# --- Local Imports ---
from ..constants.constants import ConfigConstants
from ..utils.db_utils import (
    init_database,
    persist_speed_batch,
    persist_bandwidth_batch,
    aggregate_speed_history,
    persist_app_bandwidth_batch,
    vacuum_database,
    AppBandwidthData,
    get_speed_history,
    get_app_bandwidth_usage
)
from ..utils.helpers import get_app_data_path

logger = logging.getLogger("NetSpeedTray.WidgetState")


@dataclass(slots=True)
class PerInterfaceSpeedData:
    """
    Represents per-interface network speed data at a specific timestamp.

    Attributes:
        upload: Upload speed in bytes/sec.
        download: Download speed in bytes/sec.
        timestamp: Python datetime object when the speeds were recorded.
        interface: Network interface name (e.g., "Wi-Fi", "Ethernet").
    """
    upload: float
    download: float
    timestamp: datetime
    interface: str


@dataclass(slots=True)
class AggregatedSpeedData:
    """
    Represents aggregated network speed data (total across all interfaces) at a specific timestamp.

    Attributes:
        upload: Total upload speed in bytes/sec.
        download: Total download speed in bytes/sec.
        timestamp: Python datetime object when the speeds were recorded.
    """
    upload: float
    download: float
    timestamp: datetime


class WidgetState(QObject):
    """
    Manages all network speed and bandwidth history for the NetworkSpeedWidget.
    """
    EMA_ALPHA: float = 0.2
    DB_BATCH_WRITE_INTERVAL: int = 10
    VACUUM_INTERVAL: int = 1000

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initialize the WidgetState with configuration settings.
        """
        super().__init__()
        self.logger = logging.getLogger(f"NetSpeedTray.{self.__class__.__name__}")
        self.config = config.copy()

        # Configuration Dependent State
        self.max_history_points: int = self._get_max_history_points()

        # In-Memory Storage & State
        self.in_memory_history: Deque[AggregatedSpeedData] = deque(maxlen=self.max_history_points)
        self._ema_upload: float = 0.0
        self._ema_download: float = 0.0
        self._update_counter: int = 0
        self._prune_counter: int = 0
        self._minute_counters: Dict[str, Tuple[int, int]] = {}
        self._hour_counters: Dict[str, Tuple[int, int]] = {}
        self._app_counters: Dict[Tuple[str, str], Tuple[int, int]] = {}
        self._last_minute: int = 0
        self._last_hour: int = 0

        # SQLite Setup & Batching
        self.db_path = Path(get_app_data_path()) / "speed_history.db"
        self._db_lock = threading.Lock()
        self._db_batch: List[PerInterfaceSpeedData] = []
        self._bandwidth_batch: Dict[str, List[Tuple[int, int, int, str]]] = {}
        self._app_bandwidth_batch: Dict[Tuple[str, str], List[Tuple[int, str, int, int, str]]] = {}
        self._init_database()

        self.logger.debug(
            "WidgetState initialized: Max In-Memory History=%d, DB Batch=%d",
            self.max_history_points, self.DB_BATCH_WRITE_INTERVAL
        )

    def _init_database(self) -> None:
        """
        Initialize the SQLite database with required tables.
        """
        try:
            init_database(self.db_path)
            self.logger.info("SQLite database initialized at %s", self.db_path)
        except sqlite3.Error as e:
            self.logger.error("Error initializing SQLite database: %s", e, exc_info=True)
            raise

    def _get_max_history_points(self) -> int:
        """
        Calculates the maximum number of in-memory history points based on config.
        """
        try:
            history_minutes = self.config.get("history_minutes", ConfigConstants.DEFAULT_HISTORY_MINUTES)
            update_rate_sec = self.config.get("update_rate", ConfigConstants.DEFAULT_UPDATE_RATE)

            if update_rate_sec <= 0:
                self.logger.warning("Invalid update_rate (<=0), using %.2fs for history calculation.", ConfigConstants.DEFAULT_UPDATE_RATE)
                update_rate_sec = ConfigConstants.DEFAULT_UPDATE_RATE

            calculated_points = int(round((history_minutes * 60) / update_rate_sec))
            min_points = getattr(ConfigConstants, "MINIMUM_HISTORY_POINTS", 10)
            max_points = max(min_points, calculated_points)

            max_limit = getattr(ConfigConstants, "MAXIMUM_HISTORY_POINTS", 5000)
            if max_points > max_limit:
                self.logger.warning(
                    "Calculated history points (%d) exceeds maximum (%d). Clamping.",
                    max_points, max_limit
                )
                max_points = max_limit
            
            return max_points
        except Exception as e:
            self.logger.error("Error calculating max history points: %s. Using default.", e)
            return getattr(ConfigConstants, "DEFAULT_HISTORY_POINTS", 1800)

    def apply_config(self, config: Dict[str, Any]) -> None:
        """
        Apply updated configuration and adjust state.
        """
        self.logger.debug("Applying new configuration to WidgetState...")
        self.config = config.copy()
        new_max_points = self._get_max_history_points()

        if new_max_points != self.max_history_points:
            self.max_history_points = new_max_points
            # Recreate deque with new maxlen, preserving existing data
            self.in_memory_history = deque(self.in_memory_history, maxlen=self.max_history_points)
            self.logger.info("In-memory speed history capacity updated to %d points.", self.max_history_points)

    def add_speed_data(self, upload_speed: float, download_speed: float, bytes_sent: int, bytes_recv: int) -> None:
        """
        Adds new speed data, updates EMA, and stores in history (in-memory and DB batch).
        Also adds raw byte differences to the bandwidth history batch.
        """
        # Update EMA
        self._ema_upload = (self.EMA_ALPHA * upload_speed) + (1 - self.EMA_ALPHA) * self._ema_upload
        self._ema_download = (self.EMA_ALPHA * download_speed) + (1 - self.EMA_ALPHA) * self._ema_download

        now = datetime.now()
        timestamp = int(now.timestamp())
        
        # Add to in-memory history for mini-graph
        in_memory_data = AggregatedSpeedData(upload=upload_speed, download=download_speed, timestamp=now)
        self.in_memory_history.append(in_memory_data)

        interfaces = self.config.get("selected_interfaces", []) if self.config.get("interface_mode") == "selected" else ["All"]
        for iface in interfaces:
            # Add to speed history database batch (for the graph)
            db_data = PerInterfaceSpeedData(
                upload=upload_speed,
                download=download_speed,
                timestamp=now,
                interface=iface
            )
            self._db_batch.append(db_data)

            # --- Populate the bandwidth history batch ---
            if iface not in self._bandwidth_batch:
                self._bandwidth_batch[iface] = []
            # Only add to bandwidth history if there was actual data transfer
            if bytes_sent > 0 or bytes_recv > 0:
                self._bandwidth_batch[iface].append((timestamp, bytes_sent, bytes_recv, iface))

        # Periodic database persistence
        self._update_counter += 1
        if self._update_counter % self.DB_BATCH_WRITE_INTERVAL == 0:
            self._persist_batch()

    def get_smoothed_speeds(self) -> Tuple[float, float]:
        """
        Returns the smoothed upload and download speeds using EMA.
        """
        return self._ema_upload, self._ema_download

    def get_in_memory_speed_history(self) -> List[AggregatedSpeedData]:
        """
        Retrieves the current in-memory speed history as a list.
        Used for the real-time mini-graph.
        """
        return list(self.in_memory_history)

    def _persist_batch(self) -> None:
        """
        Persist batched speed, bandwidth, and app bandwidth data to SQLite.
        Converts speed data from bytes/sec to Kbps before writing.
        """
        if not self._db_batch and not self._bandwidth_batch and not self._app_bandwidth_batch:
            return

        records_to_write_count = len(self._db_batch)

        try:
            if self._db_batch:
                # --- Convert from bytes/sec to Kbps before writing ---
                batch_to_write = [
                    (
                        int(sd.timestamp.timestamp()),
                        round(sd.upload * 8 / 1000.0, 2),  # bytes/sec to Kbps
                        round(sd.download * 8 / 1000.0, 2), # bytes/sec to Kbps
                        sd.interface
                    )
                    for sd in self._db_batch
                ]
                persist_speed_batch(self.db_path, batch_to_write, self._db_lock)
                self._db_batch.clear()

            for iface, batch in self._bandwidth_batch.items():
                if batch:
                    persist_bandwidth_batch(self.db_path, batch, self._db_lock)
            self._bandwidth_batch.clear()

            for key, batch in self._app_bandwidth_batch.items():
                if batch:
                    persist_app_bandwidth_batch(self.db_path, batch, self._db_lock)
            self._app_bandwidth_batch.clear()

            self.logger.debug("Persisted %d speed records, bandwidth, and app bandwidth batches", records_to_write_count)
        except sqlite3.Error as e:
            self.logger.error("Error persisting batch: %s", e, exc_info=True)

    def get_current_speeds(self) -> AggregatedSpeedData:
        """
        Returns the current smoothed upload and download speeds (EMA-smoothed).

        Returns:
            AggregatedSpeedData: Smoothed upload and download speeds in bytes/sec,
                with the current timestamp.
        """
        smoothed_upload = self._ema_upload if self._ema_upload is not None else 0.0
        smoothed_download = self._ema_download if self._ema_download is not None else 0.0
        return AggregatedSpeedData(
            upload=smoothed_upload,
            download=smoothed_download,
            timestamp=datetime.now()
        )

    def get_speed_history(self, start_time: Optional[datetime] = None, limit: Optional[int] = None) -> List[PerInterfaceSpeedData]:
        """
        Retrieve speed history from SQLite for the graph, combining speed_history and speed_history_aggregated.

        Args:
            start_time: Optional start time for filtering records.
            limit: Optional maximum number of records to return.

        Returns:
            List[PerInterfaceSpeedData]: List of speed data points in chronological order.
        """
        if self._db_batch:
            self.logger.debug("Flushing DB batch before reading history")
            self._persist_batch()

        try:
            interfaces = self.config.get("selected_interfaces", []) if self.config.get("interface_mode") == "selected" else None
            history = get_speed_history(self.db_path, start_time, limit, interfaces, self._db_lock)
            # Removed verbose debug logging to prevent log spam
            return sorted(history, key=lambda x: x.timestamp)
        except sqlite3.Error as e:
            self.logger.error("Error retrieving speed history: %s", e, exc_info=True)
            return []

    def get_app_bandwidth_history(self, start_time: Optional[datetime] = None, limit: Optional[int] = None, app_names: Optional[List[str]] = None) -> List[AppBandwidthData]:
        """
        Retrieve application bandwidth history from SQLite for display.

        Args:
            start_time: Optional start time for filtering records.
            limit: Optional maximum number of records to return.
            app_names: Optional list of application names to filter.

        Returns:
            List[AppBandwidthData]: List of app bandwidth data points in chronological order.
        """
        if self._app_bandwidth_batch:
            self.logger.debug("Flushing app bandwidth batch before reading history")
            self._persist_batch()

        try:
            interfaces = self.config.get("selected_interfaces", []) if self.config.get("interface_mode") == "selected" else None
            history = get_app_bandwidth_usage(self.db_path, start_time, interfaces, app_names, self._db_lock)
            # Log details of returned data
            for i, data in enumerate(history):
                self.logger.debug(
                    "App bandwidth history [%d]: bytes_sent=%r (type=%s), bytes_recv=%r (type=%s), app_name=%s, timestamp=%s, interface=%s",
                    i, data.bytes_sent, type(data.bytes_sent), data.bytes_recv, type(data.bytes_recv),
                    data.app_name, data.timestamp, data.interface
                )
            return sorted(history, key=lambda x: x.timestamp)
        except sqlite3.Error as e:
            self.logger.error("Error retrieving app bandwidth history: %s", e, exc_info=True)
            return []

    def prune_old_records(self) -> None:
        """
        Mark old records as deleted, aggregate speed data, and periodically vacuum the database.

        Aggregates `speed_history` records older than 7 days into `speed_history_aggregated`.
        Marks records as deleted in `speed_history`, `bandwidth_history`, and `app_bandwidth`
        based on the retention period (`keep_data` config). Runs VACUUM every 1000 prunes.

        Applies to:
        - `speed_history`: Records older than retention_days.
        - `bandwidth_history`: Records older than retention_days.
        - `speed_history_aggregated`: Records with period_end older than retention_days.
        - `app_bandwidth`: Records older than retention_days.
        """
        if self._db_batch or self._bandwidth_batch or self._app_bandwidth_batch:
            self.logger.debug("Flushing batches before pruning")
            self._persist_batch()

        retention_days = self.config.get("keep_data", ConfigConstants.DEFAULT_HISTORY_PERIOD_DAYS)
        if not isinstance(retention_days, int) or retention_days <= 0:
            self.logger.warning("Invalid keep_data value (%s), using default", retention_days)
            retention_days = ConfigConstants.DEFAULT_HISTORY_PERIOD_DAYS

        cutoff_time = int((datetime.now() - timedelta(days=retention_days)).timestamp())
        now_timestamp = int(datetime.now().timestamp())
        aggregation_cutoff = int((datetime.now() - timedelta(days=7)).timestamp())
        self.logger.debug("Pruning records older than %s", datetime.fromtimestamp(cutoff_time))

        try:
            aggregated = aggregate_speed_history(self.db_path, aggregation_cutoff, self._db_lock)

            with self._db_lock:
                conn = sqlite3.connect(self.db_path, timeout=10)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE speed_history
                    SET deleted_at = ?
                    WHERE timestamp < ? AND deleted_at IS NULL
                    """,
                    (now_timestamp, cutoff_time)
                )
                speed_deleted = cursor.rowcount
                cursor.execute(
                    """
                    UPDATE bandwidth_history
                    SET deleted_at = ?
                    WHERE timestamp < ? AND deleted_at IS NULL
                    """,
                    (now_timestamp, cutoff_time)
                )
                bandwidth_deleted = cursor.rowcount
                cursor.execute(
                    """
                    UPDATE speed_history_aggregated
                    SET deleted_at = ?
                    WHERE period_end < ? AND deleted_at IS NULL
                    """,
                    (now_timestamp, cutoff_time)
                )
                aggregated_deleted = cursor.rowcount
                cursor.execute(
                    """
                    UPDATE app_bandwidth
                    SET deleted_at = ?
                    WHERE timestamp < ? AND deleted_at IS NULL
                    """,
                    (now_timestamp, cutoff_time)
                )
                app_bandwidth_deleted = cursor.rowcount
                conn.commit()

            self._prune_counter += 1
            if self._prune_counter >= self.VACUUM_INTERVAL:
                space_reclaimed = vacuum_database(self.db_path, self._db_lock)
                self._prune_counter = 0
                self.logger.info("VACUUM executed after %d prunes, reclaimed %.2f MB", self.VACUUM_INTERVAL, space_reclaimed)

            self.logger.debug(
                "Pruned records: speed=%d, aggregated=%d, bandwidth=%d, app_bandwidth=%d, total aggregated=%d",
                speed_deleted, aggregated_deleted, bandwidth_deleted, app_bandwidth_deleted, aggregated
            )

        except sqlite3.Error as e:
            self.logger.error("Error pruning records: %s", e, exc_info=True)
        finally:
            if 'conn' in locals():
                conn.close()

    def cleanup(self) -> None:
        """
        Persist remaining data and release resources before shutdown.

        Flushes any pending speed, bandwidth, and app bandwidth batches.
        """
        self.logger.debug("WidgetState cleanup: Persisting final batches")
        self._persist_batch()