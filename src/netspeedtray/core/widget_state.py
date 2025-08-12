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
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple, Literal

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer

from netspeedtray import constants
from ..utils.helpers import get_app_data_path

logger = logging.getLogger("NetSpeedTray.WidgetState")

# --- Data Transfer Objects (DTOs) ---
@dataclass(slots=True, frozen=True)
class AggregatedSpeedData:
    """Represents aggregated network speed data at a specific timestamp for the mini-graph."""
    upload: float
    download: float
    timestamp: datetime

# --- Database Worker Thread ---
class DatabaseWorker(QThread):
    """
    A dedicated QThread to handle all blocking SQLite database operations,
    ensuring the main UI thread remains responsive at all times.
    """
    error = pyqtSignal(str)
    database_updated = pyqtSignal()

    _DB_VERSION = 2  # Increment this to force a schema rebuild

    def __init__(self, db_path: Path, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._queue: Deque[Tuple[str, Any]] = deque()
        self._stop_event = threading.Event()
        self.logger = logging.getLogger(f"NetSpeedTray.{self.__class__.__name__}")

    def run(self) -> None:
        """The main event loop for the database thread."""
        try:
            self._initialize_connection()
            self._check_and_create_schema()
        except sqlite3.Error as e:
            self.logger.critical("Database initialization failed: %s", e, exc_info=True)
            self.error.emit(f"Database initialization failed: {e}")
            return

        self.logger.info("Database worker thread started successfully.")
        while not self._stop_event.is_set():
            if self._queue:
                task, data = self._queue.popleft()
                self._execute_task(task, data)
            else:
                self.msleep(100) # Sleep briefly when idle

        self._close_connection()
        self.logger.info("Database worker thread stopped.")

    def stop(self) -> None:
        """Signals the worker thread to stop and waits for it to finish."""
        self.logger.debug("Stopping database worker thread...")
        self._stop_event.set()

    def enqueue_task(self, task: str, data: Any = None) -> None:
        """Adds a task to the worker's queue for asynchronous execution."""
        self._queue.append((task, data))

    def _execute_task(self, task: str, data: Any) -> None:
        """Dispatches a task to the appropriate handler method."""
        handlers = {
            "persist_speed": self._persist_speed_batch,
            "maintenance": self._run_maintenance,
        }
        handler = handlers.get(task)
        if handler:
            try:
                # Check if the data is a tuple containing config and a 'now' override for testing
                if task == "maintenance" and isinstance(data, tuple) and len(data) == 2:
                    config, now_override = data
                    handler(config, now=now_override)
                else: # Standard operation
                    handler(data)
            except sqlite3.Error as e:
                self.logger.error("Database error executing task '%s': %s", task, e, exc_info=True)
                self.error.emit(f"Database error: {e}")
        else:
            self.logger.warning("Unknown database task requested: %s", task)

    def _initialize_connection(self) -> None:
        """Establishes the SQLite connection and sets PRAGMAs for performance."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            self.db_path,
            timeout=10,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA busy_timeout = 5000;")
        self.logger.debug("Database connection established with WAL mode enabled.")

    def _close_connection(self) -> None:
        """Commits any final changes and closes the database connection."""
        if self.conn:
            self.conn.commit()
            self.conn.close()
            self.conn = None
            self.logger.debug("Database connection closed.")
            
    def _check_and_create_schema(self) -> None:
        """
        Checks the database version from the metadata table. If the version is
        outdated or the table doesn't exist, it drops old tables and creates
        the new schema.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT value FROM metadata WHERE key = 'db_version'")
            version = int(cursor.fetchone()[0])
            if version == self._DB_VERSION:
                self.logger.info("Database schema is up to date (Version %d).", self._DB_VERSION)
                return
        except (sqlite3.OperationalError, TypeError):
            self.logger.warning("Database version not found or invalid. Rebuilding schema.")

        # Drop old tables if they exist to ensure a clean slate
        self.logger.info("Dropping old tables...")
        cursor.execute("DROP TABLE IF EXISTS speed_history")
        cursor.execute("DROP TABLE IF EXISTS speed_history_aggregated")
        cursor.execute("DROP TABLE IF EXISTS speed_history_raw")
        cursor.execute("DROP TABLE IF EXISTS speed_history_minute")
        cursor.execute("DROP TABLE IF EXISTS speed_history_hour")
        cursor.execute("DROP TABLE IF EXISTS bandwidth_history")
        cursor.execute("DROP TABLE IF EXISTS app_bandwidth")
        cursor.execute("DROP TABLE IF EXISTS metadata")

        # Create new schema
        self.logger.info("Creating new database schema (Version %d)...", self._DB_VERSION)
        cursor.executescript(f"""
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO metadata (key, value) VALUES ('db_version', '{self._DB_VERSION}');

            CREATE TABLE speed_history_raw (
                timestamp INTEGER NOT NULL,
                interface_name TEXT NOT NULL,
                upload_bytes_sec REAL NOT NULL,
                download_bytes_sec REAL NOT NULL,
                PRIMARY KEY (timestamp, interface_name)
            );
            CREATE INDEX idx_raw_timestamp ON speed_history_raw (timestamp DESC);

            CREATE TABLE speed_history_minute (
                timestamp INTEGER NOT NULL,
                interface_name TEXT NOT NULL,
                upload_avg REAL NOT NULL,
                download_avg REAL NOT NULL,
                upload_max REAL NOT NULL,
                download_max REAL NOT NULL,
                PRIMARY KEY (timestamp, interface_name)
            );
            CREATE INDEX idx_minute_interface_timestamp ON speed_history_minute (interface_name, timestamp DESC);

            CREATE TABLE speed_history_hour (
                timestamp INTEGER NOT NULL,
                interface_name TEXT NOT NULL,
                upload_avg REAL NOT NULL,
                download_avg REAL NOT NULL,
                upload_max REAL NOT NULL,
                download_max REAL NOT NULL,
                PRIMARY KEY (timestamp, interface_name)
            );
            CREATE INDEX idx_hour_interface_timestamp ON speed_history_hour (interface_name, timestamp DESC);
        """)
        self.conn.commit()
        self.logger.info("New database schema created successfully.")

    def _persist_speed_batch(self, batch: List[Tuple[int, str, float, float]]) -> None:
        """Persists a batch of raw, per-second speed data in a single transaction."""
        if not batch or self.conn is None:
            return
        
        self.logger.debug("Persisting batch of %d speed records...", len(batch))
        cursor = self.conn.cursor()
        try:
            cursor.executemany(
                "INSERT OR IGNORE INTO speed_history_raw (timestamp, interface_name, upload_bytes_sec, download_bytes_sec) VALUES (?, ?, ?, ?)",
                batch
            )
            self.conn.commit()
            self.database_updated.emit()
        except sqlite3.Error as e:
            self.logger.error("Failed to persist speed batch: %s", e, exc_info=True)
            self.conn.rollback()


    def _run_maintenance(self, data: Dict[str, Any], now: Optional[datetime] = None) -> None:
        """
        Runs all periodic maintenance tasks inside a single transaction.
        The 'data' dict is expected to contain the application config.
        A 'now' timestamp can be passed for testability.
        """
        if self.conn is None: return
        
        config = data
        _now = now or datetime.now() # Use passed 'now' for testing, or current time for production
        
        self.logger.info("Starting periodic database maintenance...")
        cursor = self.conn.cursor()
        try:
            self._aggregate_raw_to_minute(cursor, _now)
            self._aggregate_minute_to_hour(cursor, _now)
            pruned = self._prune_data_with_grace_period(cursor, config, _now)
            
            self.conn.commit()
            self.logger.info("Database maintenance tasks committed successfully.")
            
            if pruned:
                self.logger.info("Significant data pruned, running VACUUM...")
                self.conn.execute("VACUUM;")
                self.logger.info("VACUUM complete.")

            self.database_updated.emit()
        except sqlite3.Error as e:
            self.logger.error("Database maintenance failed: %s", e, exc_info=True)
            self.conn.rollback()

    def _aggregate_raw_to_minute(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        """Aggregates per-second data older than 24 hours into per-minute averages/maxes."""
        cutoff = int((now - timedelta(hours=24)).timestamp())
        self.logger.debug("Aggregating raw data older than %s...", datetime.fromtimestamp(cutoff))
        
        cursor.execute("""
            INSERT INTO speed_history_minute (timestamp, interface_name, upload_avg, download_avg, upload_max, download_max)
            SELECT
                (timestamp / 60) * 60 AS minute_timestamp,
                interface_name,
                AVG(upload_bytes_sec),
                AVG(download_bytes_sec),
                MAX(upload_bytes_sec),
                MAX(download_bytes_sec)
            FROM speed_history_raw
            WHERE timestamp < ?
            GROUP BY minute_timestamp, interface_name
            ON CONFLICT(timestamp, interface_name) DO NOTHING;
        """, (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Aggregated %d per-minute records.", cursor.rowcount)
        
        cursor.execute("DELETE FROM speed_history_raw WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Pruned %d raw records after aggregation.", cursor.rowcount)

    def _aggregate_minute_to_hour(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        """Aggregates per-minute data older than 30 days into per-hour averages/maxes."""
        cutoff = int((now - timedelta(days=30)).timestamp())
        self.logger.debug("Aggregating minute data older than %s...", datetime.fromtimestamp(cutoff))

        cursor.execute("""
            INSERT INTO speed_history_hour (timestamp, interface_name, upload_avg, download_avg, upload_max, download_max)
            SELECT
                (timestamp / 3600) * 3600 AS hour_timestamp,
                interface_name,
                AVG(upload_avg),
                AVG(download_avg),
                MAX(upload_max),
                MAX(download_max)
            FROM speed_history_minute
            WHERE timestamp < ?
            GROUP BY hour_timestamp, interface_name
            ON CONFLICT(timestamp, interface_name) DO NOTHING;
        """, (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Aggregated %d per-hour records.", cursor.rowcount)

        cursor.execute("DELETE FROM speed_history_minute WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Pruned %d minute records after aggregation.", cursor.rowcount)

    def _prune_data_with_grace_period(self, cursor: sqlite3.Cursor, config: Dict[str, Any], now: datetime) -> bool:
        """
        Prunes old per-hour data based on user config, respecting a grace period.
        All time-based decisions are made using the provided 'now' parameter to
        ensure testability.
        
        Returns:
            True if any data was pruned, False otherwise.
        """
        # Get current state from metadata table, with safe fallbacks
        cursor.execute("SELECT value FROM metadata WHERE key = 'current_retention_days'")
        row = cursor.fetchone()
        current_retention_db = int(row[0]) if row else 365
        
        cursor.execute("SELECT value FROM metadata WHERE key = 'prune_scheduled_at'")
        row = cursor.fetchone()
        prune_scheduled_at_ts = int(row[0]) if row else None

        new_retention_config = config.get("keep_data", 365)
        
        # --- THIS IS THE CORRECT LOGICAL ORDER ---
        
        # 1. (HIGHEST PRIORITY) Check if a scheduled prune is due to be executed.
        if prune_scheduled_at_ts and prune_scheduled_at_ts <= int(now.timestamp()):
            cursor.execute("SELECT value FROM metadata WHERE key = 'pending_retention_days'")
            row = cursor.fetchone()
            
            if row:
                final_retention_days = int(row[0])
                self.logger.info("Grace period expired. Pruning data older than %d days.", final_retention_days)
                
                cutoff = int((now - timedelta(days=final_retention_days)).timestamp())
                cursor.execute("DELETE FROM speed_history_hour WHERE timestamp < ?", (cutoff,))
                pruned_count = cursor.rowcount
                
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('current_retention_days', ?)", (str(final_retention_days),))
                cursor.execute("DELETE FROM metadata WHERE key IN ('prune_scheduled_at', 'pending_retention_days')")
                
                return pruned_count > 0
            else:
                self.logger.warning("Scheduled prune was due, but no pending retention period was found. Cancelling.")
                cursor.execute("DELETE FROM metadata WHERE key = 'prune_scheduled_at'")
                return False

        # 2. If no prune is due, check if the user wants to reduce retention (and schedule a prune).
        elif new_retention_config < current_retention_db:
            if prune_scheduled_at_ts is None:
                grace_period_end = int((now + timedelta(hours=48)).timestamp())
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('prune_scheduled_at', ?)", (str(grace_period_end),))
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('pending_retention_days', ?)", (str(new_retention_config),))
                self.logger.info("Retention period reduced. Scheduling data prune in 48 hours.")
            return False

        # 3. If not, check if the user wants to increase retention (and cancel any pending prune).
        elif new_retention_config > current_retention_db:
            if prune_scheduled_at_ts is not None:
                cursor.execute("DELETE FROM metadata WHERE key IN ('prune_scheduled_at', 'pending_retention_days')")
                self.logger.info("Retention period increased. Pending data prune has been cancelled.")
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('current_retention_days', ?)", (str(new_retention_config),))

        # 4. If none of the above, just perform a standard, daily prune.
        cutoff = int((now - timedelta(days=current_retention_db)).timestamp())
        cursor.execute("DELETE FROM speed_history_hour WHERE timestamp < ?", (cutoff,))
        return cursor.rowcount > 0

# --- Main WidgetState Class ---
class WidgetState(QObject):
    """Manages all network speed and bandwidth history for the NetworkSpeedWidget."""

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__()
        self.logger = logger
        self.config = config.copy()

        # In-Memory Cache for real-time mini-graph
        self.max_history_points: int = self._get_max_history_points()
        self.in_memory_history: Deque[AggregatedSpeedData] = deque(maxlen=self.max_history_points)
        
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
                        (upload_bytes_sec, download_bytes_sec).
        """
        total_upload = sum(up for up, down in speed_data.values())
        total_download = sum(down for up, down in speed_data.values())
        now = datetime.now()
        
        # Add aggregated data to in-memory deque for the mini-graph
        self.in_memory_history.append(AggregatedSpeedData(
            upload=total_upload,
            download=total_download,
            timestamp=now
        ))

        # Add granular, per-interface data to the database write batch
        timestamp = int(now.timestamp())
        for interface, (up_speed, down_speed) in speed_data.items():
            # Filter out negligible activity to prevent database bloat
            if up_speed > 1.0 or down_speed > 1.0:
                self._db_batch.append((timestamp, interface, up_speed, down_speed))
    
    def get_in_memory_speed_history(self) -> List[AggregatedSpeedData]:
        """Retrieves the current in-memory speed history for the mini-graph."""
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


    def get_speed_history(self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None, interface_name: Optional[str] = "All") -> List[Tuple[datetime, float, float]]:
        """
        Retrieves speed history from the database, intelligently selecting the
        appropriate data tier based on the requested time range.
        """
        self.flush_batch()
        time.sleep(0.1)

        _start = start_time or (datetime.now() - timedelta(days=365*10))
        _end = end_time or datetime.now()
        time_delta = _end - _start
        
        results = []
        
        try:
            conn = sqlite3.connect(f"file:{self.db_worker.db_path}?mode=ro", uri=True, timeout=5)
            cursor = conn.cursor()
            
            # --- INTELLIGENT TIER SELECTION ---
            if time_delta.days < 3:
                table_name = "speed_history_raw"
                up_col, down_col = "upload_bytes_sec", "download_bytes_sec"
                self.logger.debug("Fetching from RAW tier for time delta < 3 days.")
            elif time_delta.days <= 60:
                table_name = "speed_history_minute"
                up_col, down_col = "upload_avg", "download_avg"
                self.logger.debug("Fetching from MINUTE tier for time delta <= 60 days.")
            else:
                table_name = "speed_history_hour"
                up_col, down_col = "upload_avg", "download_avg"
                self.logger.debug("Fetching from HOUR tier for time delta > 60 days.")

            base_query = f"SELECT timestamp, {up_col}, {down_col} FROM {table_name} WHERE timestamp >= ? AND timestamp <= ? {{interface_clause}} ORDER BY timestamp"
            
            params: tuple
            if interface_name == "All" or interface_name is None:
                # Aggregate all interfaces for the chosen tier
                agg_query = f"SELECT timestamp, SUM({up_col}), SUM({down_col}) FROM {table_name} WHERE timestamp >= ? AND timestamp <= ? GROUP BY timestamp ORDER BY timestamp"
                params = (int(_start.timestamp()), int(_end.timestamp()))
                cursor.execute(agg_query, params)
                results = [(datetime.fromtimestamp(ts), up, down) for ts, up, down in cursor.fetchall()]
            else:
                # Fetch for a specific interface
                query = base_query.format(interface_clause="AND interface_name = ?")
                params = (int(_start.timestamp()), int(_end.timestamp()), interface_name)
                cursor.execute(query, params)
                results = [(datetime.fromtimestamp(ts), up, down) for ts, up, down in cursor.fetchall()]

            conn.close()
        except sqlite3.Error as e:
            self.logger.error("Error retrieving intelligent speed history: %s", e, exc_info=True)
        
        return results


    def get_distinct_interfaces(self) -> List[str]:
        """Returns a sorted list of all unique interface names from the database."""
        try:
            conn = sqlite3.connect(f"file:{self.db_worker.db_path}?mode=ro", uri=True, timeout=5)
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
            conn.close()
            return interfaces
        except sqlite3.Error as e:
            self.logger.error("Error fetching distinct interfaces: %s", e, exc_info=True)
            return []

    def cleanup(self) -> None:
        """Flushes final data and cleanly stops the database worker thread."""
        self.logger.info("Cleaning up WidgetState...")
        self.batch_persist_timer.stop()
        self.maintenance_timer.stop()
        self.flush_batch()
        self.db_worker.stop()
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