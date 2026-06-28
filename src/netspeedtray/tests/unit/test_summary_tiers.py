"""
Window summaries (and the hardware-history series) for a window LONGER than the 24h raw horizon must
cover the WHOLE window — the recent <24h is still in the raw tier, the older portion in minute/hour.

Regression for the audit blocker: summarize_* read only the rollup tier for win>24h (raw fallback fired
only when the rollup was totally empty), so a 48h/week/month summary silently dropped the most recent
day and disagreed with the graph (which UNIONs all tiers). These tests seed BOTH tiers and assert the
recent raw portion is represented in the merged summary.
"""
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QThread

from netspeedtray.core.widget_state import WidgetState
from netspeedtray import constants


@pytest.fixture
def state(tmp_path: Path) -> Iterator[WidgetState]:
    cfg = constants.config.defaults.DEFAULT_CONFIG.copy()
    with patch.object(QThread, "start", lambda self: None):
        with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
            ws = WidgetState(cfg)
    w = ws.db_worker
    w.db_path = tmp_path / "speed_history.db"
    w._initialize_connection()
    w._check_and_create_schema()
    yield ws
    w._close_connection()
    ws.cleanup()


def test_network_summary_includes_recent_raw_tier_for_long_window(state):
    now = datetime.now()
    st = int((now - timedelta(hours=48)).timestamp())
    et = int(now.timestamp())
    cur = state.db_worker.conn.cursor()
    MIN = constants.data.SPEED_TABLE_MINUTE
    RAW = constants.data.SPEED_TABLE_RAW

    # Older half (24–48h ago) lives in the MINUTE rollup: 10 buckets, 60 samples each, modest speeds.
    cur.executemany(
        f"INSERT INTO {MIN} (timestamp, interface_name, upload_avg, download_avg, upload_max, download_max, sample_count) "
        f"VALUES (?, 'Ethernet', 0, 1000, 0, 2000, 60)",
        [(st + 60 * i,) for i in range(10)])
    # Recent half (<24h) lives only in RAW — with a distinctive PEAK the rollup tier never saw.
    PEAK = 9_000_000
    cur.executemany(
        f"INSERT INTO {RAW} (timestamp, interface_name, upload_bytes_sec, download_bytes_sec) "
        f"VALUES (?, 'Ethernet', 0, {PEAK})",
        [(et - 3600 + i,) for i in range(50)])
    state.db_worker.conn.commit()

    s = state.summarize_network("download", now - timedelta(hours=48), now, "all", poll_interval=1.0)
    assert s.count == 10 * 60 + 50, "summary dropped a tier (recent raw not merged with older rollup)"
    assert s.max == PEAK, "the recent raw peak was missing — the most-recent-day-dropped bug"
    assert s.exact is False                         # a rolled-up window is honestly non-exact


def test_hardware_summary_includes_recent_raw_tier_for_long_window(state):
    now = datetime.now()
    st = int((now - timedelta(hours=48)).timestamp())
    et = int(now.timestamp())
    cur = state.db_worker.conn.cursor()
    HMIN = constants.data.HARDWARE_STATS_TABLE_MINUTE
    HRAW = constants.data.HARDWARE_STATS_TABLE_RAW

    cur.executemany(
        f"INSERT INTO {HMIN} (timestamp, stat_type, avg_value, max_value, sample_count) "
        f"VALUES (?, 'cpu', 30.0, 40.0, 60)", [(st + 60 * i,) for i in range(10)])
    cur.executemany(
        f"INSERT INTO {HRAW} (timestamp, stat_type, value) VALUES (?, 'cpu', 99.0)",
        [(et - 3600 + i,) for i in range(50)])
    state.db_worker.conn.commit()

    s = state.summarize_hardware("cpu", now - timedelta(hours=48), now, poll_interval=1.0)
    assert s.count == 10 * 60 + 50
    assert s.max == 99.0, "the recent raw CPU peak (99%) was dropped"


def test_hardware_history_unions_tiers_for_long_window(state):
    now = datetime.now()
    st = int((now - timedelta(hours=48)).timestamp())
    et = int(now.timestamp())
    cur = state.db_worker.conn.cursor()
    HMIN = constants.data.HARDWARE_STATS_TABLE_MINUTE
    HRAW = constants.data.HARDWARE_STATS_TABLE_RAW

    cur.execute(f"INSERT INTO {HMIN} (timestamp, stat_type, avg_value, max_value, sample_count) "
                f"VALUES (?, 'cpu', 30.0, 40.0, 60)", (st + 120,))   # an older (minute-tier) point
    cur.execute(f"INSERT INTO {HRAW} (timestamp, stat_type, value) VALUES (?, 'cpu', 99.0)",
                (et - 120,))                                          # a recent (raw-tier) point
    state.db_worker.conn.commit()

    series = state.get_hardware_history("cpu", now - timedelta(hours=48), now)
    vals = [v for _, v in series]
    assert any(abs(v - 30.0) < 0.01 for v in vals), "older minute-tier point missing"
    assert any(abs(v - 99.0) < 0.01 for v in vals), "recent raw-tier point dropped (single-tier read)"
