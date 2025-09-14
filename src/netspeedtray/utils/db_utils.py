"""
Database utility functions for NetSpeedTray.

This module provides functions to:
- Initialize and manage SQLite database (`speed_history.db`) for storing network speed and bandwidth data.
- Persist speed and bandwidth data in batches for efficiency.
- Retrieve historical data for visualization (e.g., max speeds, bandwidth usage).
- Aggregate historical speed data to reduce database size.
- Vacuum the database to reclaim disk space after pruning.

The database schema includes:
- `speed_history`: Per-second speed data (timestamp, upload, download, interface, deleted_at).
- `speed_history_aggregated`: Aggregated per-minute speed data (period_start, period_end, avg_upload, avg_download, interface, deleted_at).
- `bandwidth_history`: Bandwidth data (timestamp, bytes_sent, bytes_recv, interface, deleted_at).
- `app_bandwidth`: Per-app bandwidth data (timestamp, app_name, bytes_sent, bytes_recv, interface, deleted_at).

All database operations are thread-safe using a provided lock.
"""

import logging
import os
import sqlite3
import threading
from collections import namedtuple
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any

# --- Local Imports ---
from .config import ConfigManager
from .. import constants

# --- Named Tuples for Data ---
SpeedData = namedtuple("SpeedData", ["timestamp", "upload", "download", "interface"])
AppBandwidthData = namedtuple("AppBandwidthData", ["app_name", "timestamp", "bytes_sent", "bytes_recv", "interface"])


def init_database(db_path: Union[str, Path]) -> None:
    """
    Initialize the SQLite database with required tables and indices.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Initializing database at %s", db_path)
    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {constants.data.SPEED_TABLE} (
                    timestamp INTEGER PRIMARY KEY, upload REAL NOT NULL, download REAL NOT NULL,
                    interface TEXT NOT NULL, deleted_at INTEGER
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{constants.data.SPEED_TABLE}_timestamp ON {constants.data.SPEED_TABLE}(timestamp)")

            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {constants.data.AGGREGATED_TABLE} (
                    period_start INTEGER NOT NULL, period_end INTEGER NOT NULL, avg_upload REAL NOT NULL,
                    avg_download REAL NOT NULL, interface TEXT NOT NULL, deleted_at INTEGER,
                    PRIMARY KEY (period_start, interface)
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{constants.data.AGGREGATED_TABLE}_period_end ON {constants.data.AGGREGATED_TABLE}(period_end)")

            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {constants.data.BANDWIDTH_TABLE} (
                    timestamp INTEGER PRIMARY KEY, bytes_sent INTEGER NOT NULL, bytes_recv INTEGER NOT NULL,
                    interface TEXT NOT NULL, deleted_at INTEGER
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{constants.data.BANDWIDTH_TABLE}_timestamp ON {constants.data.BANDWIDTH_TABLE}(timestamp)")

            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {constants.data.APP_BANDWIDTH_TABLE} (
                    timestamp INTEGER NOT NULL, app_name TEXT NOT NULL, bytes_sent INTEGER NOT NULL,
                    bytes_recv INTEGER NOT NULL, interface TEXT NOT NULL, deleted_at INTEGER
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{constants.data.APP_BANDWIDTH_TABLE}_timestamp_app ON {constants.data.APP_BANDWIDTH_TABLE}(timestamp, app_name)")

            conn.commit()
            logger.debug("Database initialized successfully")
    except sqlite3.Error as e:
        logger.error("Failed to initialize database: %s", e)
        raise


def persist_speed_batch(db_path: Union[str, Path], batch: List[Tuple[int, float, float, str]], db_lock: threading.Lock) -> None:
    """
    Persist a batch of speed data to the speed_history table.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Persisting speed batch of size %d", len(batch))
    if not batch:
        return
    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            # CORRECTED: Added comma between SQL string and batch parameter
            cursor.executemany(
                f"INSERT OR REPLACE INTO {constants.data.SPEED_TABLE} (timestamp, upload, download, interface, deleted_at) VALUES (?, ?, ?, ?, NULL)",
                batch
            )
            conn.commit()
            logger.debug("Persisted %d speed records", len(batch))
    except sqlite3.Error as e:
        logger.error("Failed to persist speed batch: %s", e)
        raise

def persist_bandwidth_batch(db_path: Union[str, Path], batch: List[Tuple[int, int, int, str]], db_lock: threading.Lock) -> None:
    """
    Persist a batch of bandwidth data to the bandwidth_history table.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Persisting bandwidth batch of size %d", len(batch))
    if not batch:
        return
    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            # CORRECTED: Added comma between SQL string and batch parameter
            cursor.executemany(
                f"INSERT OR REPLACE INTO {constants.data.BANDWIDTH_TABLE} (timestamp, bytes_sent, bytes_recv, interface, deleted_at) VALUES (?, ?, ?, ?, NULL)",
                batch
            )
            conn.commit()
            logger.debug("Persisted %d bandwidth records", len(batch))
    except sqlite3.Error as e:
        logger.error("Failed to persist bandwidth batch: %s", e)
        raise

def persist_app_bandwidth_batch(db_path: Union[str, Path], batch: List[Tuple[int, str, int, int, str]], db_lock: threading.Lock) -> None:
    """
    Persist a batch of per-app bandwidth data to the app_bandwidth table.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Persisting app bandwidth batch of size %d", len(batch))
    if not batch:
        return
    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            # CORRECTED: Added comma between SQL string and batch parameter
            cursor.executemany(
                f"INSERT INTO {constants.data.APP_BANDWIDTH_TABLE} (timestamp, app_name, bytes_sent, bytes_recv, interface, deleted_at) VALUES (?, ?, ?, ?, ?, NULL)",
                batch
            )
            conn.commit()
            logger.debug("Persisted %d app bandwidth records", len(batch))
    except sqlite3.Error as e:
        logger.error("Failed to persist app bandwidth batch: %s", e)
        raise


def get_speed_history(db_path: Union[str, Path], start_time: Optional[datetime] = None,
                      end_time: Optional[datetime] = None, interface_name: Optional[str] = None,
                      db_lock: threading.Lock = None) -> List[Tuple[datetime, float, float]]:
    """
    Retrieves speed history with an optimized query strategy based on the time range.

    - For short time ranges (< 2 days), it queries the high-resolution 'speed_history' table.
    - For long time ranges (>= 2 days), it combines data from the 'speed_history_aggregated'
      table for the older period with 'speed_history' for the recent period.

    Args:
        db_path: Path to the SQLite database file.
        start_time: The start of the time window. If None, fetches all history.
        end_time: The end of the time window. Defaults to now.
        interface_name: The specific interface to query for. If None, aggregates all.
        db_lock: Threading lock for database access.

    Returns:
        A list of tuples, each containing (timestamp, upload_bytes_sec, download_bytes_sec).
    """
    from datetime import timedelta

    logger = logging.getLogger("NetSpeedTray.db_utils")
    if end_time is None:
        end_time = datetime.now()

    # USE CONSTANT: Define the threshold where we switch from raw to aggregated data.
    aggregation_cutoff_time = end_time - timedelta(days=constants.data.AGGREGATION_CUTOFF_DAYS)

    use_only_raw = start_time and start_time > aggregation_cutoff_time
    params = []
    queries = []

    if not start_time or end_time > aggregation_cutoff_time:
        raw_start_time = max(start_time, aggregation_cutoff_time) if start_time else aggregation_cutoff_time
        
        raw_query_parts = [
            f"SELECT timestamp, upload, download FROM {constants.data.SPEED_TABLE}",
            "WHERE timestamp >= ? AND timestamp <= ?"
        ]
        params.extend([raw_start_time.timestamp(), end_time.timestamp()])

        if interface_name:
            raw_query_parts.append("AND interface = ?")
            params.append(interface_name)
        
        queries.append(" ".join(raw_query_parts))

    if not use_only_raw and (not start_time or start_time < aggregation_cutoff_time):
        agg_end_time = aggregation_cutoff_time
        
        agg_query_parts = [
            f"SELECT period_end as timestamp, avg_upload as upload, avg_download as download FROM {constants.data.AGGREGATED_TABLE}",
            "WHERE period_end <= ?"
        ]
        params.append(agg_end_time.timestamp())

        if start_time:
            agg_query_parts.append("AND period_end >= ?")
            params.append(start_time.timestamp())
        
        if interface_name:
            agg_query_parts.append("AND interface = ?")
            params.append(interface_name)
            
        queries.append(" ".join(agg_query_parts))

    if not queries:
        return []

    final_query = " UNION ALL ".join(queries) + " ORDER BY timestamp ASC"
    results = []

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            logger.debug("Executing optimized speed history query with %d params", len(params))
            cursor.execute(final_query, params)
            results = [
                (datetime.fromtimestamp(row[0]), float(row[1]), float(row[2]))
                for row in cursor.fetchall()
            ]
            logger.debug("Retrieved %d records with optimized query.", len(results))
    except sqlite3.Error as e:
        logger.error("Failed to retrieve optimized speed history: %s", e, exc_info=True)

    return results


def get_total_bandwidth_for_period(db_path: Union[str, Path], start_time: Optional[datetime],
                                   end_time: datetime, interface_name: Optional[str] = None) -> Tuple[float, float]:
    """
    Calculates total bandwidth by running SUM queries across all relevant tables.
    Uses a separate, read-only connection for thread safety.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    total_up, total_down = 0.0, 0.0
    
    _start_ts = int(start_time.timestamp()) if start_time else 0
    _end_ts = int(end_time.timestamp()) if end_time else int(datetime.now().timestamp())

    try:
        # Use a read-only connection to prevent locking issues.
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        cursor = conn.cursor()

        table_map = {
            "speed_history_raw": ("SUM(upload_bytes_sec)", "SUM(download_bytes_sec)"),
            "speed_history_minute": ("SUM(upload_avg * 60)", "SUM(download_avg * 60)"),
            "speed_history_hour": ("SUM(upload_avg * 3600)", "SUM(download_avg * 3600)")
        }

        for table, (up_sum_expr, down_sum_expr) in table_map.items():
            query = f"SELECT {up_sum_expr}, {down_sum_expr} FROM {table} WHERE timestamp BETWEEN ? AND ?"
            params: List[Any] = [_start_ts, _end_ts]

            if interface_name and interface_name != "All":
                query += " AND interface_name = ?"
                params.append(interface_name)

            cursor.execute(query, tuple(params))
            result = cursor.fetchone()
            
            if result and result[0] is not None: total_up += result[0]
            if result and result[1] is not None: total_down += result[1]
        
        conn.close()
        return total_up, total_down

    except sqlite3.Error as e:
        logger.error(f"Database error in get_total_bandwidth_for_period: {e}")
        return 0.0, 0.0


def get_max_speeds(db_path: Union[str, Path], start_time: Optional[int] = None, interfaces: Optional[List[str]] = None,
                   db_lock: threading.Lock = None) -> Tuple[float, float]:
    """
    Retrieve maximum upload and download speeds from speed_history and speed_history_aggregated.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Fetching max speeds with start_time=%s, interfaces=%s", start_time, interfaces)

    query_parts = [
        "SELECT MAX(max_upload), MAX(max_download) FROM (",
        f"SELECT MAX(upload) as max_upload, MAX(download) as max_download FROM {constants.data.SPEED_TABLE} WHERE deleted_at IS NULL",
        f"UNION ALL SELECT MAX(avg_upload), MAX(avg_download) FROM {constants.data.AGGREGATED_TABLE} WHERE deleted_at IS NULL",
        ")"
    ]
    params = []
    
    # --- Build WHERE clause with proper parameterization ---
    where_clauses = []
    if start_time:
        where_clauses.append("timestamp >= ?")
        params.append(start_time)
    if interfaces:
        placeholders = ", ".join("?" for _ in interfaces)
        where_clauses.append(f"interface IN ({placeholders})")
        params.extend(interfaces)

    if where_clauses:
        # This is a bit complex, but it correctly applies the WHERE to both subqueries
        where_str = " AND ".join(where_clauses)
        query_parts[1] += f" AND {where_str.replace('timestamp', 'timestamp')}"
        query_parts[2] += f" AND {where_str.replace('timestamp', 'period_end')}"
        # The params list is duplicated because the conditions apply to both sides of the UNION
        params.extend(params)

    query = " ".join(query_parts)

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            if row and row[0] is not None:
                return float(row[0]), float(row[1])
    except sqlite3.Error as e:
        logger.error("Failed to retrieve max speeds: %s", e)
        raise

    return 0.0, 0.0


def get_max_speeds(db_path: Union[str, Path], start_time: Optional[int] = None, interfaces: Optional[List[str]] = None,
                   db_lock: threading.Lock = None) -> Tuple[float, float]:
    """
    Retrieve maximum upload and download speeds using an efficient, aggregated query.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Fetching max speeds with start_time=%s, interfaces=%s", start_time, interfaces)

    query_parts = [
        "SELECT MAX(max_upload), MAX(max_download) FROM (",
        f"SELECT MAX(upload) as max_upload, MAX(download) as max_download FROM {constants.data.SPEED_TABLE} WHERE deleted_at IS NULL",
        f"UNION ALL SELECT MAX(avg_upload), MAX(avg_download) FROM {constants.data.AGGREGATED_TABLE} WHERE deleted_at IS NULL",
        ")"
    ]
    params = []
    
    # --- Build WHERE clause with proper parameterization ---
    where_clauses = []
    if start_time:
        where_clauses.append("timestamp >= ?")
        params.append(start_time)
    if interfaces:
        placeholders = ", ".join("?" for _ in interfaces)
        where_clauses.append(f"interface IN ({placeholders})")
        params.extend(interfaces)

    if where_clauses:
        # This is a bit complex, but it correctly applies the WHERE to both subqueries
        where_str = " AND ".join(where_clauses)
        query_parts[1] += f" AND {where_str.replace('timestamp', 'timestamp')}"
        query_parts[2] += f" AND {where_str.replace('timestamp', 'period_end')}"
        # The params list is duplicated because the conditions apply to both sides of the UNION
        params.extend(params)

    query = " ".join(query_parts)

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            if row and row[0] is not None:
                return float(row[0]), float(row[1])
    except sqlite3.Error as e:
        logger.error("Failed to retrieve max speeds: %s", e)
        raise

    return 0.0, 0.0


def get_bandwidth_usage(db_path: Union[str, Path], start_time: Optional[int] = None, interfaces: Optional[List[str]] = None,
                        db_lock: threading.Lock = None) -> Tuple[int, int]:
    """
    Retrieve total bandwidth usage (bytes sent/received) using an efficient, aggregated query.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Fetching bandwidth usage with start_time=%s, interfaces=%s", start_time, interfaces)
    
    query_parts = [f"SELECT SUM(bytes_sent), SUM(bytes_recv) FROM {constants.data.BANDWIDTH_TABLE} WHERE deleted_at IS NULL"]
    params = []

    if start_time:
        query_parts.append("AND timestamp >= ?")
        params.append(start_time)
    if interfaces:
        placeholders = ", ".join("?" for _ in interfaces)
        query_parts.append(f"AND interface IN ({placeholders})")
        params.extend(interfaces)
    
    query = " ".join(query_parts)

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            if row and row[0] is not None:
                return int(row[0]), int(row[1])
    except sqlite3.Error as e:
        logger.error("Failed to retrieve bandwidth usage: %s", e)
        raise
        
    return 0, 0


def get_app_bandwidth_usage(db_path: Union[str, Path], start_time: Optional[datetime] = None,
                            interfaces: Optional[List[str]] = None, app_names: Optional[List[str]] = None,
                            db_lock: threading.Lock = None) -> List[AppBandwidthData]:
    """
    Retrieve per-app bandwidth usage from the app_bandwidth table.

    Args:
        db_path: Path to the SQLite database file.
        start_time: Optional start time to filter records (inclusive).
        interfaces: Optional list of interfaces to filter by.
        app_names: Optional list of app names to filter by.
        db_lock: Threading lock for database access.

    Returns:
        List of AppBandwidthData namedtuples (app_name, timestamp, bytes_sent, bytes_recv, interface).
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Fetching app bandwidth usage with start_time=%s, interfaces=%s, app_names=%s",
                 start_time, interfaces, app_names)

    results: List[AppBandwidthData] = []
    start_timestamp = int(start_time.timestamp()) if start_time else None
    interface_filter = " AND interface IN ({})".format(
        ",".join([f"'{i}'" for i in interfaces]) if interfaces else "'*'"
    ) if interfaces else ""
    app_filter = " AND app_name IN ({})".format(
        ",".join([f"'{a}'" for a in app_names]) if app_names else "'*'"
    ) if app_names else ""

    query = f"""
        SELECT app_name, timestamp, bytes_sent, bytes_recv, interface
        FROM {constants.data.APP_BANDWIDTH_TABLE}
        WHERE deleted_at IS NULL
        {interface_filter}
        {app_filter}
        {"AND timestamp >= ?" if start_timestamp else ""}
        ORDER BY timestamp DESC
    """
    params = [start_timestamp] if start_timestamp else []

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            for row in rows:
                try:
                    bytes_sent = int(row[2]) if row[2] is not None else 0
                    bytes_recv = int(row[3]) if row[3] is not None else 0
                    results.append(AppBandwidthData(
                        app_name=row[0],
                        timestamp=datetime.fromtimestamp(row[1]),
                        bytes_sent=bytes_sent,
                        bytes_recv=bytes_recv,
                        interface=row[4]
                    ))
                except (ValueError, TypeError) as e:
                    logger.error("Invalid data in app bandwidth query: bytes_sent=%s, bytes_recv=%s, error=%s", row[2], row[3], e)
                    continue
            logger.debug("Retrieved %d app bandwidth records", len(results))
    except sqlite3.Error as e:
        logger.error("Failed to retrieve app bandwidth usage: %s", e)
        raise

    return results


def get_earliest_timestamp(db_path: Union[str, Path], db_lock: threading.Lock) -> Optional[int]:
    """
    Finds the earliest timestamp available in the speed history tables.

    Checks both the raw and aggregated tables and returns the absolute earliest
    timestamp found.

    Args:
        db_path: Path to the SQLite database file.
        db_lock: Threading lock for database access.

    Returns:
        The earliest Unix timestamp as an integer, or None if the DB is empty.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    earliest_ts = None

    try:
        with db_lock, sqlite3.connect(db_path, timeout=5) as conn:
            cursor = conn.cursor()
            
            # Query for the minimum timestamp in both tables
            query = f"""
                SELECT MIN(ts) FROM (
                    SELECT MIN(timestamp) as ts FROM {constants.data.SPEED_TABLE} WHERE deleted_at IS NULL
                    UNION ALL
                    SELECT MIN(period_start) as ts FROM {constants.data.AGGREGATED_TABLE} WHERE deleted_at IS NULL
                ) WHERE ts IS NOT NULL
            """
            cursor.execute(query)
            result = cursor.fetchone()

            if result and result[0] is not None:
                earliest_ts = int(result[0])
                logger.debug("Earliest timestamp found in DB: %s", datetime.fromtimestamp(earliest_ts))

    except sqlite3.Error as e:
        logger.error("Failed to retrieve the earliest timestamp from database: %s", e)
        # We can continue, will just return None

    return earliest_ts


def aggregate_speed_history(db_path: Union[str, Path], cutoff_timestamp: int, db_lock: threading.Lock) -> int:
    """
    Aggregate speed_history records older than cutoff_timestamp into speed_history_aggregated.

    Aggregates per-second data into per-minute averages, then deletes the original records.
    Skips deletion if no records were aggregated to avoid unnecessary operations.

    Args:
        db_path: Path to the SQLite database file.
        cutoff_timestamp: Timestamp before which to aggregate records.
        db_lock: Threading lock for database access.

    Returns:
        Number of raw records that were successfully aggregated and deleted.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Aggregating speed history before timestamp %d", cutoff_timestamp)

    aggregated_count = 0

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()

            # Step 1: Aggregate per-second data into per-minute periods and insert into the aggregated table.
            cursor.execute(f"""
                INSERT OR IGNORE INTO {constants.data.AGGREGATED_TABLE} (period_start, period_end, avg_upload, avg_download, interface, deleted_at)
                SELECT
                    (timestamp / 60) * 60 AS period_start,
                    ((timestamp / 60) + 1) * 60 AS period_end,
                    AVG(upload) AS avg_upload,
                    AVG(download) AS avg_download,
                    interface,
                    NULL
                FROM {constants.data.SPEED_TABLE}
                WHERE timestamp < ? AND deleted_at IS NULL
                GROUP BY (timestamp / 60), interface
            """, (cutoff_timestamp,))
            conn.commit()

            # Step 2: Delete the raw records that were just aggregated.
            # We check rowcount here to see how many records will be deleted.
            cursor.execute(f"""
                DELETE FROM {constants.data.SPEED_TABLE}
                WHERE timestamp < ? AND deleted_at IS NULL
            """, (cutoff_timestamp,))
            aggregated_count = cursor.rowcount
            conn.commit()
            
            if aggregated_count > 0:
                logger.debug("Aggregated and deleted %d raw records", aggregated_count)
            else:
                logger.debug("No raw records to aggregate")

    except sqlite3.Error as e:
        logger.error("Failed to aggregate speed history: %s", e)
        raise

    return aggregated_count


def vacuum_database(db_path: Union[str, Path], db_lock: threading.Lock) -> float:
    """
    Execute a VACUUM operation on the database to reclaim disk space.

    Measures the database size before and after the operation to log the space reclaimed.

    Args:
        db_path: Path to the SQLite database file.
        db_lock: Threading lock for database access.

    Returns:
        Space reclaimed in megabytes (MB).
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Starting VACUUM operation on database at %s", db_path)

    try:
        # Measure size before VACUUM using the constant for conversion
        size_before = os.path.getsize(db_path) / constants.logs.BYTES_TO_MEGABYTES

        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            # Setting isolation_level to None commits the VACUUM immediately.
            conn.isolation_level = None
            cursor = conn.cursor()
            cursor.execute("VACUUM")
            
        # Measure size after VACUUM
        size_after = os.path.getsize(db_path) / constants.logs.BYTES_TO_MEGABYTES
        space_reclaimed = size_before - size_after
        logger.info("VACUUM completed: reclaimed %.2f MB (before: %.2f MB, after: %.2f MB)",
                    space_reclaimed, size_before, size_after)
        return space_reclaimed

    except sqlite3.Error as e:
        logger.error("Failed to execute VACUUM: %s", e)
        raise
    except OSError as e:
        logger.error("Failed to measure database size: %s", e)
        raise