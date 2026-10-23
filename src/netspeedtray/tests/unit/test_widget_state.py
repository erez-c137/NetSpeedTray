"""
Unit tests for the WidgetState and its associated DatabaseWorker.

These tests verify the correctness of the data layer, including database schema
creation, data ingestion, aggregation, pruning, and maintenance logic.

Since the v8 switch-over (2.2) the app writes schema v8 only: ``v8_widget_state`` runs the real
worker's startup synchronously on a fresh file. The tests further down still drive a v7 file through
``managed_widget_state`` - written by 2.1.x's worker (``tests/legacy_v7_worker``) and read through the
v7 backend, which 2.2 uses while an upgrade is deferred.
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
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.tests.state_helpers import attach_v7, prepare_v8


@pytest.fixture
def mock_config() -> dict:
    """Provides a default configuration dictionary for tests."""
    return constants.config.defaults.DEFAULT_CONFIG.copy()


def _unstarted_state(tmp_path: Path, config: dict) -> WidgetState:
    with patch.object(QThread, 'start', lambda self: None):
        with patch('netspeedtray.core.widget_state.get_app_data_path', return_value=tmp_path):
            return WidgetState(config)


@pytest.fixture
def v8_widget_state(tmp_path: Path, mock_config: dict) -> Iterator[tuple[WidgetState, Path]]:
    """A WidgetState whose worker ran its real startup (fresh install -> v8) on THIS thread."""
    db_path = tmp_path / "speed_history.db"
    state = _unstarted_state(tmp_path, mock_config)
    worker = prepare_v8(state, db_path)
    yield state, db_path
    worker._close_connection()
    state.cleanup()


@pytest.fixture
def managed_widget_state(tmp_path: Path, mock_config: dict) -> Iterator[tuple[WidgetState, Path]]:
    """
    A WidgetState over a v7 file for SYNCHRONOUS testing of the v7 backend: written by 2.1.x's
    worker (its connection LEFT OPEN for the test to write through), read as a deferred-upgrade 2.2
    session reads it.
    """
    db_path = tmp_path / "speed_history.db"
    state = _unstarted_state(tmp_path, mock_config)
    worker = attach_v7(state, db_path)
    yield state, db_path
    worker._close_connection()
    state.cleanup()


def test_database_initialization_creates_correct_schema(v8_widget_state):
    """A first run on a non-existent file creates the complete v8 schema (fresh install -> v8)."""
    state, db_path = v8_widget_state
    assert db_path.exists(), "Database file was not created."
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        # 1. Every v8 table, and nothing else (bandwidth_history is gone).
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()} - {"sqlite_stat1"}
        expected_tables = {'metadata', 'usage_counter', 'interfaces', 'metrics', 'net_raw', 'net_minute',
                           'net_hour', 'hw_raw', 'hw_minute', 'hw_hour', 'app_sessions', 'link_events'}
        assert tables == expected_tables == set(S8.EXPECTED_TABLES_V8), "Incorrect set of tables were created."
        assert constants.data.BANDWIDTH_TABLE not in tables
        # 2. The v7 names exist only as read-only compatibility views.
        cursor.execute("SELECT name FROM sqlite_master WHERE type='view';")
        assert {row[0] for row in cursor.fetchall()} == set(S8.COMPAT_VIEWS_V8)
        # 3. Version, page size and incremental auto-vacuum (only settable on the empty file).
        cursor.execute("SELECT value FROM metadata WHERE key = 'db_version'")
        assert int(cursor.fetchone()[0]) == DatabaseWorker._DB_VERSION == 8
        assert cursor.execute("PRAGMA page_size").fetchone()[0] == S8.PAGE_SIZE
        assert cursor.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        assert cursor.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        # 4. net_raw stores bytes over a measured interval, never a rate.
        cols = {row[1]: (row[2], row[5]) for row in cursor.execute("PRAGMA table_info('net_raw')")}
        assert cols == {'ts': ('INTEGER', 1), 'iface_id': ('INTEGER', 2), 'up_bytes': ('INTEGER', 0),
                        'down_bytes': ('INTEGER', 0), 'dt_ms': ('INTEGER', 0), 'flags': ('INTEGER', 0)}
        # 5. The running app is a session.
        assert cursor.execute("SELECT COUNT(*) FROM app_sessions WHERE ended_ts IS NULL").fetchone()[0] == 1
    finally:
        conn.close()
    assert state.history_schema_version() == 8


def test_add_speed_data_feeds_memory_only(v8_widget_state):
    """add_speed_data is the DISPLAY path: the in-memory series get the (filtered) rates; nothing is
    batched for the DB - v8 history is fed bytes by add_net_sample."""
    state, _ = v8_widget_state
    test_speed_data = {"Wi-Fi": (1250000.0, 2500000.0), "Ethernet": (1000.0, 2000.0)}
    state.add_speed_data(test_speed_data, persist_data={"Wi-Fi": (9e6, 9e6)})
    assert len(state.in_memory_history) == 1
    assert state.in_memory_history[0].speeds == test_speed_data
    assert state._net_batch == [] and not hasattr(state, "_db_batch")


def test_add_net_sample_and_flush_batch_send_one_v8_task(v8_widget_state):
    """flush_batch hands the worker ONE persist_v8 task: every buffered poll (bytes over dt_ms) plus the
    hardware samples, then clears both buffers."""
    state, _ = v8_widget_state
    now = time.time()
    state.add_net_sample(now - 1, 1000, {})                                   # primes only
    state.add_net_sample(now, 1003.4, {"Wi-Fi": (100.0, 200.0), "Ethernet": (0.0, 5.0)})
    state.add_hardware_stat('cpu', 12.5)
    assert len(state._net_batch) == 2 and state._net_batch[1].dt_ms == 1003
    net, hw = list(state._net_batch), list(state._hw_batch)
    with patch.object(state.db_worker, 'enqueue_task') as mock_enqueue_task:
        state.flush_batch()
        mock_enqueue_task.assert_called_once()
        task, data = mock_enqueue_task.call_args[0]
        assert task == "persist_v8"
        assert data == (net, hw)
    assert state._net_batch == [] and state._hw_batch == []


def test_flush_batch_sends_the_heartbeat_even_when_empty(v8_widget_state):
    """An empty flush still reaches the worker: its flush writes the app_sessions heartbeat, the
    coverage record of an idle machine (v7 skipped empty flushes - and had no coverage record)."""
    state, db_path = v8_widget_state
    w = state.db_worker
    with patch.object(w, 'enqueue_task') as mock_enqueue_task:
        state.flush_batch()
        mock_enqueue_task.assert_called_once_with("persist_v8", ([], []))
    conn = sqlite3.connect(db_path)
    before = conn.execute("SELECT last_seen_ts FROM app_sessions").fetchone()[0]
    conn.close()
    with patch("netspeedtray.core.db.writer_v8.time.time", return_value=before + 10):
        w._execute_task("persist_v8", ([], []))
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT last_seen_ts FROM app_sessions").fetchone()[0] == before + 10
    conn.close()


def test_add_hardware_stat_clamps_pct_not_physical(v8_widget_state):
    """Utilization stats clamp 0-100; physical stats (power W / temp °C / latency ms) store unclamped
    so a 180 W draw or 200 ms ping isn't corrupted to 100."""
    state, _ = v8_widget_state
    state._hw_batch.clear()
    state.add_hardware_stat('cpu', 150.0)        # util -> clamped
    state.add_hardware_stat('cpu_power', 180.0)  # power -> unclamped
    state.add_hardware_stat('gpu_temp', 105.0)   # temp -> unclamped
    state.add_hardware_stat('latency_gw', 240.0) # latency -> unclamped
    vals = {b[1]: b[2] for b in state._hw_batch}
    assert vals['cpu'] == 100.0
    assert vals['cpu_power'] == 180.0 and vals['gpu_temp'] == 105.0 and vals['latency_gw'] == 240.0


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
    
    # "Old" data from 25 hours ago, all belonging to the SAME minute. Floor the base to the start of its
    # minute so the +1/+2/+3-second records can't straddle a clock-minute boundary - otherwise, when this
    # test happened to run in the last few seconds of a minute, the two Wi-Fi records split into two
    # per-minute buckets and the count became 3 instead of 2 (a real, intermittent CI flake).
    old_timestamp_base = (int((now - timedelta(hours=25)).timestamp()) // 60) * 60
    old_data = [
        (old_timestamp_base + 1, "Wi-Fi", 100.0, 200.0),
        (old_timestamp_base + 2, "Wi-Fi", 300.0, 400.0),
        (old_timestamp_base + 3, "Ethernet", 50.0, 60.0),
    ]

    # "Recent" data from 1 hour ago (should NOT be aggregated); floored to its minute for the same reason.
    recent_timestamp_base = (int((now - timedelta(hours=1)).timestamp()) // 60) * 60
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
    
    # "Old" minute-level data from 31 days ago. Anchor the base to an exact hour boundary so
    # the two records (+60s, +120s) always land in the SAME hour bucket - otherwise, ~1.7% of
    # wall-clock seconds put them either side of an hour boundary and the aggregation yields 2
    # hour rows instead of 1 (a latent time-of-day flake).
    old_timestamp_base = (int((now - timedelta(days=31)).timestamp()) // 3600) * 3600
    old_data_minute = [
        (old_timestamp_base + 60, "Wi-Fi", 100.0, 200.0, 150.0, 250.0, 60),
        (old_timestamp_base + 120, "Wi-Fi", 300.0, 400.0, 350.0, 450.0, 60),
    ]

    # "Recent" minute-level data from 10 days ago (also hour-anchored, for the same reason)
    recent_timestamp_base = (int((now - timedelta(days=10)).timestamp()) // 3600) * 3600
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

    # No inserted data on purpose: the query must never raise from datetime.fromtimestamp(None).
    # Since rc.2 (S4) an empty window is empty - no synthetic zero baseline (the graph then shows its
    # empty-state note instead of a flat zero line nobody measured).
    results = state.get_speed_history(
        start_time=start,
        end_time=now,
        interface_name="All",
        resolution="minute"
    )

    assert results == []
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




def test_hardware_history_raw_fallback_for_recent_long_window(managed_widget_state):
    """A 24h window selects the MINUTE tier, but the minute tier only fills from aggregating rows
    older than 24h - recent data lives in raw. get_hardware_history must fall back to raw so the
    Monitor's non-session views aren't empty (root of the #10 'Collecting data' bug). Also exercises
    RAM as a first-class stat_type."""
    import sqlite3
    state, db_path = managed_widget_state
    now = datetime.now()
    rows = [(int((now - timedelta(minutes=m)).timestamp()), "ram", 50.0 + m) for m in range(1, 6)]
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO hardware_stats_raw (timestamp, stat_type, value) VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()
    # 24h duration -> minute tier (empty) -> raw fallback returns the recent rows.
    got = state.get_hardware_history("ram", now - timedelta(hours=24), now + timedelta(minutes=1))
    assert len(got) == 5, "raw fallback should surface recent rows the empty minute tier lacks"


def test_summarize_hardware_exact_unclamped(managed_widget_state):
    """summarize_hardware reads the raw tier for a ≤24h window and computes exact stats, preserving
    unclamped physical values (a 120 W sample is not capped to 100)."""
    import sqlite3
    state, db_path = managed_widget_state
    now = datetime.now()
    rows = [(int((now - timedelta(minutes=m)).timestamp()), 'cpu_power', float(v))
            for m, v in zip(range(1, 6), [40, 60, 80, 100, 120])]
    conn = sqlite3.connect(db_path)
    conn.executemany("INSERT INTO hardware_stats_raw (timestamp, stat_type, value) VALUES (?,?,?)", rows)
    conn.commit(); conn.close()
    s = state.summarize_hardware('cpu_power', now - timedelta(hours=1), now + timedelta(minutes=1))
    assert s.exact and s.count == 5
    assert s.min == 40 and s.max == 120 and s.avg == 80
    assert s.p50 == 80 and s.coverage_pct > 0


def test_summarize_network_download(managed_widget_state):
    import sqlite3
    state, db_path = managed_widget_state
    now = datetime.now()
    rows = [(int((now - timedelta(minutes=m)).timestamp()), 'Wi-Fi', 1000.0, float(v) * 1e6)
            for m, v in zip(range(1, 4), [10, 20, 30])]
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO speed_history_raw (timestamp, interface_name, upload_bytes_sec, download_bytes_sec)"
        " VALUES (?,?,?,?)", rows)
    conn.commit(); conn.close()
    s = state.summarize_network('download', now - timedelta(hours=1), now + timedelta(minutes=1))
    assert s.exact and s.count == 3 and s.avg == 20e6 and s.max == 30e6


# --- The VACUUM gate (v2.1.5, action-plan item 10) ---------------------------------------
#
# The old gate was `if pruned:` - the rowcount of the retention DELETE - which matches
# nothing until the install outlives its retention setting (a year at the default, never
# on "keep everything"). Meanwhile the hourly aggregation DELETEs fragment the file from
# day one. The gate is now the freelist itself; the once-a-day interval and the free-page
# floor stay. NOTE: test_pruning_with_grace_period above does enter `if pruned:` but its
# DB has too few free pages to reach the VACUUM - it does NOT cover this.


def _fragment_db(db_path: Path, rows: int = 1500) -> int:
    """
    Litter the file with free pages: insert page-sized junk rows, then delete them all.
    DELETE hands pages to the freelist but never shrinks the file - exactly the state
    hourly aggregation leaves behind on a real install. Returns the freelist_count.
    """
    conn = sqlite3.connect(db_path)
    junk = "x" * 3500  # ~one 4096-byte page per row
    conn.executemany(
        "INSERT INTO speed_history_raw VALUES (?, ?, 0.0, 0.0)",
        [(i, f"junk-{i}-{junk}") for i in range(rows)],
    )
    conn.commit()
    conn.execute("DELETE FROM speed_history_raw")
    conn.commit()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    except sqlite3.Error:
        pass  # freelist_count reads through the WAL either way
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    conn.close()
    return free


@pytest.mark.parametrize("keep_data", [365, 36500])
def test_vacuum_runs_on_fragmented_db_without_prunable_data(managed_widget_state, mock_config, keep_data):
    """
    BOTH previously-unreachable shapes: a young install at default retention (prunes
    nothing for a year) and "keep everything" (prunes nothing, ever). With real freelist
    slack and no recent last_vacuum_at, maintenance must VACUUM, write last_vacuum_at,
    and leave the WAL truncated (the checkpoint must run AFTER the VACUUM - VACUUM in
    WAL mode writes the whole vacuumed DB into -wal).
    """
    state, db_path = managed_widget_state
    free_before = _fragment_db(db_path)
    assert free_before >= 1000, "fixture must produce enough slack to clear the floor"

    config = mock_config.copy()
    config["keep_data"] = keep_data
    state.db_worker._run_maintenance(config)

    wal = Path(str(db_path) + "-wal")
    if wal.exists():
        assert wal.stat().st_size == 0, (
            "WAL left at result size after VACUUM - wal_checkpoint(TRUNCATE) must run after it"
        )

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT value FROM metadata WHERE key='last_vacuum_at'").fetchone()
    free_after = conn.execute("PRAGMA freelist_count").fetchone()[0]
    conn.close()
    assert row is not None, "VACUUM did not run: last_vacuum_at was never written"
    assert int(row[0]) > 0
    assert free_after == 0, "VACUUM ran but the freelist slack was not reclaimed"


def test_vacuum_skips_below_free_page_floor(managed_widget_state, mock_config):
    """The other direction: a healthy file (freelist under the floor) is never rewritten."""
    state, db_path = managed_widget_state

    state.db_worker._run_maintenance(mock_config)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT value FROM metadata WHERE key='last_vacuum_at'").fetchone()
    conn.close()
    assert row is None, "VACUUM must not run on a file with no freelist slack"


def test_vacuum_respects_daily_interval(managed_widget_state, mock_config):
    """The retained inner gate: at most one VACUUM per day, however fragmented the file."""
    state, db_path = managed_widget_state
    free_before = _fragment_db(db_path)
    assert free_before >= 1000

    recent = str(int(datetime.now().timestamp()) - 60)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_vacuum_at', ?)", (recent,))
    conn.commit()
    conn.close()

    state.db_worker._run_maintenance(mock_config)

    conn = sqlite3.connect(db_path)
    val = conn.execute("SELECT value FROM metadata WHERE key='last_vacuum_at'").fetchone()[0]
    free_after = conn.execute("PRAGMA freelist_count").fetchone()[0]
    conn.close()
    assert val == recent, "last_vacuum_at must not be rewritten inside the daily window"
    assert free_after >= 1000, "no rewrite may happen inside the daily window"


def test_vacuum_skipped_during_shutdown(managed_widget_state, mock_config):
    """
    stop() drains queued tasks after the flag is set (database.py run loop) and cleanup()
    waits only 2000 ms for the thread - a full-file rewrite must never start mid-shutdown.
    """
    state, db_path = managed_widget_state
    free_before = _fragment_db(db_path)
    assert free_before >= 1000

    state.db_worker._stop_event.set()
    try:
        state.db_worker._run_maintenance(mock_config)
    finally:
        state.db_worker._stop_event.clear()

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT value FROM metadata WHERE key='last_vacuum_at'").fetchone()
    conn.close()
    assert row is None, "VACUUM must not start while shutdown is in progress"


def test_vacuum_warranted_ratio_and_cap(managed_widget_state):
    """
    The freelist floor alone is absolute (1000 pages = ~4 MB) with no reference to file
    size. The gate needs a ratio conjunct - a rewrite must be worth the whole-file cost -
    plus an absolute cap so a huge file with huge slack still vacuums at any ratio.
    Shapes below are the action plan's measured numbers.
    """
    state, _ = managed_widget_state
    warranted = state.db_worker._vacuum_warranted

    # Floor: under 1000 free pages is never worth a full-file rewrite.
    assert not warranted(999, 1000)
    # Ratio: the measured live-DB shape (53 MB, 84% free) - clearly warranted.
    assert warranted(10881, 12948)
    # Mostly-live data, proportionally little slack (the measured ~90 MB / 5.1% case:
    # a 1.66 s rewrite peaking at 176 MB to reclaim 4.7 MB) - not warranted.
    assert not warranted(1170, 23040)
    # Absolute cap: past it, the slack is reclaimed regardless of ratio.
    assert warranted(state.db_worker._VACUUM_FREE_PAGES_CAP, 1_000_000)
