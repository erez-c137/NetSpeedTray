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


@dataclass(slots=True, frozen=True)
class HardwareStatSnapshot:
    """Represents a single hardware utilization data point."""
    value: float
    timestamp: datetime


# --- Database Worker Thread ---
from netspeedtray.core.database import DatabaseWorker


class WidgetState(QObject):
    """Manages all system statistics and bandwidth history for the Tray Widget."""

    def __init__(self, config: Dict[str, Any], read_only: bool = False) -> None:
        super().__init__()
        self.logger = logger
        self.config = config.copy()
        # read_only: a headless reader (the --export-csv CLI) that must NOT start a second write thread
        # against the live app's DB, nor run maintenance/VACUUM. Reads go straight through
        # _get_read_conn(); the worker is constructed but never started, so flush_and_wait short-circuits.
        self._read_only = read_only

        # In-Memory Cache for real-time mini-graph
        self.max_history_points: int = self._get_max_history_points()
        self.in_memory_history: Deque[SpeedDataSnapshot] = deque(maxlen=self.max_history_points)
        self.aggregated_history: Deque[AggregatedSpeedData] = deque(maxlen=self.max_history_points)
        
        # New: Hardware history for mini-graph tabs
        self.cpu_history: Deque[HardwareStatSnapshot] = deque(maxlen=self.max_history_points)
        self.gpu_history: Deque[HardwareStatSnapshot] = deque(maxlen=self.max_history_points)
        self.ram_history: Deque[HardwareStatSnapshot] = deque(maxlen=self.max_history_points)
        
        # Batching lists for database writes
        self._db_batch: List[Tuple[int, str, float, float]] = []
        self._hw_batch: List[Tuple[int, str, float]] = []

        # Database Worker Thread
        self._db_path = Path(get_app_data_path()) / "speed_history.db"
        self.db_worker = DatabaseWorker(self._db_path)
        self.db_worker.error.connect(lambda msg: self.logger.error("DB Worker Error: %s", msg))
        if not read_only:
            self.db_worker.start()

        # Timers for periodic operations (constructed either way so cleanup() stays simple, but only
        # started for a live instance — a read_only export never writes or runs maintenance).
        self.batch_persist_timer = QTimer(self)
        self.batch_persist_timer.timeout.connect(self.flush_batch)
        self.maintenance_timer = QTimer(self)
        self.maintenance_timer.timeout.connect(self.trigger_maintenance)
        if not read_only:
            self.batch_persist_timer.start(10 * 1000) # Persist every 10 seconds
            self.maintenance_timer.start(60 * 60 * 1000) # Run maintenance every hour
            self.trigger_maintenance()

        self._read_conns: Dict[int, sqlite3.Connection] = {}
        self._read_conns_lock = threading.Lock()

        # Data-usage odometer (data-cap feature). Lazily loaded from the DB on first
        # use (the worker thread may not have created the table yet at construction).
        self._usage: Dict[str, Any] = {
            "cumulative_up": 0.0, "cumulative_down": 0.0,
            "anchor_up": 0.0, "anchor_down": 0.0, "period_key": "",
        }
        self._usage_loaded: bool = False
        self._usage_last_persist: float = 0.0

        self.logger.debug("WidgetState initialized with threaded database worker.")


    def _get_read_conn(self) -> sqlite3.Connection:
        """Returns a thread-local read connection, pruning ones left behind by dead threads.

        Each worker thread (notably the Monitor's GraphDataWorker, recreated on every Monitor open) opens
        its own read connection here, removed otherwise only in cleanup() at exit. Without pruning, every
        Monitor open/close leaked a connection (a file handle + a WAL reader slot) for the whole session,
        and a recycled thread id could even hand a new worker a dead thread's stale connection. So evict
        entries whose owning thread has exited (safe to close cross-thread: check_same_thread=False)."""
        thread_id = threading.get_ident()
        with self._read_conns_lock:
            if self._read_conns:
                live = {t.ident for t in threading.enumerate()}
                for dead in [tid for tid in self._read_conns if tid not in live and tid != thread_id]:
                    try:
                        self._read_conns.pop(dead).close()
                    except Exception:
                        pass
            if thread_id not in self._read_conns:
                conn = sqlite3.connect(self._db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                # Wait (up to 5s) for a lock instead of erroring out immediately — otherwise a
                # read concurrent with the maintenance VACUUM can fail with "database is locked".
                conn.execute("PRAGMA busy_timeout = 5000;")
                self._read_conns[thread_id] = conn
            return self._read_conns[thread_id]

    def add_speed_data(self, speed_data: Dict[str, Tuple[float, float]], now: Optional[datetime] = None, aggregated_up: Optional[float] = None, aggregated_down: Optional[float] = None) -> None:
        """Adds new per-interface speed data."""
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
        
        for interface, (up_speed, down_speed) in speed_data.items():
            if up_speed >= network.speed.MIN_RECORDABLE_SPEED_BPS or down_speed >= network.speed.MIN_RECORDABLE_SPEED_BPS:
                self._db_batch.append((timestamp, interface, min(up_speed, max_speed), min(down_speed, max_speed)))


    # Utilisation stats are 0-100% and clamped; physical stats (power W, temperature °C, latency ms)
    # are NOT percentages and must be stored unclamped — a 200 ms ping or 180 W draw must not become 100.
    _PCT_STATS = frozenset({'cpu', 'gpu', 'ram', 'vram'})

    def add_hardware_stat(self, stat_type: str, value: float, now: Optional[datetime] = None) -> None:
        """Record a hardware sample (utilisation %, power W, temperature °C, or latency ms) to the
        in-memory deque (for graphed util stats) + the DB batch (for all, via the 3-tier rollups)."""
        _now = now or datetime.now()
        snapshot = HardwareStatSnapshot(value=value, timestamp=_now)

        if stat_type == 'cpu':
            self.cpu_history.append(snapshot)
        elif stat_type == 'gpu':
            self.gpu_history.append(snapshot)
        elif stat_type == 'ram':
            self.ram_history.append(snapshot)

        # Clamp only the percentage stats; store physical stats unclamped (just floor at 0).
        v = max(0.0, min(100.0, value)) if stat_type in self._PCT_STATS else max(0.0, float(value))
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
        Returns False (so the caller defers) if the table isn't ready yet — never
        overwrites a real persisted counter with a fresh zero one."""
        try:
            conn = self._get_read_conn()
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (constants.data.USAGE_COUNTER_TABLE,))
            if cur.fetchone() is None:
                return False
            cur.execute(
                f"SELECT cumulative_up, cumulative_down, anchor_up, anchor_down, period_key "
                f"FROM {constants.data.USAGE_COUNTER_TABLE} WHERE id = 1")
            row = cur.fetchone()
            if row:
                # Validate the persisted row — a corrupt/negative/NaN value flowing into the cap
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
            self._usage_loaded = True
            return True
        except Exception as e:
            self.logger.debug("Usage counter not ready yet: %s", e)
            return False

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

    def add_usage_bytes(self, up_bytes: float, down_bytes: float) -> None:
        """Accumulate exact transferred bytes into the odometer, re-anchoring on a new
        billing period, and persist on a throttle. Called every poll; cheap."""
        if not self._usage_loaded and not self._try_load_usage():
            return  # defer until the table exists, so we don't clobber the saved total
        u = self._usage
        # Roll the period over BEFORE adding this poll, so a boundary-crossing poll's bytes
        # count toward the NEW period rather than being stranded at the anchor (audit #15).
        rolled = self._maybe_reanchor()
        u["cumulative_up"] += max(0.0, up_bytes)
        u["cumulative_down"] += max(0.0, down_bytes)
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
        self.db_worker.enqueue_task("persist_usage", (
            u["cumulative_up"], u["cumulative_down"], u["anchor_up"], u["anchor_down"],
            u["period_key"], int(time.time())))

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

    def get_usage_period_key(self) -> str:
        """
        The current billing-period key (for the alert controller's restart-safe state).
        Returns the odometer's MONOTONIC period key (advanced forward-only) so a backward
        clock/DST shift can't change it and spuriously re-fire a threshold alert — and so the
        alert period and the odometer period are one source of truth.
        """
        if self._usage_loaded or self._try_load_usage():
            self._maybe_reanchor()
            return self._usage["period_key"] or self._compute_period_key(
                self.config.get("data_cap_reset_day", 1))
        return self._compute_period_key(self.config.get("data_cap_reset_day", 1))

    def flush_batch(self) -> None:
        """Sends all batches to the database worker."""
        if self._db_batch:
            self.db_worker.enqueue_task("persist_speed", self._db_batch.copy())
            self._db_batch.clear()

        if self._hw_batch:
            self.db_worker.enqueue_task("persist_hardware", self._hw_batch.copy())
            self._hw_batch.clear()

    def flush_and_wait(self, timeout: float = 2.0) -> None:
        """
        Flush pending batches and BLOCK until the write thread has persisted everything
        enqueued so far (H3). Because the worker queue is FIFO, an Event sentinel placed
        after the flush only fires once the flush is on disk — so a read immediately after
        sees the freshly-flushed samples instead of a gap at the graph's right edge.
        Bounded by ``timeout`` so a stalled worker can never hang the reader.
        """
        try:
            self.flush_batch()
            # If the worker thread isn't running (tests, or mid-shutdown) the signal would
            # never fire — don't burn the timeout; the flush has nothing to persist anyway.
            if not self.db_worker.isRunning():
                return
            done = threading.Event()
            self.db_worker.enqueue_task("__signal__", done)
            if not done.wait(timeout):
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

        try:
            conn = self._get_read_conn()
            cursor = conn.cursor()
            
            _end = end_time or datetime.now()
            _start = start_time or (_end - timedelta(hours=24))
            
            start_ts = int(_start.timestamp())
            end_ts = int(_end.timestamp())
            
            # Target resolution by window length: raw (≤6h), minute (≤30d), hour (>30d).
            duration = end_ts - start_ts
            interval = 1 if duration <= 6 * 3600 else (60 if duration <= 30 * 86400 else 3600)

            # Union ALL tiers (non-overlapping — data is moved, not copied) and bin to the target
            # interval, mirroring get_speed_history. A recent-but-long window (e.g. 48h or a week) has
            # its most-recent <24h only in the RAW tier and the older portion in minute/hour; reading a
            # single tier silently dropped the recent half (and the old fallback only fired when the tier
            # was TOTALLY empty). Binning keeps the point count bounded by window/interval regardless.
            HRAW = constants.data.HARDWARE_STATS_TABLE_RAW
            HMIN = constants.data.HARDWARE_STATS_TABLE_MINUTE
            HHOUR = constants.data.HARDWARE_STATS_TABLE_HOUR
            bin_ts = f"CAST(timestamp / {interval} AS INTEGER) * {interval}"
            cursor.execute(f"""
                SELECT b, AVG(v) FROM (
                    SELECT {bin_ts} AS b, value AS v FROM {HRAW}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?
                    UNION ALL
                    SELECT {bin_ts} AS b, avg_value AS v FROM {HMIN}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?
                    UNION ALL
                    SELECT {bin_ts} AS b, avg_value AS v FROM {HHOUR}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?
                ) GROUP BY b ORDER BY b ASC
            """, (stat_type, start_ts, end_ts) * 3)
            rows = cursor.fetchall()

            return [(datetime.fromtimestamp(row[0]), row[1]) for row in rows]
        except Exception as e:
            self.logger.error("Error fetching hardware history: %s", e, exc_info=True)
            return []

    # --- pro-stats: tier-aware honest window summaries (exact ≤24h raw, avg+max beyond) ----------
    _RAW_SUMMARY_SECONDS = 24 * 3600   # the raw tier retains ~24h; windows within it summarise exactly

    def summarize_hardware(self, stat_type: str, start_time: datetime, end_time: datetime,
                           poll_interval: float = 1.0):
        """Honest WindowSummary for a hardware stat over [start,end] (see utils.summaries)."""
        from netspeedtray.utils import summaries as S
        if not getattr(self, 'db_worker', None):
            return S.summarize_raw([])
        win = max(0.0, (end_time - start_time).total_seconds())
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        try:
            cur = self._get_read_conn().cursor()
            if win <= self._RAW_SUMMARY_SECONDS:
                cur.execute(f"SELECT value FROM {constants.data.HARDWARE_STATS_TABLE_RAW} "
                            f"WHERE stat_type=? AND timestamp BETWEEN ? AND ?", (stat_type, st, et))
                vals = [r[0] for r in cur.fetchall()]
                return S.summarize_raw(vals, S.coverage_pct(len(vals), win, poll_interval))
            # Beyond the raw horizon the window spans tiers: the recent <24h is still in RAW, older data
            # in minute/hour. Union all three (non-overlapping — data is moved, not copied — exactly like
            # get_speed_history) so the summary covers the WHOLE window instead of only the rolled-up
            # older half, which silently dropped the most recent ~24h and disagreed with the graph.
            avgs, maxes, counts = [], [], []
            for table in (constants.data.HARDWARE_STATS_TABLE_MINUTE, constants.data.HARDWARE_STATS_TABLE_HOUR):
                cur.execute(f"SELECT avg_value, max_value, sample_count FROM {table} "
                            f"WHERE stat_type=? AND timestamp BETWEEN ? AND ?", (stat_type, st, et))
                for r in cur.fetchall():
                    if r[0] is None:
                        continue
                    avgs.append(r[0]); maxes.append(r[1]); counts.append(r[2] or 1)
            cur.execute(f"SELECT value FROM {constants.data.HARDWARE_STATS_TABLE_RAW} "
                        f"WHERE stat_type=? AND timestamp BETWEEN ? AND ?", (stat_type, st, et))
            raw_vals = [r[0] for r in cur.fetchall() if r[0] is not None]
            if not avgs:   # the whole window still fits the raw tier (e.g. a <24h-old install) -> exact
                return S.summarize_raw(raw_vals, S.coverage_pct(len(raw_vals), win, poll_interval))
            avgs += raw_vals; maxes += raw_vals; counts += [1] * len(raw_vals)   # raw samples = count-1 buckets
            tier = "minute" if win <= 30 * 86400 else "hour"
            return S.summarize_rollup(avgs, maxes, counts, tier,
                                      S.coverage_pct(sum(counts), win, poll_interval))
        except Exception as e:
            self.logger.error("summarize_hardware failed: %s", e, exc_info=True)
            return S.summarize_raw([])

    def summarize_network(self, direction: str, start_time: datetime, end_time: datetime,
                          interface_name: Optional[str] = None, poll_interval: float = 1.0):
        """Honest WindowSummary for 'download' or 'upload' bytes/sec over [start,end] (per interface,
        or aggregated when interface_name is None/'all')."""
        from netspeedtray.utils import summaries as S
        if not getattr(self, 'db_worker', None):
            return S.summarize_raw([])
        col = "download" if direction == "download" else "upload"
        win = max(0.0, (end_time - start_time).total_seconds())
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        iface = None if interface_name in (None, "all") else interface_name
        wh = "" if iface is None else " AND interface_name=?"
        params_tail = () if iface is None else (iface,)
        try:
            cur = self._get_read_conn().cursor()

            def _raw_vals():
                # Sum per-timestamp across interfaces when aggregating, so an "all" summary matches the
                # widget's aggregate rather than mixing per-NIC samples.
                cur.execute(
                    f"SELECT SUM({col}_bytes_sec) FROM {constants.data.SPEED_TABLE_RAW} "
                    f"WHERE timestamp BETWEEN ? AND ?{wh} GROUP BY timestamp", (st, et) + params_tail)
                return [r[0] for r in cur.fetchall() if r[0] is not None]

            if win <= self._RAW_SUMMARY_SECONDS:
                vals = _raw_vals()
                return S.summarize_raw(vals, S.coverage_pct(len(vals), win, poll_interval))

            # Beyond the raw horizon the window spans tiers: the recent <24h is still in RAW, older data
            # in minute/hour. Union all three (non-overlapping, like get_speed_history) so the summary
            # covers the WHOLE window instead of only the rolled-up older half (which dropped the most
            # recent ~24h and disagreed with the graph for the same window).
            avgs, maxes, counts = [], [], []
            covered_seconds = 0.0   # for coverage: count distinct TIME buckets × their duration, NOT
            #                         SUM(sample_count) — which, for an "all" aggregate, double-counts by
            #                         NIC and inflates the evidence-admissibility figure past 100%.
            for table, bucket_secs in ((constants.data.SPEED_TABLE_MINUTE, 60.0),
                                       (constants.data.SPEED_TABLE_HOUR, 3600.0)):
                cur.execute(
                    f"SELECT SUM({col}_avg), MAX(t.mx), SUM(sample_count) FROM "
                    f"(SELECT timestamp, {col}_avg, {col}_max AS mx, sample_count FROM {table} "
                    f" WHERE timestamp BETWEEN ? AND ?{wh}) t GROUP BY t.timestamp",
                    (st, et) + params_tail)
                for r in cur.fetchall():
                    if r[0] is None:
                        continue
                    avgs.append(r[0]); maxes.append(r[1]); counts.append(r[2] or 1)
                    covered_seconds += bucket_secs   # one distinct timestamp bucket of this tier
            raw_vals = _raw_vals()
            if not avgs:   # the whole window still fits the raw tier -> exact percentiles
                return S.summarize_raw(raw_vals, S.coverage_pct(len(raw_vals), win, poll_interval))
            avgs += raw_vals; maxes += raw_vals; counts += [1] * len(raw_vals)   # raw samples = count-1 buckets
            covered_seconds += len(raw_vals) * poll_interval
            coverage = min(100.0, (covered_seconds / win * 100.0)) if win > 0 else 0.0
            tier = "minute" if win <= 30 * 86400 else "hour"
            return S.summarize_rollup(avgs, maxes, counts, tier, coverage)
        except Exception as e:
            self.logger.error("summarize_network failed: %s", e, exc_info=True)
            return S.summarize_raw([])


    def get_total_bandwidth_for_period(self, start_time: Optional[datetime], end_time: Optional[datetime], interface_name: Optional[str] = None) -> Tuple[float, float]:
        """
        Calculates the total upload and download bandwidth for a given period
        by running SUM queries across all data tiers.
        """
        if not hasattr(self, 'db_worker') or not self.db_worker:
            return 0.0, 0.0

        try:
            conn = self._get_read_conn()
            cursor = conn.cursor()
            
            start_ts = int(start_time.timestamp()) if start_time else 0
            end_ts = int(end_time.timestamp())

            total_up, total_down = 0.0, 0.0
            
            # Optimization: Only query tiers that could potentially have data for this range.
            # Raw: last 2 days. Minute: last 32 days. Hour: all.
            now_ts = int(datetime.now().timestamp())
            
            # Bytes per tier = (sum of per-sample RATES) × (the seconds that sum represents). For the
            # MINUTE and HOUR rollups that span is the FIXED bucket duration (60 / 3600 s) — NOT
            # sample_count × the LIVE poll interval, which silently rescales ALL historical data when the
            # user changes the poll rate (1s→5s would 5×-over-report months of minute/hour history). When
            # the poll rate is unchanged the two agree (sample_count × poll ≈ bucket duration); the fix
            # only changes the rate-changed case. The raw tier has no stored capture interval, so it uses
            # the current poll interval (it's ≤24h, where the rate rarely changes). Per the db_utils model.
            poll_interval = float(self.config.get("update_rate", 1.0) or 1.0)
            if poll_interval <= 0:  # SMART (-1.0) / invalid → ~1s nominal
                poll_interval = 1.0

            tiers = []
            if start_ts <= now_ts:                  # raw: SUM(bytes_sec) × poll_interval
                tiers.append(("speed_history_raw", "upload_bytes_sec", "download_bytes_sec", poll_interval))
            if start_ts < (now_ts - 24 * 3600):     # minute: SUM(avg) × 60
                tiers.append(("speed_history_minute", "upload_avg", "download_avg", 60.0))
            if start_ts < (now_ts - 30 * 86400):    # hour: SUM(avg) × 3600
                tiers.append(("speed_history_hour", "upload_avg", "download_avg", 3600.0))

            for table, up_expr, down_expr, secs in tiers:
                query = f"SELECT SUM({up_expr}), SUM({down_expr}) FROM {table} WHERE timestamp BETWEEN ? AND ?"
                params = [start_ts, end_ts]
                if interface_name and str(interface_name).lower() != "all":
                    query += " AND interface_name = ?"
                    params.append(interface_name)
                cursor.execute(query, params)
                row = cursor.fetchone()
                if row:
                    total_up += (row[0] or 0.0) * secs
                    total_down += (row[1] or 0.0) * secs

            return total_up, total_down

        except Exception as e:
            self.logger.error("Error calculating total bandwidth: %s", e, exc_info=True)
            return 0.0, 0.0


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
        # Only on the top-level call — the resolution recursion (which sets _visited_resolutions)
        # must not re-barrier on every tier. Callers that run on the GUI thread on a timer (the Overview
        # reload, every few seconds) pass wait_for_flush=False so they never block the UI up to the 2s
        # flush timeout for a missing last second that doesn't matter at multi-hour resolution.
        if _visited_resolutions is None and wait_for_flush:
            self.flush_and_wait()

        # 1. Timeline Setup
        _now_ts = int(datetime.now().timestamp())
        _start_ts = int(start_time.timestamp()) if start_time else 0
        _end_ts = int(end_time.timestamp()) if end_time else _now_ts
        
        # 2. Determine Resolution
        target_res = resolution
        if target_res == 'auto':
            target_res = constants.data.history_period.get_target_resolution(start_time, end_time)

        # Resolution -> Interval (seconds) mapping
        # 'day' maps to 86400, others to their standard seconds
        res_map = {'raw': 1, 'minute': 60, 'hour': 3600, 'day': 86400}
        target_interval = res_map.get(target_res, 60)
        
        # 3. Build TARGETED Query (Single Table based on Resolution)
        # For multi-tier queries (minute/hour), we query both the aggregated table AND raw table
        # to ensure we capture recent data that hasn't been moved to aggregates yet.
        is_all_ifaces = not interface_name or str(interface_name).lower() == "all"
        
        # Map resolution to primary table and columns
        table_map = {
            'raw': ("speed_history_raw", "upload_bytes_sec", "download_bytes_sec"),
            'minute': ("speed_history_minute", "upload_avg", "download_avg"),
            'hour': ("speed_history_hour", "upload_avg", "download_avg"),
            'day': ("speed_history_hour", "upload_avg", "download_avg"),
        }
        
        table, up_col, down_col = table_map.get(target_res, table_map['minute'])
        
        try:
            conn = self._get_read_conn()
            cursor = conn.cursor()
            
            # Time binning calculation
            time_calc = f"CAST(timestamp / {target_interval} AS INTEGER) * {target_interval}"
            
            # Build inner query. For aggregated resolutions, construct a UNION
            # and keep peak speed semantics (MAX) across tiers so timeline
            # changes do not dilute/reshape the same event differently.
            if target_res in ('minute', 'hour', 'day'):
                # Multi-tier merge with explicit peak-preserving logic.
                tier_queries = []
                params = []

                def add_tier_query(table_name: str, up_expr: str, down_expr: str) -> None:
                    q = f"""
                        SELECT
                            {time_calc} as bin_ts,
                            interface_name,
                            {up_expr} as up,
                            {down_expr} as down
                        FROM {table_name}
                        WHERE timestamp BETWEEN ? AND ?
                    """
                    tier_params = [_start_ts, _end_ts]
                    if not is_all_ifaces:
                        q += " AND interface_name = ?"
                        tier_params.append(interface_name)
                    tier_queries.append(q)
                    params.extend(tier_params)

                # Raw keeps exact per-second peaks.
                add_tier_query(constants.data.SPEED_TABLE_RAW, "upload_bytes_sec", "download_bytes_sec")
                # Aggregated tiers use preserved per-bucket maxima.
                add_tier_query(constants.data.SPEED_TABLE_MINUTE, "upload_max", "download_max")

                if target_res in ('hour', 'day'):
                    add_tier_query(constants.data.SPEED_TABLE_HOUR, "upload_max", "download_max")

                union_query = " UNION ALL ".join(tier_queries)
                inner_query = f"""
                    SELECT
                        bin_ts,
                        interface_name,
                        MAX(up) as up,
                        MAX(down) as down
                    FROM ({union_query})
                    GROUP BY bin_ts, interface_name
                """
            else:
                # Raw resolution: single table query
                inner_query = f"""
                    SELECT 
                        {time_calc} as bin_ts, 
                        interface_name, 
                        AVG({up_col}) as up, 
                        AVG({down_col}) as down
                    FROM {table}
                    WHERE timestamp BETWEEN ? AND ?
                """
                params = [_start_ts, _end_ts]
                if not is_all_ifaces:
                    inner_query += " AND interface_name = ?"
                    params.append(interface_name)
                inner_query += " GROUP BY bin_ts, interface_name"
                
            # Outer query: aggregate bins
            if is_all_ifaces:
                outer_query = f"""
                    SELECT bin_ts, COALESCE(SUM(up), 0), COALESCE(SUM(down), 0)
                    FROM ({inner_query})
                    GROUP BY bin_ts
                    ORDER BY bin_ts
                """
            else:
                outer_query = f"""
                    SELECT bin_ts, COALESCE(AVG(up), 0), COALESCE(AVG(down), 0)
                    FROM ({inner_query})
                    GROUP BY bin_ts
                    ORDER BY bin_ts
                """
            
            cursor.execute(outer_query, tuple(params))
            rows = cursor.fetchall()
            self.logger.debug("History query: target_res=%s fetched_rows=%d", target_res, len(rows))
            
            # Convert rows to standard format (Timestamp, Up, Down)
            valid_rows = [row for row in rows if row and row[0] is not None]
            if len(valid_rows) != len(rows):
                self.logger.warning(
                    "Dropping %d invalid graph rows with NULL timestamp (resolution=%s).",
                    len(rows) - len(valid_rows),
                    target_res
                )

            data_points = []
            if return_raw:
                 data_points = [(int(row[0]), float(row[1] or 0.0), float(row[2] or 0.0)) for row in valid_rows]
            else:
                 data_points = [(datetime.fromtimestamp(int(row[0])), float(row[1] or 0.0), float(row[2] or 0.0)) for row in valid_rows]

            # If targeted-table query returned no rows and we targeted an aggregated
            # table (minute/hour/day), fall back to the more comprehensive
            # optimized query in utils.db_utils which unions tiers. This allows
            # freshly-started apps (with only raw data present) to still show
            # historical ranges by reading directly from raw where appropriate.
            if not data_points and target_res != 'raw':
                try:
                    from netspeedtray.utils.db_utils import get_speed_history as util_get_speed_history
                    self.logger.debug("Targeted query returned no rows; falling back to unified DB query.")
                    fallback = util_get_speed_history(self.db_worker.db_path, start_time=start_time, end_time=end_time, interface_name=interface_name)
                    self.logger.debug("Fallback unified DB query returned %d rows", len(fallback) if fallback else 0)
                    if fallback:
                        if return_raw:
                            data_points = [(int(dt.timestamp()), up, down) for dt, up, down in fallback]
                        else:
                            data_points = [(dt, up, down) for dt, up, down in fallback]
                except Exception:
                    self.logger.exception("Fallback unified DB query failed.")

            # --- SMART Edge Padding (Zero-Fill) ---
            # If no real data, create evenly-spaced zeros across the timeline.
            # This prevents gap detection from splitting into single-point segments.
            if start_time and end_time:
                duration = (_end_ts - _start_ts)  # seconds
                
                if not data_points:
                    # No real data: Generate synthetic flat baseline
                    # 1 point per hour, min 10, max 100 for performance
                    num_points = min(100, max(10, int(duration / 3600)))
                    interval = duration / max(1, num_points - 1)
                    
                    for i in range(num_points):
                        pt_ts = _start_ts + (i * interval)
                        if return_raw:
                            data_points.append((pt_ts, 0.0, 0.0))
                        else:
                            data_points.append((datetime.fromtimestamp(pt_ts), 0.0, 0.0))
                else:
                    # Has real data: Just ensure edges are covered
                    s_pt = _start_ts if return_raw else start_time
                    e_pt = _end_ts if return_raw else end_time
                    
                    if data_points[0][0] > s_pt:
                        data_points.insert(0, (s_pt, 0.0, 0.0))
                    if data_points[-1][0] < e_pt:
                        data_points.append((e_pt, 0.0, 0.0))
            
            return data_points

        except sqlite3.Error as e:
            self.logger.error("Unified graph query failed: %s", e, exc_info=True)
            return []


    def get_distinct_interfaces(self) -> List[str]:
        """Returns a sorted list of all unique interface names from the database."""
        try:
            conn = self._get_read_conn()
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


    def get_earliest_data_timestamp(self) -> Optional[datetime]:
        """
        Retrieves the earliest data timestamp from the database by querying all tiers.
        """
        self.flush_batch()
        # time.sleep(0.1)  # REMOVED: This was causing a 100ms freeze on the UI thread.
        
        try:
            conn = self._get_read_conn()
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

        return None


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
            for tid, conn in self._read_conns.items():
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
            self.aggregated_history = deque(self.aggregated_history, maxlen=self.max_history_points)
            self.logger.debug("In-memory speed history capacity updated to %d points.", self.max_history_points)
