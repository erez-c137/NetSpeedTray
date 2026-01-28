"""
Database Management Module.

This module houses the `DatabaseWorker` class, which is responsible for all
asynchronous SQLite operations, ensuring the main UI thread remains responsive.
"""

import logging
import sqlite3
import threading
import shutil
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from netspeedtray import constants

# Logger Setup
logger = logging.getLogger("NetSpeedTray.Core.Database")


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
        """The main event loop for the database thread with retry logic."""
        max_retries = 3
        retry_delay = 2.0 # seconds
        
        initialized = False
        for attempt in range(max_retries):
            try:
                self._initialize_connection()
                self._check_and_create_schema()
                initialized = True
                break
            except sqlite3.Error as e:
                self.logger.error("Database initialization attempt %d failed: %s", attempt + 1, e)
                if attempt < max_retries - 1:
                    self.msleep(int(retry_delay * 1000))
        
        if not initialized:
            self.logger.critical("Database initialization failed after %d attempts.", max_retries)
            self.error.emit(f"Critical: Database initialization failed after multiple attempts.")
            return

        self.logger.info("Database worker thread started successfully.")
        while not self._stop_event.is_set():
            if self._queue:
                task, data = self._queue.popleft()
                try:
                    self._execute_task(task, data)
                except sqlite3.Error as e:
                    self.logger.error("Database error during task execution: %s", e)
                    # If the connection is broken, attempt to reconnect once
                    if "closed" in str(e).lower() or "database is locked" in str(e).lower():
                        self.logger.info("Attempting to reconnect database...")
                        self._reconnect()
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



    def _get_current_db_version(self) -> int:
        """Retrieves the current database version, returning 0 if not found/invalid."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = 'db_version'")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except (sqlite3.OperationalError, TypeError, IndexError):
            return 0


    def _backup_database(self) -> bool:
        """Backs up the current database file before critical operations."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.db_path.with_suffix(f".db.bak.v{self._get_current_db_version()}_{timestamp}")
            self.logger.info("Backing up database to: %s", backup_path)
            shutil.copy2(self.db_path, backup_path)
            return True
        except Exception as e:
            self.logger.error("Failed to backup database: %s", e, exc_info=True)
            return False



    def _migrate_schema(self, current_version: int) -> None:
        """Handles migration from current_version to _DB_VERSION."""
        self.logger.info("Migrating database from version %d to %d...", current_version, self._DB_VERSION)
        
        # Backup first
        if not self._backup_database():
             self.logger.warning("Main database backup failed! Attempting to proceed carefully...")

        try:
            # Migration loop
            for ver in range(current_version, self._DB_VERSION):
                next_ver = ver + 1
                migration_method_name = f"_migrate_v{ver}_to_v{next_ver}"
                migration_method = getattr(self, migration_method_name, None)
                
                if migration_method:
                     self.logger.info("Running migration: %s", migration_method_name)
                     migration_method(self.conn.cursor())
                else:
                     self.logger.warning("No migration method found for v%d -> v%d. Updating version number only.", ver, next_ver)

                # Update version in DB after each step
                self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('db_version', ?)", (str(next_ver),))
                self.conn.commit()
                self.logger.info("Successfully migrated to version %d.", next_ver)

        except Exception as e:
            self.logger.critical("Database migration failed: %s", e, exc_info=True)
            self.conn.rollback()
            raise 

    def _migrate_v2_to_v3(self, cursor: sqlite3.Cursor) -> None:
        """Example migration: Add a new column or table."""
        # This is a placeholder for v3. Currently v3 == v2 structure-wise.
        pass

    def _check_and_create_schema(self) -> None:
        """
        Checks the database version. If outdated, attempts migration.
        If tables are missing (version 0), creates the new schema.
        """
        current_version = self._get_current_db_version()

        if current_version == self._DB_VERSION:
            self.logger.info("Database schema is up to date (Version %d).", self._DB_VERSION)
            return

        if current_version > 0:
             # Existing DB, needs migration
             self.logger.info("Database version mismatch (Current: %d, Target: %d). Attempting migration...", current_version, self._DB_VERSION)
             try:
                 self._migrate_schema(current_version)
                 return
             except Exception as e:
                 self.logger.error("Migration failed. Falling back to destructive rebuild. Error: %s", e)
        
        # Fresh install or failed migration (destructive rebuild)
        cursor = self.conn.cursor()
        self.logger.info("Building fresh database schema (Version %d)...", self._DB_VERSION)
        
        # Drop old tables if they exist to ensure a clean slate
        self.logger.info("Dropping old tables...")
        cursor.execute("PRAGMA foreign_keys = OFF;") # disable FKs to drop safely
        cursor.execute(f"DROP TABLE IF EXISTS {constants.data.SPEED_TABLE}")
        cursor.execute(f"DROP TABLE IF EXISTS {constants.data.AGGREGATED_TABLE}")
        cursor.execute(f"DROP TABLE IF EXISTS {constants.data.SPEED_TABLE_RAW}")
        cursor.execute(f"DROP TABLE IF EXISTS {constants.data.SPEED_TABLE_MINUTE}")
        cursor.execute(f"DROP TABLE IF EXISTS {constants.data.SPEED_TABLE_HOUR}")
        cursor.execute(f"DROP TABLE IF EXISTS {constants.data.BANDWIDTH_TABLE}")
        cursor.execute(f"DROP TABLE IF EXISTS {constants.data.APP_BANDWIDTH_TABLE}")
        cursor.execute("DROP TABLE IF EXISTS metadata")
        cursor.execute("PRAGMA foreign_keys = ON;")


        # Create new schema
        self.logger.info("Creating new database schema (Version %d)...", self._DB_VERSION)
        cursor.executescript(f"""
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO metadata (key, value) VALUES ('db_version', '{self._DB_VERSION}');

            CREATE TABLE {constants.data.SPEED_TABLE_RAW} (
                timestamp INTEGER NOT NULL,
                interface_name TEXT NOT NULL,
                upload_bytes_sec REAL NOT NULL,
                download_bytes_sec REAL NOT NULL,
                PRIMARY KEY (timestamp, interface_name)
            );
            CREATE INDEX idx_raw_timestamp ON {constants.data.SPEED_TABLE_RAW} (timestamp DESC);

            CREATE TABLE {constants.data.SPEED_TABLE_MINUTE} (
                timestamp INTEGER NOT NULL,
                interface_name TEXT NOT NULL,
                upload_avg REAL NOT NULL,
                download_avg REAL NOT NULL,
                upload_max REAL NOT NULL,
                download_max REAL NOT NULL,
                PRIMARY KEY (timestamp, interface_name)
            );
            CREATE INDEX idx_minute_interface_timestamp ON {constants.data.SPEED_TABLE_MINUTE} (interface_name, timestamp DESC);

            CREATE TABLE {constants.data.SPEED_TABLE_HOUR} (
                timestamp INTEGER NOT NULL,
                interface_name TEXT NOT NULL,
                upload_avg REAL NOT NULL,
                download_avg REAL NOT NULL,
                upload_max REAL NOT NULL,
                download_max REAL NOT NULL,
                PRIMARY KEY (timestamp, interface_name)
            );
            CREATE INDEX idx_hour_interface_timestamp ON {constants.data.SPEED_TABLE_HOUR} (interface_name, timestamp DESC);
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
                f"INSERT OR IGNORE INTO {constants.data.SPEED_TABLE_RAW} (timestamp, interface_name, upload_bytes_sec, download_bytes_sec) VALUES (?, ?, ?, ?)",
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
        
        cursor.execute(f"""
            INSERT OR IGNORE INTO {constants.data.SPEED_TABLE_MINUTE} (timestamp, interface_name, upload_avg, download_avg, upload_max, download_max)
            SELECT
                (timestamp / 60) * 60 AS minute_timestamp,
                interface_name,
                AVG(upload_bytes_sec),
                AVG(download_bytes_sec),
                MAX(upload_bytes_sec),
                MAX(download_bytes_sec)
            FROM {constants.data.SPEED_TABLE_RAW}
            WHERE timestamp < ?
            GROUP BY minute_timestamp, interface_name
        """, (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Aggregated %d per-minute records.", cursor.rowcount)
        
        cursor.execute(f"DELETE FROM {constants.data.SPEED_TABLE_RAW} WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Pruned %d raw records after aggregation.", cursor.rowcount)


    def _aggregate_minute_to_hour(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        """Aggregates per-minute data older than 30 days into per-hour averages/maxes."""
        cutoff = int((now - timedelta(days=30)).timestamp())
        self.logger.debug("Aggregating minute data older than %s...", datetime.fromtimestamp(cutoff))

        cursor.execute(f"""
            INSERT OR IGNORE INTO {constants.data.SPEED_TABLE_HOUR} (timestamp, interface_name, upload_avg, download_avg, upload_max, download_max)
            SELECT
                (timestamp / 3600) * 3600 AS hour_timestamp,
                interface_name,
                AVG(upload_avg),
                AVG(download_avg),
                MAX(upload_max),
                MAX(download_max)
            FROM {constants.data.SPEED_TABLE_MINUTE}
            WHERE timestamp < ?
            GROUP BY hour_timestamp, interface_name
        """, (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Aggregated %d per-hour records.", cursor.rowcount)

        cursor.execute(f"DELETE FROM {constants.data.SPEED_TABLE_MINUTE} WHERE timestamp < ?", (cutoff,))
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
                
        # 1. (HIGHEST PRIORITY) Check if a scheduled prune is due to be executed.
        if prune_scheduled_at_ts and prune_scheduled_at_ts <= int(now.timestamp()):
            cursor.execute("SELECT value FROM metadata WHERE key = 'pending_retention_days'")
            row = cursor.fetchone()
            
            if row:
                final_retention_days = int(row[0])
                self.logger.info("Grace period expired. Pruning data older than %d days.", final_retention_days)
                
                cutoff = int((now - timedelta(days=final_retention_days)).timestamp())
                cursor.execute(f"DELETE FROM {constants.data.SPEED_TABLE_HOUR} WHERE timestamp < ?", (cutoff,))
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
        cursor.execute(f"DELETE FROM {constants.data.SPEED_TABLE_HOUR} WHERE timestamp < ?", (cutoff,))
        return cursor.rowcount > 0


    def _reconnect(self) -> None:
        """Closes and re-opens the database connection."""
        self._close_connection()
        self.msleep(1000)
        self._initialize_connection()
        self.logger.info("Database reconnected.")
