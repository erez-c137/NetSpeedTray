"""
The schema-v7 database worker of 2.1.x, frozen for tests (2.2 / v8 step 7).

Since the switch-over the app's ``DatabaseWorker`` writes schema v8 only; a v7 file is migrated on
start and never written. Tests still need REAL v7 files - the migration's inputs, the reader parity
twins, the kill-test fixtures, the golden reader oracle - written by exactly the code 2.1.x users
ran. This is that code, copied verbatim from ``core/database.py`` at e8450a8 (the last v7 commit),
with only the class name, base class and the v7-only ``HISTORY_TABLES_V7`` changed. Do not "fix"
it: it is a fixture generator, and its fidelity to 2.1.x is the point.
"""

import logging
import queue
import re
import sqlite3
import threading
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject

from netspeedtray import constants
from netspeedtray.core.database import DatabaseWorker, MigrationAborted
from netspeedtray.core.db.schema_v8 import EXPECTED_TABLES_V8
from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri

logger = logging.getLogger("NetSpeedTray.Core.Database")


class LegacyV7Worker(DatabaseWorker):
    """The 2.1.x (schema v7) DatabaseWorker, frozen: builds, writes and maintains a v7 file
    exactly as 2.1.5-2.1.7 do. TEST SUPPORT ONLY - 2.2 has no v7 write path."""

    _DB_VERSION = 7  # Covering indexes, metadata, eager aggregation, sample_count, hardware stats, hardware hourly, usage_counter (data-cap odometer)

    # VACUUM gates (all three must hold, on top of the once-a-day interval below):
    #   * the floor - under ~4 MB of slack a full-file rewrite is never worth it;
    #   * the ratio - a rewrite costs the whole file, so the slack must be a real
    #     fraction of it (the measured bad case: ~90 MB with 5.1% free would spend a
    #     1.66 s rewrite peaking at 176 MB to reclaim 4.7 MB);
    #   * OR the cap - past ~50 MB of absolute slack, reclaim regardless of ratio.
    _VACUUM_MIN_INTERVAL_SEC = 86400
    _VACUUM_MIN_FREE_PAGES = 1000     # ~4 MB at the 4096-byte page size
    _VACUUM_MIN_FREE_RATIO = 0.20     # at least a fifth of the file is slack
    _VACUUM_FREE_PAGES_CAP = 12800    # ~50 MB of slack vacuums at any ratio

    def __init__(self, db_path: Path, parent: Optional[QObject] = None) -> None:
        super().__init__(db_path, parent)
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        # A thread-safe blocking queue (H2): the worker sleeps in get() until a task arrives -
        # no 100ms busy-poll, lower latency, near-zero idle CPU. None is a wake-up sentinel.
        self._queue: "queue.Queue[Optional[Tuple[str, Any]]]" = queue.Queue()
        self._stop_event = threading.Event()
        # Set when the file on disk was written by a NEWER build than this one. Every write
        # path is then refused - see _check_and_create_schema for why silence is not an option.
        self._schema_incompatible = False
        self._downgrade_warned = False
        # Compaction owed after a "Delete all history" whose VACUUM / WAL checkpoint couldn't finish.
        self._compact_pending = False
        self._vacuum_pending = False
        self._last_compact_attempt = 0.0
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
                if not self._schema_incompatible:
                    # Skipped on a downgrade: CREATE INDEX against a newer schema either errors
                    # (a compatibility view cannot be indexed) or writes to a file this build
                    # does not understand. Both are exactly what the guard exists to prevent.
                    self._ensure_indexes()  # idempotent; runs regardless of schema version
                    self._resume_pending_compaction()
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
        # tail) are processed before we honor the stop flag - we only break when the queue is
        # empty AND stop is set. The blocking get() replaces the old 100ms busy-poll.
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                if self._compact_pending:
                    self._retry_pending_compaction()   # throttled; idle ticks only
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






    def _execute_task(self, task: str, data: Any) -> None:
        """Dispatches a task to the appropriate handler method."""
        if task == "__signal__":
            # H3: a flush barrier. Because the queue is FIFO, every persist enqueued before
            # this signal is already done - so setting the event tells a waiting reader the
            # freshly-flushed rows are now in the DB (no gap at the graph's right edge).
            try:
                data.set()
            except Exception:
                pass
            return
        if self._schema_incompatible:
            # Downgrade: every task below writes (maintenance DELETEs and aggregates). Drop them
            # rather than letting them fail into a swallowed OperationalError. Warn once - this
            # fires per persist tick, and #263 was a 137,000-line flood from exactly this shape.
            if not self._downgrade_warned:
                self._downgrade_warned = True
                self.logger.warning(
                    "Refusing database task '%s' and all further writes: the file was written by a "
                    "newer version of NetSpeedTray. This is logged once per session.", task,
                )
            if task == "delete_history":
                self.history_deleted.emit(self.DELETE_FAILED)   # the user is waiting on an answer
            return

        handlers = {
            "persist_speed": self._persist_speed_batch,
            "persist_hardware": self._persist_hardware_batch,
            "persist_usage": self._persist_usage,
            "maintenance": self._run_maintenance,
            "delete_history": self._delete_history,
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
        Create performance indexes idempotently on every startup - covers DBs that predate
        them WITHOUT a schema-version bump (CREATE INDEX IF NOT EXISTS is a no-op when present).

        Hardware-history queries are `WHERE stat_type = ? AND timestamp BETWEEN ? AND ? ORDER BY
        timestamp`, but the tables' PK is (timestamp, stat_type) and the only extra index is
        timestamp-only - so every read scans the time range and filters stat_type row-by-row.
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






















    def _check_and_create_schema(self) -> None:
        """Checks/creates schema."""
        current_version = self._get_current_db_version()

        if current_version == self._DB_VERSION:
            self.logger.debug("Database schema is up to date (Version %d).", self._DB_VERSION)
            return

        if current_version < 0:
            # M7: version read failed unexpectedly. Never rebuild - preserve the file and
            # degrade gracefully. A retry on next launch may succeed once the lock clears.
            self.logger.error("DB version is UNKNOWN; refusing to migrate or rebuild - preserving the database as-is.")
            return

        if current_version > self._DB_VERSION:
            # DOWNGRADE. The file was written by a newer build; this one cannot understand it.
            # There is no migration to run - range(new, old) is empty - so the old code fell
            # through and did two harmful things:
            #   1. _migrate_schema() still ran _backup_database() unconditionally, writing a
            #      full copy of the database on EVERY launch, which nothing ever deletes.
            #   2. Reads kept working (a newer schema may expose compatibility views), while
            #      every write raised OperationalError - a sqlite3.Error subclass that the
            #      handlers below swallow. The app looked healthy and silently recorded nothing.
            # Refusing outright is the only honest option: no backup, no writes, no maintenance.
            self._schema_incompatible = True
            newest_backup = self._find_newest_backup()
            if newest_backup is not None:
                # A verified pre-migration backup is sitting right there - name it by
                # full path and never advise discarding the history it holds.
                self.logger.error(
                    "Database schema v%d was written by a NEWER version of NetSpeedTray than this one "
                    "(which understands v%d). Running READ-ONLY: history will be shown but nothing new "
                    "will be recorded, and no data will be modified or deleted. Upgrade again to resume "
                    "recording. A pre-migration backup of your earlier history exists at: %s - to go "
                    "back to it, follow 'Restoring a backup' in DATABASE.md exactly (renaming it into "
                    "place without deleting the -wal/-shm sidecar files corrupts it).",
                    current_version, self._DB_VERSION, newest_backup,
                )
            else:
                self.logger.error(
                    "Database schema v%d was written by a NEWER version of NetSpeedTray than this one "
                    "(which understands v%d). Running READ-ONLY: history will be shown but nothing new "
                    "will be recorded, and no data will be modified or deleted. Upgrade again to resume "
                    "recording; if you have your own backup copy, see 'Restoring a backup' in "
                    "DATABASE.md. As a last resort, moving %s aside starts a fresh, empty history.",
                    current_version, self._DB_VERSION, self.db_path.name,
                )
            self.schema_incompatible.emit(
                current_version, self._DB_VERSION,
                str(newest_backup) if newest_backup is not None else "",
            )
            return

        if current_version > 0:
             self.logger.info("Database version mismatch (Current: %d, Target: %d). Attempting migration...", current_version, self._DB_VERSION)
             try:
                 self._migrate_schema(current_version, self._DB_VERSION)
                 return
             except MigrationAborted as e:
                 self.logger.error("Migration NOT started: %s. The database is unchanged; it will be "
                                   "retried on the next launch.", e)
                 return
             except Exception as e:
                 # CRITICAL data-loss guard: NEVER fall through to the DROP/fresh-build
                 # path on an existing database - that would silently wipe the user's
                 # history. A backup was made at migration start; keep the file as-is and
                 # continue. The app degrades gracefully rather than destroying data.
                 self.logger.error("Migration failed; preserving existing database (no rebuild). Error: %s", e, exc_info=True)
                 return

        # current_version == 0: a brand-new/empty database. Final safety net - never run
        # the destructive build if real data is somehow present (e.g. a version-read glitch
        # that returned 0 for a populated DB). Losing history is never acceptable.
        if self._has_existing_data():
            self.logger.error("Schema version read as new (0) but speed-history data exists; refusing to rebuild - preserving data.")
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


    def _persist_usage(self, data: Tuple[Any, ...]) -> None:
        """
        Upsert the single-row data-usage odometer. `data` is
        (cumulative_up, cumulative_down, anchor_up, anchor_down, period_key, updated_ts[, snapshot_json]).

        The optional snapshot_json (the NIC counters the cumulative accounts up to, for same-boot
        launch reconciliation) goes to the existing `metadata` key/value table in the SAME
        transaction, so total and snapshot can never disagree. No schema change: metadata has
        existed since v3. None leaves the stored snapshot untouched; "" (usage_snapshot.
        CLEAR_SNAPSHOT) deletes it.
        """
        if data is None or self.conn is None:
            return
        cumulative_up, cumulative_down, anchor_up, anchor_down, period_key, updated_ts = data[:6]
        snapshot_json = data[6] if len(data) > 6 else None
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
            if snapshot_json == "":
                # CLEAR_SNAPSHOT: counters were seen but no snapshot could be encoded - a stale one
                # left next to this advanced total would be re-added at the next same-boot launch.
                cursor.execute("DELETE FROM metadata WHERE key = ?",
                               (constants.network.usage.SNAPSHOT_METADATA_KEY,))
            elif snapshot_json is not None:
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                               (constants.network.usage.SNAPSHOT_METADATA_KEY, snapshot_json))
            self.conn.commit()
        except sqlite3.Error as e:
            self.logger.error("Failed to persist usage counter: %s", e)
            self.conn.rollback()

    def _run_maintenance(self, data: Dict[str, Any], now: Optional[datetime] = None) -> None:
        """Runs maintenance."""
        if self.conn is None: return
        
        config = data
        _now = now or datetime.now()
        
        self.logger.debug("Starting periodic database maintenance...")
        cursor = self.conn.cursor()
        try:
            self._aggregate_raw_to_minute(cursor, _now)
            self._aggregate_minute_to_hour(cursor, _now)
            self._aggregate_hardware_raw_to_minute(cursor, _now)
            self._aggregate_hardware_minute_to_hour(cursor, _now)
            self._prune_data_with_grace_period(cursor, config, _now)
            self._prune_hardware_data(cursor, config, _now)
            
            self.conn.commit()
            
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_maintenance_at', ?)", (str(int(_now.timestamp())),))
            self.conn.commit()
            
            self.logger.debug("Database maintenance tasks committed successfully.")

            # VACUUM is a full-DB rewrite under a write lock. It used to be gated on
            # `if pruned:` - the retention DELETE's rowcount - which matches nothing until
            # the install outlives its retention setting: a year at the default 365, never
            # on "keep everything". Meanwhile the hourly aggregation DELETEs above fragment
            # the file from day one (measured: 84% free pages after two months, VACUUM never
            # run once). So gate on the freelist itself: at most once/day, and only when the
            # slack justifies the rewrite (_vacuum_warranted). Its own try: a VACUUM failure
            # must not roll back the pruning that already committed above.
            try:
                row = cursor.execute("SELECT value FROM metadata WHERE key='last_vacuum_at'").fetchone()
                last_vac = int(row[0]) if (row and str(row[0]).isdigit()) else 0
                if int(_now.timestamp()) - last_vac >= self._VACUUM_MIN_INTERVAL_SEC:
                    free_row = cursor.execute("PRAGMA freelist_count").fetchone()
                    free_pages = int(free_row[0]) if free_row else 0
                    page_row = cursor.execute("PRAGMA page_count").fetchone()
                    page_count = int(page_row[0]) if page_row else 0
                    if self._stop_event.is_set():
                        # stop() drains queued tasks after the flag is set, and cleanup()
                        # waits only 2000 ms for this thread - never start a full-file
                        # rewrite mid-shutdown.
                        self.logger.debug("Skipping VACUUM - shutdown in progress.")
                    elif self._vacuum_warranted(free_pages, page_count):
                        self.logger.info(
                            "Running VACUUM (%d of %d pages free, >= 1 day since last)...",
                            free_pages, page_count)
                        self.conn.execute("VACUUM;")
                        self.conn.execute(
                            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_vacuum_at', ?)",
                            (str(int(_now.timestamp())),))
                        self.conn.commit()
                        self.logger.info("VACUUM complete.")
                    else:
                        self.logger.debug(
                            "Skipping VACUUM - %d free of %d pages is not worth a rewrite.",
                            free_pages, page_count)
            except sqlite3.Error as e:
                self.logger.warning("VACUUM skipped (maintenance already committed): %s", e)

            # Bound the WAL file: long-lived reader connections (the Monitor's graph worker) can hold
            # back the automatic checkpoint, letting -wal grow across a long session - and VACUUM in
            # WAL mode writes the ENTIRE vacuumed database into -wal, so this must run AFTER the
            # VACUUM above or the app leaves a WAL at result size until the next hourly pass. Own
            # try-block - a busy checkpoint (a reader blocking it) is harmless and must not roll back
            # the maintenance already committed above.
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except sqlite3.Error as e:
                self.logger.debug("WAL checkpoint skipped: %s", e)

            self.database_updated.emit()
        except sqlite3.Error as e:
            self.logger.error("Maintenance failed: %s", e)
            self.conn.rollback()


    def _delete_history(self, data: Optional[Dict[str, Any]] = None) -> None:
        """
        User-requested "Delete all history": empty every history tier, then VACUUM.

        Runs on this thread like every other write, so it is serialized with the persist batches -
        WidgetState drops its pending batches and enqueues this under the batch lock, so nothing
        recorded before the request can be written after it, and samples enqueued after it land in
        the emptied tables as normal (the app keeps recording).

        The VACUUM is not optional here: a DELETE only moves pages to the freelist, where the old
        samples stay readable in the file. It bypasses the daily/slack gates of the maintenance
        VACUUM because the user asked for the data to be gone, not for the file to be tidied.

        ``data["include_usage"]`` also clears the data-usage odometer (opt-in; see HISTORY_TABLES).
        """
        include_usage = bool((data or {}).get("include_usage", False))
        if self.conn is None:
            self.history_deleted.emit(self.DELETE_FAILED)
            return
        cursor = self.conn.cursor()
        try:
            present = {r[0] for r in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            tables = [t for t in constants.data.HISTORY_TABLES_V7 if t in present]
            if include_usage and constants.data.USAGE_COUNTER_TABLE in present:
                tables.append(constants.data.USAGE_COUNTER_TABLE)
            for table in tables:
                cursor.execute(f"DELETE FROM {table}")
            self.conn.commit()
        except sqlite3.Error as e:
            self.logger.error("Delete all history failed: %s", e)
            self.conn.rollback()
            self.history_deleted.emit(self.DELETE_FAILED)
            return
        self.logger.info("History deleted (%d tables%s).", len(tables),
                         ", incl. data-usage counter" if include_usage else "")
        vacuum_ok = self._vacuum_now()
        # Only a COMPLETED truncate checkpoint moves the rewritten file out of -wal and over the old
        # pages in the .db. It does not raise when a reader is mid-query - it returns busy=1 - so the
        # result row has to be read, or the old samples silently stay in both files.
        checkpoint_ok = vacuum_ok and self._checkpoint_truncate(constants.timeouts.DB_CHECKPOINT_RETRIES)
        self.database_updated.emit()
        if vacuum_ok and checkpoint_ok:
            self.history_deleted.emit(self.DELETE_DONE)
            return
        self._compact_pending = True
        self._vacuum_pending = not vacuum_ok
        self._last_compact_attempt = time.monotonic()
        self._set_compaction_marker(True)   # survives a quit before the retry lands
        self.logger.warning(
            "History rows deleted, but the file is not compacted yet (%s); old pages may remain in "
            "the database file until it is. Retrying while idle.",
            "VACUUM failed" if not vacuum_ok else "WAL checkpoint busy")
        self.history_deleted.emit(self.DELETE_COMPACT_PENDING)

    def _vacuum_now(self) -> bool:
        """Ungated VACUUM for Delete all history. True on success; a failure is a WARNING."""
        try:
            self.conn.execute("VACUUM;")
            self.conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_vacuum_at', ?)",
                (str(int(time.time())),))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            # The rows are already deleted and committed; until a VACUUM succeeds their pages sit
            # on the freelist, still readable in the file.
            self.logger.warning("VACUUM after history delete failed: %s", e)
            return False


    def _retry_pending_compaction(self, force: bool = False) -> None:
        """Finish a compaction a Delete all history couldn't (run from the idle tick, throttled)."""
        if not self._compact_pending or self.conn is None:
            return
        now = time.monotonic()
        if not force and now - self._last_compact_attempt < constants.timeouts.DB_COMPACT_RETRY_INTERVAL_SEC:
            return
        self._last_compact_attempt = now
        if self._vacuum_pending:
            if not self._vacuum_now():
                return
            self._vacuum_pending = False
        # Clear the restart marker BEFORE the checkpoint, so the checkpoint also folds that write in.
        self._set_compaction_marker(False)
        if self._checkpoint_truncate(1):
            self._compact_pending = False
            self.logger.info("Deferred compaction after history delete completed.")
            self.database_updated.emit()
        else:
            self._set_compaction_marker(True)


    def _set_compaction_marker(self, on: bool) -> None:
        """Persist 'a history delete still owes a VACUUM + checkpoint' in metadata, so a quit before
        the idle retry lands is picked up at the next start (_resume_pending_compaction)."""
        try:
            if on:
                self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, '1')",
                                  (self._COMPACT_MARKER_KEY,))
            else:
                self.conn.execute("DELETE FROM metadata WHERE key = ?", (self._COMPACT_MARKER_KEY,))
            self.conn.commit()
        except sqlite3.Error as e:
            self.logger.debug("Compaction marker not updated: %s", e)

    def _resume_pending_compaction(self) -> None:
        """At startup: a previous session deleted history but quit before the file was compacted."""
        try:
            row = self.conn.execute("SELECT value FROM metadata WHERE key = ?",
                                    (self._COMPACT_MARKER_KEY,)).fetchone()
        except sqlite3.Error:
            return
        if row:
            self.logger.info("Resuming the file compaction a previous history delete could not finish.")
            self._compact_pending = True
            self._vacuum_pending = True    # cheap insurance: the old pages may also be on the freelist
            self._retry_pending_compaction(force=True)


    def _vacuum_warranted(self, free_pages: int, page_count: int) -> bool:
        """
        Whether the freelist justifies a full-file rewrite (see the class constants).

        The floor alone is absolute (~4 MB) with no reference to file size, so it would
        rewrite a 90 MB, 5%-free file for a 4.7 MB return. The ratio conjunct demands the
        slack be a real fraction of the file; the absolute cap makes sure a huge file
        with huge slack is still reclaimed even at a low ratio.
        """
        if free_pages < self._VACUUM_MIN_FREE_PAGES:
            return False
        if free_pages >= self._VACUUM_FREE_PAGES_CAP:
            return True
        return page_count > 0 and (free_pages / page_count) >= self._VACUUM_MIN_FREE_RATIO


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
        if cursor.rowcount > 0: self.logger.debug("Aggregated %d per-minute hardware records.", cursor.rowcount)
        cursor.execute(f"DELETE FROM {constants.data.HARDWARE_STATS_TABLE_RAW} WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.debug("Pruned %d raw hardware records after aggregation.", cursor.rowcount)


    def _aggregate_hardware_minute_to_hour(self, cursor: sqlite3.Cursor, now: datetime) -> None:
        """Aggregates per-minute hardware stats older than 30 days into per-hour records."""
        cutoff = self._bucket_floored_cutoff(now, timedelta(days=30), 3600)
        self.logger.debug("Aggregating minute hardware data older than %s...", datetime.fromtimestamp(cutoff))
        # OR IGNORE (not REPLACE): with a floored cutoff each hour bucket is rolled up exactly once when
        # complete, so there is never a conflicting row to replace - and IGNORE can't overwrite a full
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
        if cursor.rowcount > 0: self.logger.debug("Aggregated %d per-hour hardware records.", cursor.rowcount)
        cursor.execute(f"DELETE FROM {constants.data.HARDWARE_STATS_TABLE_MINUTE} WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.debug("Pruned %d minute hardware records after aggregation.", cursor.rowcount)


    @staticmethod
    def _bucket_floored_cutoff(now: datetime, delta: timedelta, bucket_seconds: int) -> int:
        """Aggregation cutoff floored DOWN to a ``bucket_seconds`` boundary, so a GROUP BY bucket is only
        ever rolled up once it is COMPLETE.

        With a raw-second cutoff, a bucket straddling it gets aggregated from only its rows ``< cutoff``
        and those raw rows are then DELETEd; the next maintenance pass re-rolls the bucket's remaining
        rows into the same bucket key, which ``INSERT OR IGNORE`` silently drops (and ``OR REPLACE``
        overwrites with only the remainder) - permanently under-counting one bucket per cycle, cascading
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
        if cursor.rowcount > 0: self.logger.debug("Aggregated %d per-minute records.", cursor.rowcount)
        
        cursor.execute(f"DELETE FROM {constants.data.SPEED_TABLE_RAW} WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.debug("Pruned %d raw records after aggregation.", cursor.rowcount)


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
        if cursor.rowcount > 0: self.logger.debug("Aggregated %d per-hour records.", cursor.rowcount)

        cursor.execute(f"DELETE FROM {constants.data.SPEED_TABLE_MINUTE} WHERE timestamp < ?", (cutoff,))
        if cursor.rowcount > 0: self.logger.debug("Pruned %d minute records after aggregation.", cursor.rowcount)


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
                self.logger.warning("Scheduled prune was due, but no pending retention period was found. Canceling.")
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
                self.logger.info("Retention period increased. Pending data prune has been canceled.")
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('current_retention_days', ?)", (str(new_retention_config),))
        
        cutoff = self._retention_cutoff(now, current_retention_db)
        cursor.execute(f"DELETE FROM {constants.data.SPEED_TABLE_HOUR} WHERE timestamp < ?", (cutoff,))
        return cursor.rowcount > 0


    def _reconnect(self) -> None:
        """Closes and re-opens the database connection."""
        self._close_connection()
        self.msleep(1000)
        self._initialize_connection()
