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

    _DB_VERSION = 5  # Covering indexes, metadata tracking, eager aggregation, sample_count, hardware stats

    def __init__(self, db_path: Path, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._queue: Deque[Tuple[str, Any]] = deque()
        self._stop_event = threading.Event()
        self.logger = logging.getLogger(f"NetSpeedTray.{self.__class__.__name__}")


    def run(self) -> None:
        """The main event loop for the database thread with retry logic."""
        max_retries = 5
        base_delay = constants.timers.MINIMUM_INTERVAL_MS / 1000.0 # Use sane base
        max_delay = 30.0

        initialized = False
        for attempt in range(max_retries):
            try:
                self._initialize_connection()
                self._check_and_create_schema()
                initialized = True
                break
            except sqlite3.Error as e:
                delay = min(max_delay, base_delay * (2 ** attempt))
                self.logger.error("Database initialization attempt %d failed: %s. Retrying in %.2fs...", attempt + 1, e, delay)
                if attempt < max_retries - 1:
                    self.msleep(int(delay * 1000))
        
        if not initialized:
            self.logger.critical("Database initialization failed after %d attempts.", max_retries)
            self.error.emit(f"Critical: Database initialization failed.")
            return

        self.logger.debug("Database worker thread started successfully.")
        while not self._stop_event.is_set():
            if self._queue:
                task, data = self._queue.popleft()
                try:
                    self._execute_task(task, data)
                except sqlite3.Error as e:
                    self.logger.error("Database error during task execution: %s", e)
                    if "closed" in str(e).lower() or "database is locked" in str(e).lower():
                        self._reconnect()
            else:
                self.msleep(100)

        self._close_connection()


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
            "persist_hardware": self._persist_hardware_batch,
            "maintenance": self._run_maintenance,
        }
        handler = handlers.get(task)
        if handler:
            try:
                if task == "maintenance" and isinstance(data, tuple) and len(data) == 2:
                    config, now_override = data
                    handler(config, now=now_override)
                else:
                    handler(data)
            except sqlite3.Error as e:
                self.logger.error("Database error executing task '%s': %s", task, e)
                self.error.emit(f"Database error: {e}")
        else:
            self.logger.warning("Unknown database task requested: %s", task)


    def _initialize_connection(self) -> None:
        """Establishes the SQLite connection and sets PRAGMAs for performance."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=10, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA busy_timeout = 5000;")


    def _close_connection(self) -> None:
        """Commits any final changes and closes the database connection."""
        if self.conn:
            self.conn.commit()
            self.conn.close()
            self.conn = None


    def _get_current_db_version(self) -> int:
        """Retrieves the current database version, returning 0 if not found/invalid."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = 'db_version'")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except: return 0


    def _backup_database(self) -> bool:
        """Backs up the current database file before critical operations."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.db_path.with_suffix(f".db.bak.v{self._get_current_db_version()}_{timestamp}")
            self.logger.info("Backing up database to: %s", backup_path)
            shutil.copy2(self.db_path, backup_path)
            return True
        except: return False


    def _migrate_schema(self, current_version: int) -> None:
        """Handles migration from current_version to _DB_VERSION."""
        self.logger.info("Migrating database from version %d to %d...", current_version, self._DB_VERSION)
        
        self._backup_database()

        try:
            for ver in range(current_version, self._DB_VERSION):
                next_ver = ver + 1
                migration_method_name = f"_migrate_v{ver}_to_v{next_ver}"
                migration_method = getattr(self, migration_method_name, None)
                
                if migration_method:
                     self.logger.info("Running migration: %s", migration_method_name)
                     migration_method(self.conn.cursor())
                else:
                     self.logger.warning("No migration method found for v%d -> v%d. Updating version number only.", ver, next_ver)

                self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('db_version', ?)", (str(next_ver),))
                self.conn.commit()
                self.logger.info("Successfully migrated to version %d.", next_ver)

        except Exception:
            self.conn.rollback()
            raise 

    def _migrate_v4_to_v5(self, cursor: sqlite3.Cursor) -> None:
        """Migration v4 to v5: Add hardware stats tables."""
        self.logger.info("Executing v4->v5 migration: Adding hardware stats tables.")
        cursor.executescript(f"""
            CREATE TABLE IF NOT EXISTS {constants.data.HARDWARE_STATS_TABLE_RAW} (
                timestamp INTEGER NOT NULL,
                stat_type TEXT NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (timestamp, stat_type)
            );
            CREATE INDEX IF NOT EXISTS idx_hw_raw_timestamp ON {constants.data.HARDWARE_STATS_TABLE_RAW} (timestamp DESC);

            CREATE TABLE IF NOT EXISTS {constants.data.HARDWARE_STATS_TABLE_MINUTE} (
                timestamp INTEGER NOT NULL,
                stat_type TEXT NOT NULL,
                avg_value REAL NOT NULL,
                max_value REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                PRIMARY KEY (timestamp, stat_type)
            );
            CREATE INDEX IF NOT EXISTS idx_hw_minute_timestamp ON {constants.data.HARDWARE_STATS_TABLE_MINUTE} (timestamp DESC);
        """)

    def _migrate_v3_to_v4(self, cursor: sqlite3.Cursor) -> None:
        """
        Migration from v3 to v4:
        - Add sample_count column to aggregated tables to allow accurate averaging and bandwidth calculation.
        """
        self.logger.info("Executing v3->v4 migration: Adding sample_count column.")
        
        # Add sample_count column to minute and hour tables
        # Using DEFAULT 1 ensures existing data is treated as representing 1 second/minute respectively
        # (though this is an approximation for legacy data, it's safer than NULL).
        try:
            cursor.execute(f"ALTER TABLE {constants.data.SPEED_TABLE_MINUTE} ADD COLUMN sample_count INTEGER NOT NULL DEFAULT 1")
            cursor.execute(f"ALTER TABLE {constants.data.SPEED_TABLE_HOUR} ADD COLUMN sample_count INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                self.logger.warning("sample_count column already exists.")
            else:
                raise

    def _migrate_v2_to_v3(self, cursor: sqlite3.Cursor) -> None:
        """
        Migration from v2 to v3:
        - Drop old simple indexes, add covering indexes for graph queries
        - Add created_at metadata
        - Create total_bandwidth table if missing
        """
        self.logger.info("Executing v2->v3 migration: Adding covering indexes and metadata.")
        
        # Drop old indexes (they may not exist, hence IF EXISTS)
        cursor.execute("DROP INDEX IF EXISTS idx_minute_interface_timestamp")
        cursor.execute("DROP INDEX IF EXISTS idx_minute_timestamp")
        cursor.execute("DROP INDEX IF EXISTS idx_hour_interface_timestamp")
        cursor.execute("DROP INDEX IF EXISTS idx_hour_timestamp")
        
        # Create covering indexes
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_minute_covering ON {constants.data.SPEED_TABLE_MINUTE} 
            (timestamp DESC, interface_name, upload_avg, download_avg)
        """)
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_hour_covering ON {constants.data.SPEED_TABLE_HOUR} 
            (timestamp DESC, interface_name, upload_avg, download_avg)
        """)
        
        # Add created_at metadata if missing
        now_ts = int(datetime.now().timestamp())
        cursor.execute("INSERT OR IGNORE INTO metadata (key, value) VALUES ('created_at', ?)", (str(now_ts),))
        
        # Create total_bandwidth table if missing
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {constants.data.BANDWIDTH_TABLE} (
                interface_name TEXT PRIMARY KEY,
                total_upload_bytes REAL NOT NULL DEFAULT 0,
                total_download_bytes REAL NOT NULL DEFAULT 0
            )
        """)


    def _check_and_create_schema(self) -> None:
        """Checks/creates schema."""
        current_version = self._get_current_db_version()

        if current_version == self._DB_VERSION:
            self.logger.debug("Database schema is up to date (Version %d).", self._DB_VERSION)
            return

        if current_version > 0:
             self.logger.info("Database version mismatch (Current: %d, Target: %d). Attempting migration...", current_version, self._DB_VERSION)
             try:
                 self._migrate_schema(current_version)
                 return
             except Exception as e:
                 self.logger.error("Migration failed: %s", e)
        
        # Build fresh
        cursor = self.conn.cursor()
        self.logger.info("Building fresh database schema (Version %d)...", self._DB_VERSION)
        
        self.logger.info("Dropping old tables...")
        cursor.execute("PRAGMA foreign_keys = OFF;")
        for table in [constants.data.SPEED_TABLE_RAW, constants.data.SPEED_TABLE_MINUTE, 
                      constants.data.SPEED_TABLE_HOUR, constants.data.BANDWIDTH_TABLE,
                      constants.data.HARDWARE_STATS_TABLE_RAW, constants.data.HARDWARE_STATS_TABLE_MINUTE]:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
        cursor.execute("DROP TABLE IF EXISTS metadata")
        cursor.execute("PRAGMA foreign_keys = ON;")

        now_ts = int(datetime.now().timestamp())
        self.logger.info("Creating new database schema (Version %d)...", self._DB_VERSION)
        cursor.executescript(f"""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata (key, value) VALUES ('db_version', '{self._DB_VERSION}');
            INSERT INTO metadata (key, value) VALUES ('created_at', '{now_ts}');

            CREATE TABLE {constants.data.SPEED_TABLE_RAW} (
                timestamp INTEGER NOT NULL, interface_name TEXT NOT NULL,
                upload_bytes_sec REAL NOT NULL, download_bytes_sec REAL NOT NULL,
                PRIMARY KEY (timestamp, interface_name)
            );
            CREATE INDEX idx_raw_timestamp ON {constants.data.SPEED_TABLE_RAW} (timestamp DESC);

            CREATE TABLE {constants.data.SPEED_TABLE_MINUTE} (
                timestamp INTEGER NOT NULL, interface_name TEXT NOT NULL,
                upload_avg REAL NOT NULL, download_avg REAL NOT NULL,
                upload_max REAL NOT NULL, download_max REAL NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (timestamp, interface_name)
            );
            CREATE INDEX idx_minute_covering ON {constants.data.SPEED_TABLE_MINUTE} (timestamp DESC, interface_name, upload_avg, download_avg);

            CREATE TABLE {constants.data.SPEED_TABLE_HOUR} (
                timestamp INTEGER NOT NULL, interface_name TEXT NOT NULL,
                upload_avg REAL NOT NULL, download_avg REAL NOT NULL,
                upload_max REAL NOT NULL, download_max REAL NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (timestamp, interface_name)
            );
            CREATE INDEX idx_hour_covering ON {constants.data.SPEED_TABLE_HOUR} (timestamp DESC, interface_name, upload_avg, download_avg);

            CREATE TABLE {constants.data.BANDWIDTH_TABLE} (
                interface_name TEXT PRIMARY KEY,
                total_upload_bytes REAL NOT NULL DEFAULT 0,
                total_download_bytes REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE {constants.data.HARDWARE_STATS_TABLE_RAW} (
                timestamp INTEGER NOT NULL, stat_type TEXT NOT NULL, value REAL NOT NULL,
                PRIMARY KEY (timestamp, stat_type)
            );
            CREATE INDEX idx_hw_raw_timestamp ON {constants.data.HARDWARE_STATS_TABLE_RAW} (timestamp DESC);

            CREATE TABLE {constants.data.HARDWARE_STATS_TABLE_MINUTE} (
                timestamp INTEGER NOT NULL, stat_type TEXT NOT NULL,
                avg_value REAL NOT NULL, max_value REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                PRIMARY KEY (timestamp, stat_type)
            );
            CREATE INDEX idx_hw_minute_timestamp ON {constants.data.HARDWARE_STATS_TABLE_MINUTE} (timestamp DESC);
        """)
        self.conn.commit()
        self.logger.info("New database schema created successfully.")


    def _persist_speed_batch(self, batch: List[Tuple[int, str, float, float]]) -> None:
        """Persists network speed data."""
        if not batch or self.conn is None: return
        
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
            self.logger.error("Failed to persist speed batch: %s", e)
            self.conn.rollback()


    def _persist_hardware_batch(self, batch: List[Tuple[int, str, float]]) -> None:
        """Persists hardware utilization data."""
        if not batch or self.conn is None: return
        self.logger.debug("Persisting batch of %d hardware records...", len(batch))
        cursor = self.conn.cursor()
        try:
            cursor.executemany(
                f"INSERT OR IGNORE INTO {constants.data.HARDWARE_STATS_TABLE_RAW} (timestamp, stat_type, value) VALUES (?, ?, ?)",
                batch
            )
            self.conn.commit()
            self.database_updated.emit()
        except sqlite3.Error as e:
            self.logger.error("Failed to persist hardware batch: %s", e)
            self.conn.rollback()


    def _run_maintenance(self, data: Dict[str, Any], now: Optional[datetime] = None) -> None:
        """Runs maintenance."""
        if self.conn is None: return
        
        config = data
        _now = now or datetime.now()
        
        self.logger.info("Starting periodic database maintenance...")
        cursor = self.conn.cursor()
        try:
            self._aggregate_raw_to_minute(cursor, _now)
            self._aggregate_minute_to_hour(cursor, _now)
            self._aggregate_hardware_raw_to_minute(cursor, _now)
            pruned = self._prune_data_with_grace_period(cursor, config, _now)
            self._prune_hardware_data(cursor, _now)
            
            self.conn.commit()
            
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_maintenance_at', ?)", (str(int(_now.timestamp())),))
            self.conn.commit()
            
            self.logger.info("Database maintenance tasks committed successfully.")
            
            if pruned:
                self.logger.info("Significant data pruned, running VACUUM...")
                self.conn.execute("VACUUM;")
                self.logger.info("VACUUM complete.")

            self.database_updated.emit()
        except sqlite3.Error as e:
            self.logger.error("Maintenance failed: %s", e)
            self.conn.rollback()


    def _aggregate_hardware_raw_to_minute(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        """Aggregates hardware stats older than 24 hours."""
        cutoff = int((now - timedelta(hours=24)).timestamp())
        self.logger.debug("Aggregating raw hardware data older than %s...", datetime.fromtimestamp(cutoff))
        cursor.execute(f"""
            INSERT OR IGNORE INTO {constants.data.HARDWARE_STATS_TABLE_MINUTE} (timestamp, stat_type, avg_value, max_value, sample_count)
            SELECT (timestamp / 60) * 60, stat_type, AVG(value), MAX(value), COUNT(*)
            FROM {constants.data.HARDWARE_STATS_TABLE_RAW}
            WHERE timestamp < ?
            GROUP BY (timestamp / 60) * 60, stat_type
        """, (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Aggregated %d per-minute hardware records.", cursor.rowcount)
        cursor.execute(f"DELETE FROM {constants.data.HARDWARE_STATS_TABLE_RAW} WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Pruned %d raw hardware records after aggregation.", cursor.rowcount)


    def _prune_hardware_data(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        """Prunes hardware stats older than 30 days (default for HW)."""
        cutoff = int((now - timedelta(days=30)).timestamp())
        self.logger.debug("Pruning hardware data older than %s...", datetime.fromtimestamp(cutoff))
        cursor.execute(f"DELETE FROM {constants.data.HARDWARE_STATS_TABLE_MINUTE} WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Pruned %d minute hardware records.", cursor.rowcount)


    def _aggregate_raw_to_minute(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        """Aggregates per-second data older than 24 hours into per-minute averages/maxes."""
        cutoff = int((now - timedelta(hours=24)).timestamp())
        self.logger.debug("Aggregating raw data older than %s...", datetime.fromtimestamp(cutoff))
        
        cursor.execute(f"""
            INSERT OR IGNORE INTO {constants.data.SPEED_TABLE_MINUTE} (timestamp, interface_name, upload_avg, download_avg, upload_max, download_max, sample_count)
            SELECT
                (timestamp / 60) * 60 AS minute_timestamp,
                interface_name,
                AVG(upload_bytes_sec),
                AVG(download_bytes_sec),
                MAX(upload_bytes_sec),
                MAX(download_bytes_sec),
                COUNT(*)
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
            INSERT OR IGNORE INTO {constants.data.SPEED_TABLE_HOUR} (timestamp, interface_name, upload_avg, download_avg, upload_max, download_max, sample_count)
            SELECT
                (timestamp / 3600) * 3600 AS hour_timestamp,
                interface_name,
                SUM(upload_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                SUM(download_avg * sample_count) / NULLIF(SUM(sample_count), 0),
                MAX(upload_max),
                MAX(download_max),
                SUM(sample_count)
            FROM {constants.data.SPEED_TABLE_MINUTE}
            WHERE timestamp < ?
            GROUP BY hour_timestamp, interface_name
        """, (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Aggregated %d per-hour records.", cursor.rowcount)

        cursor.execute(f"DELETE FROM {constants.data.SPEED_TABLE_MINUTE} WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Pruned %d minute records after aggregation.", cursor.rowcount)


    def _prune_data_with_grace_period(self, cursor: sqlite3.Cursor, config: Dict[str, Any], now: datetime) -> bool:
        """Prunes speed data."""
        cursor.execute("SELECT value FROM metadata WHERE key = 'current_retention_days'")
        row = cursor.fetchone()
        current_retention_db = int(row[0]) if row else 365
        
        cursor.execute("SELECT value FROM metadata WHERE key = 'prune_scheduled_at'")
        row = cursor.fetchone()
        prune_scheduled_at_ts = int(row[0]) if row else None

        new_retention_config = config.get("keep_data", 365)
                
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
        elif new_retention_config < current_retention_db:
            if prune_scheduled_at_ts is None:
                grace_period_end = int((now + timedelta(hours=48)).timestamp())
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('prune_scheduled_at', ?)", (str(grace_period_end),))
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('pending_retention_days', ?)", (str(new_retention_config),))
                self.logger.info("Retention period reduced. Scheduling data prune in 48 hours.")
            return False
        elif new_retention_config > current_retention_db:
            if prune_scheduled_at_ts is not None:
                cursor.execute("DELETE FROM metadata WHERE key IN ('prune_scheduled_at', 'pending_retention_days')")
                self.logger.info("Retention period increased. Pending data prune has been cancelled.")
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('current_retention_days', ?)", (str(new_retention_config),))
        
        cutoff = int((now - timedelta(days=current_retention_db)).timestamp())
        cursor.execute(f"DELETE FROM {constants.data.SPEED_TABLE_HOUR} WHERE timestamp < ?", (cutoff,))
        return cursor.rowcount > 0


    def _reconnect(self) -> None:
        """Closes and re-opens the database connection."""
        self._close_connection()
        self.msleep(1000)
        self._initialize_connection()
