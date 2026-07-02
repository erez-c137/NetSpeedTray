"""
Read helper for the speed-history database.

Only ``get_speed_history`` remains here: it is the tier-aware fallback query used
by ``WidgetState`` (``core/widget_state.py``). Everything else this module used to
contain (init / persist / bandwidth / app-bandwidth / aggregate / vacuum helpers)
targeted the pre-2.0 **legacy** schema - single ``speed_history`` /
``bandwidth_history`` / ``app_bandwidth`` tables - that ``core/database.py``
replaced with the 3-tier raw/minute/hour schema. Those functions were unreachable
dead code and were removed; two of them also carried defects worth not keeping
around (an f-string-built WHERE clause that interpolated user-supplied app /
interface names, and a ``params.extend(params)`` self-duplication). All live
writes and reads now go through ``core/database.py`` and ``core/widget_state.py``.

Note: ``get_speed_history`` builds its query with f-strings only to insert the
*constant* table names from ``constants.data`` (SQLite cannot bind a table name);
the time-range and interface values are always passed as bound ``?`` parameters.
"""

import logging
import sqlite3
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Union

from netspeedtray import constants


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
    logger = logging.getLogger("NetSpeedTray.db_utils")
    if end_time is None:
        end_time = datetime.now()

    # USE CONSTANT: Define the threshold where we switch from raw to aggregated data.
    aggregation_cutoff_time = end_time - timedelta(days=constants.data.AGGREGATION_CUTOFF_DAYS)

    # Build queries for modern schema: raw, minute, hour
    queries: List[str] = []
    params: List[Union[float, str]] = []

    # Raw: recent high-resolution data
    try:
        if not start_time or end_time > aggregation_cutoff_time:
            raw_start_ts = int(max(start_time, aggregation_cutoff_time).timestamp()) if start_time else int(aggregation_cutoff_time.timestamp())
            raw_end_ts = int(end_time.timestamp())
            raw_q = f"SELECT timestamp, upload_bytes_sec, download_bytes_sec FROM {constants.data.SPEED_TABLE_RAW} WHERE timestamp BETWEEN ? AND ?"
            raw_params = [raw_start_ts, raw_end_ts]
            if interface_name:
                raw_q += " AND interface_name = ?"
                raw_params.append(interface_name)
            queries.append(raw_q)
            params.extend(raw_params)

        # Aggregated minute/hour: older ranges
        if not start_time or start_time < aggregation_cutoff_time:
            agg_start_ts = int(start_time.timestamp()) if start_time else 0
            agg_end_ts = int(min(end_time, aggregation_cutoff_time).timestamp())
            # Use minute table for aggregated portion
            agg_q = f"SELECT timestamp, upload_avg as upload, download_avg as download FROM {constants.data.SPEED_TABLE_MINUTE} WHERE timestamp BETWEEN ? AND ?"
            agg_params = [agg_start_ts, agg_end_ts]
            if interface_name:
                agg_q += " AND interface_name = ?"
                agg_params.append(interface_name)
            queries.append(agg_q)
            params.extend(agg_params)

        if not queries:
            return []

        final_query = " UNION ALL ".join(queries) + " ORDER BY timestamp ASC"
        results: List[Tuple[datetime, float, float]] = []

        with (db_lock or nullcontext()), sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.cursor()
            logger.debug("Executing modern optimized speed history query with %d params", len(params))
            cursor.execute(final_query, tuple(params))
            rows = cursor.fetchall()
            for row in rows:
                results.append((datetime.fromtimestamp(int(row[0])), float(row[1]), float(row[2])))
            logger.debug("Retrieved %d records with modern optimized query.", len(results))
        return results
    except sqlite3.Error as e:
        logger.error("Failed to retrieve optimized speed history: %s", e, exc_info=True)
        return []
