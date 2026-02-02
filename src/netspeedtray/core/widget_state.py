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
from datetime import datetime, timedelta
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


# --- Database Worker Thread ---
from netspeedtray.core.database import DatabaseWorker


class WidgetState(QObject):
    """Manages all network speed and bandwidth history for the NetworkSpeedWidget."""

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__()
        self.logger = logger
        self.config = config.copy()

        # In-Memory Cache for real-time mini-graph
        self.max_history_points: int = self._get_max_history_points()
        self.in_memory_history: Deque[SpeedDataSnapshot] = deque(maxlen=self.max_history_points)
        self.aggregated_history: Deque[AggregatedSpeedData] = deque(maxlen=self.max_history_points)
        # Batching list for database writes
        self._db_batch: List[Tuple[int, str, float, float]] = []

        # Database Worker Thread
        db_path = Path(get_app_data_path()) / "speed_history.db"
        self.db_worker = DatabaseWorker(db_path)
        self.db_worker.error.connect(lambda msg: self.logger.error("DB Worker Error: %s", msg))
        self.db_worker.start()

        # Timers for periodic operations
        self.batch_persist_timer = QTimer(self)
        self.batch_persist_timer.timeout.connect(self.flush_batch)
        self.batch_persist_timer.start(10 * 1000) # Persist every 10 seconds

        self.maintenance_timer = QTimer(self)
        self.maintenance_timer.timeout.connect(self.trigger_maintenance)
        self.maintenance_timer.start(60 * 60 * 1000) # Run maintenance every hour

        # Persistent Read Connections (per-thread)
        self._read_conns: Dict[int, sqlite3.Connection] = {}
        self._read_conns_lock = threading.Lock()

        self.logger.debug("WidgetState initialized with threaded database worker.")


    def _get_read_conn(self) -> sqlite3.Connection:
        """
        Provides a thread-specific, read-only SQLite connection.
        Using a persistent connection reduces the overhead of repeatedly 
        opening/closing the database during UI updates or graph rendering.
        """
        tid = threading.get_ident()
        
        with self._read_conns_lock:
            if tid not in self._read_conns:
                self.logger.debug("Opening persistent READ connection for thread %d", tid)
                try:
                    # Use 'ro' mode for safety; timeout allows for occasional write locks
                    conn = sqlite3.connect(
                        f"file:{self.db_worker.db_path}?mode=ro", 
                        uri=True, 
                        timeout=timeouts.DB_BUSY_TIMEOUT_MS / 1000.0
                    )
                    conn.execute(f"PRAGMA busy_timeout = {timeouts.DB_BUSY_TIMEOUT_MS};")
                    self._read_conns[tid] = conn
                except sqlite3.Error as e:
                    self.logger.error("Failed to open read connection for thread %d: %s", tid, e)
                    # Fallback: try opening a non-URI connection if URI fails (though it shouldn't)
                    return sqlite3.connect(self.db_worker.db_path, timeout=5)
            
            return self._read_conns[tid]


    def add_speed_data(self, speed_data: Dict[str, Tuple[float, float]], now: Optional[datetime] = None) -> None:
        """
        Adds new per-interface speed data. Updates in-memory state and adds
        to the database write batch.

        Args:
            speed_data: A dictionary mapping interface names to a tuple of
                        (upload_bytes_sec, download_bytes_sec) as FLOATS.
            now: Optional datetime override (defaults to datetime.now()).
        """
        _now = now or datetime.now()
        
        # The in-memory history now stores the full per-interface data for live filtering.
        self.in_memory_history.append(SpeedDataSnapshot(
            speeds=speed_data.copy(),
            timestamp=_now
        ))

        # --- PRE-AGGREGATION OPTIMIZATION ---
        # Sum all interface speeds now so the renderer doesn't have to do it every frame.
        total_up = sum(speeds[0] for speeds in speed_data.values())
        total_down = sum(speeds[1] for speeds in speed_data.values())
        self.aggregated_history.append(AggregatedSpeedData(
            upload=total_up,
            download=total_down,
            timestamp=_now
        ))

        timestamp = int(_now.timestamp())
        min_speed = network.speed.MIN_RECORDABLE_SPEED_BPS
        for interface, (up_speed, down_speed) in speed_data.items():
            # Only add to the database batch if the speed is significant
            if up_speed >= min_speed or down_speed >= min_speed:
                self._db_batch.append((timestamp, interface, up_speed, down_speed))


    def get_total_bandwidth_for_period(self, start_time: Optional[datetime], end_time: datetime, interface_name: Optional[str] = None) -> Tuple[float, float]:
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
            
            tiers = []
            if start_ts <= now_ts: # Always check raw as it might have unaggregated data
                tiers.append(("speed_history_raw", "upload_bytes_sec", "download_bytes_sec"))
            
            if start_ts < (now_ts - 24*3600): # Might have minute data
                tiers.append(("speed_history_minute", "upload_avg * 60", "download_avg * 60"))
                
            if start_ts < (now_ts - 30*86400): # Might have hour data
                tiers.append(("speed_history_hour", "upload_avg * 3600", "download_avg * 3600"))

            for table, up_expr, down_expr in tiers:
                query = f"SELECT SUM({up_expr}), SUM({down_expr}) FROM {table} WHERE timestamp BETWEEN ? AND ?"
                params = [start_ts, end_ts]
                
                if interface_name and str(interface_name).lower() != "all":
                    query += " AND interface_name = ?"
                    params.append(interface_name)
                
                cursor.execute(query, params)
                row = cursor.fetchone()
                if row:
                    total_up += (row[0] or 0.0)
                    total_down += (row[1] or 0.0)

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


    def flush_batch(self) -> None:
        """Sends the current batch of speed data to the database worker."""
        if self._db_batch:
            batch_to_send = self._db_batch.copy()
            self._db_batch.clear()
            self.db_worker.enqueue_task("persist_speed", batch_to_send)


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


    def get_speed_history(self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None, interface_name: Optional[str] = None, return_raw: bool = False, resolution: Literal['auto', 'raw', 'minute', 'hour', 'day'] = 'auto', _visited_resolutions: set = None) -> List[Tuple[Union[datetime, float], float, float]]:
        """
        Retrieves speed history by querying ALL relevant database tiers (raw, minute, hour)
        and unifying them into a single timeline.
        """
        self.flush_batch()

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
        # This replaces the slow UNION ALL approach
        is_all_ifaces = not interface_name or str(interface_name).lower() == "all"
        
        # Map resolution to table and columns
        table_map = {
            'raw': ("speed_history_raw", "upload_bytes_sec", "download_bytes_sec"),
            'minute': ("speed_history_minute", "upload_avg", "download_avg"),
            'hour': ("speed_history_hour", "upload_avg", "download_avg"),
            'day': ("speed_history_hour", "upload_avg", "download_avg"),  # Use hour table for day aggregation
        }
        
        table, up_col, down_col = table_map.get(target_res, table_map['minute'])
        
        try:
            conn = self._get_read_conn()
            cursor = conn.cursor()
            
            # Time binning calculation
            time_calc = f"CAST(timestamp / {target_interval} AS INTEGER) * {target_interval}"
            
            # Build inner query
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
            
            # Outer Query: Aggregate logic
            # Note: LIMIT removed - aggregation already reduces data volume adequately.
            # A LIMIT here would cut off recent data (ordered ASC, oldest first).
            
            if is_all_ifaces:
                # Sum across interfaces (Eth + Wifi)
                outer_query = f"""
                    SELECT bin_ts, COALESCE(SUM(up), 0), COALESCE(SUM(down), 0)
                    FROM ({inner_query})
                    GROUP BY bin_ts
                    ORDER BY bin_ts
                """
            else:
                # Single interface: Just order
                outer_query = f"""
                    SELECT bin_ts, COALESCE(AVG(up), 0), COALESCE(AVG(down), 0)
                    FROM ({inner_query})
                    GROUP BY bin_ts
                    ORDER BY bin_ts
                """
            
            # self.logger.debug(f"Executing Query: {outer_query} | Params: {params}")
            cursor.execute(outer_query, tuple(params))
            
            rows = cursor.fetchall()
            
            # Convert rows to standard format (Timestamp, Up, Down)
            data_points = []
            if return_raw:
                 data_points = [(row[0], row[1], row[2]) for row in rows]
            else:
                 data_points = [(datetime.fromtimestamp(row[0]), row[1], row[2]) for row in rows]

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