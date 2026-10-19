"""
FROZEN ORACLE - verbatim copies of the eight ``WidgetState`` history readers (plus the
``_read_usage_snapshot`` helper) as they stood on ``main`` @ a13bf62, BEFORE they were delegated to
``core/db/history_reader.py`` (v8 step 2). Main had moved to 0092539 by the time this branch was
rebased; those commits touched only comments inside these methods, so the oracle is still the code
the facade replaced.

Do not edit, "fix" or modernise this file. Its only job is to be the old behaviour the golden tests
(test_history_reader_golden.py) diff the facade against. The methods are called unbound with a real
``WidgetState`` as ``self`` - e.g. ``LegacyV7Readers.get_speed_history(ws, ...)`` - so they read through
the same thread-local ``_get_read_conn()`` the facade uses. The one change from the original source:
``_try_load_usage`` calls ``LegacyV7Readers._read_usage_snapshot(self, cur)`` explicitly, and the class
constant ``_RAW_SUMMARY_SECONDS`` lives here, so the oracle cannot drift if WidgetState's copies move.
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from netspeedtray import constants
from netspeedtray.core import usage_snapshot
from netspeedtray.utils.timer_utils import resolve_poll_interval_seconds


class LegacyV7Readers:
    _RAW_SUMMARY_SECONDS = 24 * 3600

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

            # Union ALL tiers (non-overlapping - data is moved, not copied) and bin to the target
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
            if win <= LegacyV7Readers._RAW_SUMMARY_SECONDS:
                cur.execute(f"SELECT value FROM {constants.data.HARDWARE_STATS_TABLE_RAW} "
                            f"WHERE stat_type=? AND timestamp BETWEEN ? AND ?", (stat_type, st, et))
                vals = [r[0] for r in cur.fetchall()]
                return S.summarize_raw(vals, S.coverage_pct(len(vals), win, poll_interval))
            # Beyond the raw horizon the window spans tiers: the recent <24h is still in RAW, older data
            # in minute/hour. Union all three (non-overlapping - data is moved, not copied - exactly like
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

            if win <= LegacyV7Readers._RAW_SUMMARY_SECONDS:
                vals = _raw_vals()
                return S.summarize_raw(vals, S.coverage_pct(len(vals), win, poll_interval))

            # Beyond the raw horizon the window spans tiers: the recent <24h is still in RAW, older data
            # in minute/hour. Union all three (non-overlapping, like get_speed_history) so the summary
            # covers the WHOLE window instead of only the rolled-up older half (which dropped the most
            # recent ~24h and disagreed with the graph for the same window).
            avgs, maxes, counts = [], [], []
            covered_seconds = 0.0   # for coverage: count distinct TIME buckets × their duration, NOT
            #                         SUM(sample_count) - which, for an "all" aggregate, double-counts by
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
            # MINUTE and HOUR rollups that span is the FIXED bucket duration (60 / 3600 s) - NOT
            # sample_count × the LIVE poll interval, which silently rescales ALL historical data when the
            # user changes the poll rate (1s→5s would 5×-over-report months of minute/hour history). When
            # the poll rate is unchanged the two agree (sample_count × poll ≈ bucket duration); the fix
            # only changes the rate-changed case. The raw tier has no stored capture interval, so it uses
            # the current poll interval (it's ≤24h, where the rate rarely changes). Per the db_utils model.
            # SMART (-1.0) / the historical 0 sentinel actually sample every 2.0 s, not 1.0 s -
            # resolve through the shared helper so raw-tier volume is not under-reported 2x.
            poll_interval = resolve_poll_interval_seconds(self.config.get("update_rate", 1.0))

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

    def _try_load_usage(self) -> bool:
        """Load the persisted odometer once the worker has created the table.
        Returns False (so the caller defers) if the table isn't ready yet - never
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
            self._usage_launch_snapshot = LegacyV7Readers._read_usage_snapshot(self, cur)
            self._usage_loaded = True
            return True
        except Exception as e:
            self.logger.debug("Usage counter not ready yet: %s", e)
            return False

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
