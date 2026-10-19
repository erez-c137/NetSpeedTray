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

The side effects that belong to the app rather than to storage stay in ``WidgetState``: the
flush-before-read barrier, the ``db_worker`` presence guard, the poll-interval lookup from config, and
the odometer's in-memory state.
"""

import logging
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Literal, Optional, Tuple, Union

from netspeedtray import constants

Resolution = Literal['auto', 'raw', 'minute', 'hour', 'day']
SpeedPoint = Tuple[Union[datetime, float], float, float]


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

    def summarize_hardware(self, stat_type: str, start_time: datetime, end_time: datetime,
                           poll_interval: float = 1.0):
        from netspeedtray.utils import summaries as S
        win = max(0.0, (end_time - start_time).total_seconds())
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        try:
            cur = self._conn().cursor()
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
