"""
Unit tests for the WidgetState and its associated DatabaseWorker.

These tests verify the correctness of the data layer, including database schema
creation, data ingestion, aggregation, pruning, and maintenance logic.
"""

import pytest
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta
from typing import Iterator

from PyQt6.QtCore import QThread

from netspeedtray.core.widget_state import WidgetState
from netspeedtray import constants


@pytest.fixture
def mock_config() -> dict:
    """Provides a default configuration dictionary for tests."""
    return constants.config.defaults.DEFAULT_CONFIG.copy()


@pytest.fixture
def managed_widget_state(tmp_path: Path, mock_config: dict) -> Iterator[tuple[WidgetState, Path]]:
    """
    Provides a WidgetState instance with a temporary DB for SYNCHRONOUS testing.
    It PREVENTS the DatabaseWorker QThread from starting and initializes the DB directly.
    """
    db_path = tmp_path / "speed_history.db"
    
    with patch.object(QThread, 'start', lambda self: None):
        # Patch the helper function to ensure WidgetState uses our temporary path.
        with patch('netspeedtray.core.widget_state.get_app_data_path', return_value=tmp_path):
            state = WidgetState(mock_config)
        
        # Get the worker instance *after* WidgetState has created it.
        worker = state.db_worker
        
        # Manually ensure its db_path is set to our temporary path. This is crucial.
        worker.db_path = db_path
        
        # Now, call the setup methods synchronously.
        worker._initialize_connection() 
        worker._check_and_create_schema()
        
        # The connection IS LEFT OPEN for the duration of the test.
        # This is necessary for the test functions to write data to it.
        
        yield state, db_path # Hand over the ready-to-use state object
        
        # Teardown: close the connection we manually opened.
        worker._close_connection()
        state.cleanup()


def test_database_initialization_creates_correct_schema(managed_widget_state):
    """
    Tests if the DatabaseWorker correctly creates all required tables and metadata
    on its first run with a non-existent database file.
    """
    # ARRANGE
    state, db_path = managed_widget_state
    
    # ACT
    # The fixture already initializes the worker. We just need to give it a moment
    # to create the database file and schema. 200ms is more than enough time.
    time.sleep(0.2) 

    # ASSERT
    assert db_path.exists(), "Database file was not created."

    # Connect directly to the database to inspect its schema
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

"""
Unit tests for the WidgetState and its associated DatabaseWorker.

These tests verify the correctness of the data layer, including database schema
creation, data ingestion, aggregation, pruning, and maintenance logic.
"""

import pytest
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta
from typing import Iterator

from PyQt6.QtCore import QThread

from netspeedtray.core.widget_state import WidgetState
from netspeedtray import constants
from netspeedtray.core.database import DatabaseWorker


@pytest.fixture
def mock_config() -> dict:
    """Provides a default configuration dictionary for tests."""
    return constants.config.defaults.DEFAULT_CONFIG.copy()


@pytest.fixture
def managed_widget_state(tmp_path: Path, mock_config: dict) -> Iterator[tuple[WidgetState, Path]]:
    """
    Provides a WidgetState instance with a temporary DB for SYNCHRONOUS testing.
    It PREVENTS the DatabaseWorker QThread from starting and initializes the DB directly.
    """
    db_path = tmp_path / "speed_history.db"
    
    with patch.object(QThread, 'start', lambda self: None):
        # Patch the helper function to ensure WidgetState uses our temporary path.
        with patch('netspeedtray.core.widget_state.get_app_data_path', return_value=tmp_path):
            state = WidgetState(mock_config)
        
        # Get the worker instance *after* WidgetState has created it.
        worker = state.db_worker
        
        # Manually ensure its db_path is set to our temporary path. This is crucial.
        worker.db_path = db_path
        
        # Now, call the setup methods synchronously.
        worker._initialize_connection() 
        worker._check_and_create_schema()
        
        # The connection IS LEFT OPEN for the duration of the test.
        # This is necessary for the test functions to write data to it.
        
        yield state, db_path # Hand over the ready-to-use state object
        
        # Teardown: close the connection we manually opened.
        worker._close_connection()
        state.cleanup()


def test_database_initialization_creates_correct_schema(managed_widget_state):
    """
    Tests if the DatabaseWorker correctly creates all required tables and metadata
    on its first run with a non-existent database file.
    """
    # ARRANGE
    state, db_path = managed_widget_state
    
    # ACT
    # The fixture already initializes the worker. We just need to give it a moment
    # to create the database file and schema. 200ms is more than enough time.
    time.sleep(0.2) 

    # ASSERT
    assert db_path.exists(), "Database file was not created."

    # Connect directly to the database to inspect its schema
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Check if all tables were created
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    expected_tables = {'metadata', 'speed_history_raw', 'speed_history_minute', 'speed_history_hour', 'bandwidth_history'}
    assert tables == expected_tables, "Incorrect set of tables were created."

    # 2. Check the database version in the metadata table
    cursor.execute("SELECT value FROM metadata WHERE key = 'db_version'")
    # The DatabaseWorker._DB_VERSION is the source of truth.
    assert int(cursor.fetchone()[0]) == DatabaseWorker._DB_VERSION, "Database version is incorrect."


    # 3. Check the schema of the 'speed_history_raw' table
    cursor.execute("PRAGMA table_info('speed_history_raw');")
    columns_raw = {row[1]: (row[2], row[5]) for row in cursor.fetchall()} # name -> (type, pk_index)
    expected_raw = {
        'timestamp': ('INTEGER', 1),
        'interface_name': ('TEXT', 2),
        'upload_bytes_sec': ('REAL', 0),
        'download_bytes_sec': ('REAL', 0),
    }
    assert columns_raw == expected_raw, "Schema for 'speed_history_raw' is incorrect."

    # 4. Check the schema of the 'speed_history_minute' table
    cursor.execute("PRAGMA table_info('speed_history_minute');")
    columns_minute = {row[1]: (row[2], row[5]) for row in cursor.fetchall()}
    expected_minute_hour = {
        'timestamp': ('INTEGER', 1),
        'interface_name': ('TEXT', 2),
        'upload_avg': ('REAL', 0),
        'download_avg': ('REAL', 0),
        'upload_max': ('REAL', 0),
        'download_max': ('REAL', 0),
        'sample_count': ('INTEGER', 0),
    }
    assert columns_minute == expected_minute_hour, "Schema for 'speed_history_minute' is incorrect."
    
    # 5. Check the schema of the 'speed_history_hour' table (should be same as minute)
    cursor.execute("PRAGMA table_info('speed_history_hour');")
    columns_hour = {row[1]: (row[2], row[5]) for row in cursor.fetchall()}
    assert columns_hour == expected_minute_hour, "Schema for 'speed_history_hour' is incorrect."

    # 6. Check that indexes were created
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index';")
    indexes = {row[0] for row in cursor.fetchall()}
    expected_indexes = {'idx_raw_timestamp', 'idx_minute_covering', 'idx_hour_covering'}
    assert expected_indexes.issubset(indexes), f"Required indexes were not created. Found: {indexes}"

    conn.close()


def test_add_speed_data_queues_data_for_worker(managed_widget_state):
    """
    Tests that add_speed_data correctly processes per-interface data,
    adds it to the internal batch for the DB worker, and updates the
    in-memory deque for the mini-graph. It also tests that negligible
    speeds are ignored.
    """
    # ARRANGE
    state, _ = managed_widget_state
    
    # Define sample data from the controller
    # Speeds are in Bytes per second
    test_speed_data = {
        "Wi-Fi": (1250000.0, 2500000.0),      # 10 Mbps Up, 20 Mbps Down
        "Ethernet": (1000.0, 2000.0),         # Meaningful but small traffic
        "vEthernet (WSL)": (0.5, 0.8),        # Negligible traffic, should be ignored
        "Bluetooth PAN": (0.0, 0.0)           # Zero traffic, should be ignored
    }
    
    # ACT
    state.add_speed_data(test_speed_data)
    
    # ASSERT
    
    # 1. Assert the in-memory deque (for live graph) was updated correctly.
    # It should contain the full dictionary of speeds.
    assert len(state.in_memory_history) == 1
    in_memory_snapshot = state.in_memory_history[0]
    
    assert in_memory_snapshot.speeds == test_speed_data

    # 2. Assert the internal database batch (`_db_batch`) was populated correctly.
    # It should contain only the per-interface data for *non-negligible* speeds.
    assert len(state._db_batch) == 2, "Batch should only contain 2 records with significant speed."
    
    # Find the Wi-Fi data in the batch
    wifi_data = next((item for item in state._db_batch if item[1] == "Wi-Fi"), None)
    assert wifi_data is not None, "Wi-Fi data not found in batch."
    # wifi_data is a tuple: (timestamp, interface_name, upload_bytes_sec, download_bytes_sec)
    assert wifi_data[2] == 1250000.0
    assert wifi_data[3] == 2500000.0
    
    # Find the Ethernet data in the batch
    ethernet_data = next((item for item in state._db_batch if item[1] == "Ethernet"), None)
    assert ethernet_data is not None, "Ethernet data not found in batch."
    assert ethernet_data[2] == 1000.0
    assert ethernet_data[3] == 2000.0
    
    # 3. Assert that negligible/zero speed interfaces were NOT added to the batch
    assert "vEthernet (WSL)" not in [item[1] for item in state._db_batch]
    assert "Bluetooth PAN" not in [item[1] for item in state._db_batch]


def test_flush_batch_sends_data_to_worker(managed_widget_state):
    """
    Tests that calling flush_batch on the WidgetState correctly calls the
    enqueue_task method on the DatabaseWorker with the batched data.
    """
    # ARRANGE
    state, _ = managed_widget_state
    
    # Use the public API to populate the batch, just like the real application.
    snapshot1 = (int(time.time()), "Wi-Fi", 100.0, 200.0)
    snapshot2 = (int(time.time()), "Ethernet", 300.0, 400.0)
    
    # Simulate the controller sending data to the state object.
    # We must create a dictionary, as this is what add_speed_data expects.
    state.add_speed_data({"Wi-Fi": (snapshot1[2], snapshot1[3])})
    state.add_speed_data({"Ethernet": (snapshot2[2], snapshot2[3])})

    # The internal batch will have timestamps generated by add_speed_data.
    # We can retrieve the batch to create our "expected" data for the assertion.
    expected_batch = state._db_batch.copy() # It's okay to read it for assertion setup.
    assert len(expected_batch) == 2

    # Spy on the db_worker's enqueue_task method
    with patch.object(state.db_worker, 'enqueue_task') as mock_enqueue_task:
        # ACT
        state.flush_batch()
        
        # ASSERT
        # 1. Assert that the enqueue_task method was called exactly once.
        mock_enqueue_task.assert_called_once()
        
        # 2. Assert that it was called with the correct arguments.
        #    call_args[0] is the tuple of positional arguments.
        call_args = mock_enqueue_task.call_args[0]
        assert call_args[0] == "persist_speed", "The task name should be 'persist_speed'."
        assert call_args[1] == expected_batch, "The data passed to the worker does not match the batch."

        # 3. Assert that the internal batch was cleared after flushing.
        assert len(state._db_batch) == 0, "The internal batch should be empty after flushing."

def test_flush_batch_does_nothing_if_batch_is_empty(managed_widget_state):
    """
    Tests that flush_batch does not interact with the worker thread
    if there is no data to be persisted.
    """
    # ARRANGE
    state, _ = managed_widget_state
    assert len(state._db_batch) == 0 # Pre-condition

    # Spy on the db_worker's enqueue_task method
    with patch.object(state.db_worker, 'enqueue_task') as mock_enqueue_task:
        # ACT
        state.flush_batch()
        
        # ASSERT
        # Assert that the enqueue_task method was NOT called.
        mock_enqueue_task.assert_not_called()


def test_aggregation_raw_to_minute(managed_widget_state, mock_config):
    """
    Tests the maintenance task that aggregates per-second raw data older than
    24 hours into per-minute averages and maxes.
    """
    # ARRANGE
    state, db_path = managed_widget_state
    time.sleep(0.2) 
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    now = datetime.now()
    
    # "Old" data from 25 hours ago, belonging to the same minute
    old_timestamp_base = int((now - timedelta(hours=25)).timestamp())
    old_data = [
        (old_timestamp_base + 1, "Wi-Fi", 100.0, 200.0),
        (old_timestamp_base + 2, "Wi-Fi", 300.0, 400.0),
        (old_timestamp_base + 3, "Ethernet", 50.0, 60.0),
    ]
    
    # "Recent" data from 1 hour ago (should NOT be aggregated)
    recent_timestamp_base = int((now - timedelta(hours=1)).timestamp())
    recent_data = [ (recent_timestamp_base + 1, "Wi-Fi", 1000.0, 2000.0) ]
    
    cursor.executemany("INSERT INTO speed_history_raw VALUES (?, ?, ?, ?)", old_data + recent_data)
    conn.commit()
    conn.close()

    # ACT
    # CORRECTED: Pass the config to the maintenance task
    state.db_worker._run_maintenance(mock_config)
    time.sleep(0.2)

    # ASSERT
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # (Assert logic remains the same...)
    cursor.execute("SELECT * FROM speed_history_minute")
    minute_records = cursor.fetchall()
    assert len(minute_records) == 2

    wifi_agg = next((rec for rec in minute_records if rec[1] == "Wi-Fi"), None)
    assert wifi_agg is not None
    assert wifi_agg[2] == pytest.approx(200.0)
    assert wifi_agg[3] == pytest.approx(300.0)
    assert wifi_agg[4] == pytest.approx(300.0)
    assert wifi_agg[5] == pytest.approx(400.0)

    cursor.execute("SELECT * FROM speed_history_raw WHERE timestamp < ?", (recent_timestamp_base,))
    assert len(cursor.fetchall()) == 0

    cursor.execute("SELECT * FROM speed_history_raw")
    remaining_raw = cursor.fetchall()
    assert len(remaining_raw) == 1
    assert remaining_raw[0][0] == recent_data[0][0]

    conn.close()


def test_aggregation_minute_to_hour(managed_widget_state, mock_config):
    """
    Tests the maintenance task that aggregates per-minute data older than
    30 days into per-hour averages and maxes.
    """
    # ARRANGE
    state, db_path = managed_widget_state
    time.sleep(0.2)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    now = datetime.now()
    
    # "Old" minute-level data from 31 days ago
    old_timestamp_base = int((now - timedelta(days=31)).timestamp())
    old_data_minute = [
        (old_timestamp_base + 60, "Wi-Fi", 100.0, 200.0, 150.0, 250.0, 60),
        (old_timestamp_base + 120, "Wi-Fi", 300.0, 400.0, 350.0, 450.0, 60),
    ]

    # "Recent" minute-level data from 10 days ago
    recent_timestamp_base = int((now - timedelta(days=10)).timestamp())
    recent_data_minute = [ (recent_timestamp_base + 60, "Wi-Fi", 1000.0, 2000.0, 1500.0, 2500.0, 60) ]
    
    cursor.executemany("INSERT INTO speed_history_minute VALUES (?, ?, ?, ?, ?, ?, ?)", old_data_minute + recent_data_minute)
    conn.commit()
    conn.close()

    # ACT
    # CORRECTED: Pass the config to the maintenance task
    state.db_worker._run_maintenance(mock_config)
    time.sleep(0.2)

    # ASSERT
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # (Assert logic remains the same...)
    cursor.execute("SELECT * FROM speed_history_hour")
    hour_records = cursor.fetchall()
    assert len(hour_records) == 1

    wifi_agg_hour = hour_records[0]
    assert wifi_agg_hour[1] == "Wi-Fi"
    assert wifi_agg_hour[2] == pytest.approx(200.0)
    assert wifi_agg_hour[3] == pytest.approx(300.0)
    assert wifi_agg_hour[4] == pytest.approx(350.0)
    assert wifi_agg_hour[5] == pytest.approx(450.0)

    cursor.execute("SELECT * FROM speed_history_minute WHERE timestamp < ?", (recent_timestamp_base,))
    assert len(cursor.fetchall()) == 0

    cursor.execute("SELECT * FROM speed_history_minute")
    remaining_minute = cursor.fetchall()
    assert len(remaining_minute) == 1
    assert remaining_minute[0][0] == recent_data_minute[0][0]

    conn.close()


def test_pruning_with_grace_period(managed_widget_state, mock_config):
    """
    Tests the full lifecycle of reducing the data retention period, ensuring
    the 48-hour grace period is respected.
    """
    # ARRANGE
    state, db_path = managed_widget_state
    
    # The fixture already initializes the DB, so we can connect immediately.
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    now = datetime.now()
    
    # Insert "very old" data (older than our new target retention) and "recent" data
    very_old_timestamp = int((now - timedelta(days=40)).timestamp())
    recent_timestamp = int((now - timedelta(days=10)).timestamp())
    
    # Insert placeholder data into the hour table (the target for pruning)
    cursor.execute("INSERT INTO speed_history_hour VALUES (?, 'Wi-Fi', 0, 0, 0, 0, 1)", (very_old_timestamp,))
    cursor.execute("INSERT INTO speed_history_hour VALUES (?, 'Wi-Fi', 0, 0, 0, 0, 1)", (recent_timestamp,))
    
    # Set the initial, long retention period in the database metadata
    cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('current_retention_days', '365')")
    conn.commit()
    conn.close()

    # --- ACT 1: User reduces the retention period ---
    # The new config requests a 30-day retention
    new_config = mock_config.copy()
    new_config['keep_data'] = 30
    
    # Manually trigger maintenance, passing the current time explicitly
    state.db_worker._execute_task("maintenance", (new_config, now))
    
    # ASSERT 1: The grace period is scheduled, and NO data is deleted yet
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM speed_history_hour")
    assert len(cursor.fetchall()) == 2, "Data should NOT be pruned during the grace period."
    
    cursor.execute("SELECT value FROM metadata WHERE key = 'prune_scheduled_at'")
    scheduled_at_ts = int(cursor.fetchone()[0])
    assert scheduled_at_ts > now.timestamp(), "A future prune should be scheduled."
    
    cursor.execute("SELECT value FROM metadata WHERE key = 'pending_retention_days'")
    assert int(cursor.fetchone()[0]) == 30, "The pending retention period should be 30 days."
    conn.close()
    
    # --- ACT 2: Simulate the passage of 48 hours and run maintenance again ---
    future_time = now + timedelta(hours=49)
    
    # Pass the future time directly to the maintenance function to simulate time travel
    state.db_worker._execute_task("maintenance", (new_config, future_time))

    # ASSERT 2: The prune has now been executed
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM speed_history_hour")
    remaining_records = cursor.fetchall()
    assert len(remaining_records) == 1, "The very old record should have been pruned."
    assert remaining_records[0][0] == recent_timestamp, "Only the recent record should remain."
    
    # Assert that the grace period metadata has been cleaned up
    cursor.execute("SELECT value FROM metadata WHERE key = 'prune_scheduled_at'")
    assert cursor.fetchone() is None, "Scheduled prune metadata should be removed after execution."
    
    cursor.execute("SELECT value FROM metadata WHERE key = 'current_retention_days'")
    assert int(cursor.fetchone()[0]) == 30, "The current retention period should now be updated to 30."
    conn.close()


def test_get_speed_history_unified_tiers(managed_widget_state):
    """
    Verifies that get_speed_history correctly unions data from all tiers
    even when a large time range is requested.
    """
    state, db_path = managed_widget_state
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    now = datetime.now()
    now_ts = int(now.timestamp())
    
    # Tier 1: Raw (10 seconds ago - will bin to current minute)
    cursor.execute("INSERT INTO speed_history_raw VALUES (?, 'eth0', 100, 200)", (now_ts - 10,))
    
    # Tier 2: Minute (2 days ago - distinct from current minute)
    cursor.execute("INSERT INTO speed_history_minute VALUES (?, 'eth0', 50, 60, 70, 80, 60)", (now_ts - 2 * 86400,))
    
    # Tier 3: Hour (40 days ago - distinct from recent time)
    cursor.execute("INSERT INTO speed_history_hour VALUES (?, 'eth0', 10, 20, 30, 40, 60)", (now_ts - 40 * 86400,))
    
    conn.commit()
    conn.close()
    
    # ACT: Request "All" time range (None start time)
    results = state.get_speed_history(start_time=None, end_time=now)
    
    # ASSERT: The new multi-tier UNION query correctly returns data from all tiers
    # that have data for the requested range. Since we inserted data at distinct time periods,
    # we should get 3 data points (potentially more if binning creates additional buckets,
    # but at minimum we have 3 distinct time boundaries covered).
    assert len(results) >= 3, f"Expected at least 3 results from multi-tier query, got {len(results)}"
    
    # Verify we can find data from all three tiers by checking for characteristic uploads
    # Note: Historical queries now preserve peaks (MAX) instead of averages to ensure visibility.
    uploads = [r[1] for r in results]
    assert 30.0 in uploads, "Missing hour tier data (peak)"
    assert 70.0 in uploads, "Missing minute tier data (peak)"
    assert 100.0 in uploads, "Missing raw tier data"


def test_get_speed_history_minute_resolution_never_returns_null_timestamp(managed_widget_state):
    """
    Regression test for graph worker crashes:
    minute/hour/day queries must not synthesize NULL bin timestamps.
    """
    state, _ = managed_widget_state
    now = datetime.now()
    start = now - timedelta(hours=6)

    # No inserted data on purpose: query should return either empty+padding data,
    # but it must never raise from datetime.fromtimestamp(None).
    results = state.get_speed_history(
        start_time=start,
        end_time=now,
        interface_name="All",
        resolution="minute"
    )

    assert results, "Expected padded timeline points for empty minute-resolution history."
    assert all(isinstance(ts, datetime) for ts, _, _ in results)


def test_get_speed_history_merges_overlapping_tiers_without_double_count(managed_widget_state):
    """
    Regression test for timeline consistency:
    when raw+minute overlap in the same bin, "All interfaces" must not double-count.
    """
    state, db_path = managed_widget_state
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    now_ts = int(datetime.now().timestamp())
    minute_start = (now_ts // 60) * 60

    # One second represented in raw + the rest of the minute represented in minute tier.
    cursor.execute(
        "INSERT INTO speed_history_raw VALUES (?, 'eth0', 100.0, 200.0)",
        (minute_start + 59,)
    )
    cursor.execute(
        "INSERT INTO speed_history_minute VALUES (?, 'eth0', 100.0, 200.0, 100.0, 200.0, 59)",
        (minute_start,)
    )
    conn.commit()
    conn.close()

    start = datetime.fromtimestamp(minute_start)
    end = datetime.fromtimestamp(minute_start + 59)
    results = state.get_speed_history(
        start_time=start,
        end_time=end,
        interface_name="All",
        resolution="minute"
    )

    non_zero = [(up, down) for _, up, down in results if up > 0 or down > 0]
    assert non_zero, "Expected at least one non-zero point."
    max_up = max(up for up, _ in non_zero)
    max_down = max(down for _, down in non_zero)
    assert max_up == pytest.approx(100.0)
    assert max_down == pytest.approx(200.0)


def test_minute_to_hour_aggregation_uses_weighted_average(managed_widget_state, mock_config):
    """
    Ensures hour aggregation respects sample_count and does not use simple AVG(upload_avg).
    """
    state, db_path = managed_widget_state
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    old_ts = int((datetime.now() - timedelta(days=31)).timestamp())
    hour_start = (old_ts // 3600) * 3600

    # Same hour, very different sample counts.
    cursor.execute(
        "INSERT INTO speed_history_minute VALUES (?, 'Wi-Fi', 100.0, 100.0, 100.0, 100.0, 60)",
        (hour_start + 60,)
    )
    cursor.execute(
        "INSERT INTO speed_history_minute VALUES (?, 'Wi-Fi', 1000.0, 1000.0, 1000.0, 1000.0, 1)",
        (hour_start + 120,)
    )
    conn.commit()
    conn.close()

    state.db_worker._run_maintenance(mock_config)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT upload_avg, download_avg, sample_count FROM speed_history_hour WHERE timestamp = ? AND interface_name = 'Wi-Fi'",
        (hour_start,)
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None, "Expected aggregated hour row to exist."
    expected_weighted = (100.0 * 60 + 1000.0 * 1) / 61
    assert row[0] == pytest.approx(expected_weighted)
    assert row[1] == pytest.approx(expected_weighted)
    assert row[2] == 61


def test_get_speed_history_peak_is_consistent_across_resolutions(managed_widget_state):
    """
    The same spike should keep its peak value across minute/hour/day timeline resolutions.
    """
    state, db_path = managed_widget_state
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    now_ts = int(datetime.now().timestamp())
    hour_start = (now_ts // 3600) * 3600

    # Two samples in the same minute/hour/day bin: one low, one peak.
    cursor.execute("INSERT INTO speed_history_raw VALUES (?, 'eth0', 1.0, 2.0)", (hour_start + 10,))
    cursor.execute("INSERT INTO speed_history_raw VALUES (?, 'eth0', 200.0, 400.0)", (hour_start + 20,))
    conn.commit()
    conn.close()

    start = datetime.fromtimestamp(hour_start)
    end = datetime.fromtimestamp(hour_start + 59)

    peaks = {}
    for res in ("minute", "hour", "day"):
        points = state.get_speed_history(
            start_time=start,
            end_time=end,
            interface_name="All",
            resolution=res
        )
        non_zero = [(u, d) for _, u, d in points if u > 0 or d > 0]
        assert non_zero, f"Expected non-zero points for resolution={res}"
        peaks[res] = (
            max(u for u, _ in non_zero),
            max(d for _, d in non_zero),
        )

    assert peaks["minute"][0] == pytest.approx(200.0)
    assert peaks["minute"][1] == pytest.approx(400.0)
    assert peaks["hour"][0] == pytest.approx(200.0)
    assert peaks["hour"][1] == pytest.approx(400.0)
    assert peaks["day"][0] == pytest.approx(200.0)
    assert peaks["day"][1] == pytest.approx(400.0)


