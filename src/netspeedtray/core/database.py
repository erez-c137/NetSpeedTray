"""
Database Management Module.

This module houses the `DatabaseWorker` class, the ONE thread that writes the history database.
Everything that changes the file - the v7 -> v8 migration, every persist, maintenance, "Delete all
history" - runs here, so the UI thread never blocks on SQLite and no two writers ever interleave.

**Schema v8 (2.2).** History is written by :class:`~netspeedtray.core.db.writer_v8.WriterV8` on this
thread's connection. On start the worker (in this order, before anything else touches the file):

1. ``migrate_v8.recover()`` - finishes or undoes a migration a kill interrupted. Runs on EVERY launch,
   before any connection to the live name (a plain connect in a half-swapped state would create an
   empty database there).
2. probes the live file's ``db_version`` READ-ONLY;
3. a missing / empty file -> a fresh v8 file (``schema_v8.create_v8_schema`` on the empty file, then
   WAL); a v7 file -> ``migrate_v8.migrate()`` with no connection of ours open (the renamed v7 file IS
   the backup - no ``_backup_database`` copy for this step); a v2-v6 file -> the old in-place chain to
   v7 (verified backup first, as before), then the v8 migration;
4. after a successful migration, one ``WriterV8.run_maintenance()`` (the first polls land inside the
   folded minute and are merged within a tick, not an hour);
5. opens the write connection (autocommit; every unit is an explicit transaction), builds the
   writer on this thread and starts an ``app_sessions`` row.

**Any refusal** - not enough disk, a verification failure, a rename blocked by another program
(``SwapDeferred``), a half-done swap (``SwapIncomplete``), a journal state ``recover()`` will not
guess about (``RecoveryError``), a file it cannot read - leaves the worker READ-ONLY for the session:
no write connection is ever opened, every write task is dropped, readers use ``mode=ro`` connections
(or none, when the live name is absent), and ``migration_deferred`` fires so the widget can show a
calm flyout. Nothing is retried in-session; the next launch tries again. There is no v7 write path in
2.2: a v7 file is never written by this build.
"""

import logging
import os
import queue
import stat
import re
import sqlite3
import threading
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from netspeedtray import constants
from netspeedtray.core.db import migrate_v8
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.db.schema_v8 import EXPECTED_TABLES_V8
from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri

# Logger Setup
logger = logging.getLogger("NetSpeedTray.Core.Database")


class MigrationAborted(Exception):
    """A schema migration refused to start (no verified pre-migration backup). Nothing was changed."""


# Database threads still running when quit gave up waiting (WidgetState.cleanup, DB_SHUTDOWN_WAIT_MS):
# kept referenced so nothing destroys a running QThread (Qt aborts the process - a crash dialog -
# when a QThread is destroyed while running). monitor.py then exits the process hard, which is the
# termination the v8 migration journal and the writer's transactions are kill-tested against.
_DETACHED_WORKERS: List["DatabaseWorker"] = []


def detach_running_worker(worker: "DatabaseWorker") -> None:
    if worker not in _DETACHED_WORKERS:
        _DETACHED_WORKERS.append(worker)


def detached_workers_running() -> bool:
    return any(w.isRunning() for w in _DETACHED_WORKERS)


@contextmanager
def explicit_txn(conn: sqlite3.Connection, log: logging.Logger) -> Iterator[sqlite3.Cursor]:
    """One explicit ``BEGIN IMMEDIATE .. COMMIT`` on the worker connection, for every write that is NOT
    the v8 writer's (odometer, metadata bookkeeping).

    Step-7 note 6: such writes must never interleave with WriterV8's transaction. They cannot - both
    run on the worker thread, one task at a time, the connection is autocommit (no implicit BEGIN can
    stay open between tasks), and every WriterV8 unit commits or rolls back before it returns - and
    this makes the boundary explicit: a transaction found open here is a bug (logged, then committed
    so it can never be folded into this unit), and this unit commits or rolls back before returning,
    whatever is raised."""
    if conn.in_transaction:
        log.warning("A transaction was left open on the worker connection; committing it before an "
                    "odometer/metadata write.")
        conn.execute("COMMIT")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn.cursor()
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


class DatabaseWorker(QThread):
    """
    A dedicated QThread to handle all blocking SQLite database operations,
    ensuring the main UI thread remains responsive at all times.
    """
    error = pyqtSignal(str)
    database_updated = pyqtSignal()
    # Emitted when the file on disk was written by a NEWER build and this worker goes
    # read-only: (file_version, supported_version, newest_backup_full_path or ""). The
    # view surfaces it as a flyout - a log line alone leaves a rolled-back install
    # looking perfectly healthy while recording nothing, forever.
    schema_incompatible = pyqtSignal(int, int, str)
    # Emitted when the v7 -> v8 upgrade of the history file (or the recovery of an interrupted one)
    # could not complete this launch, so this session runs READ-ONLY and retries at the next start.
    # The argument is one of the MIGRATION_* reason codes below. Same flyout machinery as above.
    migration_deferred = pyqtSignal(str)
    # Emitted once, when THIS launch upgraded a v7 history file to v8 and the session is writing it.
    # The view shows one calm "history upgraded" flyout (its own persisted flag keeps it to once).
    history_upgraded = pyqtSignal()
    # Result of a user-requested "Delete all history" - one of the DELETE_* statuses below. Emitted
    # from this thread; WidgetState re-emits it so the Settings dialog hears it on the GUI thread.
    history_deleted = pyqtSignal(str)
    # DELETE_DONE: rows deleted AND scrubbed from the .db and -wal files.
    # DELETE_COMPACT_PENDING: rows deleted (gone from every query) but the WAL checkpoint couldn't
    #   finish yet (a reader mid-query), so old page images may still sit in -wal; the worker keeps
    #   retrying on its idle tick (_retry_pending_compaction).
    # DELETE_FAILED: nothing was deleted.
    DELETE_DONE = "done"
    DELETE_COMPACT_PENDING = "compact_pending"
    DELETE_FAILED = "failed"
    # Rows deleted, but a database backup was KEPT: it belongs to an upgrade that has not finished
    # (removed at a later start, once it has), or it cannot be removed at all (read-only and linked
    # to another file) - the user is told, never shown "done".
    DELETE_BACKUP_KEPT_UPGRADE = "backup_kept_upgrade"
    DELETE_BACKUP_KEPT = "backup_kept"

    # migration_deferred reasons.
    MIGRATION_NO_SPACE = "no_space"                 # InsufficientSpace: free disk < 1.5x the file
    MIGRATION_FILE_IN_USE = "file_in_use"           # SwapDeferred: another program held the file
    MIGRATION_SWAP_INCOMPLETE = "swap_incomplete"   # SwapIncomplete: live name empty until next start
    MIGRATION_RECOVERY = "recovery"                 # recover() refused (RecoveryError) or failed
    MIGRATION_FAILED = "failed"                     # verification failed / any other migration error
    MIGRATION_UNREADABLE = "unreadable"             # the version of the file could not be read
    DB_UNAVAILABLE = "unavailable"                  # the file would not open for writing (read-only
    #                                                 file, a lock held elsewhere, an OSError)

    _DB_VERSION = 8   # v8: bytes at capture, dimension tables, watermarked tiers (core/db/schema_v8.py)
    # Files older than v7 are first brought to v7 by the in-place chain below, then migrated to v8.
    _LEGACY_CHAIN_TARGET = 7

    def __init__(self, db_path: Path, parent: Optional[QObject] = None, *, poll_ms: int = 1000,
                 app_version: Optional[str] = None,
                 is_physical: Optional[Callable[[str], bool]] = None) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        # A thread-safe blocking queue (H2): the worker sleeps in get() until a task arrives -
        # no 100ms busy-poll, lower latency, near-zero idle CPU. None is a wake-up sentinel.
        self._queue: "queue.Queue[Optional[Tuple[str, Any]]]" = queue.Queue()
        self._stop_event = threading.Event()
        # Set when this worker must not write: the file was written by a NEWER build, or the v8
        # upgrade could not complete (migration_deferred). Every write task is then refused.
        self._schema_incompatible = False
        self._downgrade_warned = False
        self._migration_deferred: str = ""          # a MIGRATION_* code while deferred
        # Compaction owed after a "Delete all history" whose WAL checkpoint couldn't finish.
        self._compact_pending = False
        self._vacuum_pending = False
        self._backups_pending = False   # Delete all history could not remove a backup yet (retried)
        self._wal_pending = False       # ... or could not checkpoint the WAL yet
        # The pending compaction is only an hourly pass's busy checkpoint (no delete): retried without
        # the restart marker. A history delete taking over clears it (see _delete_history).
        self._compact_after_maintenance = False
        self._backup_attempts = 0       # backup-removal retries this session (exponential backoff)
        self._vacuum_attempts = 0       # deferred incremental-vacuum retries this session
        self._kept_backups: set = set()   # names given up on for good (read-only + linked elsewhere)
        self._last_clock: Optional[Tuple[int, float]] = None   # (wall, monotonic) of the last age-out pass
        self._backup_retry_at = 0.0     # monotonic time of the next backup-removal retry
        self._zeroed: set = set()       # backup files already overwritten this session (never re-zero)
        self._backup_warned: set = set()  # files whose removal failure was already logged at WARNING
        self._last_delete_outcome: Optional[str] = None   # "usage_reset" / "kept_usage" / "failed"
        self._last_compact_attempt = 0.0
        # The v8 writer (built on this thread in run()) and its settings.
        self._writer = None
        self._poll_ms = max(1, int(poll_ms))
        if app_version is None:
            from netspeedtray import __version__ as app_version
        self._app_version = str(app_version)
        self._is_physical = is_physical
        self._post_migration_maintenance = False
        # After a migration: wall time from which one more roll-up pass merges the first v8 polls that
        # landed in the folded minute (below the raw watermark, invisible to readers until rolled up).
        # Armed by the first flush after the upgrade; None otherwise.
        self._post_migration_followup_at: Optional[float] = None
        self._post_migration_followup_armed = False
        self.migration_result: Optional[migrate_v8.MigrationResult] = None
        # Read-side contract for WidgetState, valid once ``ready`` is set: the schema the LIVE file can
        # be read as (8, 7 while a v8 upgrade is deferred, a newer one on a downgrade) or None when
        # nothing under the live name may be opened; and whether readers must use mode=ro connections.
        self.ready = threading.Event()
        self.reader_version: Optional[int] = None
        self.readers_read_only = False
        self.logger = logging.getLogger(f"NetSpeedTray.{self.__class__.__name__}")


    _INIT_ATTEMPTS = 5

    def _start_storage(self) -> bool:
        """``_prepare_storage`` with retries. True when this session may write; otherwise it is
        READ-ONLY with the flyout raised - never a half-built writer, never an exception out of run().

        A failure on an EXISTING file (read-only file, a write lock another process holds, an OSError
        on the way) is retried with backoff; each attempt waits at most DB_INIT_LOCK_TIMEOUT_MS for a
        lock, so a held lock costs ~12 s, not ~30. After the last attempt the session reads whatever
        a read-only probe can open (so history and the odometer still show) and says so."""
        base_delay = constants.timers.MINIMUM_INTERVAL_MS / 1000.0
        last: Optional[BaseException] = None
        for attempt in range(self._INIT_ATTEMPTS):
            try:
                self._prepare_storage()
                return self._writer is not None
            except Exception as e:   # sqlite3.Error, OSError (mkdir / stat / a rename), anything
                last = e
                self._writer = None
                self._abandon_connection()
                delay = min(30.0, base_delay * (2 ** attempt))
                self.logger.error("Database initialization attempt %d failed: %s. Retrying in %.2fs...",
                                  attempt + 1, e, delay)
                if attempt < self._INIT_ATTEMPTS - 1 and not self._stop_event.is_set():
                    self.msleep(int(delay * 1000))
                elif self._stop_event.is_set():
                    break
        try:
            readable = self._probe_version()
        except Exception:
            readable = None
        if readable is not None and readable < migrate_v8.V7:
            readable = None
        self._enter_read_only(self.DB_UNAVAILABLE, f"{type(last).__name__}: {last}", readable)
        return False

    def run(self) -> None:
        """The database thread: open the history file (``_start_storage``), then serve the queue."""
        try:
            self._start_storage()
        except Exception as e:   # belt and braces: _start_storage already catches everything
            self.logger.critical("Database initialization failed: %s", e, exc_info=True)
            self._writer = None
            self._schema_incompatible = True
        finally:
            # Readers wait for this; set it whatever happened (they then read reader_version).
            self.ready.set()

        self.logger.debug("Database worker thread started (writable: %s).", self._writer is not None)
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
                # Wake-up sentinel from stop(). Everything enqueued before it is done; with the queue
                # empty, exit now rather than idling out one more 0.5 s get() (that was 0.5 s on
                # every quit).
                if self._stop_event.is_set() and self._queue.empty():
                    break
                continue
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
            # this signal is already done - so setting the event tells a waiting reader the
            # freshly-flushed rows are now in the DB (no gap at the graph's right edge).
            try:
                data.set()
            except Exception:
                pass
            return
        if self._schema_incompatible or self._writer is None:
            # Read-only (a newer file, or a deferred v8 upgrade): every task below writes. Drop them
            # rather than letting them fail into a swallowed OperationalError. Warn once - this
            # fires per persist tick, and #263 was a 137,000-line flood from exactly this shape.
            if not self._downgrade_warned:
                self._downgrade_warned = True
                why = ("the history database could not be upgraded this launch (%s)" % self._migration_deferred
                       if self._migration_deferred else
                       "the file was written by a newer version of NetSpeedTray"
                       if self._schema_incompatible else "the database is not open")
                self.logger.warning(
                    "Refusing database task '%s' and all further writes: %s. This is logged once per "
                    "session.", task, why)
            if task == "delete_history":
                self.history_deleted.emit(self.DELETE_FAILED)   # the user is waiting on an answer
            return

        handlers = {
            "persist_v8": self._persist_v8,
            "persist_usage": self._persist_usage,
            "persist_usage_after_delete": self._persist_usage_after_delete,
            "maintenance": self._run_maintenance,
            "delete_history": self._delete_history,
            "set_poll_ms": self._set_poll_ms,
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
            except Exception as e:
                # Never let a bug in one task end the only write thread (run() catches sqlite3.Error
                # only): log it and keep serving the queue.
                self.logger.error("Unexpected error executing database task '%s': %s", task, e,
                                  exc_info=True)
        else:
            self.logger.warning("Unknown database task requested: %s", task)


    def _initialize_connection(self) -> None:
        """Opens the write connection and sets PRAGMAs. Only ever called on a file this build may
        write (a v8 file) - never on a v7 file, a newer one, or a missing live name.

        ``isolation_level=None`` (autocommit): Python's legacy implicit BEGIN would otherwise open a
        transaction on the first INSERT of a non-writer path and keep it open across tasks, folding
        that write into whatever WriterV8 commits next. Here every unit of work is an explicit
        ``BEGIN IMMEDIATE .. COMMIT`` - WriterV8's own, and :meth:`_txn` for the odometer / metadata."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Startup waits only DB_INIT_LOCK_TIMEOUT_MS for a lock (_start_storage retries); the normal
        # 5 s busy timeout is set once the writer is up (_prepare_storage).
        init_wait = int(constants.timeouts.DB_INIT_LOCK_TIMEOUT_MS)
        self.conn = sqlite3.connect(self.db_path, timeout=init_wait / 1000.0, isolation_level=None)
        self.conn.execute(f"PRAGMA busy_timeout = {init_wait};")
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA foreign_keys = ON;")
        # WAL alone leaves synchronous=FULL (an fsync per commit). NORMAL is the documented,
        # crash-safe pairing for WAL and is much cheaper for our 10 s commits. A larger
        # negative cache_size = ~8 MB page cache keeps the hot tiers in memory.
        self.conn.execute("PRAGMA synchronous = NORMAL;")
        self.conn.execute("PRAGMA cache_size = -8000;")
        # Keep GROUP BY / temp B-trees (rollup aggregation + the Monitor's nested-GROUP-BY history
        # queries) in RAM instead of spilling to disk, and memory-map the (small) DB to cut read
        # syscalls on the repeated Monitor scans. mmap_size is a ceiling, not an allocation.
        self.conn.execute("PRAGMA temp_store = MEMORY;")
        self.conn.execute("PRAGMA mmap_size = 268435456;")  # 256 MB; the DB is far smaller

    def _txn(self):
        """One explicit write transaction on the worker connection (see :func:`explicit_txn`)."""
        return explicit_txn(self.conn, self.logger)

    def _close_connection(self) -> None:
        """Ends the app session (a clean exit: final flush + ended_ts) and closes the connection."""
        if self._writer is not None and self.conn is not None:
            try:
                self._writer.end_session()
            except Exception as e:
                self.logger.error("Could not end the v8 history session cleanly: %s", e)
        if self.conn:
            try:
                if self.conn.in_transaction:
                    self.conn.execute("COMMIT")
            finally:
                self.conn.close()
                self.conn = None

    def _abandon_connection(self) -> None:
        """Close the connection without ending the session (an init retry, a reconnect)."""
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None


    def _get_current_db_version(self) -> int:
        """
        Returns the schema version: a positive int for an existing DB, 0 for a genuinely
        new/empty DB (no metadata table, or no db_version row), or -1 (UNKNOWN) if the
        version can't be read for an unexpected reason.

        The 0-vs-(-1) distinction is the M7 data-loss guard: a bare ``return 0`` on any
        error would let a transient read failure (lock, corruption) masquerade as a fresh
        install and trigger the destructive DROP/rebuild path. Callers MUST treat a
        negative result as "do not rebuild - preserve the file."
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
            # Unexpected read failure - fail closed. Returning -1 keeps the destructive
            # fresh-build path from ever running on a DB whose version we couldn't read.
            self.logger.error("Could not read DB version (treating as UNKNOWN, will not rebuild): %s", e)
            return -1


    def _has_existing_data(self) -> bool:
        """
        True if any history table (v7 or v8 names) exists and holds at least one row. Used as a
        final guard so the destructive fresh-build can never run over real user data.
        On any error, conservatively returns True (if we can't be sure, don't wipe).
        """
        try:
            cursor = self.conn.cursor()
            for table in (constants.data.SPEED_TABLE_RAW, constants.data.SPEED_TABLE_MINUTE,
                          constants.data.SPEED_TABLE_HOUR, S8.NET_RAW, S8.NET_MINUTE, S8.NET_HOUR):
                cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?",
                               (table,))
                if cursor.fetchone() is None:
                    continue
                cursor.execute(f"SELECT 1 FROM {table} LIMIT 1")
                if cursor.fetchone() is not None:
                    return True
            return False
        except Exception:
            return True

    # Keep the newest N pre-migration backups overall, PLUS the newest backup of every schema
    # version (see _prune_old_backups). Nothing used to delete these at all, and at a year-scale
    # database that is hundreds of MB of abandoned copies in the user's roaming profile.
    _BACKUP_RETENTION = 2

    # The tables each schema version is DEFINED to have - static, from the version history
    # (v2 first tiered schema; v3 bandwidth_history; v4 sample_count column only; v5 hardware
    # raw/minute; v6 hardware hour; v7 usage_counter). _verify_backup checks a backup against
    # this, never against the live source's own table list: a damaged source that lost a table
    # would otherwise "verify" a backup missing the same table. A version absent here cannot be
    # verified, so it cannot be migrated - add the new set in the same commit as a schema bump
    # (test_v7_hardening pins the current one to what a fresh build creates).
    _V2_TABLES = ("metadata", constants.data.SPEED_TABLE_RAW,
                  constants.data.SPEED_TABLE_MINUTE, constants.data.SPEED_TABLE_HOUR)
    _V3_TABLES = _V2_TABLES + (constants.data.BANDWIDTH_TABLE,)
    _V5_TABLES = _V3_TABLES + (constants.data.HARDWARE_STATS_TABLE_RAW,
                               constants.data.HARDWARE_STATS_TABLE_MINUTE)
    _V6_TABLES = _V5_TABLES + (constants.data.HARDWARE_STATS_TABLE_HOUR,)
    EXPECTED_TABLES: Dict[int, Tuple[str, ...]] = {
        2: _V2_TABLES,
        3: _V3_TABLES,
        4: _V3_TABLES,
        5: _V5_TABLES,
        6: _V6_TABLES,
        7: _V6_TABLES + (constants.data.USAGE_COUNTER_TABLE,),
        # Defined with the v8 DDL so the set cannot drift from it. _verify_backup only ever asks
        # for the version being migrated FROM (v2-v6 in the legacy chain); the v7 -> v8 step
        # verifies itself (migrate_v8.verify).
        8: EXPECTED_TABLES_V8,
    }

    def _backup_database(self) -> bool:
        """
        Make a **verified**, WAL-safe copy of the database before a migration.

        Uses ``VACUUM INTO`` rather than a file copy. ``shutil.copy2`` copies only the
        main ``.db`` file, but in WAL mode everything committed since the last
        checkpoint lives in the ``-wal`` sidecar - so a copy taken while the WAL is hot
        silently loses it. That is precisely the state during an upgrade: the installer
        force-kills the running app (``taskkill /F`` in setup.iss), so no checkpoint
        runs. Measured against a hot WAL, a ``copy2`` backup recovered **zero of 30,000
        committed rows** - and the result still passed ``PRAGMA integrity_check``,
        because an empty database is a perfectly valid one.

        ``VACUUM INTO`` reads through this connection, so it sees WAL content, and it
        compacts the copy as a side effect.

        The copy is then opened and checked against the source before this returns
        True. A backup that silently isn't one is worse than no backup at all, because
        the caller reasons about its existence.
        """
        backup_path: Optional[Path] = None
        try:
            version = self._get_current_db_version()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.db_path.with_suffix(f".db.bak.v{version}_{timestamp}")

            # VACUUM INTO refuses to write an existing file. The timestamp is only
            # second-resolution while run()'s init retry backs off in 0.1s steps, so two
            # attempts land in the same second - and that collision would fire in exactly
            # the retry path a backup exists to protect.
            suffix = 0
            while backup_path.exists():
                suffix += 1
                backup_path = self.db_path.with_suffix(f".db.bak.v{version}_{timestamp}_{suffix}")

            self.logger.info("Backing up database to: %s", backup_path)
            self.conn.execute("VACUUM INTO ?", (str(backup_path),))

            if not self._verify_backup(backup_path, version):
                self.logger.error("Pre-migration backup FAILED VERIFICATION; treating as no backup.")
                backup_path.unlink(missing_ok=True)
                return False

            self._prune_old_backups()
            return True
        except Exception as e:
            # Don't swallow silently: the migration's data-loss guard assumes a backup was made, so a
            # disk-full / permission / lock failure here must be visible in the log (and the bak's absence).
            self.logger.error("Pre-migration database backup FAILED: %s", e)
            if backup_path is not None:
                try:
                    backup_path.unlink(missing_ok=True)   # never leave a partial copy that looks real
                except Exception:
                    pass
            return False


    def _verify_backup(self, backup_path: Path, expected_version: int) -> bool:
        """
        Open the backup and prove it holds the same data as the source.

        Structural checks alone are not enough: the failure this exists to catch (a
        WAL-blind copy) produces a *valid* database that is merely missing rows, which
        ``integrity_check`` reports as ``ok``. So compare content - the schema version
        and the row count of every table the version is DEFINED to have
        (``EXPECTED_TABLES[expected_version]``). The source's own table list is never the
        reference: a table missing from both source and backup fails here, it does not pass.
        """
        expected_tables = self.EXPECTED_TABLES.get(expected_version)
        if expected_tables is None:
            self.logger.error(
                "Backup verification: no expected table set is known for schema v%s; "
                "refusing to call the backup verified.", expected_version)
            return False
        check: Optional[sqlite3.Connection] = None
        try:
            # NOTE: `with sqlite3.connect(...)` commits/rolls back the transaction but does
            # NOT close the connection. On Windows the leaked handle keeps the file locked,
            # so _prune_old_backups() then fails with WinError 32. Close it explicitly.
            check = sqlite3.connect(sqlite_ro_uri(backup_path), uri=True)   # encoded: a # in the path

            if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                self.logger.error("Backup verification: quick_check did not return ok.")
                return False

            row = check.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()
            if row is None or int(row[0]) != expected_version:
                self.logger.error(
                    "Backup verification: db_version is %s, expected %d.",
                    row[0] if row else "missing", expected_version,
                )
                return False

            _tables_sql = "SELECT name FROM sqlite_master WHERE type='table'"
            src_tables = {r[0] for r in self.conn.execute(_tables_sql)}
            dst_tables = {r[0] for r in check.execute(_tables_sql)}
            for label, present in (("source", src_tables), ("backup", dst_tables)):
                missing = sorted(set(expected_tables) - present)
                if missing:
                    self.logger.error(
                        "Backup verification: the %s is missing table(s) %s that schema v%d must "
                        "have. Not migrating a database whose backup cannot be proven whole.",
                        label, ", ".join(missing), expected_version,
                    )
                    return False
            for table in expected_tables:
                src = self.conn.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
                dst = check.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
                if src != dst:
                    self.logger.error(
                        "Backup verification: table '%s' has %d rows in the backup but %d in the "
                        "source. The backup is incomplete.", table, dst, src,
                    )
                    return False
            return True
        except Exception as e:
            self.logger.error("Backup verification failed to read the copy: %s", e)
            return False
        finally:
            if check is not None:
                check.close()


    _SIDECARS = ("-wal", "-shm", "-journal")

    def _backup_files(self, pattern: str) -> List[Path]:
        """``speed_history.db.bak.<pattern>`` files that ARE backups - never their SQLite sidecars.
        Anything that opens a backup (DB Browser, a read-only check) leaves ``<backup>-wal`` / ``-shm``
        beside it, newer than the backup and matching the same ``.db.bak.v{N}_`` pattern: found live
        (step 7), 2.1.7 named such a -shm as the file to restore."""
        out = []
        for p in self._history_backup_files():
            kind = self._history_file_kind(p.name)
            if kind and kind[0] == "bak" and not kind[3]:
                out.append(p)
        return out

    def _prune_old_backups(self) -> None:
        """Keep the newest `_BACKUP_RETENTION` backups overall PLUS the newest backup of each
        schema version (the version is the one the backup holds: ``.db.bak.v{N}_...``).

        Version-blind "newest 2" would let two v8-era backups delete the last v7 one - the only
        file a rolled-back 2.1.x can restore (_find_newest_backup skips newer versions). A backup
        whose name carries no version is kept only while it is among the newest overall."""
        try:
            backups = sorted(
                self._backup_files("*"),
                key=lambda p: (p.stat().st_mtime, p.name),
                reverse=True,
            )
            keep = set(backups[:self._BACKUP_RETENTION])
            seen_versions = set()
            for path in backups:   # newest first, so the first hit per version is its newest
                m = re.search(r"\.db\.bak\.v(\d+)_", path.name, re.IGNORECASE)
                if m and m.group(1) not in seen_versions:
                    seen_versions.add(m.group(1))
                    keep.add(path)
            for stale in backups:
                if stale in keep:
                    continue
                stale.unlink(missing_ok=True)
                for side in self._SIDECARS:   # a backup someone opened has sidecars: they go with it
                    Path(str(stale) + side).unlink(missing_ok=True)
                self.logger.debug("Pruned old database backup: %s", stale.name)
        except Exception as e:
            self.logger.warning("Could not prune old database backups: %s", e)


    def _find_newest_backup(self) -> Optional[Path]:
        """
        The newest pre-migration backup sibling (``speed_history.db.bak.v*``), or None.

        Used by the downgrade guard to point the user AT their data: the old refusal
        message advised moving the database aside to start a fresh history while a
        verified backup sat in the same folder. A rollback session never prunes these
        (_prune_old_backups only runs from _backup_database, inside _migrate_schema),
        so whatever the migrating build left behind is still here - and pruning keeps the
        newest backup of every schema version, so a later build's own backups never
        displace this build's restore target.
        """
        try:
            backups = sorted(
                self._backup_files("v*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for path in backups:
                m = re.search(r"\.db\.bak\.v(\d+)_", path.name, re.IGNORECASE)
                if m and int(m.group(1)) <= self._DB_VERSION:
                    return path
            # Every backup on disk was written by a NEWER schema than this build reads -
            # naming one would send the user into a dead-end restore (review C5). Treat as
            # "no backup": the refusal message then leads with Upgrade, which is the only
            # path that can actually open those files.
            return None
        except Exception as e:
            self.logger.debug("Could not scan for database backups: %s", e)
            return None


    def _migrate_schema(self, current_version: int, target_version: int) -> None:
        """The pre-v8 in-place chain: ``current_version`` -> ``target_version`` (always 7 since 2.2;
        the v7 -> v8 step is ``migrate_v8``, build-aside-and-swap). A verified backup comes first."""
        self.logger.info("Migrating database from version %d to %d...", current_version, target_version)

        if not self._backup_database():
            # ABORT (release-plan Gate 2, item 3). The per-step transaction below cannot roll back an
            # executescript() (it COMMITs implicitly), and a migration whose only undo is a backup
            # that failed or did not verify has no undo at all. Nothing has been changed yet: the
            # file stays on its old version and the next launch retries.
            raise MigrationAborted(
                f"pre-migration backup failed or did not verify; staying on schema v{current_version}")

        try:
            for ver in range(current_version, target_version):
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



    # ---- startup: recover, probe, create / migrate, open ------------------------------------------

    def _probe_version(self) -> Optional[int]:
        """db_version of the live file through a READ-ONLY connection (never creates or writes the
        file). None = no file / an empty one (a fresh install); 0 = a database without a version;
        -1 = unreadable (never guess)."""
        p = self.db_path
        if not p.exists():
            return None
        wal = Path(str(p) + "-wal")
        if p.stat().st_size == 0 and (not wal.exists() or wal.stat().st_size == 0):
            return None
        c = None
        try:
            c = sqlite3.connect(sqlite_ro_uri(p), uri=True)
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone() is None:
                return 0
            row = c.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            self.logger.error("Could not read the history database's version (read-only probe): %s", e)
            return -1
        finally:
            if c is not None:
                c.close()

    def _probe_has_data(self) -> bool:
        """Whether a version-less file holds history rows (read-only). True on any doubt."""
        c = None
        try:
            c = sqlite3.connect(sqlite_ro_uri(self.db_path), uri=True)
            prev, self.conn = self.conn, c
            try:
                return self._has_existing_data()
            finally:
                self.conn = prev
        except Exception:
            return True
        finally:
            if c is not None:
                c.close()

    def _create_fresh_v8(self) -> None:
        """A new install: the complete v8 schema on the EMPTY file, then the version stamp. Must run
        before anything switches the file to WAL (that writes page 1 and silently disables
        page_size / auto_vacuum=INCREMENTAL - schema_v8.create_v8_schema refuses a non-empty file)."""
        self.logger.info("Creating a new history database (schema v%d).", self._DB_VERSION)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            S8.create_v8_schema(c)
            now_ts = int(time.time())
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', ?)", (str(self._DB_VERSION),))
            c.execute("INSERT INTO metadata (key, value) VALUES ('created_at', ?)", (str(now_ts),))
            c.execute("COMMIT")
        finally:
            c.close()

    def _enter_read_only(self, reason: str, detail: Any, reader_version: Optional[int]) -> None:
        """A v8 upgrade (or the recovery of one) could not complete: this session writes NOTHING.

        No write connection is opened (none is open here), every write task is refused, readers use
        mode=ro connections - or none at all when ``reader_version`` is None (the live name is absent
        or unsafe) - and the view is told (migration_deferred -> flyout). The next launch retries:
        recover() and migrate() are idempotent and pick up exactly where this one stopped."""
        self._writer = None            # never a half-built writer on a connection that is gone
        self._abandon_connection()
        self._schema_incompatible = True
        self._migration_deferred = reason
        self.reader_version = reader_version
        self.readers_read_only = True
        self.logger.error(
            "The history database could not be upgraded to v%d this launch (%s: %s). NetSpeedTray will "
            "show the history it has but record nothing new until the next start, when it tries again. "
            "Nothing was deleted or overwritten.", self._DB_VERSION, reason, detail)
        self.migration_deferred.emit(reason)

    def _reason_for(self, e: BaseException) -> Tuple[str, Optional[int]]:
        """(MIGRATION_* code, what the live name can be read as) for a migration failure. Every case
        but SwapIncomplete leaves the untouched v7 file live; SwapIncomplete leaves the live name
        EMPTY (the v7 file is already the backup), so nothing may open it - a plain connect would
        create an empty database there and J5 recovery would then have to move it aside."""
        if isinstance(e, migrate_v8.SwapIncomplete):
            return self.MIGRATION_SWAP_INCOMPLETE, None
        if isinstance(e, (migrate_v8.SwapDeferred, migrate_v8.FileInUse)):
            return self.MIGRATION_FILE_IN_USE, 7
        if isinstance(e, PermissionError) or getattr(e, "winerror", None) in (5, 32):
            # Another program holds a file of ours (an AV scanner, a SQLite tool on the -wal): the v7
            # file is still live and untouched (a journal written before the error is J1: the next
            # launch finishes or rolls back the swap).
            return self.MIGRATION_FILE_IN_USE, 7
        if isinstance(e, migrate_v8.InsufficientSpace):
            return self.MIGRATION_NO_SPACE, 7
        if isinstance(e, migrate_v8.RecoveryError):
            return self.MIGRATION_RECOVERY, None
        return self.MIGRATION_FAILED, 7

    def _prepare_storage(self) -> None:
        """Bring the live file to schema v8 (or decide this session is read-only), then open it.

        Order is the contract (step-7 notes 1-5): recover() before ANY connect to the live name; the
        version is probed read-only; migrate() runs with no connection of ours open; no
        _backup_database() for the v8 step (the renamed v7 file is the backup); run_maintenance()
        right after a successful migrate()."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            recovered = migrate_v8.recover(self.db_path)
            if recovered:
                self.logger.warning("Recovered an interrupted database upgrade: %s.", recovered)
        except Exception as e:
            # RecoveryError (a state recover() will not guess about) or a rename that keeps failing
            # (SwapError). The live name may be absent (then nothing may open it - a plain connect
            # would create a file there) or hold a file recover() would not choose; a v7 / v8 file
            # under it is shown read-only, anything else not at all.
            readable = self._probe_version() if self.db_path.exists() else None
            self._enter_read_only(self.MIGRATION_RECOVERY, f"{type(e).__name__}: {e}",
                                  readable if readable in (migrate_v8.V7, migrate_v8.V8) else None)
            return

        version = self._probe_version()
        if version is None:
            self._create_fresh_v8()
            version = self._DB_VERSION
        elif version == 0:
            if self._probe_has_data():
                self._enter_read_only(self.MIGRATION_UNREADABLE,
                                      "the file has history rows but no schema version", None)
                return
            # A database with no version and no history (a crash mid-create, a stray file): never
            # delete what we did not make - move it aside and start a fresh v8 file.
            aside = migrate_v8._move_aside(self.db_path)
            self.logger.warning("Moved an unversioned, empty database aside (%s); starting fresh.", aside.name)
            self._create_fresh_v8()
            version = self._DB_VERSION
        elif version < 0:
            self._enter_read_only(self.MIGRATION_UNREADABLE, "the schema version could not be read", None)
            return

        if 0 < version < self._LEGACY_CHAIN_TARGET:
            # v2-v6: the old in-place chain to v7 (verified backup first), then the v8 migration.
            try:
                self._run_legacy_chain(version)
            except Exception as e:
                self._enter_read_only(self.MIGRATION_FAILED, f"v{version} -> v7: {e}", None)
                return
            version = self._LEGACY_CHAIN_TARGET

        if version == migrate_v8.V7:
            try:
                res = migrate_v8.migrate(self.db_path)
            except Exception as e:
                reason, readable = self._reason_for(e)
                self._enter_read_only(reason, f"{type(e).__name__}: {e}", readable)
                return
            self.migration_result = res
            if res.status == "migrated":
                self._post_migration_maintenance = True
                self.logger.info(
                    "History database upgraded to v8 in %.1f s (%d -> %d bytes). The v7 file is kept "
                    "unchanged as %s.", res.timings.get("total", 0.0), res.v7_bytes, res.v8_bytes,
                    Path(res.backup_path).name)
            elif res.status != "already_v8":
                self._enter_read_only(self.MIGRATION_FAILED, f"migrate() returned {res.status!r}", None)
                return
            version = self._DB_VERSION

        if version > self._DB_VERSION:
            self._enter_downgrade(version)
            return

        # version == 8: the only file this build writes.
        self._initialize_connection()
        actual = self._get_current_db_version()
        if actual != self._DB_VERSION:
            self._abandon_connection()
            self._enter_read_only(self.MIGRATION_UNREADABLE,
                                  f"expected schema v{self._DB_VERSION}, found v{actual}", None)
            return
        from netspeedtray.core.db.writer_v8 import WriterV8
        self._writer = WriterV8(self.conn, version=self._app_version, poll_ms=self._poll_ms,
                                is_physical=self._is_physical, log=self.logger)
        self._writer.bind_to_current_thread()   # step-7 note 7: every writer call on this thread
        if self._post_migration_maintenance:
            self._post_migration_maintenance = False
            self._stamp_v7_migration()
            if self._stop_event.is_set():
                # Quitting already: the migration is complete and durable; the merge of the first polls
                # into the folded minute waits for the next start's maintenance. Reach the safe point.
                self.logger.info("Quit requested: post-upgrade maintenance deferred to the next start.")
            else:
                out = self._writer.run_maintenance()   # step-7 note 5: right after migrate()
                self.logger.info("Post-upgrade maintenance: %s", {k: v for k, v in out.items() if "moved" in k})
                # rc.2 D4: that pass moves a day of raw hardware rows (107k on the owner's file) and
                # leaves ~3 MB of frames in the WAL. Nothing else checkpointed them reliably: the startup
                # maintenance's TRUNCATE races the first GUI reads (the odometer load), and then the WAL
                # sat just under the 1000-page auto-checkpoint until ~4-5 min of polls tipped it over -
                # the unexplained post-upgrade burst minute. The readers are still held off here (ready
                # is set after this returns), so fold those pages into the upgrade moment instead.
                self._checkpoint_truncate(constants.timeouts.DB_CHECKPOINT_RETRIES)
                # That pass ran before any v8 poll existed, so polls landing in the folded minute stay
                # below the raw watermark - hidden - until a roll-up: the next hourly pass, up to an hour
                # away. One extra pass once that minute is over and flushed (see _persist_v8) closes it.
                self._post_migration_followup_armed = True
        self._writer.start_session()
        self.conn.execute("PRAGMA busy_timeout = 5000;")
        self._resume_pending_compaction()
        self.reader_version = self._DB_VERSION
        self.readers_read_only = False
        if self.migration_result is not None and self.migration_result.status == "migrated":
            # Only now: the upgrade completed AND this session writes the new file (a later refusal
            # above returns read-only with the "paused" flyout instead).
            self.history_upgraded.emit()

    MIGRATED_FROM_V7_KEY = "migrated_from_v7_at"
    MIGRATED_FROM_V7_BACKUP_KEY = "migrated_from_v7_backup"

    def _stamp_v7_migration(self) -> None:
        """Record that THIS live file is a completed v7 -> v8 migration (and which backup it left).
        Only a stamped file lets maintenance age a pre-v8 backup out: a fresh v8 file (e.g. a stuck
        upgrade whose journal was deleted by hand) has none, and its v7 backup is the only full history."""
        res = self.migration_result
        try:
            with self._txn() as cur:
                cur.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                            (self.MIGRATED_FROM_V7_KEY, str(int(time.time()))))
                if res is not None and res.backup_path:
                    cur.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                                (self.MIGRATED_FROM_V7_BACKUP_KEY, Path(res.backup_path).name))
        except sqlite3.Error as e:
            self.logger.warning("Could not record the completed upgrade (%s); its backup will be kept.", e)

    def _run_legacy_chain(self, version: int) -> None:
        """v2-v6 -> v7 in place, exactly as 2.1.x did it (verified backup, then the per-step
        migrations), on a connection that is closed again before the v8 migration starts."""
        self.conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            self.conn.execute("PRAGMA busy_timeout = 5000;")
            self._migrate_schema(version, self._LEGACY_CHAIN_TARGET)
        finally:
            self._abandon_connection()

    def _enter_downgrade(self, current_version: int) -> None:
        """The file was written by a NEWER build than this one: read-only, no backup, no writes,
        no write connection at all. (See 2.1.5's guard for why silence is not an option: reads keep
        working through compatibility views while every write would fail into a swallowed error.)"""
        self._schema_incompatible = True
        self.reader_version = current_version
        self.readers_read_only = True
        newest_backup = self._find_newest_backup()
        if newest_backup is not None:
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

    # ---- v8 write tasks ---------------------------------------------------------------------------

    def _persist_v8(self, data: Tuple[List[Any], List[Tuple[int, str, float]]]) -> None:
        """The 10 s persist tick: buffered net samples + hardware samples -> WriterV8, then ONE
        flush (rows + the app_sessions heartbeat) in one transaction. Runs even when both lists are
        empty - the heartbeat is the coverage record."""
        net, hw = data if data else ([], [])
        w = self._writer
        for s in net or ():
            try:
                w.add_net_sample(s)
            except (TypeError, ValueError, AttributeError) as e:
                self.logger.debug("Dropped a malformed network sample: %s", e)
        for row in hw or ():
            try:
                ts, name, value = row[0], row[1], row[2]
                w.add_hw_sample(ts, name, value)
            except (TypeError, ValueError, IndexError) as e:
                self.logger.debug("Dropped a malformed hardware sample: %s", e)
        if w.flush():
            self.database_updated.emit()
            self._maybe_post_migration_followup()

    # The follow-up pass waits this long after the first post-upgrade flush, and at least this long past
    # the raw watermark: the folded minute is over and its last polls have been flushed (10 s tick).
    _POST_MIGRATION_FOLLOWUP_DELAY_S = 70.0
    _POST_MIGRATION_FOLLOWUP_WM_MARGIN_S = 20.0

    def _maybe_post_migration_followup(self) -> None:
        """After a v7 -> v8 upgrade: arm one roll-up pass at the first flush, run it on the first flush
        at or after its time. Roll-ups only (``retention_days=None``): it needs no config and prunes
        nothing; the clock guard applies as on every pass."""
        if self._post_migration_followup_armed:
            self._post_migration_followup_armed = False
            wm = self._writer._meta_int(S8.WATERMARK_RAW_KEY) or 0
            self._post_migration_followup_at = max(time.time() + self._POST_MIGRATION_FOLLOWUP_DELAY_S,
                                                   wm + self._POST_MIGRATION_FOLLOWUP_WM_MARGIN_S)
            return
        due = self._post_migration_followup_at
        if due is None or time.time() < due:
            return
        self._post_migration_followup_at = None
        try:
            out = self._writer.run_maintenance()
            self.logger.info("Post-upgrade follow-up roll-up: %s", {k: v for k, v in out.items() if "moved" in k})
            self.database_updated.emit()
        except sqlite3.Error as e:
            self.logger.warning("Post-upgrade follow-up roll-up failed (the hourly pass will do it): %s", e)

    def _set_poll_ms(self, poll_ms: Any) -> None:
        self._poll_ms = max(1, int(poll_ms))
        self._writer.set_poll_ms(self._poll_ms)

    def _persist_usage_after_delete(self, data: Dict[str, Any]) -> None:
        """Session end with a usage reset still unanswered on the GUI side: the delete itself already
        ran (FIFO), so persist whichever odometer matches its outcome - the post-request traffic if the
        reset committed, the unchanged-plus-traffic total if it failed."""
        payload = (data or {}).get("if_reset" if self._last_delete_outcome == "usage_reset" else "if_failed")
        if payload is not None:
            self._persist_usage(payload)

    def _persist_usage(self, data: Tuple[Any, ...]) -> None:
        """
        Upsert the single-row data-usage odometer. `data` is
        (cumulative_up, cumulative_down, anchor_up, anchor_down, period_key, updated_ts[, snapshot_json]).

        The optional snapshot_json (the NIC counters the cumulative accounts up to, for same-boot
        launch reconciliation) goes to the `metadata` key/value table in the SAME transaction, so
        total and snapshot can never disagree. None leaves the stored snapshot untouched; ""
        (usage_snapshot.CLEAR_SNAPSHOT) deletes it. Its own explicit transaction (_txn): never
        folded into a WriterV8 unit, never left open for one.
        """
        if data is None or self.conn is None:
            return
        cumulative_up, cumulative_down, anchor_up, anchor_down, period_key, updated_ts = data[:6]
        snapshot_json = data[6] if len(data) > 6 else None
        memory_json = data[7] if len(data) > 7 else None
        try:
            with explicit_txn(self.conn, self.logger) as cursor:
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
                # 8th element: the odometer's re-key memory; "" deletes it, None leaves it untouched.
                if memory_json == "":
                    cursor.execute("DELETE FROM metadata WHERE key = ?",
                                   (constants.network.usage.REKEY_MEMORY_METADATA_KEY,))
                elif memory_json is not None:
                    cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                                   (constants.network.usage.REKEY_MEMORY_METADATA_KEY, memory_json))
        except sqlite3.Error as e:
            self.logger.error("Failed to persist usage counter: %s", e)

    def _run_maintenance(self, data: Dict[str, Any], now: Optional[datetime] = None,
                         mono: Optional[float] = None) -> None:
        """The hourly pass: retention resolved through the 48 h grace logic (metadata only), then
        WriterV8's roll-ups + watermarks + prune + ``incremental_vacuum(N)`` (never a full VACUUM -
        the file is auto_vacuum=INCREMENTAL), then a TRUNCATE checkpoint to bound the WAL."""
        if self.conn is None:
            return
        config = data or {}
        if now is None and mono is None:
            mono = time.monotonic()   # the real clock: the backup guard compares it with wall time
        _now = now or datetime.now()
        now_ts = int(_now.timestamp())
        self.logger.debug("Starting periodic database maintenance...")
        try:
            with self._txn() as cursor:
                retention = self._resolve_retention_days(cursor, config, _now)
                row = cursor.execute("SELECT value FROM metadata WHERE key = 'last_maintenance_at'").fetchone()
                prev_maintenance = int(row[0]) if row and str(row[0]).isdigit() else None
            out = self._writer.run_maintenance(now_ts=now_ts, retention_days=retention, mono=mono)
            with self._txn() as cursor:
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_maintenance_at', ?)",
                               (str(now_ts),))
            self.logger.debug("Database maintenance committed: %s", out)
        except sqlite3.Error as e:
            self.logger.error("Maintenance failed: %s", e)
            return
        try:
            self._age_out_backups(retention, _now, prev_maintenance, mono)
        except Exception as e:   # never fatal to maintenance (the stamp above is already written)
            self.logger.warning("Backup age-out skipped: %s", e)
        # Bound the WAL: long-lived reader connections (the Monitor's graph worker) can hold back the
        # automatic checkpoint. A busy checkpoint is harmless and must not undo the maintenance. One
        # attempt on the SHORT busy timeout (_checkpoint_truncate): the raw pragma waited the full 5 s
        # whenever the Monitor was reading, stalling every queued write each hourly pass (review (b),
        # rc.2). A busy one is left to the idle retry, which never blocks the queue either.
        if not self._checkpoint_truncate(1):
            self._wal_pending = True
            if not self._compact_pending:
                self._compact_pending = True
                self._compact_after_maintenance = True
                self._last_compact_attempt = time.monotonic()
        self.database_updated.emit()

    def _resolve_retention_days(self, cursor: sqlite3.Cursor, config: Dict[str, Any], now: datetime) -> float:
        """The retention (days) the hour tiers are pruned at this pass, via the 48 h grace period
        for reductions (the v7 rule, unchanged in its metadata keys): a REDUCED setting only takes
        effect 48 h after it was made (so an accidental change is reversible); an INCREASE applies at
        once and cancels a pending reduction. Writes only metadata; the DELETEs are WriterV8's."""
        row = cursor.execute("SELECT value FROM metadata WHERE key = 'current_retention_days'").fetchone()
        current = int(row[0]) if row and str(row[0]).isdigit() else 365
        row = cursor.execute("SELECT value FROM metadata WHERE key = 'prune_scheduled_at'").fetchone()
        scheduled = int(row[0]) if row and str(row[0]).isdigit() else None
        try:
            wanted = int(config.get("keep_data", 365))
        except (TypeError, ValueError):
            wanted = current
        if scheduled and scheduled <= int(now.timestamp()):
            row = cursor.execute("SELECT value FROM metadata WHERE key = 'pending_retention_days'").fetchone()
            cursor.execute("DELETE FROM metadata WHERE key IN ('prune_scheduled_at', 'pending_retention_days')")
            if row and str(row[0]).isdigit():
                final = int(row[0])
                self.logger.info("Grace period expired. Pruning data older than %d days.", final)
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('current_retention_days', ?)",
                               (str(final),))
                return float(final)
            self.logger.warning("Scheduled prune was due, but no pending retention period was found. Canceling.")
            return float(current)
        if wanted < current:
            if scheduled is None:
                end = int((now + timedelta(hours=48)).timestamp())
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('prune_scheduled_at', ?)", (str(end),))
                cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('pending_retention_days', ?)",
                               (str(wanted),))
                self.logger.info("Retention period reduced. Scheduling data prune in 48 hours.")
            return float(current)
        if wanted > current:
            if scheduled is not None:
                cursor.execute("DELETE FROM metadata WHERE key IN ('prune_scheduled_at', 'pending_retention_days')")
                self.logger.info("Retention period increased. Pending data prune has been canceled.")
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('current_retention_days', ?)",
                           (str(wanted),))
            return float(wanted)
        return float(current)


    def _delete_history(self, data: Optional[Dict[str, Any]] = None) -> None:
        """
        User-requested "Delete all history": WriterV8.delete_history empties every v8 history table
        (``constants.data.HISTORY_TABLES`` - the dimension tables too, adapter names are
        identifying) in one transaction with ``secure_delete`` on, drops every cached id, starts a new
        session, and returns the freed pages (incremental_vacuum). Then a TRUNCATE checkpoint moves the
        zeroed pages out of -wal over the old ones in the .db.

        Runs on this thread like every other write, so it is serialized with the persist batches -
        WidgetState drops its pending batches and enqueues this under the batch lock, so nothing
        recorded before the request can be written after it.

        ``data["include_usage"]`` also clears the data-usage odometer (opt-in; decision D5).
        """
        include_usage = bool((data or {}).get("include_usage", False))
        if self.conn is None or self._writer is None:
            self.history_deleted.emit(self.DELETE_FAILED)
            return
        try:
            vacuum_ok = self._writer.delete_history(include_usage=include_usage) is not False
        except Exception as e:
            # Only a failure BEFORE the commit gets here (WriterV8 rolls back and re-raises); one after
            # it - returning the free pages - comes back as vacuum_ok False and is a done delete.
            # ANY failure is an answer the user (and WidgetState's pending odometer reset) waits on:
            # _execute_task would swallow a non-sqlite3 exception and leave both hanging.
            self.logger.error("Delete all history failed: %s", e)
            self._last_delete_outcome = "failed"
            self.history_deleted.emit(self.DELETE_FAILED)
            return
        self._last_delete_outcome = "usage_reset" if include_usage else "kept_usage"
        self.logger.info("History deleted (%d tables%s).", len(constants.data.HISTORY_TABLES),
                         ", incl. data-usage counter" if include_usage else "")
        # The pre-upgrade backups hold the same history (the whole 2.1 file, for the v7 one): a delete
        # that kept them would not have deleted it.
        backups = self._remove_history_backups()
        checkpoint_ok = self._checkpoint_truncate(constants.timeouts.DB_CHECKPOINT_RETRIES)
        self.database_updated.emit()
        T = constants.timeouts
        self._compact_after_maintenance = False   # a delete's compaction owns the retry from here on
        self._vacuum_pending = not vacuum_ok
        self._vacuum_attempts = 0
        self._wal_pending = not checkpoint_ok
        # "journal": kept until the upgrade has finished - removed at a later start ("2" marker), not
        # retried this session. "retry": held open / transient - retried with backoff.
        self._backups_pending = backups in ("retry", "journal")
        self._backup_attempts = T.DB_BACKUP_RETRY_MAX_ATTEMPTS if backups == "journal" else 0
        if self._vacuum_pending or self._wal_pending or self._backups_pending:
            self._compact_pending = True
            self._last_compact_attempt = time.monotonic()
            self._backup_retry_at = self._last_compact_attempt + T.DB_COMPACT_RETRY_INTERVAL_SEC
            self._set_compaction_marker(True, backups=self._backups_pending)   # survives a quit
            why = []
            if not checkpoint_ok:
                why.append("the WAL checkpoint is busy (old page images may remain in the -wal file)")
            if not vacuum_ok:
                why.append("the free pages could not be returned yet")
            if backups == "retry":
                why.append("a database backup could not be removed yet")
            if why:
                self.logger.warning("History rows deleted, but %s. Retrying while idle.", "; ".join(why))
        if backups == "journal":
            self.history_deleted.emit(self.DELETE_BACKUP_KEPT_UPGRADE)
        elif backups == "kept" or self._kept_backups:
            self.history_deleted.emit(self.DELETE_BACKUP_KEPT)
        elif self._compact_pending:
            self.history_deleted.emit(self.DELETE_COMPACT_PENDING)
        else:
            self.history_deleted.emit(self.DELETE_DONE)

    # Exactly the names this app (and 2.1.x) writes - never a user's own file that merely starts the
    # same way (speed_history.db.backup, speed_history.db.bak_mine.zip, ...). Case-INSENSITIVE: NTFS
    # is, so SPEED_HISTORY.DB.BAK.V7_... IS that backup, and Delete, age-out, prune and the restore
    # hint (_find_newest_backup) must all agree on what is one.
    _SIDE_RE = r"(?:-wal|-shm|-journal)?"

    def _history_file_kind(self, name: str) -> Optional[Tuple[str, Optional[int], Optional[str], bool]]:
        """(kind, schema version, name timestamp, is_sidecar) for an app-made history copy, else None.
        kind: "bak" = ``<db>.bak.v<N>_<YYYYmmdd>_<HHMMSS>[_n]``, "legacy" = bare ``<db>.bak``,
        "stray" = ``<db>.stray_<YYYYmmdd>_<HHMMSS>[_n]`` (migrate_v8.recover() moved it off the live
        name); each with an optional -wal / -shm / -journal suffix. Case-insensitive."""
        db = re.escape(self.db_path.name)
        side = self._SIDE_RE
        flags = re.IGNORECASE
        m = re.fullmatch(rf"{db}\.bak\.v(\d+)_(\d{{8}}_\d{{6}})(?:_\d+)?({side})", name, flags)
        if m:
            return "bak", int(m.group(1)), m.group(2), bool(m.group(3))
        m = re.fullmatch(rf"{db}\.bak({side})", name, flags)
        if m:
            return "legacy", None, None, bool(m.group(1))
        m = re.fullmatch(rf"{db}{re.escape(migrate_v8.STRAY_INFIX)}(\d{{8}}_\d{{6}})(?:_\d+)?({side})", name, flags)
        if m:
            return "stray", None, m.group(1), bool(m.group(2))
        return None

    def _history_backup_files(self) -> List[Path]:
        """Every app-made file in the data folder that can hold an old copy of the history - backups,
        the legacy bare ``.bak`` and recover()'s ``.stray_<ts>`` files, each with its sidecars - by
        exact name (see _history_file_kind). Directories are never included."""
        out: List[Path] = []
        try:
            entries = list(os.scandir(self.db_path.parent))
        except OSError:
            return []
        for e in entries:
            try:
                if self._history_file_kind(e.name) is None or e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            out.append(Path(e.path))
        return sorted(out)

    _KEPT_KEY = "backups_unremovable"

    def _load_kept_backups(self) -> None:
        """The names given up on for good in an earlier session (see _scrub_and_unlink)."""
        try:
            row = self.conn.execute("SELECT value FROM metadata WHERE key = ?", (self._KEPT_KEY,)).fetchone()
            if row:
                import json
                self._kept_backups |= {str(n).lower() for n in json.loads(row[0])}
        except Exception:
            pass

    def _save_kept_backups(self) -> None:
        import json
        try:
            with self._txn() as cur:
                if self._kept_backups:
                    cur.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                                (self._KEPT_KEY, json.dumps(sorted(self._kept_backups))))
                else:
                    cur.execute("DELETE FROM metadata WHERE key = ?", (self._KEPT_KEY,))
        except sqlite3.Error as e:
            self.logger.debug("Kept-backup list not saved: %s", e)

    def _remove_history_backups(self, retrying: bool = False) -> str:
        """Delete all history, part 2: overwrite every app-made history copy with zeros, then delete it.
        The overwrite is best effort (an SSD or a file-system snapshot may still keep old blocks - the
        same limit as secure_delete inside the database); deleting is what makes it unrecoverable
        through NetSpeedTray. Returns:
          "ok"      - none is left;
          "journal" - a v8 migration journal exists: recover() needs the v7 backup to undo or finish a
                      swap, so nothing is touched (removed at a later start, once it is gone);
          "retry"   - something is held open / transiently failing (retried with backoff);
          "kept"    - only files that can never be removed are left (given up on, recorded, told).
        A file given up on is still tried once by a NEW Delete all history request, never by a retry."""
        if migrate_v8.journal_path_for(self.db_path).exists():
            self.logger.warning("Delete all history: a migration journal exists; database backups kept "
                                "until the upgrade has been finished or undone.")
            return "journal"
        if self.conn is not None and not self._kept_backups:
            self._load_kept_backups()
        retry = kept = False
        for path in self._history_backup_files():
            if retrying and path.name.lower() in self._kept_backups:
                kept = True
                continue
            outcome = self._scrub_and_unlink(path)
            if outcome == "retry":
                retry = True
            elif outcome == "kept":
                kept = True
        if self.conn is not None:
            self._save_kept_backups()
        return "retry" if retry else "kept" if kept else "ok"

    _FILE_ATTRIBUTE_REPARSE_POINT = 0x400

    @staticmethod
    def _is_all_zero(path: Path) -> bool:
        with open(path, "rb") as f:
            while True:
                block = f.read(1 << 20)
                if not block:
                    return True
                if block.count(0) != len(block):
                    return False

    def _scrub_and_unlink(self, path: Path) -> str:
        """Zero ``path`` in place (best effort), fsync, delete. "gone" / "retry" / "kept".

        * Never follows a link out of the data folder: a symlink / junction / other reparse point, or a
          file with more than one hard link, is only UNLINKED (its own name goes; the data it points at,
          which may be anything anywhere, is never written). If such a link cannot be unlinked because
          the file is read-only, it is given up on for good ("kept": recorded, logged once, never
          retried) - clearing the attribute would change the file it shares with the other name.
        * Never re-zeroes: a file already zeroed this session (or found all-zero) is only unlinked on a
          retry - a reader holding it open must not see it rewritten every few seconds.
        * A read-only app file that is NOT linked (it carries our exact name, in our folder) is made
          writable first.
        * A failure is a WARNING once per file per session, then DEBUG (a held file is retried)."""
        key = str(path)
        linked = False
        try:
            st = os.lstat(path)
            if stat.S_ISDIR(st.st_mode):
                return "gone"   # never ours to delete; not counted as left behind
            linked = (stat.S_ISLNK(st.st_mode)
                      or bool(getattr(st, "st_file_attributes", 0) & self._FILE_ATTRIBUTE_REPARSE_POINT)
                      or st.st_nlink > 1)
            if not linked:
                if not st.st_mode & stat.S_IWRITE:
                    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
                if key not in self._zeroed and st.st_size and not self._is_all_zero(path):
                    with open(path, "r+b", buffering=0) as f:
                        chunk = bytes(1 << 20)
                        left = st.st_size
                        while left > 0:
                            n = min(left, len(chunk))
                            f.write(chunk[:n])
                            left -= n
                        os.fsync(f.fileno())
                self._zeroed.add(key)
            os.unlink(path)
            self._zeroed.discard(key)
            self._kept_backups.discard(path.name.lower())
            self.logger.info("Removed database backup %s%s.", path.name, " (a link: unlinked only)" if linked else "")
            return "gone"
        except FileNotFoundError:
            return "gone"
        except OSError as e:
            if linked and isinstance(e, PermissionError):
                try:
                    readonly = not os.lstat(path).st_mode & stat.S_IWRITE
                except OSError:
                    readonly = False
                if readonly:
                    if path.name.lower() not in self._kept_backups:
                        self._kept_backups.add(path.name.lower())
                        self.logger.warning(
                            "Database backup %s is read-only and linked to another file; it cannot be "
                            "removed without changing that file, so it is left in place: %s", path.name, e)
                    return "kept"
            if key in self._backup_warned:
                self.logger.debug("Database backup %s still could not be removed: %s", path.name, e)
            else:
                self._backup_warned.add(key)
                self.logger.warning("Could not remove database backup %s (will retry): %s", path.name, e)
            return "retry"

    def _observe_clock(self, now_ts: int, prev_maintenance: Optional[int],
                       mono: Optional[float]) -> Tuple[float, bool]:
        """One age-out pass's view of the clock: (steady seconds to credit to every backup present,
        whether the clock has been continuous for BACKUP_CLOCK_CONTINUITY_DAYS).

        A JUMP resets the continuity count and credits nothing:
          * across restarts / passes: the gap since the previous maintenance is negative or longer
            than BACKUP_CLOCK_MAX_GAP_DAYS (app-off time looks like a gap; a bigger one is suspect);
          * within a session (``mono`` known on both passes): wall-clock and monotonic time disagree by
            more than the tolerance - catches a +13 day jump that the gap rule would take for app-off.
        Steady credit is at most BACKUP_STEADY_CREDIT_MAX_SEC per pass (and never more than monotonic
        time, in a session), so it measures time the app OBSERVED, not what the dates claim."""
        R = constants.data.retention
        key = "backup_clock_continuous_since"
        row = self.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        since = int(row[0]) if row and str(row[0]).isdigit() else None
        gap = None if prev_maintenance is None else now_ts - prev_maintenance
        jump = gap is None or gap < -3600 or gap > R.BACKUP_CLOCK_MAX_GAP_DAYS * 86400
        mono_gap = None
        last = self._last_clock
        if mono is not None and last is not None and last[1] is not None:
            wall_d, mono_gap = now_ts - last[0], mono - last[1]
            if abs(wall_d - mono_gap) > max(R.BACKUP_CLOCK_DRIFT_TOLERANCE_SEC, 0.01 * max(mono_gap, 0.0)):
                jump = True
        self._last_clock = (now_ts, mono)
        if jump and gap is not None:
            self.logger.info("Clock moved %+.1f days since the last maintenance (or disagrees with the "
                             "monotonic clock); database backups are not aged out until it has run "
                             "steadily for %d days.", gap / 86400.0, R.BACKUP_CLOCK_CONTINUITY_DAYS)
        if jump or since is None or since > now_ts:
            since = now_ts
            with self._txn() as cur:
                cur.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, str(since)))
        credit = 0.0
        if not jump:
            credit = min(float(max(gap, 0)), float(R.BACKUP_STEADY_CREDIT_MAX_SEC))
            if mono_gap is not None:
                credit = min(credit, max(mono_gap, 0.0))
        return credit, now_ts - since >= R.BACKUP_CLOCK_CONTINUITY_DAYS * 86400

    _SEEN_PREFIX = "backup_seen_s:"

    def _credit_backups_seen(self, files: List[Path], credit: float) -> Dict[str, float]:
        """Add ``credit`` steady seconds to every backup present (metadata ``backup_seen_s:<name>``) and
        drop the counts of backups that are gone. Returns name(lower) -> seconds seen."""
        present = {p.name.lower() for p in files}
        seen = {}
        for k, v in self.conn.execute("SELECT key, value FROM metadata WHERE key LIKE ?",
                                      (self._SEEN_PREFIX + "%",)).fetchall():
            try:
                seen[k[len(self._SEEN_PREFIX):]] = float(v)
            except (TypeError, ValueError):
                seen[k[len(self._SEEN_PREFIX):]] = 0.0
        with self._txn() as cur:
            for name in list(seen):
                if name not in present:
                    cur.execute("DELETE FROM metadata WHERE key = ?", (self._SEEN_PREFIX + name,))
                    del seen[name]
            for name in present:
                seen[name] = seen.get(name, 0.0) + credit
                cur.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                            (self._SEEN_PREFIX + name, str(int(seen[name]))))
        return seen

    def _age_out_backups(self, retention_days: float, now: datetime,
                         prev_maintenance: Optional[int] = None, mono: Optional[float] = None) -> None:
        """Maintenance: an app-made history copy (backup, legacy ``.bak``, ``.stray_<ts>``) ages out with
        "Keep history for". A wrong deletion is unrecoverable, so it takes ALL of:
          * age by its dates >= the retention (and >= BACKUP_MIN_KEEP_DAYS): from the LATER of its name
            date and its mtime, never 'now' alone; a file dated in the future is kept;
          * >= BACKUP_MIN_KEEP_DAYS of STEADY time the app has observed with that file present
            (_observe_clock / backup_seen_s:<name>) - a clock corrected forward, or a jump the gap rule
            would take for app-off time, can make the dates look old but never adds observed time;
          * a clock continuous for BACKUP_CLOCK_CONTINUITY_DAYS (a jump to 2030 would otherwise make a
            day-old backup look years old, on the pass that prunes the live rows);
          * for anything older than a v8 file (a ``.bak.v<N>`` with N < 8, the bare ``.bak``, a stray): a
            live file that records a COMPLETED v7 -> v8 migration (migrated_from_v7_at) - a fresh v8
            file next to a v7 backup (a stuck upgrade) means that backup is the only history.
        Never while migrate_v8.journal exists; never a file given up on as unremovable."""
        if self.conn is None:
            return
        R = constants.data.retention
        now_ts = int(now.timestamp())
        credit, continuous = self._observe_clock(now_ts, prev_maintenance, mono)
        files = [p for p in self._history_backup_files() if not self._history_file_kind(p.name)[3]]
        seen = self._credit_backups_seen(files, credit)
        keep_days = max(float(retention_days), float(R.BACKUP_MIN_KEEP_DAYS))
        if keep_days >= R.UNLIMITED_DAYS or not continuous:
            return
        if migrate_v8.journal_path_for(self.db_path).exists():
            return
        if not self._kept_backups:
            self._load_kept_backups()
        row = self.conn.execute("SELECT value FROM metadata WHERE key = ?",
                                (self.MIGRATED_FROM_V7_KEY,)).fetchone()
        migrated = row is not None
        cutoff = now_ts - keep_days * 86400.0
        for path in files:
            try:
                kind, version, stamp, _side = self._history_file_kind(path.name)
                if (kind != "bak" or version < 8) and not migrated:
                    continue
                if path.name.lower() in self._kept_backups:
                    continue
                if seen.get(path.name.lower(), 0.0) < R.BACKUP_MIN_KEEP_DAYS * 86400:
                    continue
                st = os.lstat(path)
                made = st.st_mtime
                if stamp:
                    made = max(made, datetime.strptime(stamp, "%Y%m%d_%H%M%S").timestamp())
                if made > now_ts + 86400 or made >= cutoff:
                    continue
                outcomes = [self._scrub_and_unlink(p)
                            for p in [path] + [Path(str(path) + side) for side in self._SIDECARS]
                            if os.path.lexists(p)]
                if all(o == "gone" for o in outcomes):
                    self.logger.info("Database backup %s is older than the history retention (%d days); "
                                     "removed.", path.name, int(keep_days))
            except Exception as e:   # one bad file never stops the others
                self.logger.debug("Backup age-out skipped %s: %s", path.name, e)
        self._save_kept_backups()

    def _vacuum_now(self, quiet: bool = False) -> bool:
        """Hand every free page back (v8: ``incremental_vacuum``, never a full-file VACUUM). True on
        success; a failure is a WARNING (DEBUG when ``quiet``: a retry)."""
        try:
            self._writer.incremental_vacuum(None)
            return True
        except sqlite3.Error as e:
            (self.logger.debug if quiet else self.logger.warning)(
                "Incremental vacuum after history delete failed: %s", e)
            return False

    def _checkpoint_truncate(self, attempts: int) -> bool:
        """PRAGMA wal_checkpoint(TRUNCATE), retried up to ``attempts`` times. True only when the row
        reports busy == 0 (the whole WAL was copied back and the file truncated).

        The connection's 5 s busy_timeout is lowered for these attempts: a checkpoint blocked by a
        reader would otherwise park this thread (and every queued write) for 5 s per attempt - and
        the idle retry would do it again every few seconds while the Monitor stays open."""
        try:
            prev = self.conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        except (sqlite3.Error, TypeError):
            prev = 5000
        try:
            self.conn.execute(f"PRAGMA busy_timeout = {int(constants.timeouts.DB_BUSY_TIMEOUT_MS)};")
            for i in range(max(1, attempts)):
                try:
                    row = self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
                    if row is not None and int(row[0]) == 0:
                        return True
                except sqlite3.Error as e:
                    self.logger.debug("WAL checkpoint attempt %d failed: %s", i + 1, e)
                if i + 1 < attempts:
                    time.sleep(constants.timeouts.DB_CHECKPOINT_RETRY_SLEEP)
            return False
        finally:
            try:
                self.conn.execute(f"PRAGMA busy_timeout = {int(prev)};")
            except sqlite3.Error:
                pass

    def _retry_pending_compaction(self, force: bool = False) -> None:
        """Finish a compaction a Delete all history couldn't (run from the idle tick, throttled)."""
        if not self._compact_pending or self.conn is None or self._writer is None:
            return
        now = time.monotonic()
        if not force and now - self._last_compact_attempt < constants.timeouts.DB_COMPACT_RETRY_INTERVAL_SEC:
            return
        self._last_compact_attempt = now
        T = constants.timeouts
        if self._vacuum_pending and self._vacuum_attempts < T.DB_BACKUP_RETRY_MAX_ATTEMPTS:
            # Never blocks the rest (the old early return starved the checkpoint and the backups).
            if self._vacuum_now(quiet=self._vacuum_attempts > 0):
                self._vacuum_pending = False
            else:
                self._vacuum_attempts += 1
        if self._backups_pending and (force or now >= self._backup_retry_at) \
                and self._backup_attempts < T.DB_BACKUP_RETRY_MAX_ATTEMPTS:
            outcome = self._remove_history_backups(retrying=True)
            if outcome in ("ok", "kept"):
                self._backups_pending = False   # done, or only files given up on for good are left
            elif outcome == "journal":
                self._backup_attempts = T.DB_BACKUP_RETRY_MAX_ATTEMPTS   # a later start, once it is gone
            else:
                self._backup_attempts += 1
                self._backup_retry_at = now + min(
                    T.DB_COMPACT_RETRY_INTERVAL_SEC * (2 ** self._backup_attempts), T.DB_BACKUP_RETRY_MAX_SEC)
                if self._backup_attempts >= T.DB_BACKUP_RETRY_MAX_ATTEMPTS:
                    self.logger.warning("A database backup still could not be removed after %d attempts; "
                                        "trying again at the next start.", self._backup_attempts)
        backups_owed = self._backups_pending
        if self._compact_after_maintenance:
            # Only an hourly pass's busy checkpoint is owed (no history delete): nothing to carry over
            # a restart, so no marker writes - just retry the checkpoint on the short timeout.
            if self._wal_pending and self._checkpoint_truncate(1):
                self._wal_pending = False
            if not self._wal_pending:
                self._compact_pending = False
                self._compact_after_maintenance = False
            return
        if self._wal_pending:
            # The restart marker is written to its final state BEFORE the checkpoint, so the
            # checkpoint also folds that write in; a failed checkpoint re-arms it.
            self._set_compaction_marker(backups_owed, backups=backups_owed)
            if self._checkpoint_truncate(1):
                self._wal_pending = False
            else:
                self._set_compaction_marker(True, backups=backups_owed)
        elif not backups_owed:
            self._set_compaction_marker(False)
        vacuum_owed = self._vacuum_pending and self._vacuum_attempts < T.DB_BACKUP_RETRY_MAX_ATTEMPTS
        if not self._wal_pending and not vacuum_owed and (
                not backups_owed or self._backup_attempts >= T.DB_BACKUP_RETRY_MAX_ATTEMPTS):
            # Done - or only a backup is left, given up for this session (the "2" marker carries it).
            self._compact_pending = False
            if not backups_owed:
                self.logger.info("Deferred compaction after history delete completed.")
            self.database_updated.emit()

    _COMPACT_MARKER_KEY = "pending_history_compaction"
    # Marker values: "1" = a checkpoint is owed (2.1.x writes this one too, and the migration carries
    # it over); "2" = a checkpoint AND backup removal are owed. Only a "2" ever removes a backup at
    # startup - a "1" from a 2.1.x file must never take the upgrade's own v7 backup with it.
    _MARKER_BACKUPS = "2"

    def _set_compaction_marker(self, on: bool, backups: bool = False) -> None:
        """Persist 'a history delete still owes a checkpoint' (and, with ``backups``, 'still owes
        removing a database backup') in metadata, so a quit before the idle retry lands is picked up
        at the next start (_resume_pending_compaction)."""
        try:
            with self._txn() as cur:
                if on:
                    cur.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                                (self._COMPACT_MARKER_KEY, self._MARKER_BACKUPS if backups else "1"))
                else:
                    cur.execute("DELETE FROM metadata WHERE key = ?", (self._COMPACT_MARKER_KEY,))
        except sqlite3.Error as e:
            self.logger.debug("Compaction marker not updated: %s", e)

    def _resume_pending_compaction(self) -> None:
        """At startup: a previous session deleted history but quit before the file was compacted
        (the marker may also have been carried over from a 2.1.x file by the migration)."""
        try:
            row = self.conn.execute("SELECT value FROM metadata WHERE key = ?",
                                    (self._COMPACT_MARKER_KEY,)).fetchone()
        except sqlite3.Error:
            return
        if row:
            self.logger.info("Resuming the file compaction a previous history delete could not finish.")
            self._compact_pending = True
            self._compact_after_maintenance = False
            self._vacuum_pending = True    # cheap insurance: the old pages may also be on the freelist
            self._wal_pending = True
            self._backup_attempts = 0
            self._vacuum_attempts = 0
            # Only a 2.2 delete that could not remove a backup (locked) asks for that here.
            self._backups_pending = str(row[0]) == self._MARKER_BACKUPS
            self._retry_pending_compaction(force=True)

    @staticmethod
    def _retention_cutoff(now: datetime, retention_days: float) -> int:
        """Unix-seconds cutoff ``retention_days`` before ``now``, computed arithmetically and floored at
        0 (the 36500-day "keep forever" option lands before 1970, where ``datetime.timestamp()``
        raises OSError(22) on Windows)."""
        try:
            secs = float(now.timestamp()) - float(retention_days) * 86400.0
        except (TypeError, ValueError, OverflowError, OSError):
            return 0
        return max(0, int(secs))

    def _reconnect(self) -> None:
        """Closes and re-opens the write connection; the writer keeps its session and buffers and is
        pointed at the new connection with its cached ids dropped."""
        self._abandon_connection()
        self.msleep(1000)
        self._initialize_connection()
        if self._writer is not None:
            self._writer.conn = self.conn
            self._writer.invalidate_caches()
