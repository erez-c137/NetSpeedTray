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
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple, Literal, Union

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer

from netspeedtray import constants
from netspeedtray.constants import network, timeouts
from netspeedtray.core import usage_snapshot
from netspeedtray.core.db.history_reader import (
    HistoryNotReadyError, HistoryReader, MeteredScope, V7HistoryReader, V8HistoryReader, VolumeResult,
)
from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri
from netspeedtray.core.db.writer_v8 import NetSample
from netspeedtray.utils.helpers import get_app_data_path
from netspeedtray.utils.timer_utils import resolve_poll_interval_seconds

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


@dataclass(slots=True, frozen=True)
class HardwareStatSnapshot:
    """Represents a single hardware utilization data point."""
    value: float
    timestamp: datetime


# --- Database Worker Thread ---
from netspeedtray.core.database import DatabaseWorker


class WidgetState(QObject):
    """Manages all system statistics and bandwidth history for the Tray Widget."""

    # Re-emitted from DatabaseWorker.schema_incompatible: (file_version,
    # supported_version, newest_backup_full_path or ""). The worker detects the
    # downgrade on its own thread during startup; chaining through this QObject queues
    # delivery onto the GUI thread's event loop, which only spins after the view has
    # finished construction and connected (main.py wires the flyout there) - so the
    # notification cannot be emitted before anyone is listening.
    db_schema_incompatible = pyqtSignal(int, int, str)
    # Re-emitted from DatabaseWorker.migration_deferred (a MIGRATION_* reason): the v7 -> v8 upgrade
    # could not complete this launch, so nothing is recorded until the next start. Same queued
    # delivery as above, so the flyout is wired before it can fire.
    db_migration_deferred = pyqtSignal(str)
    # Re-emitted from DatabaseWorker.history_upgraded: this launch upgraded the history to v8 (the
    # one-time "history upgraded" flyout). Same queued delivery as above.
    db_history_upgraded = pyqtSignal()
    # Re-emitted from DatabaseWorker.history_deleted (a DatabaseWorker.DELETE_* status), queued onto
    # the GUI thread.
    history_deleted = pyqtSignal(str)

    def __init__(self, config: Dict[str, Any], read_only: bool = False) -> None:
        super().__init__()
        self.logger = logger
        self.config = config.copy()
        # read_only: a headless reader (the --export-csv CLI) that must NOT start a second write thread
        # against the live app's DB, nor run maintenance/VACUUM. Reads go straight through
        # _get_read_conn(); the worker is constructed but never started, so flush_and_wait short-circuits.
        self._read_only = read_only

        # In-Memory Cache for real-time mini-graph.
        #   * in_memory_history holds per-tick raw snapshots (a dict per entry - heavy), so it stays
        #     sized to the *current* window.
        #   * the mini-graph series (aggregated speed + per-stat hardware %) are light (a few floats per
        #     entry), so they buffer the *maximum* selectable window. That way GROWING the graph timespan
        #     reveals already-recorded samples immediately instead of waiting minutes for them to
        #     accumulate; the renderer slices each series down to the configured window.
        self.max_history_points: int = self._get_max_history_points()
        self._graph_buffer_points: int = self._get_graph_buffer_points()
        self.in_memory_history: Deque[SpeedDataSnapshot] = deque(maxlen=self.max_history_points)
        self.aggregated_history: Deque[AggregatedSpeedData] = deque(maxlen=self._graph_buffer_points)

        # New: Hardware history for mini-graph tabs
        self.cpu_history: Deque[HardwareStatSnapshot] = deque(maxlen=self._graph_buffer_points)
        self.gpu_history: Deque[HardwareStatSnapshot] = deque(maxlen=self._graph_buffer_points)
        self.ram_history: Deque[HardwareStatSnapshot] = deque(maxlen=self._graph_buffer_points)
        
        # Batching lists for database writes (schema v8): one NetSample per poll (exact byte deltas
        # over the measured interval - writer_v8), and hardware samples.
        self._net_batch: List[NetSample] = []
        self._hw_batch: List[Tuple[int, str, float]] = []
        # Guards the two batch lists: they're APPENDED on the GUI thread but flushed (snapshot+reset)
        # from the graph-worker thread (get_speed_history(wait_for_flush=True)). Without it an append
        # landing between the snapshot and the reset would silently drop that persisted row (#6).
        self._batch_lock = threading.Lock()

        # Database Worker Thread
        self._db_path = Path(get_app_data_path()) / "speed_history.db"
        self.db_worker = DatabaseWorker(self._db_path, poll_ms=self._poll_ms(),
                                        is_physical=self._classify_physical)
        self.db_worker.error.connect(lambda msg: self.logger.error("DB Worker Error: %s", msg))
        # Surface the downgrade guard (v2.1.5, action-plan item 1) and a deferred v8 upgrade (2.2).
        # Connected BEFORE start() so the worker can never trip either while nothing is wired.
        self.db_worker.schema_incompatible.connect(self.db_schema_incompatible)
        self.db_worker.migration_deferred.connect(self.db_migration_deferred)
        self.db_worker.history_upgraded.connect(self.db_history_upgraded)
        # The odometer reset waits for the delete's answer (connected FIRST, so the state is settled
        # before Settings hears the same status through the re-emit below).
        self._usage_reset_pending = False
        self._usage_reset_stash: List[Tuple[Any, ...]] = []
        self._usage_post_reset: List[Any] = [0.0, 0.0, None]
        self.db_worker.history_deleted.connect(self._on_worker_history_deleted)
        self.db_worker.history_deleted.connect(self.history_deleted)
        if not read_only:
            self.db_worker.start()

        # Timers for periodic operations (constructed either way so cleanup() stays simple, but only
        # started for a live instance - a read_only export never writes or runs maintenance).
        self.batch_persist_timer = QTimer(self)
        self.batch_persist_timer.timeout.connect(self.flush_batch)
        self.maintenance_timer = QTimer(self)
        self.maintenance_timer.timeout.connect(self.trigger_maintenance)
        if not read_only:
            self.batch_persist_timer.start(10 * 1000) # Persist every 10 seconds
            self.maintenance_timer.start(60 * 60 * 1000) # Run maintenance every hour
            self.trigger_maintenance()

        self._read_conns: Dict[int, Tuple[sqlite3.Connection, float]] = {}   # tid -> (conn, last_used)
        self._read_conns_lock = threading.Lock()
        # Set by cleanup(): from then on _get_read_conn refuses (readers read empty) instead of opening
        # a fresh read-write connection that nothing would ever close.
        self._reads_closed = False
        # Threads whose reads were interrupted for a stop (interrupt_reads(thread_id=...)): their
        # further reads are refused too, so the rest of an in-flight Monitor refresh fails fast
        # instead of running its next query. Cleared when the thread releases its connection.
        self._reads_refused: set = set()

        # Data-usage odometer (data-cap feature). Lazily loaded from the DB on first
        # use (the worker thread may not have created the table yet at construction).
        self._usage: Dict[str, Any] = {
            "cumulative_up": 0.0, "cumulative_down": 0.0,
            "anchor_up": 0.0, "anchor_down": 0.0, "period_key": "",
        }
        self._usage_loaded: bool = False
        self._usage_last_persist: float = 0.0
        # Launch reconciliation (audit item 2): the NIC counters the cumulative has been counted UP
        # TO (persisted with it, in the same transaction), and the snapshot loaded from the previous
        # session - read once at load, so this session's own persists can never feed it back.
        self._usage_live_counters: Optional[Dict[str, Any]] = None
        self._usage_live_sampled_at: Optional[float] = None
        self._usage_live_primary: Optional[str] = None
        self._usage_launch_snapshot: Dict[str, Any] = {}
        # Odometer polls that arrived before the row could be loaded (see add_usage_bytes).
        self._usage_pending: List[Tuple[Any, ...]] = []

        self.logger.debug("WidgetState initialized with threaded database worker.")


    # How long a read connection may sit untouched before another thread may close it. Generous on
    # purpose: it only has to exceed the interval at which a live worker re-fetches its connection
    # (the Monitor refreshes every few seconds), and erring high costs one idle file handle while
    # erring low would close a connection out from under a slow query.
    _READ_CONN_IDLE_SEC: float = 120.0

    def _poll_ms(self) -> int:
        return int(round(resolve_poll_interval_seconds(self.config.get("update_rate", 1.0)) * 1000))

    @staticmethod
    def _classify_physical(name: str) -> bool:
        """interfaces.is_physical, from the same classifier the Settings picker uses (2.2 scope C)."""
        from netspeedtray.utils.interface_classifier import get_classifier
        return bool(get_classifier().is_physical(name))

    def _read_schema(self) -> Tuple[Optional[int], bool, bool]:
        """(schema the live file reads as, use mode=ro connections, final).

        A live instance asks its worker, which decides this during startup (recover / migrate) -
        until it has, nothing may open the live name (a half-swapped migration leaves it absent, and a
        plain connect would create an empty database there; an open handle would also block the
        swap's rename), so readers get no connection and simply come back empty. A read-only
        instance (the --export-csv CLI) never starts the worker: it probes the file itself, read-only,
        and never runs recover() (that writes)."""
        if self._read_only:
            ver = None
            if self._db_path.exists():
                c = None
                try:
                    c = sqlite3.connect(sqlite_ro_uri(self._db_path), uri=True)
                    row = c.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()
                    ver = int(row[0]) if row else None
                except Exception as e:
                    self.logger.debug("Read-only schema probe failed: %s", e)
                finally:
                    if c is not None:
                        c.close()
            return ver, True, True
        w = self.db_worker
        ready = getattr(w, "ready", None)
        if not isinstance(ready, threading.Event):
            # A hand-assembled test double: behave as before (a plain v7 read).
            return 7, False, True
        if not ready.is_set():
            return None, True, False
        return getattr(w, "reader_version", None), bool(getattr(w, "readers_read_only", False)), True

    def _get_read_conn(self) -> sqlite3.Connection:
        """Returns a thread-local read connection, pruning ones left behind by dead threads.

        Each worker thread (notably the Monitor's GraphDataWorker, recreated on every Monitor open) opens
        its own read connection here, and closes it itself as it exits (release_read_conn, wired by
        GraphHost) - pruning is the safety net for a thread that never does. Without pruning, every
        Monitor open/close leaked a connection (a file handle + a WAL reader slot) for the whole session,
        and a recycled thread id could even hand a new worker a dead thread's stale connection. So evict
        entries whose owning thread has stopped using them.

        **Pruning is by IDLE TIME, not by thread liveness, and that is not a stylistic choice.** This
        used to evict any connection whose thread id was missing from ``threading.enumerate()`` - but
        Qt worker threads (``QThread``) never appear there unless they happen to call
        ``threading.current_thread()``, and ours only call ``get_ident()``. So every Monitor graph
        worker was invisible, and the next caller from any other thread closed its connection **while
        it was still querying** - "Cannot operate on a closed database", caught and logged, once per
        Monitor session.

        Registering the thread instead (calling ``current_thread()``) is worse: on CPython 3.11 the
        resulting ``_DummyThread`` never leaves ``enumerate()`` when the native thread dies, so
        nothing would ever be pruned and the leak this exists to prevent comes straight back
        (verified, not assumed).

        A thread mid-query has just fetched its connection, so an idle threshold it cannot trip is
        both simpler and correct. Closing cross-thread stays safe: check_same_thread=False."""
        thread_id = threading.get_ident()
        now = time.monotonic()
        with self._read_conns_lock:
            if self._reads_closed or thread_id in self._reads_refused:
                raise HistoryNotReadyError("history reads are closed (shutting down)")
            if self._read_conns:
                for stale in [tid for tid, (_c, seen) in self._read_conns.items()
                              if tid != thread_id and (now - seen) > self._READ_CONN_IDLE_SEC]:
                    try:
                        self._read_conns.pop(stale)[0].close()
                    except Exception:
                        pass
            if thread_id not in self._read_conns:
                ver, ro, _final = self._read_schema()
                if ver is None:
                    # Nothing safe to open yet (the worker is still migrating), or ever (the live name
                    # is absent until the next launch's recover()). Readers catch this and read empty.
                    raise HistoryNotReadyError("history database not available for reading")
                if ro:
                    # Read-only session (deferred upgrade / downgrade / export): never a read-write
                    # handle on a file this build must not touch - not even the checkpoint SQLite runs
                    # when the last read-write connection to a WAL file closes.
                    conn = sqlite3.connect(sqlite_ro_uri(self._db_path), uri=True, check_same_thread=False)
                else:
                    conn = sqlite3.connect(self._db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                # Wait (up to 5s) for a lock instead of erroring out immediately - otherwise a
                # read concurrent with the maintenance VACUUM can fail with "database is locked".
                conn.execute("PRAGMA busy_timeout = 5000;")
                # The Monitor reloads ~a dozen multi-tier scans every few seconds on these read
                # connections; give them the same cache + in-RAM temp B-trees + memory-mapped reads as
                # the writer so the nested-GROUP-BY history queries don't run under-provisioned.
                conn.execute("PRAGMA cache_size = -8000;")
                conn.execute("PRAGMA temp_store = MEMORY;")
                conn.execute("PRAGMA mmap_size = 268435456;")
                self._read_conns[thread_id] = (conn, now)
                return conn
            conn, _seen = self._read_conns[thread_id]
            self._read_conns[thread_id] = (conn, now)   # touch: this thread is demonstrably alive
            return conn

    def release_read_conn(self) -> None:
        """Close the CALLING thread's read connection, if it has one. A worker thread that reads
        history calls this as it exits (GraphHost wires it to its QThread's ``finished``, run on that
        thread), so every connection is closed by the thread that used it - never from under it."""
        with self._read_conns_lock:
            self._reads_refused.discard(threading.get_ident())
            entry = self._read_conns.pop(threading.get_ident(), None)
        if entry is not None:
            try:
                entry[0].close()
            except Exception:
                pass

    def interrupt_reads(self, thread_id: Optional[int] = None) -> None:
        """Abort the statement running on another thread's read connection (all other threads' when
        ``thread_id`` is None) - ``sqlite3_interrupt``, the one call SQLite documents as safe from a
        different thread. The query raises ``OperationalError('interrupted')`` on its own thread,
        which the readers catch (they read empty). The connection stays open; its owner still closes
        it. Held under the lock, so it can never race that close. A targeted ``thread_id`` is being
        stopped: its later reads are refused until it releases its connection."""
        me = threading.get_ident()
        with self._read_conns_lock:
            if thread_id is not None:
                self._reads_refused.add(thread_id)
            for tid, (conn, _seen) in self._read_conns.items():
                if tid == me or (thread_id is not None and tid != thread_id):
                    continue
                try:
                    conn.interrupt()
                except Exception:
                    pass

    @property
    def history_reader(self) -> HistoryReader:
        """The storage backend every history read goes through (v8 step 2). Built on first use rather
        than in __init__ so a WidgetState assembled by hand in tests still reads; it holds no connection
        of its own - each call fetches this thread's via _get_read_conn()."""
        reader = getattr(self, "_history_reader", None)
        if reader is not None:
            return reader
        ver, ro, final = self._read_schema()
        if ver == 7:
            reader = V7HistoryReader(self._get_read_conn, lambda: self.db_worker.db_path, self.logger)
            # The db_utils fallback opens a plain read-write connection; never on a file this session
            # must not write (a deferred upgrade leaves the v7 file live and untouched).
            reader.allow_fallback = not ro
        else:
            # v8 - and, best effort, a newer file. Before the worker is ready the conn provider
            # raises, so every read comes back empty; the backend is cached only once the answer is
            # final.
            reader = V8HistoryReader(self._get_read_conn, lambda: self.db_worker.db_path, self.logger)
        if final:
            self._history_reader = reader
        return reader

    def history_schema_version(self) -> Optional[int]:
        """The schema history is read from (8, 7 while an upgrade is deferred), or None before the
        worker has decided / when nothing can be read."""
        return self._read_schema()[0]

    def add_speed_data(self, speed_data: Dict[str, Tuple[float, float]], now: Optional[datetime] = None,
                       aggregated_up: Optional[float] = None, aggregated_down: Optional[float] = None,
                       persist_data: Optional[Dict[str, Tuple[float, float]]] = None) -> None:
        """Adds new per-interface speed data to the IN-MEMORY live series (mini graph, Monitor session
        view) - what the user sees; the controller passes its spike-FILTERED rates.

        Nothing here is persisted any more (schema v8): history stores the exact byte deltas of every
        poll, handed over by :meth:`add_net_sample`, never a rate. ``persist_data`` (the measured rates)
        is accepted for call-site compatibility and ignored."""
        _now = now or datetime.now()
        self.in_memory_history.append(SpeedDataSnapshot(speeds=speed_data.copy(), timestamp=_now))

        if aggregated_up is not None and aggregated_down is not None:
            total_up, total_down = aggregated_up, aggregated_down
        else:
            total_up = sum(speeds[0] for speeds in speed_data.values())
            total_down = sum(speeds[1] for speeds in speed_data.values())
            
        self.aggregated_history.append(AggregatedSpeedData(upload=total_up, download=total_down, timestamp=_now))

    def add_net_sample(self, ts: float, dt_ms: float, byte_deltas: Dict[str, Tuple[float, float]],
                       counters: Optional[Dict[str, Any]] = None,
                       if_stats: Optional[Dict[str, Any]] = None) -> None:
        """One poll for the v8 history: the controller's exact per-interface ``byte_deltas`` (the
        odometer's - pre-spike-filter, clamped, capped) over the measured interval ``dt_ms`` ending at
        wall time ``ts``, plus the raw psutil ``counters`` (err/drop deltas, counter resets) and, when
        sampled, ``if_stats`` (link up/down, speed, duplex). An empty ``byte_deltas`` only primes the
        writer's counter baselines. Buffered here (GUI thread) and handed to the worker - the only
        thread that touches WriterV8 - on the 10 s persist tick."""
        if self._read_only:
            return
        sample = NetSample(ts=float(ts), dt_ms=max(1, int(round(dt_ms))), byte_deltas=dict(byte_deltas),
                           counters=counters, if_stats=if_stats)
        with self._batch_lock:
            self._net_batch.append(sample)


    # Utilization stats are 0-100% and clamped; physical stats (power W, temperature °C, latency ms)
    # are NOT percentages and must be stored unclamped - a 200 ms ping or 180 W draw must not become 100.
    _PCT_STATS = frozenset({'cpu', 'gpu', 'ram', 'vram'})

    def add_hardware_stat(self, stat_type: str, value: float, now: Optional[datetime] = None,
                          persist: bool = True) -> None:
        """Record a hardware sample (utilization %, power W, temperature °C, or latency ms) to the
        in-memory deque (for graphed util stats) + the DB batch (for all, via the 3-tier rollups).

        ``persist=False`` feeds only the in-memory live series (mini graph, Monitor live tiles) and
        writes nothing to disk - the caller (StatsController, via hardware_persist_allowed) passes it
        when the user has hardware recording off or isn't monitoring that metric."""
        _now = now or datetime.now()
        snapshot = HardwareStatSnapshot(value=value, timestamp=_now)

        if stat_type == 'cpu':
            self.cpu_history.append(snapshot)
        elif stat_type == 'gpu':
            self.gpu_history.append(snapshot)
        elif stat_type == 'ram':
            self.ram_history.append(snapshot)

        if not persist:
            return
        # Clamp only the percentage stats; store physical stats unclamped (just floor at 0).
        v = max(0.0, min(100.0, value)) if stat_type in self._PCT_STATS else max(0.0, float(value))
        with self._batch_lock:
            self._hw_batch.append((int(_now.timestamp()), stat_type, v))


    # --- Data-usage odometer (data-cap feature) ------------------------------
    @staticmethod
    def _compute_period_key(reset_day: int, today: Optional[date] = None) -> str:
        """The ISO start date of the billing period containing `today`, given a
        reset day-of-month (clamped 1-28 so every month has a valid reset)."""
        today = today or date.today()
        reset_day = max(1, min(28, int(reset_day)))
        if today.day >= reset_day:
            start = date(today.year, today.month, reset_day)
        else:
            first_of_month = date(today.year, today.month, 1)
            last_prev = first_of_month - timedelta(days=1)
            start = date(last_prev.year, last_prev.month, reset_day)
        return start.isoformat()

    def _try_load_usage(self) -> bool:
        """Load the persisted odometer once the worker has created the table.
        Returns False (so the caller defers) if the table isn't ready yet - never
        overwrites a real persisted counter with a fresh zero one."""
        try:
            rec = self.history_reader.load_usage()
            if rec is None:
                return False
            row = rec.counter_row
            if row:
                # Validate the persisted row - a corrupt/negative/NaN value flowing into the cap
                # math could trigger a false 100% alert. Coerce to a finite, non-negative float
                # and never let the anchor exceed the cumulative (that would read as negative usage).
                def _f(v: object) -> float:
                    try:
                        x = float(v)
                    except (TypeError, ValueError):
                        return 0.0
                    if x != x or x in (float("inf"), float("-inf")):  # NaN / inf
                        return 0.0
                    return max(0.0, x)
                cu, cd = _f(row[0]), _f(row[1])
                au, ad = min(_f(row[2]), cu), min(_f(row[3]), cd)
                self._usage = {"cumulative_up": cu, "cumulative_down": cd,
                               "anchor_up": au, "anchor_down": ad, "period_key": str(row[4] or "")}
            self._usage_launch_snapshot = rec.snapshot
            self._usage_left = WidgetState._decode_left_periods(getattr(rec, "rekey_memory", None))
            WidgetState._drop_stale_left_periods(self)
            self._usage_loaded = True
            return True
        except Exception as e:
            self.logger.debug("Usage counter not ready yet: %s", e)
            return False

    def get_usage_launch_snapshot(self) -> Optional[Dict[str, Any]]:
        """The counter snapshot persisted by the PREVIOUS session ({} when there is none), or None
        while the odometer has not loaded yet (the caller retries)."""
        if getattr(self, "_usage_reset_pending", False):
            return None   # a reset is in flight: whether this snapshot still counts is not known yet
        if not self._usage_loaded and not self._try_load_usage():
            return None
        return self._usage_launch_snapshot

    def _maybe_reanchor(self) -> bool:
        """
        Advance the period anchor on a genuine FORWARD rollover (returns True if the period changed).

        Anchors at the cumulative-so-far, so a boundary poll's bytes (added right after this in
        add_usage_bytes) land in the NEW period instead of being lost to both. Runs on both the
        write path AND the read path, so an idle-across-reset still rolls the period over even
        with no traffic. ISO date keys sort lexically, so '>' (not '!=') makes a backward
        clock/DST shift unable to wipe the running total, and the empty initial key first-anchors.
        Two explicit events break the forward-only rule: a reset-day change and a stored period
        that starts in the future (both below).
        """
        u = self._usage
        reset_day = WidgetState._reset_day(self.config)
        pk = self._compute_period_key(reset_day)
        stored = u["period_key"]
        if stored and stored != pk:
            try:
                stored_start = date.fromisoformat(stored)
            except (TypeError, ValueError):
                stored_start = None
            if stored_start is not None and stored_start.day != reset_day:
                # Every key is a period START on the reset day, so a stored key on another day means
                # the reset day was changed (in Settings, or while the app was closed). The forward-only
                # rule below must not apply: the new period can start BEFORE the stored one (15 -> 10
                # on the 21st -> the 10th), and a stored key "newer" than every computed one froze the
                # cap - each poll then looked like a closed period's and went only to the lifetime total.
                WidgetState._rekey_for_reset_day(self, pk, stored_start)
                return True
            tolerance = timedelta(days=constants.network.usage.FUTURE_PERIOD_TOLERANCE_DAYS)
            if stored_start is not None and stored_start > date.today() + tolerance \
                    and WidgetState._clock_is_current(self, pk):
                WidgetState._rekey_from_future(self, pk, stored)
                return True
        if pk > u["period_key"]:
            back = WidgetState._usage_left_periods(self).get(pk)
            if back is not None and back["mono"] is not None and time.monotonic() - back["mono"] < \
                    constants.network.usage.FUTURE_PERIOD_TOLERANCE_DAYS * 86400 \
                    and WidgetState._restore_left_period(self, pk, "future"):
                # The clock is going back to the period this session re-keyed away from as "in the
                # future" - sooner than real time could reach it, so that re-key was the wrong call
                # (the clock was BEHIND then, not ahead before). Its anchor comes back: nothing is lost.
                self.logger.warning("Data-cap period %s restored: the clock was behind, not ahead.", pk)
            else:
                WidgetState._remember_left_period(self, u["period_key"], "rollover")
                u["anchor_up"] = u["cumulative_up"]
                u["anchor_down"] = u["cumulative_down"]
            u["period_key"] = pk
            self._persist_usage_now()
            self._usage_last_persist = time.time()
            return True
        return False

    @staticmethod
    def _reset_day(config: Dict[str, Any]) -> int:
        """The data-cap reset day as ``_compute_period_key`` uses it (clamped 1-28; 1 if unreadable)."""
        try:
            return max(1, min(28, int(config.get("data_cap_reset_day", 1))))
        except (TypeError, ValueError):
            return 1

    # --- explicit period re-keys -------------------------------------------------------------------

    _USAGE_LEFT_MAX = 8

    def _usage_left_periods(self) -> Dict[str, Dict[str, Any]]:
        """Periods the odometer left: key -> {au, ad: the anchor it had; cu, cd: the totals when it left;
        mono: time.monotonic() then (None once reloaded); why: 'rollover' / 'future' / 'reset_day'}. It
        lets a re-key that turns out wrong, or is undone (a reset day toggled back), restore the exact
        anchor. 'rollover' and 'reset_day' entries are persisted with the odometer (a restart keeps the
        exact path); 'future' entries only count within this session (their test is monotonic time).
        Cleared by a usage reset; an entry is only ever restored onto the same running total."""
        left = getattr(self, "_usage_left", None)
        if left is None:
            left = self._usage_left = {}
        return left

    def _remember_left_period(self, key: str, why: str) -> None:
        if not key:
            return
        u = self._usage
        left = WidgetState._usage_left_periods(self)
        left.pop(key, None)
        left[key] = {"au": u["anchor_up"], "ad": u["anchor_down"], "cu": u["cumulative_up"],
                     "cd": u["cumulative_down"], "mono": time.monotonic(), "why": why}
        while len(left) > WidgetState._USAGE_LEFT_MAX:
            left.pop(next(iter(left)))

    def _restore_left_period(self, key: str, why: str) -> bool:
        """Put back the anchor ``key`` had when the odometer left it for reason ``why`` - only onto the
        SAME running total: one that is now smaller (a usage reset since, or a total from another
        machine) would turn an old anchor into a wrong count."""
        u = self._usage
        back = WidgetState._usage_left_periods(self).get(key)
        if back is None or back["why"] != why:
            return False
        if u["cumulative_up"] < back["cu"] or u["cumulative_down"] < back["cd"] \
                or back["au"] > back["cu"] or back["ad"] > back["cd"]:
            return False
        u["anchor_up"], u["anchor_down"] = back["au"], back["ad"]
        return True

    def _drop_stale_left_periods(self) -> None:
        """Forget remembered periods taken on a LARGER total than the current one: the total was reset
        since (a usage reset by a build that did not clear this memory, or one undone elsewhere), so
        their anchors belong to a count that no longer exists."""
        u = self._usage
        left = WidgetState._usage_left_periods(self)
        for k in [k for k, v in left.items()
                  if v["cu"] > u["cumulative_up"] or v["cd"] > u["cumulative_down"]]:
            del left[k]

    @staticmethod
    def _encode_left_periods(left: Dict[str, Dict[str, Any]]) -> str:
        """The persistable part of the re-key memory as JSON ("" = none: the stored key is deleted)."""
        import json
        keep = {k: [v["au"], v["ad"], v["cu"], v["cd"], v["why"]] for k, v in left.items()
                if v.get("why") in ("rollover", "reset_day")}
        return json.dumps(keep, separators=(",", ":")) if keep else ""

    @staticmethod
    def _decode_left_periods(raw: Any) -> Dict[str, Dict[str, Any]]:
        import json
        out: Dict[str, Dict[str, Any]] = {}
        try:
            data = json.loads(raw) if raw else {}
            for k, v in (data.items() if isinstance(data, dict) else ()):
                au, ad, cu, cd = (float(x) for x in v[:4])
                if v[4] in ("rollover", "reset_day") and all(x >= 0 and x == x for x in (au, ad, cu, cd)):
                    date.fromisoformat(str(k))
                    out[str(k)] = {"au": au, "ad": ad, "cu": cu, "cd": cd, "mono": None, "why": v[4]}
        except Exception:
            return {}
        return out

    def _clock_is_current(self, pk: str) -> bool:
        """Evidence that the wall clock is right NOW, before a period stored in the future is treated as
        one a clock that ran ahead wrote. The same picture arises when the clock is BEHIND now (a dead
        CMOS battery, a VM snapshot restore, a date set back by hand) - re-keying then, and anchoring at
        the total when the clock is corrected, wiped the period's usage. Evidence: the history writer's
        last trusted maintenance stamp (the clock is not behind it); only when there is no stamp at
        all, the odometer having rolled forward out of exactly that period on this running total."""
        stamp = None
        try:
            stamp = self.history_reader.clock_trusted_ts()
        except Exception:
            stamp = None
        if stamp is not None:
            # The stamp decides, both ways. A clock behind it is not evidence of anything - and a
            # legit rollover always leaves the entry below, so falling back to it would re-key a date
            # set back across a reset day, merging two periods (and later wiping one). Accepted cost:
            # after a jump of a few days inside a session (which the writer trusts as sleep, so its
            # stamp runs ahead) the re-key waits until the writer re-accepts the clock (~6 h); the
            # odometer keeps counting meanwhile.
            return time.time() >= stamp - constants.data.retention.CLOCK_BEHIND_TOLERANCE_SEC
        # No stamp at all (a file the history writer has not maintained yet, a v7 file): the odometer
        # having rolled forward out of exactly this period on THIS total is the evidence; a re-key it
        # proves wrong is undone at the next rollover (see _maybe_reanchor).
        back = WidgetState._usage_left_periods(self).get(pk)
        u = self._usage
        return back is not None and back["why"] == "rollover" \
            and u["cumulative_up"] >= back["cu"] and u["cumulative_down"] >= back["cd"]

    def _rekey_from_future(self, pk: str, stored: str) -> None:
        """A period that STARTS days from now cannot be real: a poll with the clock ahead (a manual date
        change, a bad RTC before NTP, a VM restore) advanced the key, and the forward-only rule would
        hold it there - no rollover until that date. Re-key to today's period. If this session saw the
        jump, the period it left gets its exact anchor back; otherwise the anchor stays (the count since
        the jump) and, where history counts exactly what the odometer counts, it is raised to the
        recorded volume since the period began. Remembered, so a re-key that proves wrong is undone."""
        u = self._usage
        WidgetState._remember_left_period(self, stored, "future")
        if not WidgetState._restore_left_period(self, pk, "rollover"):
            hist = WidgetState._strict_period_volume(self, date.fromisoformat(pk))
            if hist is not None and WidgetState.metered_scope_is_exact(self.config):
                for i, d in enumerate(("up", "down")):
                    used = max(0.0, u[f"cumulative_{d}"] - u[f"anchor_{d}"])
                    u[f"anchor_{d}"] = max(0.0, u[f"cumulative_{d}"] - max(used, hist[i]))
        if not getattr(self, "_usage_future_key_logged", False):
            self._usage_future_key_logged = True
            self.logger.warning("Data-cap period %s starts in the future (the clock ran ahead); "
                                "back to the current period %s.", stored, pk)
        u["period_key"] = pk
        self._persist_usage_now()
        self._usage_last_persist = time.time()

    def _strict_period_volume(self, start: date) -> Optional[Tuple[float, float]]:
        """(up, down) the odometer's adapters recorded since ``start``, or None when history cannot say
        (no history, a failed read, a v7 file) - never a zero standing in for an error."""
        try:
            scope = WidgetState.metered_scope(self.config, self.history_reader.interface_names())
            v = self.history_reader.metered_volume_strict(
                datetime(start.year, start.month, start.day), datetime.now(), scope)
        except Exception as e:
            self.logger.debug("Usage re-key: no history read (%s).", e)
            return None
        if v is None:
            return None
        return (max(0.0, float(v.up_bytes)), max(0.0, float(v.down_bytes)))

    def _rekey_for_reset_day(self, new_key: str, old_start: date) -> None:
        """The reset day changed: move the odometer to the period ``new_key`` starts, with "used this
        period" as close to the bytes moved since THAT start as can be justified - never moved by more
        than history accounts for.

        A period this session already left by a reset-day change (toggled back) gets its exact anchor
        back. Otherwise the odometer's own count is the base, and recorded history (the metered adapters'
        volume since the new and since the old start) adjusts it:

        * a new period that began EARLIER contains the old one: + what history recorded between the two
          starts - only when history counts exactly what the odometer counts (not in auto mode, which
          meters one routing adapter history cannot replay), else the count is kept as is;
        * one that began LATER: history since the new start, plus what the odometer counted beyond
          history since the old start (bytes it recovered for a closed app; history never saw them) -
          never more than the count. No usable history: the count is kept.

        The cap never stops counting, and an unusable read never turns into a 0. Known over-count
        (accepted, never an under-count): after "Delete all history" with the usage kept, history
        starts at the delete, so for a later start the bytes between the old start and the delete
        look like bytes history never saw and stay counted - the odometer keeps no per-day record that
        could tell them apart. The next rollover ends it."""
        u = self._usage
        new_start = date.fromisoformat(new_key)
        WidgetState._remember_left_period(self, old_start.isoformat(), "reset_day")
        how = "restored"
        if not WidgetState._restore_left_period(self, new_key, "reset_day"):
            used = (max(0.0, u["cumulative_up"] - u["anchor_up"]),
                    max(0.0, u["cumulative_down"] - u["anchor_down"]))
            seeded, how = used, "kept the count (no usable history)"
            h_new = WidgetState._strict_period_volume(self, new_start)
            h_old = WidgetState._strict_period_volume(self, old_start) if h_new is not None else None
            if h_new is not None and h_old is not None:
                if new_start <= old_start:
                    if WidgetState.metered_scope_is_exact(self.config):
                        seeded = tuple(used[i] + max(0.0, h_new[i] - h_old[i]) for i in range(2))
                        how = "count + recorded history before the old start"
                    else:
                        how = "kept the count (history cannot match auto mode's adapter)"
                else:
                    seeded = tuple(min(used[i], h_new[i] + max(0.0, used[i] - h_old[i])) for i in range(2))
                    how = "recorded history since the new start + the count history never saw"
            u["anchor_up"] = max(0.0, u["cumulative_up"] - seeded[0])
            u["anchor_down"] = max(0.0, u["cumulative_down"] - seeded[1])
        u["period_key"] = new_key
        used_gb = (u["cumulative_up"] - u["anchor_up"] + u["cumulative_down"] - u["anchor_down"]) / 1e9
        self.logger.info("Data-cap reset day changed: period %s -> %s; usage this period %.2f GB (%s).",
                         old_start.isoformat(), new_key, used_gb, how)
        self._persist_usage_now()
        self._usage_last_persist = time.time()

    def add_usage_bytes(self, up_bytes: float, down_bytes: float,
                        counters: Optional[Dict[str, Any]] = None,
                        sampled_at: Optional[float] = None,
                        window_start: Optional[float] = None,
                        primary: Optional[str] = None) -> None:
        """Accumulate exact transferred bytes into the odometer, re-anchoring on a new
        billing period, and persist on a throttle. Called every poll; cheap.

        `counters` are the raw NIC counters the total now accounts up to (sampled at wall time
        `sampled_at`; `primary` is the auto-mode interface they were metered on); they are persisted
        with the total so the next launch in the same boot can recover what moves while the app is
        closed. Kept by reference - serialized only on persist.

        `window_start` is the wall time the measurement window began (the previous sample, or the
        launch snapshot). Bytes are charged to the period in which their window BEGAN: a standby
        gap or a closed-app window spanning the reset day lands in the period it started in, never
        all in the new one (which could fire an early overage alert)."""
        if getattr(self, "_usage_reset_pending", False):
            # Traffic after a reset request: if the reset lands, these bytes are the new total.
            post = self._usage_post_reset
            post[0] += max(0.0, up_bytes)
            post[1] += max(0.0, down_bytes)
            if counters is not None:
                post[2] = (counters, sampled_at if sampled_at is not None else time.time(), primary)
        if not self._usage_loaded and not self._try_load_usage():
            # Not loaded yet - the worker is still opening (or migrating) the file and the readers are
            # held off until it has. Never write a fresh zero over the saved total, and never drop these
            # bytes either: on a large v7 file the upgrade takes seconds, and every poll in that window
            # is real usage. Buffered in memory (bounded) and replayed, in order, once the row loads.
            pend = getattr(self, "_usage_pending", None)
            if pend is None:
                pend = self._usage_pending = []
            if len(pend) < WidgetState._USAGE_PENDING_MAX:
                pend.append((up_bytes, down_bytes, counters, sampled_at, window_start, primary))
            else:
                last = pend[-1]   # fold the overflow into the newest entry: totals stay exact
                pend[-1] = (last[0] + up_bytes, last[1] + down_bytes, counters, sampled_at, last[4], primary)
            return
        if getattr(self, "_usage_pending", None):
            pending, self._usage_pending = self._usage_pending, []
            for args in pending:
                WidgetState._add_usage_loaded(self, *args)
        WidgetState._add_usage_loaded(self, up_bytes, down_bytes, counters, sampled_at, window_start, primary)

    _USAGE_PENDING_MAX = 4096

    def _add_usage_loaded(self, up_bytes: float, down_bytes: float, counters: Optional[Dict[str, Any]],
                          sampled_at: Optional[float], window_start: Optional[float],
                          primary: Optional[str]) -> None:
        u = self._usage
        # Roll the period over BEFORE adding this poll, so a boundary-crossing poll's bytes
        # count toward the NEW period rather than being stranded at the anchor (audit #15).
        # (This may persist - with the PREVIOUS counters, which is what the total still matches.)
        rolled = self._maybe_reanchor()
        up, down = max(0.0, up_bytes), max(0.0, down_bytes)
        u["cumulative_up"] += up
        u["cumulative_down"] += down
        if window_start is not None and (up or down):
            try:
                start_pk = self._compute_period_key(self.config.get("data_cap_reset_day", 1),
                                                    date.fromtimestamp(window_start))
            except (OverflowError, OSError, ValueError):
                start_pk = u["period_key"]
            # Compared with the OLDER of the stored and the computed key: a stored key newer than
            # today's period (a clock that ran ahead, see _maybe_reanchor) must not make every window
            # look like a closed period's - that froze the cap's count.
            today_pk = self._compute_period_key(self.config.get("data_cap_reset_day", 1))
            if start_pk < min(u["period_key"], today_pk):
                # The window began in an already-closed period: lifetime total yes, this period no.
                # Moving the anchor with it works whether the rollover happened just now or earlier
                # (e.g. on a read) - the anchor never exceeds the cumulative.
                u["anchor_up"] += up
                u["anchor_down"] += down
        if counters is not None:
            # Only now does the total include these counters' bytes - pair them from here on.
            self._usage_live_counters = counters
            self._usage_live_sampled_at = sampled_at if sampled_at is not None else time.time()
            self._usage_live_primary = primary
        if rolled:
            self._persist_usage_now()  # capture this poll's bytes into the fresh period now
            self._usage_last_persist = time.time()
            return

        now = time.time()
        if now - self._usage_last_persist >= 30:
            self._persist_usage_now()
            self._usage_last_persist = now

    def _persist_usage_now(self) -> None:
        if not self._usage_loaded or getattr(self, "_usage_reset_pending", False):
            # While a reset is in flight nothing is written: the old totals must not land after the
            # delete (resurrecting them), and zeros must not land unless the delete succeeds.
            # (Session end with a reset still in flight: persist_usage_final.)
            return
        self.db_worker.enqueue_task("persist_usage", WidgetState._usage_payload(
            self._usage, (self._usage_live_counters, self._usage_live_sampled_at, self._usage_live_primary),
            WidgetState._encode_left_periods(WidgetState._usage_left_periods(self))))

    @staticmethod
    def _usage_payload(u: Dict[str, Any], live: Optional[Tuple[Any, Any, Any]],
                       memory: Optional[str] = None) -> Tuple[Any, ...]:
        """The persist_usage tuple for odometer ``u`` paired with ``live`` = (counters, sampled_at,
        primary) - the NIC counters that total accounts up to, or counters None - and ``memory``, the
        encoded re-key memory ("" deletes it, None leaves it untouched)."""
        snapshot_json = None
        counters, sampled_at, primary = live if live else (None, None, None)
        if counters is not None:
            # Fresh boot id at every persist (not cached at start): boot_time() follows wall-clock
            # corrections, so the latest reading is the one the next launch will compare against.
            snapshot_json = usage_snapshot.encode_snapshot(
                counters, sampled_at or time.time(), usage_snapshot.current_boot_time(), primary=primary)
            if snapshot_json is None:
                # Counters seen but no trustworthy snapshot (boot id unreadable): the total is about
                # to advance past the STORED snapshot, so it must go - leaving it would make the next
                # same-boot launch re-add bytes this total already holds.
                snapshot_json = usage_snapshot.CLEAR_SNAPSHOT
        # 7th element: the counter snapshot, written in the SAME transaction as the total;
        # CLEAR_SNAPSHOT deletes the stored one; None (no counters seen yet) leaves it untouched.
        return (u["cumulative_up"], u["cumulative_down"], u["anchor_up"], u["anchor_down"],
                u["period_key"], int(time.time()), snapshot_json, memory)

    def persist_usage_final(self) -> None:
        """Session end: persist the odometer tail (the ~30 s throttle window). With a usage reset still
        unanswered on this thread, BOTH outcomes are handed to the write thread, which ran the delete
        already (FIFO) and persists the one that matches: the traffic seen since the request if the
        reset committed, or the old total plus everything since if it failed. Never zeros-by-guess."""
        if not getattr(self, "_usage_reset_pending", False):
            WidgetState._persist_usage_now(self)
            return
        post = self._usage_post_reset
        reset = {"cumulative_up": float(post[0]), "cumulative_down": float(post[1]),
                 "anchor_up": 0.0, "anchor_down": 0.0,
                 "period_key": self._compute_period_key(self.config.get("data_cap_reset_day", 1))}
        if_failed = None
        if self._usage_loaded:
            kept = dict(self._usage)
            for up, down, *_rest in self._usage_reset_stash:   # the set-aside pre-request polls
                kept["cumulative_up"] += max(0.0, up)
                kept["cumulative_down"] += max(0.0, down)
            if_failed = WidgetState._usage_payload(
                kept, (self._usage_live_counters, self._usage_live_sampled_at, self._usage_live_primary),
                WidgetState._encode_left_periods(WidgetState._usage_left_periods(self)))
        self.db_worker.enqueue_task("persist_usage_after_delete", {
            "if_reset": WidgetState._usage_payload(reset, post[2], ""),
            "if_failed": if_failed})

    def get_usage_this_period(self) -> Tuple[float, float]:
        """(upload_bytes, download_bytes) used since the current period's reset day."""
        if not self._usage_loaded and not self._try_load_usage():
            return (0.0, 0.0)
        # Roll over on read too: if the reset day passed with no traffic, the anchor would
        # otherwise stay stale and report last period's usage as this period's (audit #5).
        self._maybe_reanchor()
        u = self._usage
        return (max(0.0, u["cumulative_up"] - u["anchor_up"]),
                max(0.0, u["cumulative_down"] - u["anchor_down"]))

    def get_usage_lifetime(self) -> Optional[Tuple[float, float]]:
        """(upload_bytes, download_bytes) the odometer has counted since it began (never reset by a
        period rollover; only "Delete all history" with the usage option zeroes it). None until the
        odometer has loaded."""
        if not self._usage_loaded and not self._try_load_usage():
            return None
        u = self._usage
        return (max(0.0, u["cumulative_up"]), max(0.0, u["cumulative_down"]))

    def get_usage_period_key(self) -> str:
        """
        The current billing-period key (for the alert controller's restart-safe state).
        Returns the odometer's MONOTONIC period key (advanced forward-only) so a backward
        clock/DST shift can't change it and spuriously re-fire a threshold alert - and so the
        alert period and the odometer period are one source of truth.
        """
        if self._usage_loaded or self._try_load_usage():
            self._maybe_reanchor()
            return self._usage["period_key"] or self._compute_period_key(
                self.config.get("data_cap_reset_day", 1))
        return self._compute_period_key(self.config.get("data_cap_reset_day", 1))

    def flush_batch(self) -> None:
        """Sends all batches to the database worker. Each list is swapped out for a fresh one under the
        lock (atomic snapshot+reset), so a concurrent GUI-thread append can't land between copy and
        clear and be lost (#6). Enqueue happens after the lock is released (the worker queue is its own
        thread-safe handoff)."""
        if self._read_only:
            return
        with self._batch_lock:
            net_batch, self._net_batch = self._net_batch, []
            hw_batch, self._hw_batch = self._hw_batch, []
            # Enqueued INSIDE the lock (queue.put on the unbounded worker queue never blocks), so a
            # flush from the graph-worker thread can't slip a pre-delete batch in behind a
            # delete_all_history() that ran between the snapshot and the enqueue. Enqueued even when
            # empty: the worker's flush also writes the app_sessions heartbeat (the coverage record).
            self.db_worker.enqueue_task("persist_v8", (net_batch, hw_batch))

    def can_delete_history(self) -> bool:
        """Whether a delete request would be answered: a live (not read-only) instance whose write
        thread is running and accepting writes. Checked up front so Settings never waits forever."""
        w = self.db_worker
        return (not self._read_only and w.isRunning()
                and not getattr(w, "_schema_incompatible", False))

    def delete_all_history(self, include_usage: bool = False) -> None:
        """
        "Delete all history" (Settings > Advanced): wipe every recorded speed/hardware/latency sample,
        on disk (via the DatabaseWorker, then VACUUM) and in memory, and keep recording.

        Order matters, and all of it is on the GUI thread, which is where samples are appended:
          1. under the batch lock, drop the not-yet-flushed batches and enqueue the delete - so a
             sample taken before the request can never be written after it (flush_batch also
             enqueues under this lock);
          2. clear the in-memory series IN PLACE (readers take list() copies; reassigning would
             strand a reference held elsewhere), so the mini graph and the Monitor's live windows
             stop showing deleted data right away;
          3. only if the user opted in, reset the data-usage odometer - on disk inside the delete's own
             transaction (WriterV8 clears usage_counter AND its counter snapshot), in memory only once
             the delete reports success (_on_worker_history_deleted). Until then nothing about the
             odometer is persisted; a FAILED delete leaves it exactly as it was.

        Not touched: settings, the latency probe's short live window (~2 min, never on disk on its
        own), and - unless ``include_usage`` - the odometer (D5: the data cap's billing source).
        Completion is reported by ``history_deleted``.
        """
        if self._read_only:
            return
        with self._batch_lock:
            self._net_batch = []
            self._hw_batch = []
            self.db_worker.enqueue_task("delete_history", {"include_usage": bool(include_usage)})
        for series in (self.in_memory_history, self.aggregated_history,
                       self.cpu_history, self.gpu_history, self.ram_history):
            series.clear()
        if include_usage:
            # Polls buffered while the file was still opening are PRE-reset traffic: set aside, dropped
            # if the reset lands, restored if it fails. Everything from here on is tallied separately.
            # (Settings disables the button until the answer, so a second request while one is in
            # flight is not expected; if one comes, it joins the first instead of losing its stash.)
            if not self._usage_reset_pending:
                self._usage_reset_stash = []
                self._usage_post_reset = [0.0, 0.0, None]
            self._usage_reset_pending = True
            self._usage_reset_stash += list(getattr(self, "_usage_pending", None) or [])
            self._usage_pending = []
        self.logger.info("Delete all history requested (include_usage=%s).", bool(include_usage))

    def _on_worker_history_deleted(self, status: str) -> None:
        """The delete's answer (GUI thread). Settles a pending odometer reset: success -> the odometer
        restarts from the traffic seen since the request; failure -> nothing changes."""
        if not getattr(self, "_usage_reset_pending", False):
            return
        self._usage_reset_pending = False
        stash, self._usage_reset_stash = self._usage_reset_stash, []
        post, self._usage_post_reset = self._usage_post_reset, [0.0, 0.0, None]
        if status == DatabaseWorker.DELETE_FAILED:
            # Nothing was deleted, the usage row included: put the set-aside polls back. Replayed
            # WITHOUT their counters - they are older than the counters the total already pairs with.
            if stash:
                if self._usage_loaded:
                    for up, down, _c, _t, ws, _p in stash:
                        WidgetState._add_usage_loaded(self, up, down, None, None, ws, None)
                else:
                    self._usage_pending = stash + list(self._usage_pending)
            self._persist_usage_now()
            self._usage_last_persist = time.time()
            self.logger.info("Delete all history failed; the data-usage counter was left unchanged.")
            return
        # The delete committed, usage_counter and its snapshot with it. Pre-reset bytes (loaded,
        # buffered or set aside) are gone; what moved since the request starts the new count.
        self._usage = {
            "cumulative_up": float(post[0]), "cumulative_down": float(post[1]),
            "anchor_up": 0.0, "anchor_down": 0.0,
            "period_key": self._compute_period_key(self.config.get("data_cap_reset_day", 1)),
        }
        self._usage_pending = []
        self._usage_left = {}                  # no anchor from before the reset may ever come back
        self._usage_loaded = True              # a later lazy load must not read anything back
        self._usage_launch_snapshot = {}       # nothing from before the reset is reconciled
        if post[2] is not None:
            self._usage_live_counters, self._usage_live_sampled_at, self._usage_live_primary = post[2]
        else:
            # No counters since the request: none pair with the new total. The snapshot on disk was
            # deleted with the row, and persisting with no counters leaves it deleted.
            self._usage_live_counters = self._usage_live_sampled_at = self._usage_live_primary = None
        self._persist_usage_now()
        self._usage_last_persist = time.time()

    def flush_and_wait(self, timeout: float = 2.0) -> None:
        """
        Flush pending batches and BLOCK until the write thread has persisted everything
        enqueued so far (H3). Because the worker queue is FIFO, an Event sentinel placed
        after the flush only fires once the flush is on disk - so a read immediately after
        sees the freshly-flushed samples instead of a gap at the graph's right edge.
        Bounded by ``timeout`` so a stalled worker can never hang the reader.
        """
        try:
            self.flush_batch()
            # If the worker thread isn't running (tests, or mid-shutdown) the signal would
            # never fire - don't burn the timeout; the flush has nothing to persist anyway.
            if not self.db_worker.isRunning():
                return
            done = threading.Event()
            self.db_worker.enqueue_task("__signal__", done)
            # Poll in short steps rather than one long wait: if the worker thread exits AFTER the
            # isRunning() check above (e.g. a graph read overlapping shutdown), the barrier would never
            # fire and a single wait(timeout) would stall the reader for the full timeout (#15).
            waited = 0.0
            while waited < timeout:
                if done.wait(0.05):
                    return
                if not self.db_worker.isRunning():
                    return   # worker exited; the barrier will never fire - don't burn the timeout
                waited += 0.05
            self.logger.warning("flush_and_wait timed out after %.1fs; reading possibly-stale history.", timeout)
        except Exception as e:
            self.logger.error("flush_and_wait failed: %s", e, exc_info=True)


    def get_cpu_history(self) -> List[HardwareStatSnapshot]:
        """Returns in-memory CPU utilization history."""
        return list(self.cpu_history)


    def get_gpu_history(self) -> List[HardwareStatSnapshot]:
        """Returns in-memory GPU utilization history."""
        return list(self.gpu_history)


    def get_ram_history(self) -> List[HardwareStatSnapshot]:
        """Returns in-memory RAM%-utilization history."""
        return list(self.ram_history)


    def get_hardware_history(self, stat_type: str, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> List[Tuple[datetime, float]]:
        """
        Retrieves historical hardware utilization data from the database.
        Returns: A list of (timestamp, value) tuples.
        """
        if not hasattr(self, 'db_worker') or not self.db_worker:
            return []

        return self.history_reader.hardware_history(stat_type, start_time, end_time)

    # --- pro-stats: tier-aware honest window summaries (exact ≤24h raw, avg+max beyond) ----------
    # The SQL lives in core/db/history_reader.py (V7HistoryReader.summarize_*).

    def summarize_hardware(self, stat_type: str, start_time: datetime, end_time: datetime,
                           poll_interval: float = 1.0):
        """Honest WindowSummary for a hardware stat over [start,end] (see utils.summaries)."""
        from netspeedtray.utils import summaries as S
        if not getattr(self, 'db_worker', None):
            return S.summarize_raw([])
        return self.history_reader.summarize_hardware(stat_type, start_time, end_time, poll_interval)

    def summarize_network(self, direction: str, start_time: datetime, end_time: datetime,
                          interface_name: Optional[str] = None, poll_interval: float = 1.0):
        """Honest WindowSummary for 'download' or 'upload' bytes/sec over [start,end] (per interface,
        or aggregated when interface_name is None/'all')."""
        from netspeedtray.utils import summaries as S
        if not getattr(self, 'db_worker', None):
            return S.summarize_raw([])
        return self.history_reader.summarize_network(direction, start_time, end_time, interface_name,
                                                     poll_interval)

    def get_network_hourly_profile(self, direction: str, start_time: datetime, end_time: datetime,
                                   interface_name: Optional[str] = None) -> Dict[int, float]:
        """{local hour: average B/s} for 'download' or 'upload' over [start,end] - the basis for the
        Stats sheet's busiest / quietest hour (averages, never the graph's per-bucket peaks)."""
        if not getattr(self, 'db_worker', None):
            return {}
        return self.history_reader.network_hourly_profile(direction, start_time, end_time, interface_name)


    def get_total_bandwidth_for_period(self, start_time: Optional[datetime], end_time: Optional[datetime], interface_name: Optional[str] = None) -> Tuple[float, float]:
        """
        Calculates the total upload and download bandwidth for a given period
        by running SUM queries across all data tiers.
        """
        if not hasattr(self, 'db_worker') or not self.db_worker:
            return 0.0, 0.0

        # v8 ignores the poll rate (every raw row carries its dt_ms); a v7 file read while an upgrade
        # is deferred scales its raw tier by it.
        poll_interval = resolve_poll_interval_seconds(self.config.get("update_rate", 1.0))
        return self.history_reader.total_bandwidth(start_time, end_time, interface_name, poll_interval)

    def get_history_volume_for_period(self, start_time: Optional[datetime], end_time: Optional[datetime],
                                      interface_name: Optional[str] = None) -> VolumeResult:
        """get_total_bandwidth_for_period with its provenance (decision D5): ``reconstructed`` is True
        when any part was estimated from stored rates (history recorded before 2.2), so the UI labels
        the number "recorded history" rather than presenting it as exact."""
        if not hasattr(self, 'db_worker') or not self.db_worker:
            return VolumeResult(0.0, 0.0, 0.0, 0.0, False)
        poll_interval = resolve_poll_interval_seconds(self.config.get("update_rate", 1.0))
        return self.history_reader.volume(start_time, end_time or datetime.now(), interface_name,
                                          poll_interval)

    @staticmethod
    def metered_scope(config: Dict[str, Any], names: Optional[Iterable[str]] = None,
                      classifier: Any = None) -> Optional[MeteredScope]:
        """The adapters the data-usage odometer meters, as a history filter (``MeteredScope``), by the
        controller's own rules: ``selected`` -> exactly the selected adapters; ``all_virtual`` (and an
        unknown mode, which the controller sums in full) -> every adapter; ``all_physical`` and ``auto``
        -> of ``names`` (the adapters history knows), those the shared classifier calls physical with the
        user's ``excluded_interfaces`` applied, or - when none is - the fallback-eligible ones (never a
        known VPN / tunnel). Auto meters ONE routing adapter that history cannot replay poll by poll, so
        for auto this is a superset (see ``metered_scope_is_exact``). ``names`` None: the stored
        ``is_physical`` flag stands in for the classifier."""
        mode = config.get("interface_mode", "auto")
        if mode == "selected":
            return MeteredScope(names=tuple(config.get("selected_interfaces", []) or ()))
        if mode not in ("auto", "all_physical"):
            return None
        if names is None:
            return MeteredScope(physical_only=True)
        iface = constants.network.interface
        if classifier is None:
            from netspeedtray.utils.interface_classifier import get_classifier
            classifier = get_classifier()
        exclusions = config.get("excluded_interfaces", iface.DEFAULT_EXCLUSIONS)
        names = list(names)
        chosen = [n for n in names if classifier.kind(n, exclusions) == iface.KIND_PHYSICAL]
        if not chosen:
            chosen = [n for n in names if classifier.fallback_eligible(n, exclusions)]
        return MeteredScope(names=tuple(chosen))

    @staticmethod
    def metered_scope_is_exact(config: Dict[str, Any]) -> bool:
        """Whether history over ``metered_scope`` counts exactly the odometer's adapters (every mode but
        ``auto``) - only then may history RAISE a usage figure above the odometer's own count."""
        return config.get("interface_mode", "auto") != "auto"

    def get_metered_volume_for_period(self, start_time: Optional[datetime],
                                      end_time: Optional[datetime]) -> VolumeResult:
        """Recorded volume over [start, end] for the adapters the odometer meters (``metered_scope``) -
        the hover card's "Today" row. Exact in every mode but auto, where it is the physical adapters."""
        if not hasattr(self, 'db_worker') or not self.db_worker:
            return VolumeResult(0.0, 0.0, 0.0, 0.0, False)
        poll_interval = resolve_poll_interval_seconds(self.config.get("update_rate", 1.0))
        try:
            names = self.history_reader.interface_names()
        except Exception:
            names = None
        scope = WidgetState.metered_scope(self.config, names)
        return self.history_reader.metered_volume(start_time, end_time or datetime.now(), scope, poll_interval)

    def get_in_memory_speed_history(self) -> List[SpeedDataSnapshot]:
        """
        Retrieves the current in-memory speed history.
        
        Returns:
            A list of SpeedDataSnapshot objects, each containing a dictionary of
            per-interface speeds for a specific timestamp.
        """
        return list(self.in_memory_history)


    def get_aggregated_speed_history(self) -> List[AggregatedSpeedData]:
        """
        Retrieves the pre-calculated aggregated speed history.
        This is optimized for the mini-graph renderer.
        """
        return list(self.aggregated_history)





    def trigger_maintenance(self, now: Optional[datetime] = None) -> None:
        """
        Public method to enqueue a maintenance task for the database worker,
        passing it the current application configuration.
        """
        self.logger.debug("Triggering periodic database maintenance.")
        config = self.config.copy()
        if now:
            # Pass (config, now) tuple as expected by DatabaseWorker._execute_task
            self.db_worker.enqueue_task("maintenance", (config, now))
        else:
            self.db_worker.enqueue_task("maintenance", config)


    def update_retention_period(self) -> None:
        """
        To be called after the user changes the retention setting. This triggers
        a maintenance run where the new config will be evaluated.
        """
        self.logger.info("User changed retention period. Triggering maintenance check.")
        # We don't need to pass config here; the trigger method will grab the latest.
        self.trigger_maintenance()


    def get_speed_history(self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None, interface_name: Optional[str] = None, return_raw: bool = False, resolution: Literal['auto', 'raw', 'minute', 'hour', 'day'] = 'auto', _visited_resolutions: set = None, wait_for_flush: bool = True) -> List[Tuple[Union[datetime, float], float, float]]:
        """
        Retrieves speed history by querying ALL relevant database tiers (raw, minute, hour)
        and unifying them into a single timeline.
        """
        # Flush AND wait for persistence so the read includes the just-buffered samples (H3):
        # the previous fire-and-forget flush could miss them, leaving a gap at the graph edge.
        # Only on the top-level call - the resolution recursion (which sets _visited_resolutions)
        # must not re-barrier on every tier. Callers that run on the GUI thread on a timer (the Overview
        # reload, every few seconds) pass wait_for_flush=False so they never block the UI up to the 2s
        # flush timeout for a missing last second that doesn't matter at multi-hour resolution.
        if _visited_resolutions is None and wait_for_flush:
            self.flush_and_wait()

        return self.history_reader.speed_history(start_time, end_time, interface_name, return_raw, resolution)


    def get_distinct_interfaces(self) -> List[str]:
        """Returns a sorted list of all unique interface names from the database."""
        return self.history_reader.distinct_interfaces()


    def get_earliest_data_timestamp(self) -> Optional[datetime]:
        """
        Retrieves the earliest data timestamp from the database by querying all tiers.
        """
        self.flush_batch()
        # time.sleep(0.1)  # REMOVED: This was causing a 100ms freeze on the UI thread.
        return self.history_reader.earliest_timestamp()


    def persist_essentials(self, timeout: float = 2.0) -> None:
        """Windows is ending the session: queue the last history batch and the odometer tail, and wait
        (bounded by ``timeout``) for the database thread to write them. Stops nothing - the full
        cleanup() may never get to run before the process is ended. A data-usage reset still waiting
        for its delete's answer is settled by the write thread (persist_usage_final)."""
        self.flush_batch()
        self.persist_usage_final()
        self.flush_and_wait(timeout=timeout)

    def cleanup(self) -> None:
        """Flushes final data and cleanly stops the database worker thread."""
        self.logger.info("Cleaning up WidgetState...")
        self.batch_persist_timer.stop()
        self.maintenance_timer.stop()
        self.flush_batch()
        # Persist the final odometer tail so the ~30s throttle window isn't lost on exit
        # (no-op if the counter was never loaded). The worker drains this before stopping.
        WidgetState.persist_usage_final(self)

        # Read connections. Never close another thread's connection from here: closing a sqlite3
        # connection while its thread is mid-query is undefined behaviour (segfaulted 4/4 in a plain
        # 3.13 repro; the lucky outcome was "Cannot operate on a closed database"), and quitting with
        # the Monitor open hit exactly that. So: refuse new reads, close this thread's own connection,
        # interrupt any statement still running elsewhere, and leave those connections to their owners
        # (a worker closes its own as it exits - release_read_conn). The widget closes the Monitor and
        # joins its graph worker before calling this; anything still registered is reclaimed at exit.
        with self._read_conns_lock:
            self._reads_closed = True
            own = self._read_conns.pop(threading.get_ident(), None)
        if own is not None:
            try:
                own[0].close()
            except Exception:
                pass
        self.interrupt_reads()

        self.db_worker.stop()
        # Only wait for the thread if it was actually running. Usually it drains in well under a
        # second, but a quit right after start can land inside the v7 -> v8 migration or the first
        # maintenance pass; the worker skips what it can once stop is set, and the wait is bounded.
        # Never let the QThread be destroyed while it runs (Qt aborts the process): past the bound it
        # is detached, and monitor.py exits hard - a kill the migration and the writer are tested for.
        if self.db_worker.isRunning():
            if not self.db_worker.wait(int(constants.timeouts.DB_SHUTDOWN_WAIT_MS)):
                from netspeedtray.core.database import detach_running_worker
                self.logger.warning("The database thread is still busy after %d ms; exiting without "
                                    "waiting for it (an interrupted upgrade resumes at the next start).",
                                    int(constants.timeouts.DB_SHUTDOWN_WAIT_MS))
                detach_running_worker(self.db_worker)


    def _get_max_history_points(self) -> int:
        """Calculates max points for the in-memory deque based on config."""
        try:
            history_minutes = self.config.get("history_minutes", 30)
            update_rate_sec = resolve_poll_interval_seconds(self.config.get("update_rate", 1.0))
            points = int(round((history_minutes * 60) / update_rate_sec))
            return max(10, min(points, 5000))

        except Exception as e:
            self.logger.error("Error calculating max history points: %s. Using default.", e)
            return 1800

    def _get_graph_buffer_points(self) -> int:
        """Capacity for the lightweight mini-graph series (aggregated speed + per-stat hardware %).
        Sized to the MAXIMUM selectable graph window - not the current one - so that growing the graph
        timespan reveals already-buffered samples instantly; the renderer slices this down to the
        configured window. Bounded (≤5000 points) so each float series stays well under ~0.5 MB."""
        try:
            from netspeedtray import constants
            _, max_minutes = constants.ui.history.HISTORY_MINUTES_RANGE
            update_rate_sec = resolve_poll_interval_seconds(self.config.get("update_rate", 1.0))
            points = int(round((max_minutes * 60) / update_rate_sec))
            return max(10, min(points, 5000))
        except Exception as e:
            self.logger.error("Error calculating graph buffer points: %s. Using default.", e)
            return 5000

          
    def apply_config(self, config: Dict[str, Any]) -> None:
        """Apply updated configuration and adjust state."""
        self.logger.debug("Applying new configuration to WidgetState...")
        old_poll = self._poll_ms()
        self.config = config.copy()
        if not self._read_only and self._poll_ms() != old_poll:
            # Only the writer's gap threshold depends on it; stored bytes never do.
            self.db_worker.enqueue_task("set_poll_ms", self._poll_ms())
        new_max_points = self._get_max_history_points()
        if new_max_points != self.max_history_points:
            self.max_history_points = new_max_points
            self.in_memory_history = deque(self.in_memory_history, maxlen=self.max_history_points)
            self.logger.debug("In-memory raw history capacity updated to %d points.", self.max_history_points)

        # The graph series buffer the *max* window, so they only resize when update_rate changes (not on
        # a timespan change - the renderer just slices a different amount from the same buffer).
        new_graph_points = self._get_graph_buffer_points()
        if new_graph_points != self._graph_buffer_points:
            self._graph_buffer_points = new_graph_points
            self.aggregated_history = deque(self.aggregated_history, maxlen=new_graph_points)
            self.cpu_history = deque(self.cpu_history, maxlen=new_graph_points)
            self.gpu_history = deque(self.gpu_history, maxlen=new_graph_points)
            self.ram_history = deque(self.ram_history, maxlen=new_graph_points)
            self.logger.debug("Mini-graph buffer capacity updated to %d points.", new_graph_points)
