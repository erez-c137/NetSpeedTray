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
from typing import Any, Deque, Dict, List, Optional, Tuple, Literal, Union

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer

from netspeedtray import constants
from netspeedtray.constants import network, timeouts
from netspeedtray.core import usage_snapshot
from netspeedtray.core.db.history_reader import HistoryReader, V7HistoryReader
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
        
        # Batching lists for database writes
        self._db_batch: List[Tuple[int, str, float, float]] = []
        self._hw_batch: List[Tuple[int, str, float]] = []
        # Guards the two batch lists: they're APPENDED on the GUI thread but flushed (snapshot+reset)
        # from the graph-worker thread (get_speed_history(wait_for_flush=True)). Without it an append
        # landing between the snapshot and the reset would silently drop that persisted row (#6).
        self._batch_lock = threading.Lock()

        # Database Worker Thread
        self._db_path = Path(get_app_data_path()) / "speed_history.db"
        self.db_worker = DatabaseWorker(self._db_path)
        self.db_worker.error.connect(lambda msg: self.logger.error("DB Worker Error: %s", msg))
        # Surface the downgrade guard (v2.1.5, action-plan item 1). Connected BEFORE
        # start() so the worker can never trip the guard while nothing is wired.
        self.db_worker.schema_incompatible.connect(self.db_schema_incompatible)
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

        self.logger.debug("WidgetState initialized with threaded database worker.")


    # How long a read connection may sit untouched before another thread may close it. Generous on
    # purpose: it only has to exceed the interval at which a live worker re-fetches its connection
    # (the Monitor refreshes every few seconds), and erring high costs one idle file handle while
    # erring low would close a connection out from under a slow query.
    _READ_CONN_IDLE_SEC: float = 120.0

    def _get_read_conn(self) -> sqlite3.Connection:
        """Returns a thread-local read connection, pruning ones left behind by dead threads.

        Each worker thread (notably the Monitor's GraphDataWorker, recreated on every Monitor open) opens
        its own read connection here, removed otherwise only in cleanup() at exit. Without pruning, every
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
            if self._read_conns:
                for stale in [tid for tid, (_c, seen) in self._read_conns.items()
                              if tid != thread_id and (now - seen) > self._READ_CONN_IDLE_SEC]:
                    try:
                        self._read_conns.pop(stale)[0].close()
                    except Exception:
                        pass
            if thread_id not in self._read_conns:
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

    @property
    def history_reader(self) -> HistoryReader:
        """The storage backend every history read goes through (v8 step 2). Built on first use rather
        than in __init__ so a WidgetState assembled by hand in tests still reads; it holds no connection
        of its own - each call fetches this thread's via _get_read_conn()."""
        reader = getattr(self, "_history_reader", None)
        if reader is None:
            reader = V7HistoryReader(self._get_read_conn, lambda: self.db_worker.db_path, self.logger)
            self._history_reader = reader
        return reader

    def add_speed_data(self, speed_data: Dict[str, Tuple[float, float]], now: Optional[datetime] = None,
                       aggregated_up: Optional[float] = None, aggregated_down: Optional[float] = None,
                       persist_data: Optional[Dict[str, Tuple[float, float]]] = None) -> None:
        """Adds new per-interface speed data.

        ``speed_data`` feeds the in-memory live series (mini graph, Monitor session view) and is
        what the user sees - the controller passes its spike-FILTERED rates. ``persist_data``, when
        given, is what goes to the DB batch instead: the MEASURED rates, so history holds
        measurements rather than the filter's substitute values (v8 step 0). Omitted, the DB gets
        ``speed_data`` (direct callers, tests)."""
        _now = now or datetime.now()
        self.in_memory_history.append(SpeedDataSnapshot(speeds=speed_data.copy(), timestamp=_now))

        if aggregated_up is not None and aggregated_down is not None:
            total_up, total_down = aggregated_up, aggregated_down
        else:
            total_up = sum(speeds[0] for speeds in speed_data.values())
            total_down = sum(speeds[1] for speeds in speed_data.values())
            
        self.aggregated_history.append(AggregatedSpeedData(upload=total_up, download=total_down, timestamp=_now))

        timestamp = int(_now.timestamp())
        max_speed = network.interface.MAX_REASONABLE_SPEED_BPS
        
        to_persist = speed_data if persist_data is None else persist_data
        with self._batch_lock:
            for interface, (up_speed, down_speed) in to_persist.items():
                if up_speed >= network.speed.MIN_RECORDABLE_SPEED_BPS or down_speed >= network.speed.MIN_RECORDABLE_SPEED_BPS:
                    self._db_batch.append((timestamp, interface, min(up_speed, max_speed), min(down_speed, max_speed)))


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
            self._usage_loaded = True
            return True
        except Exception as e:
            self.logger.debug("Usage counter not ready yet: %s", e)
            return False

    def get_usage_launch_snapshot(self) -> Optional[Dict[str, Any]]:
        """The counter snapshot persisted by the PREVIOUS session ({} when there is none), or None
        while the odometer has not loaded yet (the caller retries)."""
        if not self._usage_loaded and not self._try_load_usage():
            return None
        return self._usage_launch_snapshot

    def _maybe_reanchor(self) -> bool:
        """
        Advance the period anchor on a genuine FORWARD rollover (returns True if it did).

        Anchors at the cumulative-so-far, so a boundary poll's bytes (added right after this in
        add_usage_bytes) land in the NEW period instead of being lost to both. Runs on both the
        write path AND the read path, so an idle-across-reset still rolls the period over even
        with no traffic. ISO date keys sort lexically, so '>' (not '!=') makes a backward
        clock/DST shift unable to wipe the running total, and the empty initial key first-anchors.
        """
        u = self._usage
        pk = self._compute_period_key(self.config.get("data_cap_reset_day", 1))
        if pk > u["period_key"]:
            u["anchor_up"] = u["cumulative_up"]
            u["anchor_down"] = u["cumulative_down"]
            u["period_key"] = pk
            self._persist_usage_now()
            self._usage_last_persist = time.time()
            return True
        return False

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
        if not self._usage_loaded and not self._try_load_usage():
            return  # defer until the table exists, so we don't clobber the saved total
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
            if start_pk < u["period_key"]:
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
        if not self._usage_loaded:
            return
        u = self._usage
        snapshot_json = None
        if self._usage_live_counters is not None:
            # Fresh boot id at every persist (not cached at start): boot_time() follows wall-clock
            # corrections, so the latest reading is the one the next launch will compare against.
            snapshot_json = usage_snapshot.encode_snapshot(
                self._usage_live_counters, self._usage_live_sampled_at or time.time(),
                usage_snapshot.current_boot_time(), primary=self._usage_live_primary)
            if snapshot_json is None:
                # Counters seen but no trustworthy snapshot (boot id unreadable): the total is about
                # to advance past the STORED snapshot, so it must go - leaving it would make the next
                # same-boot launch re-add bytes this total already holds.
                snapshot_json = usage_snapshot.CLEAR_SNAPSHOT
        # 7th element: the counter snapshot, written in the SAME transaction as the total;
        # CLEAR_SNAPSHOT deletes the stored one; None (no counters seen yet) leaves it untouched.
        self.db_worker.enqueue_task("persist_usage", (
            u["cumulative_up"], u["cumulative_down"], u["anchor_up"], u["anchor_down"],
            u["period_key"], int(time.time()), snapshot_json))

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
        with self._batch_lock:
            db_batch, self._db_batch = self._db_batch, []
            hw_batch, self._hw_batch = self._hw_batch, []
            # Enqueued INSIDE the lock (queue.put on the unbounded worker queue never blocks), so a
            # flush from the graph-worker thread can't slip a pre-delete batch in behind a
            # delete_all_history() that ran between the snapshot and the enqueue.
            if db_batch:
                self.db_worker.enqueue_task("persist_speed", db_batch)
            if hw_batch:
                self.db_worker.enqueue_task("persist_hardware", hw_batch)

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
          3. only if the user opted in, zero the data-usage odometer and persist the zeros AFTER the
             delete (FIFO), so the next throttled persist can't resurrect the old totals.

        Not touched: settings, the latency probe's short live window (~2 min, never on disk on its
        own), and - unless ``include_usage`` - the odometer (D5: the data cap's billing source).
        Completion is reported by ``history_deleted``.
        """
        if self._read_only:
            return
        with self._batch_lock:
            self._db_batch = []
            self._hw_batch = []
            self.db_worker.enqueue_task("delete_history", {"include_usage": bool(include_usage)})
        for series in (self.in_memory_history, self.aggregated_history,
                       self.cpu_history, self.gpu_history, self.ram_history):
            series.clear()
        if include_usage:
            self._usage = {
                "cumulative_up": 0.0, "cumulative_down": 0.0,
                "anchor_up": 0.0, "anchor_down": 0.0,
                "period_key": self._compute_period_key(self.config.get("data_cap_reset_day", 1)),
            }
            # Treat the zeros as loaded even if the lazy load hadn't run yet: the worker creates the
            # table before it drains its queue, so this persist lands, and a later lazy load must not
            # read back the pre-delete row.
            self._usage_loaded = True
            self._persist_usage_now()
            self._usage_last_persist = time.time()
        self.logger.info("Delete all history requested (include_usage=%s).", bool(include_usage))

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


    def get_total_bandwidth_for_period(self, start_time: Optional[datetime], end_time: Optional[datetime], interface_name: Optional[str] = None) -> Tuple[float, float]:
        """
        Calculates the total upload and download bandwidth for a given period
        by running SUM queries across all data tiers.
        """
        if not hasattr(self, 'db_worker') or not self.db_worker:
            return 0.0, 0.0

        # The raw tier has no stored capture interval: the reader scales it by the current poll rate.
        poll_interval = resolve_poll_interval_seconds(self.config.get("update_rate", 1.0))
        return self.history_reader.total_bandwidth(start_time, end_time, interface_name, poll_interval)


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


    def cleanup(self) -> None:
        """Flushes final data and cleanly stops the database worker thread."""
        self.logger.info("Cleaning up WidgetState...")
        self.batch_persist_timer.stop()
        self.maintenance_timer.stop()
        self.flush_batch()
        # Persist the final odometer tail so the ~30s throttle window isn't lost on exit
        # (no-op if the counter was never loaded). The worker drains this before stopping.
        self._persist_usage_now()

        # Close persistent read connections
        with self._read_conns_lock:
            for tid, (conn, _seen) in self._read_conns.items():
                try:
                    conn.close()
                except:
                    pass
            self._read_conns.clear()

        self.db_worker.stop()
        # Only wait for the thread if it was actually running
        if self.db_worker.isRunning():
            self.db_worker.wait(2000) # Wait up to 2 seconds for the thread to finish


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
        self.config = config.copy()
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
