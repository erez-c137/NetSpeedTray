"""
The read side of the history database, behind one interface (v8 rebuild, step 2).

``WidgetState`` used to hold the SQL for every history read. Those reads now go through a
``HistoryReader``, so the v8 schema can arrive as a second backend (step 3) and be switched in at one
place (step 7) instead of rewriting eight call sites against a new schema at once.

``V7HistoryReader`` is today's schema and must stay **behaviour-identical** to the methods it replaced:
``tests/unit/test_history_reader_golden.py`` diffs every method against a frozen copy of the old code
(``tests/unit/legacy_v7_reader_oracle.py``) on one rich fixture database written through the real
``DatabaseWorker``. Change a query here and that test is the thing to satisfy - not to edit.

Threading: the reader owns no connection. It asks ``conn_provider`` (``WidgetState._get_read_conn``)
for one on every call, so each calling thread keeps its own thread-local read connection exactly as
before - the Monitor's graph worker never shares a cursor with the GUI thread.

**Tier precedence.** The tiers are query-precedence layers, not additive: each tier answers only
``[its floor, the finer tiers' MIN(timestamp))`` - raw wherever raw has data, else minute, else hour.
The finer MIN only looks inside that tier's retention horizon (+ slack), so a row stamped far in the
past by a clock set back costs its own seconds and never hides the coarser history above it; and it
is a subquery of the statement it bounds, so boundary and data come from one snapshot. Readers that
need several statements run them in one read transaction (``_snapshot``), so an hourly roll-up that
commits mid-read can neither drop nor double-count the slice it moved.
v7 MOVES rows down the chain, so the tiers never overlap and the rule is a no-op on every real v7
database (the golden test proves it). It is here because v8 keeps independent per-tier retention, and
the design review measured an unguarded fan-out at exactly **2.00x** the true data-cap total
(database-v8-final.md 4.1) - every reader used to SUM every tier with no mutual exclusion.
``tests/unit/test_history_reader_precedence.py`` pins the 1.00x.

The side effects that belong to the app rather than to storage stay in ``WidgetState``: the
flush-before-read barrier, the ``db_worker`` presence guard, the poll-interval lookup from config, and
the odometer's in-memory state.
"""

import logging
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

from netspeedtray import constants
from netspeedtray.core import usage_snapshot

Resolution = Literal['auto', 'raw', 'minute', 'hour', 'day']
SpeedPoint = Tuple[Union[datetime, float], float, float]


@dataclass(frozen=True)
class UsageRecord:
    """The persisted data-usage odometer, exactly as stored - validation is the caller's job.

    ``counter_row`` is (cumulative_up, cumulative_down, anchor_up, anchor_down, period_key), or None
    when the table exists but holds no row yet. ``snapshot`` is the previous session's decoded NIC
    counter snapshot, {} when there is none or it is unreadable."""
    counter_row: Optional[Tuple[Any, ...]]
    snapshot: Dict[str, Any] = field(default_factory=dict)


class HistoryReader(ABC):
    """Every read the app makes against the history database."""

    @abstractmethod
    def speed_history(self, start_time: Optional[datetime], end_time: Optional[datetime],
                      interface_name: Optional[str], return_raw: bool,
                      resolution: Resolution) -> List[SpeedPoint]:
        """(timestamp, up B/s, down B/s) binned to ``resolution``, edges zero-padded when the window
        is bounded. ``timestamp`` is a datetime, or epoch seconds when ``return_raw``."""

    @abstractmethod
    def hardware_history(self, stat_type: str, start_time: Optional[datetime],
                         end_time: Optional[datetime]) -> List[Tuple[datetime, float]]:
        """(timestamp, value) for one hardware stat, binned by window length (1 s / 60 s / 3600 s)."""

    @abstractmethod
    def summarize_hardware(self, stat_type: str, start_time: datetime, end_time: datetime,
                           poll_interval: float = 1.0):
        """Honest WindowSummary for a hardware stat over [start, end] (see utils.summaries)."""

    @abstractmethod
    def summarize_network(self, direction: str, start_time: datetime, end_time: datetime,
                          interface_name: Optional[str] = None, poll_interval: float = 1.0):
        """Honest WindowSummary for 'download' or 'upload' B/s over [start, end], per interface or
        aggregated across interfaces when ``interface_name`` is None/'all'."""

    @abstractmethod
    def total_bandwidth(self, start_time: Optional[datetime], end_time: datetime,
                        interface_name: Optional[str], poll_interval: float) -> Tuple[float, float]:
        """(upload_bytes, download_bytes) moved in [start, end]. ``poll_interval`` is the seconds one
        raw row stands for (the caller resolves it from config)."""

    @abstractmethod
    def distinct_interfaces(self) -> List[str]:
        """Every interface name in any speed tier, sorted."""

    @abstractmethod
    def earliest_timestamp(self) -> Optional[datetime]:
        """The oldest speed sample in any tier, or None for an empty history."""

    @abstractmethod
    def load_usage(self) -> Optional[UsageRecord]:
        """The odometer row + counter snapshot, or None while the table does not exist yet (the
        caller defers). Storage errors propagate; a bad snapshot only empties ``snapshot``."""


class V7HistoryReader(HistoryReader):
    """The v7 schema: speed_history_{raw,minute,hour} + hardware_stats_{raw,minute,hour} +
    usage_counter + metadata."""

    def __init__(self, conn_provider: Callable[[], sqlite3.Connection],
                 db_path_provider: Callable[[], Path],
                 logger: Optional[logging.Logger] = None) -> None:
        self._conn = conn_provider
        self._db_path = db_path_provider
        # The owner's logger, so every message keeps the name and wording it had inside WidgetState.
        self.logger = logger or logging.getLogger("NetSpeedTray.HistoryReader")

    # --- tier precedence ----------------------------------------------------------------------

    _NO_BOUND = "9e18"   # past any epoch second: "no finer data inside the horizon" bounds nothing

    @staticmethod
    def _horizons(now_ts: int) -> Tuple[int, int]:
        """Oldest timestamp a legitimately-unrolled raw / minute row can have: the tier's retention,
        plus slack for the hourly maintenance cadence and the bucket floor. A finer row older than
        this is a stray (clock set back, VM snapshot, dead CMOS) and must not set a tier boundary."""
        d = constants.data
        slack = d.TIER_BOUNDARY_SLACK_SECONDS
        return (now_ts - d.RAW_TIER_RETENTION_SECONDS - slack,
                now_ts - d.MINUTE_TIER_RETENTION_SECONDS - slack)

    def _bounds(self, tables: Tuple[str, ...]) -> List[Tuple[str, tuple]]:
        """Per tier (``tables`` finest first), the SQL clause + params confining it under every FINER
        tier's earliest in-horizon row. The finest tier is unbounded. The MIN is an uncorrelated
        scalar subquery - evaluated once, inside the bounded statement's own snapshot - and a single
        seek on the (timestamp, ...) primary key."""
        horizons = self._horizons(int(datetime.now().timestamp()))
        out: List[Tuple[str, tuple]] = [("", ())]
        for i in range(1, len(tables)):
            parts = [f"COALESCE((SELECT MIN(timestamp) FROM {tables[j]} WHERE timestamp >= ?), {self._NO_BOUND})"
                     for j in range(i)]
            expr = parts[0] if len(parts) == 1 else f"MIN({', '.join(parts)})"
            out.append((f" AND timestamp < {expr}", tuple(horizons[:i])))
        return out

    @staticmethod
    def _begin_snapshot(conn: sqlite3.Connection) -> bool:
        """Open a read transaction so every statement of one read sees the same snapshot (WAL). Returns
        whether this call opened it (and so must end it). Read connections never write."""
        if getattr(conn, "in_transaction", False):
            return False
        conn.execute("BEGIN")
        return True

    @staticmethod
    def _end_snapshot(conn: sqlite3.Connection, began: bool) -> None:
        if began:
            try:
                conn.execute("COMMIT")   # nothing written - just releases the snapshot
            except sqlite3.Error:
                pass

    _SPEED_TIERS = (constants.data.SPEED_TABLE_RAW, constants.data.SPEED_TABLE_MINUTE,
                    constants.data.SPEED_TABLE_HOUR)
    _HW_TIERS = (constants.data.HARDWARE_STATS_TABLE_RAW, constants.data.HARDWARE_STATS_TABLE_MINUTE,
                 constants.data.HARDWARE_STATS_TABLE_HOUR)

    # The raw tier retains ~24h; windows within it summarize exactly (pro-stats: tier-aware honest
    # window summaries - exact <=24h raw, avg+max beyond).
    _RAW_SUMMARY_SECONDS = 24 * 3600

    # --- speed --------------------------------------------------------------------------------

    def speed_history(self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None,
                      interface_name: Optional[str] = None, return_raw: bool = False,
                      resolution: Resolution = 'auto') -> List[SpeedPoint]:
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
            conn = self._conn()
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
                bound = dict(zip(self._SPEED_TIERS, self._bounds(self._SPEED_TIERS)))

                def add_tier_query(table_name: str, up_expr: str, down_expr: str) -> None:
                    below_sql, below_params = bound[table_name]
                    q = f"""
                        SELECT
                            {time_calc} as bin_ts,
                            interface_name,
                            {up_expr} as up,
                            {down_expr} as down
                        FROM {table_name}
                        WHERE timestamp BETWEEN ? AND ?{below_sql}
                    """
                    tier_params = [_start_ts, _end_ts, *below_params]
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
            # (Kept for parity. It reads a SUBSET of what the union above already read - raw from the
            # aggregation cutoff on, minute before it - so when the union is empty it is empty too.)
            if not data_points and target_res != 'raw':
                try:
                    from netspeedtray.utils.db_utils import get_speed_history as util_get_speed_history
                    self.logger.debug("Targeted query returned no rows; falling back to unified DB query.")
                    fallback = util_get_speed_history(self._db_path(), start_time=start_time, end_time=end_time, interface_name=interface_name)
                    self.logger.debug("Fallback unified DB query returned %d rows", len(fallback) if fallback else 0)
                    if fallback:
                        if return_raw:
                            data_points = [(int(dt.timestamp()), up, down) for dt, up, down in fallback]
                        else:
                            data_points = [(dt, up, down) for dt, up, down in fallback]
                except Exception:
                    self.logger.exception("Fallback unified DB query failed.")

            # --- Edge Padding (Zero-Fill) ---
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

    def summarize_network(self, direction: str, start_time: datetime, end_time: datetime,
                          interface_name: Optional[str] = None, poll_interval: float = 1.0):
        from netspeedtray.utils import summaries as S
        col = "download" if direction == "download" else "upload"
        win = max(0.0, (end_time - start_time).total_seconds())
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        iface = None if interface_name in (None, "all") else interface_name
        wh = "" if iface is None else " AND interface_name=?"
        params_tail = () if iface is None else (iface,)
        conn, began = None, False
        try:
            conn = self._conn()
            began = self._begin_snapshot(conn)
            cur = conn.cursor()

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
            # in minute/hour. Union all three so the summary covers the WHOLE window instead of only the
            # rolled-up older half (which dropped the most recent ~24h and disagreed with the graph for
            # the same window).
            avgs, maxes, counts = [], [], []
            covered_seconds = 0.0   # for coverage: count distinct TIME buckets x their duration, NOT
            #                         SUM(sample_count) - which, for an "all" aggregate, double-counts by
            #                         NIC and inflates the evidence-admissibility figure past 100%.
            _, min_bound, hour_bound = self._bounds(self._SPEED_TIERS)
            for table, bucket_secs, (below_sql, below_params) in (
                    (constants.data.SPEED_TABLE_MINUTE, 60.0, min_bound),
                    (constants.data.SPEED_TABLE_HOUR, 3600.0, hour_bound)):
                cur.execute(
                    f"SELECT SUM({col}_avg), MAX(t.mx), SUM(sample_count) FROM "
                    f"(SELECT timestamp, {col}_avg, {col}_max AS mx, sample_count FROM {table} "
                    f" WHERE timestamp BETWEEN ? AND ?{below_sql}{wh}) t GROUP BY t.timestamp",
                    (st, et) + below_params + params_tail)
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
        finally:
            if conn is not None:
                self._end_snapshot(conn, began)

    def total_bandwidth(self, start_time: Optional[datetime], end_time: datetime,
                        interface_name: Optional[str], poll_interval: float) -> Tuple[float, float]:
        conn, began = None, False
        try:
            conn = self._conn()
            began = self._begin_snapshot(conn)
            cursor = conn.cursor()

            start_ts = int(start_time.timestamp()) if start_time else 0
            end_ts = int(end_time.timestamp())

            total_up, total_down = 0.0, 0.0

            # Optimization: Only query tiers that could potentially have data for this range.
            now_ts = int(datetime.now().timestamp())

            # Bytes per tier = (sum of per-sample RATES) x (the seconds that sum represents). For the
            # MINUTE and HOUR rollups that span is the FIXED bucket duration (60 / 3600 s) - NOT
            # sample_count x the LIVE poll interval, which silently rescales ALL historical data when the
            # user changes the poll rate (1s->5s would 5x-over-report months of minute/hour history). The
            # raw tier has no stored capture interval, so it uses the current poll interval (it's <=24h,
            # where the rate rarely changes).
            #
            # KNOWN OVER-COUNT, deliberately kept (v8 step 2 is behaviour-identical): avg x 60 assumes
            # every second of the bucket was sampled, but the writer only stores a raw row when the NIC
            # moved >= 1 B/s, so avg is the mean over ACTIVE seconds and avg x 60 over-states a partly
            # idle minute. The v8 backend fixes it by storing bytes (SUM(bytes)), not by patching this.
            tiers = []
            if start_ts <= now_ts:                  # raw: SUM(bytes_sec) x poll_interval
                tiers.append(("speed_history_raw", "upload_bytes_sec", "download_bytes_sec", poll_interval))
            if start_ts < (now_ts - 24 * 3600):     # minute: SUM(avg) x 60
                tiers.append(("speed_history_minute", "upload_avg", "download_avg", 60.0))
            if start_ts < (now_ts - 30 * 86400):    # hour: SUM(avg) x 3600
                tiers.append(("speed_history_hour", "upload_avg", "download_avg", 3600.0))

            # Precedence: a tier is summed only below every finer tier's earliest row, so a range two
            # tiers both hold is billed once (raw where present, else minute, else hour).
            bound = dict(zip(self._SPEED_TIERS, self._bounds(self._SPEED_TIERS)))
            for table, up_expr, down_expr, secs in tiers:
                below_sql, below_params = bound[table]
                query = (f"SELECT SUM({up_expr}), SUM({down_expr}) FROM {table} "
                         f"WHERE timestamp BETWEEN ? AND ?{below_sql}")
                params = [start_ts, end_ts, *below_params]
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
        finally:
            if conn is not None:
                self._end_snapshot(conn, began)

    def distinct_interfaces(self) -> List[str]:
        try:
            conn = self._conn()
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

    def earliest_timestamp(self) -> Optional[datetime]:
        try:
            conn = self._conn()
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

    # --- hardware -----------------------------------------------------------------------------

    def hardware_history(self, stat_type: str, start_time: Optional[datetime] = None,
                         end_time: Optional[datetime] = None) -> List[Tuple[datetime, float]]:
        try:
            conn = self._conn()
            cursor = conn.cursor()

            _end = end_time or datetime.now()
            _start = start_time or (_end - timedelta(hours=24))

            start_ts = int(_start.timestamp())
            end_ts = int(_end.timestamp())

            # Target resolution by window length: raw (<=6h), minute (<=30d), hour (>30d).
            duration = end_ts - start_ts
            interval = 1 if duration <= 6 * 3600 else (60 if duration <= 30 * 86400 else 3600)

            # Union ALL tiers and bin to the target interval, mirroring speed_history. A
            # recent-but-long window (e.g. 48h or a week) has its most-recent <24h only in the RAW tier
            # and the older portion in minute/hour; reading a single tier silently dropped the recent
            # half (and the old fallback only fired when the tier was TOTALLY empty). Binning keeps the
            # point count bounded by window/interval regardless.
            HRAW = constants.data.HARDWARE_STATS_TABLE_RAW
            HMIN = constants.data.HARDWARE_STATS_TABLE_MINUTE
            HHOUR = constants.data.HARDWARE_STATS_TABLE_HOUR
            bin_ts = f"CAST(timestamp / {interval} AS INTEGER) * {interval}"
            _, (min_sql, min_params), (hour_sql, hour_params) = self._bounds(self._HW_TIERS)
            cursor.execute(f"""
                SELECT b, AVG(v) FROM (
                    SELECT {bin_ts} AS b, value AS v FROM {HRAW}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?
                    UNION ALL
                    SELECT {bin_ts} AS b, avg_value AS v FROM {HMIN}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?{min_sql}
                    UNION ALL
                    SELECT {bin_ts} AS b, avg_value AS v FROM {HHOUR}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?{hour_sql}
                ) GROUP BY b ORDER BY b ASC
            """, (stat_type, start_ts, end_ts) + (stat_type, start_ts, end_ts) + min_params
                 + (stat_type, start_ts, end_ts) + hour_params)
            rows = cursor.fetchall()

            return [(datetime.fromtimestamp(row[0]), row[1]) for row in rows]
        except Exception as e:
            self.logger.error("Error fetching hardware history: %s", e, exc_info=True)
            return []

    def summarize_hardware(self, stat_type: str, start_time: datetime, end_time: datetime,
                           poll_interval: float = 1.0):
        from netspeedtray.utils import summaries as S
        win = max(0.0, (end_time - start_time).total_seconds())
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        conn, began = None, False
        try:
            conn = self._conn()
            began = self._begin_snapshot(conn)
            cur = conn.cursor()
            if win <= self._RAW_SUMMARY_SECONDS:
                cur.execute(f"SELECT value FROM {constants.data.HARDWARE_STATS_TABLE_RAW} "
                            f"WHERE stat_type=? AND timestamp BETWEEN ? AND ?", (stat_type, st, et))
                vals = [r[0] for r in cur.fetchall()]
                return S.summarize_raw(vals, S.coverage_pct(len(vals), win, poll_interval))
            # Beyond the raw horizon the window spans tiers: the recent <24h is still in RAW, older data
            # in minute/hour. Union all three so the summary covers the WHOLE window instead of only the
            # rolled-up older half, which silently dropped the most recent ~24h and disagreed with the
            # graph.
            avgs, maxes, counts = [], [], []
            _, min_bound, hour_bound = self._bounds(self._HW_TIERS)
            for table, (below_sql, below_params) in ((constants.data.HARDWARE_STATS_TABLE_MINUTE, min_bound),
                                                     (constants.data.HARDWARE_STATS_TABLE_HOUR, hour_bound)):
                cur.execute(f"SELECT avg_value, max_value, sample_count FROM {table} "
                            f"WHERE stat_type=? AND timestamp BETWEEN ? AND ?{below_sql}",
                            (stat_type, st, et) + below_params)
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
        finally:
            if conn is not None:
                self._end_snapshot(conn, began)

    # --- data-usage odometer ------------------------------------------------------------------

    def load_usage(self) -> Optional[UsageRecord]:
        conn = self._conn()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (constants.data.USAGE_COUNTER_TABLE,))
        if cur.fetchone() is None:
            return None
        cur.execute(
            f"SELECT cumulative_up, cumulative_down, anchor_up, anchor_down, period_key "
            f"FROM {constants.data.USAGE_COUNTER_TABLE} WHERE id = 1")
        row = cur.fetchone()
        return UsageRecord(tuple(row) if row else None, self._read_usage_snapshot(cur))

    def _read_usage_snapshot(self, cur: sqlite3.Cursor) -> Dict[str, Any]:
        """The previous session's counter snapshot (metadata row), or {} - never raises, so a
        missing/garbled snapshot can only cost the reconciliation, never the odometer load."""
        try:
            cur.execute("SELECT value FROM metadata WHERE key = ?",
                        (constants.network.usage.SNAPSHOT_METADATA_KEY,))
            row = cur.fetchone()
            return usage_snapshot.decode_snapshot(row[0] if row else None)
        except Exception as e:
            self.logger.debug("No usage counter snapshot readable: %s", e)
            return {}
