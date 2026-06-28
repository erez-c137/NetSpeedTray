"""
get_total_bandwidth_for_period — period totals must scale with the poll interval (audit #2/#3).

The tier sums are "sum of per-sample rates"; bytes = that × seconds-per-sample (update_rate).
The old code implicitly assumed 1s, so the "data used" glance under-reported at 2s/5s/10s.
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
def state_with_db(tmp_path: Path) -> Iterator[WidgetState]:
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    with patch.object(QThread, "start", lambda self: None), \
         patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        state = WidgetState(cfg)
    w = state.db_worker
    w.db_path = tmp_path / "speed_history.db"
    w._initialize_connection()
    w._check_and_create_schema()
    # Insert 3 raw samples (rate 100 up / 200 down each) → rate-sum 300 / 600.
    now = int(datetime.now().timestamp())
    cur = w.conn.cursor()
    for i in range(3):
        cur.execute(
            f"INSERT INTO {constants.data.SPEED_TABLE_RAW} "
            "(timestamp, interface_name, upload_bytes_sec, download_bytes_sec) VALUES (?,?,?,?)",
            (now - i, "eth0", 100.0, 200.0))
    w.conn.commit()
    yield state
    w._close_connection()
    state.cleanup()


def _totals(state):
    start = datetime.now() - timedelta(minutes=5)
    return state.get_total_bandwidth_for_period(start, datetime.now())


def test_totals_at_1s_are_the_rate_sum(state_with_db):
    state_with_db.config["update_rate"] = 1.0
    up, down = _totals(state_with_db)
    assert up == pytest.approx(300.0)   # 3 × 100 × 1s
    assert down == pytest.approx(600.0)


def test_totals_scale_with_poll_interval(state_with_db):
    state_with_db.config["update_rate"] = 2.0
    up, down = _totals(state_with_db)
    assert up == pytest.approx(600.0)   # 3 × 100 × 2s  (was 300 before the fix — under-reported)
    assert down == pytest.approx(1200.0)


def test_smart_mode_uses_nominal_one_second(state_with_db):
    state_with_db.config["update_rate"] = -1.0  # SMART/adaptive
    up, down = _totals(state_with_db)
    assert up == pytest.approx(300.0)   # nominal 1s
    assert down == pytest.approx(600.0)


def test_minute_tier_is_poll_rate_independent(state_with_db):
    """A minute/hour rollup bucket represents a FIXED 60/3600s span. Changing the poll rate must NOT
    rescale historical rollup data (the audit's rate-change over-report: 1s→5s 5×-ing months of data)."""
    w = state_with_db.db_worker
    cur = w.conn.cursor()
    base = (int((datetime.now() - timedelta(hours=36)).timestamp()) // 60) * 60   # one minute bucket, 36h ago
    cur.execute(
        f"INSERT INTO {constants.data.SPEED_TABLE_MINUTE} "
        "(timestamp, interface_name, upload_avg, download_avg, upload_max, download_max, sample_count) "
        "VALUES (?,?,?,?,?,?,?)", (base, "eth0", 100.0, 200.0, 150.0, 250.0, 60))
    w.conn.commit()
    # Window isolates the minute tier (excludes the recent raw samples).
    start = datetime.now() - timedelta(hours=48)
    end = datetime.now() - timedelta(hours=25)

    state_with_db.config["update_rate"] = 1.0
    up1, down1 = state_with_db.get_total_bandwidth_for_period(start, end)
    state_with_db.config["update_rate"] = 5.0
    up5, down5 = state_with_db.get_total_bandwidth_for_period(start, end)

    assert up1 == pytest.approx(up5) and down1 == pytest.approx(down5)   # rate change doesn't rescale
    assert up1 == pytest.approx(100.0 * 60)     # avg × the FIXED 60s bucket duration
    assert down1 == pytest.approx(200.0 * 60)
