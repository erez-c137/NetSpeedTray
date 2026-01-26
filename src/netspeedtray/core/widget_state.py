"""
Manages the application's data layer, including in-memory state and SQLite persistence.

This module defines `WidgetState`, which acts as the main interface for the application's
data, and `DatabaseWorker`, a dedicated QThread for all database write and maintenance
operations to ensure the UI remains responsive.

Key Features:
- Manages an in-memory deque of recent speeds for the real-time mini-graph.
- Stores granular, per-interface network speed data in a multi-tiered SQLite database.
- Implements a multi-tier aggregation strategy:
  - Per-second data is kept for 24 hours.
  - Per-minute aggregates are kept for 30 days.
  - Per-hour aggregates are kept for up to 1 year.
- Handles user-configurable data retention with a 48-hour grace period for reductions.
- Performs all database writes and maintenance (pruning, aggregation, VACUUM) in a
  dedicated background thread to prevent UI blocking.
- Guarantees data integrity through the use of atomic transactions.
"""

import logging
import sqlite3
import threading
import time
import shutil
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple, Literal, Union

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer

from netspeedtray.constants import network, timeouts
from netspeedtray.utils.helpers import get_app_data_path

logger = logging.getLogger("NetSpeedTray.WidgetState")


# --- Data Transfer Objects (DTOs) ---
@dataclass(slots=True, frozen=True)
class AggregatedSpeedData:
    """Represents aggregated network speed data at a specific timestamp for the mini-graph."""
    upload: float
    download: float
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class SpeedDataSnapshot:
    """Represents a snapshot of per-interface network speeds at a specific timestamp."""
    speeds: Dict[str, Tuple[float, float]]
    timestamp: datetime


# --- Database Worker Thread ---
from netspeedtray.core.database import DatabaseWorker


class WidgetState(QObject):
    """Manages all network speed and bandwidth history for the NetworkSpeedWidget."""

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__()
        self.logger = logger
        self.config = config.copy()

        # In-Memory Cache for real-time mini-graph
        self.max_history_points: int = self._get_max_history_points()
        self.in_memory_history: Deque[SpeedDataSnapshot] = deque(maxlen=self.max_history_points)
        
        # Batching list for database writes
        self._db_batch: List[Tuple[int, str, float, float]] = []

        # Database Worker Thread
        db_path = Path(get_app_data_path()) / "speed_history.db"
        self.db_worker = DatabaseWorker(db_path)
        self.db_worker.error.connect(lambda msg: self.logger.error("DB Worker Error: %s", msg))
        self.db_worker.start()

        # Timers for periodic operations
        self.batch_persist_timer = QTimer(self)
        self.batch_persist_timer.timeout.connect(self.flush_batch)
        self.batch_persist_timer.start(10 * 1000) # Persist every 10 seconds

        self.maintenance_timer = QTimer(self)
        self.maintenance_timer.timeout.connect(self.trigger_maintenance)
        self.maintenance_timer.start(60 * 60 * 1000) # Run maintenance every hour

        self.logger.info("WidgetState initialized with threaded database worker.")


    def add_speed_data(self, speed_data: Dict[str, Tuple[float, float]]) -> None:
        """
        Adds new per-interface speed data. Updates in-memory state and adds
        to the database write batch.

        Args:
            speed_data: A dictionary mapping interface names to a tuple of
                        (upload_bytes_sec, download_bytes_sec) as FLOATS.
        """
        now = datetime.now()
        
        # The in-memory history now stores the full per-interface data for live filtering.
        self.in_memory_history.append(SpeedDataSnapshot(
            speeds=speed_data.copy(),
            timestamp=now
        ))

        timestamp = int(now.timestamp())
        min_speed = network.speed.MIN_RECORDABLE_SPEED_BPS
        for interface, (up_speed, down_speed) in speed_data.items():
            # Only add to the database batch if the speed is significant
            if up_speed >= min_speed or down_speed >= min_speed:
                self._db_batch.append((timestamp, interface, up_speed, down_speed))


    def get_total_bandwidth_for_period(self, start_time: Optional[datetime], end_time: datetime, interface_name: Optional[str] = None) -> Tuple[float, float]:
        """
        Calculates the total upload and download bandwidth for a given period
        by running a SUM query directly on the aggregated database tables.
        This is much more efficient than calculating it in Python.

        Returns:
            A tuple of (total_upload_bytes, total_download_bytes).
        """
        if not hasattr(self, 'db_worker') or not self.db_worker:
            return 0.0, 0.0

        # Determine which table to query for maximum efficiency
        # For periods over 2 days, the hourly aggregate is accurate enough and much faster.
        if start_time and (end_time - start_time).days > 2:
            table_name = "hour_data"
        else:
            table_name = "minute_data"

        return self.db_worker.get_total_bandwidth(table_name, start_time, end_time, interface_name)


    def get_in_memory_speed_history(self) -> List[SpeedDataSnapshot]:
        """
        Retrieves the current in-memory speed history.
        
        Returns:
            A list of SpeedDataSnapshot objects, each containing a dictionary of
            per-interface speeds for a specific timestamp.
        """
        return list(self.in_memory_history)


    def flush_batch(self) -> None:
        """Sends the current batch of speed data to the database worker."""
        if self._db_batch:
            batch_to_send = self._db_batch.copy()
            self._db_batch.clear()
            self.db_worker.enqueue_task("persist_speed", batch_to_send)


    def trigger_maintenance(self) -> None:
        """
        Public method to enqueue a maintenance task for the database worker,
        passing it the current application configuration.
        """
        self.logger.debug("Triggering periodic database maintenance.")
        self.db_worker.enqueue_task("maintenance", self.config.copy())


    def update_retention_period(self) -> None:
        """
        To be called after the user changes the retention setting. This triggers
        a maintenance run where the new config will be evaluated.
        """
        self.logger.info("User changed retention period. Triggering maintenance check.")
        # We don't need to pass config here; the trigger method will grab the latest.
        self.trigger_maintenance()


    def get_speed_history(self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None, interface_name: Optional[str] = None, return_raw: bool = False) -> List[Tuple[Union[datetime, float], float, float]]:
        """
        Retrieves speed history from the database, intelligently querying all
        data tiers to ensure all relevant data is returned.
        """
        self.flush_batch()
        time.sleep(timeouts.DB_FLUSH_BATCH_SYNC_SLEEP)

        results = []
        _start_ts = int(start_time.timestamp()) if start_time else 0
        _end_ts = int(end_time.timestamp()) if end_time else int(datetime.now().timestamp())
        
        conn = None
        try:
            conn = sqlite3.connect(f"file:{self.db_worker.db_path}?mode=ro", uri=True, timeout=5)
            conn.execute(f"PRAGMA busy_timeout = {timeouts.DB_BUSY_TIMEOUT_MS};")
            cursor = conn.cursor()
            
            # --- Build query and params iteratively and explicitly ---
            params: List[Any] = []
            
            base_selects = [
                "SELECT timestamp, upload_bytes_sec as upload, download_bytes_sec as download FROM speed_history_raw",
                "SELECT timestamp, upload_avg as upload, download_avg as download FROM speed_history_minute",
                "SELECT timestamp, upload_avg as upload, download_avg as download FROM speed_history_hour"
            ]
            
            query_parts = []
            for select_stmt in base_selects:
                # Start with the base time filter for every subquery
                query_part = f"{select_stmt} WHERE timestamp BETWEEN ? AND ?"
                params.extend([_start_ts, _end_ts])
                
                # If a specific interface is requested, add its filter to this subquery
                if interface_name and interface_name != "All":
                    query_part += " AND interface_name = ?"
                    params.append(interface_name)
                
                query_parts.append(query_part)
            
            # Combine the fully-formed subqueries
            union_query = " UNION ALL ".join(query_parts)
            
            if interface_name and interface_name != "All":
                # For a specific interface, just order the combined results
                final_query = union_query + " ORDER BY timestamp"
            else:
                # For "All" interfaces, wrap the union in an aggregation
                final_query = f"SELECT timestamp, SUM(upload), SUM(download) FROM ({union_query}) GROUP BY timestamp ORDER BY timestamp"
            
            cursor.execute(final_query, tuple(params))
            
            if return_raw:
                # Return raw timestamps (floats) directly to avoid expensive datetime instantiation
                results = [(ts, up, down) for ts, up, down in cursor.fetchall() if up is not None and down is not None]
            else:
                results = [(datetime.fromtimestamp(ts), up, down) for ts, up, down in cursor.fetchall() if up is not None and down is not None]

            self.logger.debug("Retrieved %d records for period %s to %s for interface '%s'.", len(results), start_time, end_time, interface_name)
        except sqlite3.Error as e:
            self.logger.error("Error retrieving unified speed history: %s", e, exc_info=True)
        finally:
            if conn:
                conn.close()
        
        return results


    def get_distinct_interfaces(self) -> List[str]:
        """Returns a sorted list of all unique interface names from the database."""
        conn = None
        try:
            conn = sqlite3.connect(f"file:{self.db_worker.db_path}?mode=ro", uri=True, timeout=5)
            conn.execute("PRAGMA busy_timeout = 250;") # Wait up to 250ms if locked
            cursor = conn.cursor()

            # Query all three tables to be comprehensive
            cursor.execute("""
                SELECT DISTINCT interface_name FROM speed_history_raw
                UNION
                SELECT DISTINCT interface_name FROM speed_history_minute
                UNION
                SELECT DISTINCT interface_name FROM speed_history_hour
                ORDER BY interface_name
            """)
            interfaces = [row[0] for row in cursor.fetchall()]
            return interfaces
        except sqlite3.Error as e:
            self.logger.error("Error fetching distinct interfaces: %s", e, exc_info=True)
            return []
        finally:
            if conn:
                conn.close()


    def get_earliest_data_timestamp(self) -> Optional[datetime]:
        """
        Retrieves the earliest data timestamp from the database by querying all tiers.
        """
        self.flush_batch()
        time.sleep(0.1)
        
        conn = None
        try:
            conn = sqlite3.connect(f"file:{self.db_worker.db_path}?mode=ro", uri=True, timeout=5)
            conn.execute("PRAGMA busy_timeout = 250;") # Wait up to 250ms if locked
            cursor = conn.cursor()

            query = """
                SELECT MIN(earliest_ts) FROM (
                    SELECT MIN(timestamp) as earliest_ts FROM speed_history_raw
                    UNION ALL
                    SELECT MIN(timestamp) as earliest_ts FROM speed_history_minute
                    UNION ALL
                    SELECT MIN(timestamp) as earliest_ts FROM speed_history_hour
                ) WHERE earliest_ts IS NOT NULL;
            """
            cursor.execute(query)
            result = cursor.fetchone()
            
            if result and result[0] is not None:
                earliest_ts = int(result[0])
                return datetime.fromtimestamp(earliest_ts)

        except sqlite3.Error as e:
            self.logger.error("Failed to retrieve the earliest timestamp from database: %s", e, exc_info=True)
        finally:
            if conn:
                conn.close()

        return None


    def cleanup(self) -> None:
        """Flushes final data and cleanly stops the database worker thread."""
        self.logger.info("Cleaning up WidgetState...")
        self.batch_persist_timer.stop()
        self.maintenance_timer.stop()
        self.flush_batch()
        self.db_worker.stop()
        # Only wait for the thread if it was actually running
        if self.db_worker.isRunning():
            self.db_worker.wait(2000) # Wait up to 2 seconds for the thread to finish


    def _get_max_history_points(self) -> int:
        """Calculates max points for the in-memory deque based on config."""
        try:
            history_minutes = self.config.get("history_minutes", 30)
            update_rate_sec = self.config.get("update_rate", 1.0)
            if update_rate_sec <= 0: update_rate_sec = 1.0
            
            points = int(round((history_minutes * 60) / update_rate_sec))
            return max(10, min(points, 5000))

        except Exception as e:
            self.logger.error("Error calculating max history points: %s. Using default.", e)
            return 1800

          
    def apply_config(self, config: Dict[str, Any]) -> None:
        """Apply updated configuration and adjust state."""
        self.logger.debug("Applying new configuration to WidgetState...")
        self.config = config.copy()
        new_max_points = self._get_max_history_points()

        if new_max_points != self.max_history_points:
            self.max_history_points = new_max_points
            self.in_memory_history = deque(self.in_memory_history, maxlen=self.max_history_points)
            self.logger.info("In-memory speed history capacity updated to %d points.", self.max_history_points)