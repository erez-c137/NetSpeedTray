"""
Database Management Module.

This module houses the `DatabaseWorker` class, which is responsible for all
asynchronous SQLite operations, ensuring the main UI thread remains responsive.
"""

import logging
import queue
import sqlite3
import threading
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

    _DB_VERSION = 7  # Covering indexes, metadata, eager aggregation, sample_count, hardware stats, hardware hourly, usage_counter (data-cap odometer)

    def __init__(self, db_path: Path, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        # A thread-safe blocking queue (H2): the worker sleeps in get() until a task arrives —
        # no 100ms busy-poll, lower latency, near-zero idle CPU. None is a wake-up sentinel.
        self._queue: "queue.Queue[Optional[Tuple[str, Any]]]" = queue.Queue()
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
                self._ensure_indexes()  # idempotent; runs regardless of schema version
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
        # Drain fully before exiting: tasks already enqueued (incl. the final flush/odometer
        # tail) are processed before we honor the stop flag — we only break when the queue is
        # empty AND stop is set. The blocking get() replaces the old 100ms busy-poll.
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            if item is None:
                continue  # wake-up sentinel from stop(); re-check the loop condition
            task, data = item
            try:
                self._execute_task(task, data)
            except sqlite3.Error as e:
                self.logger.error("Database error during task execution: %s", e)
                if "closed" in str(e).lower() or "database is locked" in str(e).lower():
                    self._reconnect()

        self._close_connection()


    def stop(self) -> None:
        """Signals the worker thread to stop (drains pending tasks first) and wakes it."""
        self.logger.debug("Stopping database worker thread...")
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)  # wake the blocking get() so shutdown is prompt
        except Exception:
            pass


    def enqueue_task(self, task: str, data: Any = None) -> None:
        """Adds a task to the worker's queue for asynchronous execution."""
        self._queue.put((task, data))


    def _execute_task(self, task: str, data: Any) -> None:
        """Dispatches a task to the appropriate handler method."""
        if task == "__signal__":
            # H3: a flush barrier. Because the queue is FIFO, every persist enqueued before
            # this signal is already done — so setting the event tells a waiting reader the
            # freshly-flushed rows are now in the DB (no gap at the graph's right edge).
            try:
                data.set()
            except Exception:
                pass
            return
        handlers = {
            "persist_speed": self._persist_speed_batch,
            "persist_hardware": self._persist_hardware_batch,
            "persist_usage": self._persist_usage,
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
        # WAL alone leaves synchronous=FULL (an fsync per commit). NORMAL is the documented,
        # crash-safe pairing for WAL and is much cheaper for our once-a-second writes. A larger
        # negative cache_size = ~8 MB page cache keeps the hot tiers in memory.
        self.conn.execute("PRAGMA synchronous = NORMAL;")
        self.conn.execute("PRAGMA cache_size = -8000;")
        # Keep GROUP BY / temp B-trees (rollup aggregation + the Monitor's nested-GROUP-BY history
        # queries) in RAM instead of spilling to disk, and memory-map the (small) DB to cut read
        # syscalls on the repeated Monitor scans. mmap_size is a ceiling, not an allocation.
        self.conn.execute("PRAGMA temp_store = MEMORY;")
        self.conn.execute("PRAGMA mmap_size = 268435456;")  # 256 MB; the DB is far smaller


    def _ensure_indexes(self) -> None:
        """
        Create performance indexes idempotently on every startup — covers DBs that predate
        them WITHOUT a schema-version bump (CREATE INDEX IF NOT EXISTS is a no-op when present).

        Hardware-history queries are `WHERE stat_type = ? AND timestamp BETWEEN ? AND ? ORDER BY
        timestamp`, but the tables' PK is (timestamp, stat_type) and the only extra index is
        timestamp-only — so every read scans the time range and filters stat_type row-by-row.
        A (stat_type, timestamp) index turns that into a selective seek + ordered range.
        (Speed tables already have covering indexes for the common all-interface aggregate.)
        """
        if not self.conn:
            return
        try:
            for table in (constants.data.HARDWARE_STATS_TABLE_RAW,
                          constants.data.HARDWARE_STATS_TABLE_MINUTE,
                          constants.data.HARDWARE_STATS_TABLE_HOUR):
                self.conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_type_ts ON {table} (stat_type, timestamp);")
            # Per-interface raw-tier reads (single-NIC graph/summaries) are `WHERE timestamp BETWEEN ? AND ?
            # AND interface_name = ?` against the largest table (~24h × 1 row/s/NIC). The only raw index is
            # timestamp-only, so they range-scan then filter the NIC row-by-row. The minute/hour tiers already
            # have (timestamp, interface_name) covering indexes; this closes the same gap on raw.
            raw = constants.data.SPEED_TABLE_RAW
            self.conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{raw}_iface_ts ON {raw} (interface_name, timestamp);")
            self.conn.commit()
        except sqlite3.Error as e:
            self.logger.warning("Could not ensure performance indexes: %s", e)

    def _close_connection(self) -> None:
        """Commits any final changes and closes the database connection."""
        if self.conn:
            self.conn.commit()
            self.conn.close()
            self.conn = None


    def _get_current_db_version(self) -> int:
        """
        Returns the schema version: a positive int for an existing DB, 0 for a genuinely
        new/empty DB (no metadata table, or no db_version row), or -1 (UNKNOWN) if the
        version can't be read for an unexpected reason.

        The 0-vs-(-1) distinction is the M7 data-loss guard: a bare ``return 0`` on any
        error would let a transient read failure (lock, corruption) masquerade as a fresh
        install and trigger the destructive DROP/rebuild path. Callers MUST treat a
        negative result as "do not rebuild — preserve the file."
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='metadata'")
            if cursor.fetchone() is None:
                return 0  # no metadata table → genuinely fresh DB
            cursor.execute("SELECT value FROM metadata WHERE key = 'db_version'")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            # Unexpected read failure — fail closed. Returning -1 keeps the destructive
            # fresh-build path from ever running on a DB whose version we couldn't read.
            self.logger.error("Could not read DB version (treating as UNKNOWN, will not rebuild): %s", e)
            return -1


    def _has_existing_data(self) -> bool:
        """
        True if any speed-history table exists and holds at least one row. Used as a
        final guard so the destructive fresh-build can never run over real user data.
        On any error, conservatively returns True (if we can't be sure, don't wipe).
        """
        try:
            cursor = self.conn.cursor()
            for table in (constants.data.SPEED_TABLE_RAW, constants.data.SPEED_TABLE_MINUTE,
                          constants.data.SPEED_TABLE_HOUR):
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                if cursor.fetchone() is None:
                    continue
                cursor.execute(f"SELECT 1 FROM {table} LIMIT 1")
                if cursor.fetchone() is not None:
                    return True
            return False
        except Exception:
            return True

    def _backup_database(self) -> bool:
        """Backs up the current database file before critical operations."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.db_path.with_suffix(f".db.bak.v{self._get_current_db_version()}_{timestamp}")
            self.logger.info("Backing up database to: %s", backup_path)
            shutil.copy2(self.db_path, backup_path)
            return True
        except Exception as e:
            # Don't swallow silently: the migration's data-loss guard assumes a backup was made, so a
            # disk-full / permission / lock failure here must be visible in the log (and the bak's absence).
            self.logger.error("Pre-migration database backup FAILED: %s", e)
            return False


    def _migrate_schema(self, current_version: int) -> None:
        """Handles migration from current_version to _DB_VERSION."""
        self.logger.info("Migrating database from version %d to %d...", current_version, self._DB_VERSION)

        if not self._backup_database():
            # The per-migration transaction below is still the primary rollback safety for an in-progress
            # failure; surface loudly that the extra .bak net is missing this run (don't abort — a backup
            # failure shouldn't brick the app on an otherwise-valid migration).
            self.logger.warning("Schema migration proceeding WITHOUT a pre-migration backup copy.")

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

    def _migrate_v6_to_v7(self, cursor: sqlite3.Cursor) -> None:
        """Migration v6 to v7: Add the usage_counter odometer table for the data-cap feature."""
        self.logger.info("Executing v6->v7 migration: Adding usage_counter table.")
        cursor.executescript(f"""
            CREATE TABLE IF NOT EXISTS {constants.data.USAGE_COUNTER_TABLE} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cumulative_up REAL NOT NULL DEFAULT 0,
                cumulative_down REAL NOT NULL DEFAULT 0,
                anchor_up REAL NOT NULL DEFAULT 0,
                anchor_down REAL NOT NULL DEFAULT 0,
                period_key TEXT NOT NULL DEFAULT '',
                updated_ts INTEGER NOT NULL DEFAULT 0
            );
        """)

    def _migrate_v5_to_v6(self, cursor: sqlite3.Cursor) -> None:
        """Migration v5 to v6: Add hardware_stats_hour table for long-term hardware history."""
        self.logger.info("Executing v5->v6 migration: Adding hardware_stats_hour table.")
        cursor.executescript(f"""
            CREATE TABLE IF NOT EXISTS {constants.data.HARDWARE_STATS_TABLE_HOUR} (
                timestamp INTEGER NOT NULL,
                stat_type TEXT NOT NULL,
                avg_value REAL NOT NULL,
                max_value REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                PRIMARY KEY (timestamp, stat_type)
            );
            CREATE INDEX IF NOT EXISTS idx_hw_hour_timestamp ON {constants.data.HARDWARE_STATS_TABLE_HOUR} (timestamp DESC);
        """)

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

        if current_version < 0:
            # M7: version read failed unexpectedly. Never rebuild — preserve the file and
            # degrade gracefully. A retry on next launch may succeed once the lock clears.
            self.logger.error("DB version is UNKNOWN; refusing to migrate or rebuild — preserving the database as-is.")
            return

        if current_version > 0:
             self.logger.info("Database version mismatch (Current: %d, Target: %d). Attempting migration...", current_version, self._DB_VERSION)
             try:
                 self._migrate_schema(current_version)
                 return
             except Exception as e:
                 # CRITICAL data-loss guard: NEVER fall through to the DROP/fresh-build
                 # path on an existing database — that would silently wipe the user's
                 # history. A backup was made at migration start; keep the file as-is and
                 # continue. The app degrades gracefully rather than destroying data.
                 self.logger.error("Migration failed; preserving existing database (no rebuild). Error: %s", e, exc_info=True)
                 return

        # current_version == 0: a brand-new/empty database. Final safety net — never run
        # the destructive build if real data is somehow present (e.g. a version-read glitch
        # that returned 0 for a populated DB). Losing history is never acceptable.
        if self._has_existing_data():
            self.logger.error("Schema version read as new (0) but speed-history data exists; refusing to rebuild — preserving data.")
            return

        # Build fresh
        cursor = self.conn.cursor()
        self.logger.info("Building fresh database schema (Version %d)...", self._DB_VERSION)

        self.logger.info("Dropping old tables...")
        cursor.execute("PRAGMA foreign_keys = OFF;")
        for table in [constants.data.SPEED_TABLE_RAW, constants.data.SPEED_TABLE_MINUTE,
                      constants.data.SPEED_TABLE_HOUR, constants.data.BANDWIDTH_TABLE,
                      constants.data.HARDWARE_STATS_TABLE_RAW, constants.data.HARDWARE_STATS_TABLE_MINUTE,
                      constants.data.HARDWARE_STATS_TABLE_HOUR, constants.data.USAGE_COUNTER_TABLE]:
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

            CREATE TABLE {constants.data.USAGE_COUNTER_TABLE} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cumulative_up REAL NOT NULL DEFAULT 0,
                cumulative_down REAL NOT NULL DEFAULT 0,
                anchor_up REAL NOT NULL DEFAULT 0,
                anchor_down REAL NOT NULL DEFAULT 0,
                period_key TEXT NOT NULL DEFAULT '',
                updated_ts INTEGER NOT NULL DEFAULT 0
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

            CREATE TABLE {constants.data.HARDWARE_STATS_TABLE_HOUR} (
                timestamp INTEGER NOT NULL, stat_type TEXT NOT NULL,
                avg_value REAL NOT NULL, max_value REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                PRIMARY KEY (timestamp, stat_type)
            );
            CREATE INDEX idx_hw_hour_timestamp ON {constants.data.HARDWARE_STATS_TABLE_HOUR} (timestamp DESC);
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


    def _persist_usage(self, data: Tuple[float, float, float, float, str, int]) -> None:
        """
        Upsert the single-row data-usage odometer. `data` is
        (cumulative_up, cumulative_down, anchor_up, anchor_down, period_key, updated_ts).
        """
        if data is None or self.conn is None:
            return
        cumulative_up, cumulative_down, anchor_up, anchor_down, period_key, updated_ts = data
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"""INSERT INTO {constants.data.USAGE_COUNTER_TABLE}
                    (id, cumulative_up, cumulative_down, anchor_up, anchor_down, period_key, updated_ts)
                    VALUES (1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        cumulative_up=excluded.cumulative_up,
                        cumulative_down=excluded.cumulative_down,
                        anchor_up=excluded.anchor_up,
                        anchor_down=excluded.anchor_down,
                        period_key=excluded.period_key,
                        updated_ts=excluded.updated_ts""",
                (cumulative_up, cumulative_down, anchor_up, anchor_down, period_key, updated_ts),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            self.logger.error("Failed to persist usage counter: %s", e)
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
            self._aggregate_hardware_minute_to_hour(cursor, _now)
            pruned = self._prune_data_with_grace_period(cursor, config, _now)
            self._prune_hardware_data(cursor, config, _now)
            
            self.conn.commit()
            
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_maintenance_at', ?)", (str(int(_now.timestamp())),))
            self.conn.commit()
            
            self.logger.info("Database maintenance tasks committed successfully.")

            # Bound the WAL file: long-lived reader connections (the Monitor's graph worker) can hold
            # back the automatic checkpoint, letting -wal grow across a long session. A TRUNCATE
            # checkpoint each maintenance pass reclaims it. Own try-block — a busy checkpoint (a reader
            # blocking it) is harmless and must not roll back the maintenance already committed above.
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except sqlite3.Error as e:
                self.logger.debug("WAL checkpoint skipped: %s", e)
            
            if pruned:
                # VACUUM is a full-DB rewrite under a write lock. On a long-running install the
                # rolling 24h/30d/1yr windows mean `pruned` is ~always truthy, so this ran every
                # maintenance cycle (hourly). Gate it to at most once/day AND only when there's
                # real fragmentation to reclaim (M2 + audit #25). Its own try: a VACUUM failure
                # must not roll back the pruning that already committed above.
                try:
                    row = cursor.execute("SELECT value FROM metadata WHERE key='last_vacuum_at'").fetchone()
                    last_vac = int(row[0]) if (row and str(row[0]).isdigit()) else 0
                    if int(_now.timestamp()) - last_vac >= 86400:
                        free_row = cursor.execute("PRAGMA freelist_count").fetchone()
                        free_pages = int(free_row[0]) if free_row else 0
                        if free_pages >= 1000:  # ~4 MB of slack — below that it's not worth it
                            self.logger.info("Running VACUUM (%d free pages, >= 1 day since last)...", free_pages)
                            self.conn.execute("VACUUM;")
                            self.conn.execute(
                                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_vacuum_at', ?)",
                                (str(int(_now.timestamp())),))
                            self.conn.commit()
                            self.logger.info("VACUUM complete.")
                        else:
                            self.logger.debug("Skipping VACUUM — only %d free pages.", free_pages)
                except sqlite3.Error as e:
                    self.logger.warning("VACUUM skipped (maintenance already committed): %s", e)

            self.database_updated.emit()
        except sqlite3.Error as e:
            self.logger.error("Maintenance failed: %s", e)
            self.conn.rollback()


    def _aggregate_hardware_raw_to_minute(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        """Aggregates hardware stats older than 24 hours."""
        cutoff = self._bucket_floored_cutoff(now, timedelta(hours=24), 60)
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


    def _aggregate_hardware_minute_to_hour(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        """Aggregates per-minute hardware stats older than 30 days into per-hour records."""
        cutoff = self._bucket_floored_cutoff(now, timedelta(days=30), 3600)
        self.logger.debug("Aggregating minute hardware data older than %s...", datetime.fromtimestamp(cutoff))
        # OR IGNORE (not REPLACE): with a floored cutoff each hour bucket is rolled up exactly once when
        # complete, so there is never a conflicting row to replace — and IGNORE can't overwrite a full
        # bucket with a partial remainder if maintenance ever re-runs. Matches the speed-table path.
        cursor.execute(f"""
            INSERT OR IGNORE INTO {constants.data.HARDWARE_STATS_TABLE_HOUR} (timestamp, stat_type, avg_value, max_value, sample_count)
            SELECT
                (timestamp / 3600) * 3600 AS hour_timestamp,
                stat_type,
                SUM(avg_value * sample_count) / NULLIF(SUM(sample_count), 0),
                MAX(max_value),
                SUM(sample_count)
            FROM {constants.data.HARDWARE_STATS_TABLE_MINUTE}
            WHERE timestamp < ?
            GROUP BY hour_timestamp, stat_type
        """, (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Aggregated %d per-hour hardware records.", cursor.rowcount)
        cursor.execute(f"DELETE FROM {constants.data.HARDWARE_STATS_TABLE_MINUTE} WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Pruned %d minute hardware records after aggregation.", cursor.rowcount)

    @staticmethod
    def _retention_cutoff(now: datetime, retention_days: float) -> int:
        """Unix-seconds cutoff ``retention_days`` before ``now``, computed arithmetically and floored at
        0. Computing it as ``now - timedelta(days=retention_days)`` then ``.timestamp()`` CRASHES on
        Windows when retention is large (the 36500-day "keep forever" option lands in 1926, and
        ``datetime.timestamp()`` raises OSError(22) for pre-1970 dates) — which was killing the DB
        maintenance thread on startup and silently stopping ALL history writes. Arithmetic on the
        already-valid ``now.timestamp()`` avoids ever building a pre-epoch datetime; a negative result
        (retain longer than the epoch) floors to 0, so the DELETE simply removes nothing."""
        try:
            secs = float(now.timestamp()) - float(retention_days) * 86400.0
        except (TypeError, ValueError, OverflowError, OSError):
            return 0
        return max(0, int(secs))

    @staticmethod
    def _bucket_floored_cutoff(now: datetime, delta: timedelta, bucket_seconds: int) -> int:
        """Aggregation cutoff floored DOWN to a ``bucket_seconds`` boundary, so a GROUP BY bucket is only
        ever rolled up once it is COMPLETE.

        With a raw-second cutoff, a bucket straddling it gets aggregated from only its rows ``< cutoff``
        and those raw rows are then DELETEd; the next maintenance pass re-rolls the bucket's remaining
        rows into the same bucket key, which ``INSERT OR IGNORE`` silently drops (and ``OR REPLACE``
        overwrites with only the remainder) — permanently under-counting one bucket per cycle, cascading
        into the hour tier and corrupting the long-term totals the app exports. Flooring guarantees no
        bucket straddles the cutoff (both bucket starts and the cutoff are multiples of ``bucket_seconds``),
        so each bucket is aggregated exactly once, complete. The partially-elapsed boundary bucket simply
        waits in the lower tier until the next cycle, when it too is wholly in the past."""
        return (int((now - delta).timestamp()) // bucket_seconds) * bucket_seconds

    def _prune_hardware_data(self, cursor: sqlite3.Cursor, config: Dict[str, Any], now: datetime) -> None:
        """Prunes hourly hardware stats using the same retention period as speed data."""
        retention_days = config.get("keep_data", 365)
        cutoff = self._retention_cutoff(now, retention_days)
        cursor.execute(f"DELETE FROM {constants.data.HARDWARE_STATS_TABLE_HOUR} WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.info("Pruned %d hourly hardware records older than %d days.", cursor.rowcount, retention_days)


    def _aggregate_raw_to_minute(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        """Aggregates per-second data older than 24 hours into per-minute averages/maxes."""
        cutoff = self._bucket_floored_cutoff(now, timedelta(hours=24), 60)
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
        cutoff = self._bucket_floored_cutoff(now, timedelta(days=30), 3600)
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

                cutoff = self._retention_cutoff(now, final_retention_days)
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
        
        cutoff = self._retention_cutoff(now, current_retention_db)
        cursor.execute(f"DELETE FROM {constants.data.SPEED_TABLE_HOUR} WHERE timestamp < ?", (cutoff,))
        return cursor.rowcount > 0


    def _reconnect(self) -> None:
        """Closes and re-opens the database connection."""
        self._close_connection()
        self.msleep(1000)
        self._initialize_connection()
