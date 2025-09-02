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
from typing import Dict, List, Optional, Tuple, Union

# --- Local Imports ---
from .config import ConfigManager

# --- Named Tuples for Data ---
SpeedData = namedtuple("SpeedData", ["timestamp", "upload", "download", "interface"])
AppBandwidthData = namedtuple("AppBandwidthData", ["app_name", "timestamp", "bytes_sent", "bytes_recv", "interface"])

# --- Constants ---
SPEED_TABLE = "speed_history"
AGGREGATED_TABLE = "speed_history_aggregated"
BANDWIDTH_TABLE = "bandwidth_history"
APP_BANDWIDTH_TABLE = "app_bandwidth"


def init_database(db_path: Union[str, Path]) -> None:
    """
    Initialize the SQLite database with required tables and indices.

    Creates the following tables if they don't exist:
    - speed_history: Stores per-second speed data.
    - speed_history_aggregated: Stores aggregated per-minute speed data.
    - bandwidth_history: Stores bandwidth data.
    - app_bandwidth: Stores per-app bandwidth data.

    Args:
        db_path: Path to the SQLite database file.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Initializing database at %s", db_path)

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()

            # Speed History Table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {SPEED_TABLE} (
                    timestamp INTEGER PRIMARY KEY,
                    upload REAL NOT NULL,
                    download REAL NOT NULL,
                    interface TEXT NOT NULL,
                    deleted_at INTEGER
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{SPEED_TABLE}_timestamp ON {SPEED_TABLE}(timestamp)")

            # Aggregated Speed History Table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {AGGREGATED_TABLE} (
                    period_start INTEGER NOT NULL,
                    period_end INTEGER NOT NULL,
                    avg_upload REAL NOT NULL,
                    avg_download REAL NOT NULL,
                    interface TEXT NOT NULL,
                    deleted_at INTEGER,
                    PRIMARY KEY (period_start, interface)
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{AGGREGATED_TABLE}_period_end ON {AGGREGATED_TABLE}(period_end)")

            # Bandwidth History Table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {BANDWIDTH_TABLE} (
                    timestamp INTEGER PRIMARY KEY,
                    bytes_sent INTEGER NOT NULL,
                    bytes_recv INTEGER NOT NULL,
                    interface TEXT NOT NULL,
                    deleted_at INTEGER
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{BANDWIDTH_TABLE}_timestamp ON {BANDWIDTH_TABLE}(timestamp)")

            # App Bandwidth Table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {APP_BANDWIDTH_TABLE} (
                    timestamp INTEGER NOT NULL,
                    app_name TEXT NOT NULL,
                    bytes_sent INTEGER NOT NULL,
                    bytes_recv INTEGER NOT NULL,
                    interface TEXT NOT NULL,
                    deleted_at INTEGER
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{APP_BANDWIDTH_TABLE}_timestamp_app ON {APP_BANDWIDTH_TABLE}(timestamp, app_name)")

            conn.commit()
            logger.debug("Database initialized successfully")
    except sqlite3.Error as e:
        logger.error("Failed to initialize database: %s", e)
        raise


def persist_speed_batch(db_path: Union[str, Path], batch: List[Tuple[int, float, float, str]], db_lock: threading.Lock) -> None:
    """
    Persist a batch of speed data to the speed_history table.

    Args:
        db_path: Path to the SQLite database file.
        batch: List of tuples (timestamp, upload, download, interface).
        db_lock: Threading lock for database access.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Persisting speed batch of size %d", len(batch))

    if not batch:
        return

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                f"INSERT OR REPLACE INTO {SPEED_TABLE} (timestamp, upload, download, interface, deleted_at) VALUES (?, ?, ?, ?, NULL)",
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

    Args:
        db_path: Path to the SQLite database file.
        batch: List of tuples (timestamp, bytes_sent, bytes_recv, interface).
        db_lock: Threading lock for database access.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Persisting bandwidth batch of size %d", len(batch))

    if not batch:
        return

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                f"INSERT OR REPLACE INTO {BANDWIDTH_TABLE} (timestamp, bytes_sent, bytes_recv, interface, deleted_at) VALUES (?, ?, ?, ?, NULL)",
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

    Args:
        db_path: Path to the SQLite database file.
        batch: List of tuples (timestamp, app_name, bytes_sent, bytes_recv, interface).
        db_lock: Threading lock for database access.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Persisting app bandwidth batch of size %d", len(batch))

    if not batch:
        return

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                f"INSERT INTO {APP_BANDWIDTH_TABLE} (timestamp, app_name, bytes_sent, bytes_recv, interface, deleted_at) VALUES (?, ?, ?, ?, ?, NULL)",
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

    # Define the threshold where we switch from raw to aggregated data (e.g., 2 days ago)
    aggregation_cutoff_time = end_time - timedelta(days=2)

    # If the user is asking for a period that is entirely within the raw data timeframe,
    # or if no start time is given (implying 'All', which needs both), we decide the strategy.
    use_only_raw = start_time and start_time > aggregation_cutoff_time

    params = []
    queries = []

    # --- Query for the recent, high-resolution data ---
    # This part is almost always run, unless the user requests a period ending >2 days ago.
    if not start_time or end_time > aggregation_cutoff_time:
        raw_start_time = max(start_time, aggregation_cutoff_time) if start_time else aggregation_cutoff_time
        
        raw_query_parts = [
            f"SELECT timestamp, upload, download FROM {SPEED_TABLE}",
            "WHERE timestamp >= ?",
            "AND timestamp <= ?"
        ]
        params.extend([raw_start_time.timestamp(), end_time.timestamp()])

        if interface_name:
            raw_query_parts.append("AND interface = ?")
            params.append(interface_name)
        
        queries.append(" ".join(raw_query_parts))


    # --- Query for the older, aggregated data (only for long time ranges) ---
    if not use_only_raw and (not start_time or start_time < aggregation_cutoff_time):
        agg_end_time = aggregation_cutoff_time
        
        agg_query_parts = [
            f"SELECT period_end as timestamp, avg_upload as upload, avg_download as download FROM {AGGREGATED_TABLE}",
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

    final_query = " UNION ALL ".join(queries)
    final_query += " ORDER BY timestamp ASC" # Order ascending for correct plotting

    results = []
    if not final_query:
        return results

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            logger.debug("Executing optimized speed history query with %d params", len(params))
            cursor.execute(final_query, params)
            # The direct conversion here is faster than creating namedtuples in a loop
            results = [
                (datetime.fromtimestamp(row[0]), float(row[1]), float(row[2]))
                for row in cursor.fetchall()
            ]
            logger.debug("Retrieved %d records with optimized query.", len(results))
    except sqlite3.Error as e:
        logger.error("Failed to retrieve optimized speed history: %s", e, exc_info=True)

    return results


def get_max_speeds(db_path: Union[str, Path], start_time: Optional[int] = None, interfaces: Optional[List[str]] = None,
                   db_lock: threading.Lock = None) -> Tuple[float, float]:
    """
    Retrieve maximum upload and download speeds from speed_history and speed_history_aggregated.

    Args:
        db_path: Path to the SQLite database file.
        start_time: Optional start timestamp to filter records (inclusive).
        interfaces: Optional list of interfaces to filter by.
        db_lock: Threading lock for database access.

    Returns:
        Tuple of (max_upload, max_download) in Kbps as floats.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Fetching max speeds with start_time=%s, interfaces=%s", start_time, interfaces)

    max_upload, max_download = 0.0, 0.0
    interface_filter = " AND interface IN ({})".format(
        ",".join([f"'{i}'" for i in interfaces]) if interfaces else "'*'"
    ) if interfaces else ""

    query = f"""
        SELECT MAX(upload), MAX(download)
        FROM {SPEED_TABLE}
        WHERE deleted_at IS NULL
        {interface_filter}
        {"AND timestamp >= ?" if start_time else ""}
        UNION ALL
        SELECT MAX(avg_upload), MAX(avg_download)
        FROM {AGGREGATED_TABLE}
        WHERE deleted_at IS NULL
        {interface_filter}
        {"AND period_end >= ?" if start_time else ""}
    """
    params = [start_time, start_time] if start_time else []

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            for row in rows:
                try:
                    upload = float(row[0]) if row[0] is not None else 0.0
                    download = float(row[1]) if row[1] is not None else 0.0
                    max_upload = max(max_upload, upload)
                    max_download = max(max_download, download)
                except (ValueError, TypeError) as e:
                    logger.error("Invalid data in max speeds query: upload=%s, download=%s, error=%s", row[0], row[1], e)
                    continue
            logger.debug("Max speeds: upload=%.2f, download=%.2f", max_upload, max_download)
    except sqlite3.Error as e:
        logger.error("Failed to retrieve max speeds: %s", e)
        raise

    return max_upload, max_download


def get_bandwidth_usage(db_path: Union[str, Path], start_time: Optional[int] = None, interfaces: Optional[List[str]] = None,
                        db_lock: threading.Lock = None) -> Tuple[int, int]:
    """
    Retrieve total bandwidth usage (bytes sent and received) from bandwidth_history.

    Args:
        db_path: Path to the SQLite database file.
        start_time: Optional start timestamp to filter records (inclusive).
        interfaces: Optional list of interfaces to filter by.
        db_lock: Threading lock for database access.

    Returns:
        Tuple of (total_bytes_sent, total_bytes_recv) as integers.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Fetching bandwidth usage with start_time=%s, interfaces=%s", start_time, interfaces)

    total_sent, total_recv = 0, 0
    interface_filter = " AND interface IN ({})".format(
        ",".join([f"'{i}'" for i in interfaces]) if interfaces else "'*'"
    ) if interfaces else ""

    query = f"""
        SELECT bytes_sent, bytes_recv
        FROM {BANDWIDTH_TABLE}
        WHERE deleted_at IS NULL
        {interface_filter}
        {"AND timestamp >= ?" if start_time else ""}
    """
    params = [start_time] if start_time else []

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            for row in rows:
                try:
                    sent = int(row[0]) if row[0] is not None else 0
                    recv = int(row[1]) if row[1] is not None else 0
                    total_sent += sent
                    total_recv += recv
                except (ValueError, TypeError) as e:
                    logger.error("Invalid data in bandwidth usage query: bytes_sent=%s, bytes_recv=%s, error=%s", row[0], row[1], e)
                    continue
            logger.debug("Total bandwidth: sent=%d, recv=%d", total_sent, total_recv)
    except sqlite3.Error as e:
        logger.error("Failed to retrieve bandwidth usage: %s", e)
        raise

    return total_sent, total_recv


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
        FROM {APP_BANDWIDTH_TABLE}
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
                    SELECT MIN(timestamp) as ts FROM {SPEED_TABLE} WHERE deleted_at IS NULL
                    UNION ALL
                    SELECT MIN(period_start) as ts FROM {AGGREGATED_TABLE} WHERE deleted_at IS NULL
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
        Number of records aggregated.
    """
    logger = logging.getLogger("NetSpeedTray.db_utils")
    logger.debug("Aggregating speed history before timestamp %d", cutoff_timestamp)

    aggregated_count = 0

    try:
        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()

            # Aggregate per-second data into per-minute periods
            cursor.execute(f"""
                INSERT OR IGNORE INTO {AGGREGATED_TABLE} (period_start, period_end, avg_upload, avg_download, interface, deleted_at)
                SELECT
                    (timestamp / 60) * 60 AS period_start,
                    ((timestamp / 60) + 1) * 60 AS period_end,
                    AVG(upload) AS avg_upload,
                    AVG(download) AS avg_download,
                    interface,
                    NULL
                FROM {SPEED_TABLE}
                WHERE timestamp < ? AND deleted_at IS NULL
                GROUP BY (timestamp / 60), interface
            """, (cutoff_timestamp,))
            aggregated_count = cursor.rowcount
            conn.commit()

            # Delete aggregated records from speed_history if any were aggregated
            if aggregated_count > 0:
                cursor.execute(f"""
                    DELETE FROM {SPEED_TABLE}
                    WHERE timestamp < ? AND deleted_at IS NULL
                """, (cutoff_timestamp,))
                conn.commit()
                logger.debug("Aggregated and deleted %d records", aggregated_count)
            else:
                logger.debug("No records to aggregate")

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
        # Measure size before VACUUM
        size_before = os.path.getsize(db_path) / (1024 * 1024)  # Size in MB

        with db_lock, sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute("VACUUM")
            conn.commit()

        # Measure size after VACUUM
        size_after = os.path.getsize(db_path) / (1024 * 1024)  # Size in MB
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