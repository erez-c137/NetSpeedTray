"""
2.2.1 review: the legacy v7 reader at the 0.5 s update rate (#456).

The v7 raw tables key on (timestamp, ...) with an INTEGER timestamp, so they hold at most one row per
second whatever the poll rate. The v7 reader weighed each raw row as ``poll_interval`` seconds, so at
0.5 s a fully-recorded window read as 50 % coverage and half its volume. The v8 reader already floors
the poll at ``schema_v8.RAW_TS_RESOLUTION_SECONDS``; these pin the same floor on every v7 path.
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from netspeedtray import constants
from netspeedtray.core.db.history_reader import V7HistoryReader
from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker

ROWS = 600          # ten minutes of raw rows, one per second
RATE = 1000.0       # bytes/s down, every second


@pytest.fixture
def reader(tmp_path: Path):
    db = tmp_path / "speed_history.db"
    w = LegacyV7Worker(db)
    w._initialize_connection()
    w._check_and_create_schema()
    w._close_connection()

    end = int(datetime.now().timestamp()) - 5
    start = end - ROWS
    conn = sqlite3.connect(db, check_same_thread=False)
    conn.executemany(f"INSERT INTO {constants.data.SPEED_TABLE_RAW} VALUES (?, 'eth0', 0, ?)",
                     [(start + i, RATE) for i in range(ROWS)])
    conn.executemany(f"INSERT INTO {constants.data.HARDWARE_STATS_TABLE_RAW} VALUES (?, 'cpu', 25.0)",
                     [(start + i, ) for i in range(ROWS)])
    # One older minute bucket, so a two-day window takes the multi-tier (rollup) path too.
    old = (end - 36 * 3600) // 60 * 60
    conn.execute(f"INSERT INTO {constants.data.SPEED_TABLE_MINUTE} VALUES (?, 'eth0', 0, ?, 0, ?, 60)",
                 (old, RATE, RATE))
    conn.execute(f"INSERT INTO {constants.data.HARDWARE_STATS_TABLE_MINUTE} VALUES (?, 'cpu', 25.0, 25.0, 60)",
                 (old,))
    conn.commit()
    yield V7HistoryReader(lambda: conn, lambda: db), datetime.fromtimestamp(start), datetime.fromtimestamp(start + ROWS)
    conn.close()


def test_raw_window_is_fully_covered_at_half_a_second(reader):
    r, start, end = reader
    assert r.summarize_network("download", start, end, None, 0.5).coverage_pct == 100.0
    assert r.summarize_hardware("cpu", start, end, 0.5).coverage_pct == 100.0


def test_multi_tier_window_matches_the_one_second_rate(reader):
    r, _start, end = reader
    start = end - timedelta(days=2)
    half = r.summarize_network("download", start, end, None, 0.5)
    one = r.summarize_network("download", start, end, None, 1.0)
    assert half.coverage_pct == one.coverage_pct > 0
    half_hw = r.summarize_hardware("cpu", start, end, 0.5)
    one_hw = r.summarize_hardware("cpu", start, end, 1.0)
    assert half_hw.coverage_pct == one_hw.coverage_pct > 0


def test_raw_volume_is_not_halved_at_half_a_second(reader):
    r, start, end = reader
    up, down = r.total_bandwidth(start, end, None, 0.5)
    assert down == pytest.approx(ROWS * RATE)
    assert r.total_bandwidth(start, end, None, 0.5) == r.total_bandwidth(start, end, None, 1.0)


def test_a_slower_poll_still_counts_its_own_interval(reader):
    """The floor only lifts sub-second polls: a 2 s preset row still stands for 2 s."""
    r, start, end = reader
    _up, down = r.total_bandwidth(start, end, None, 2.0)
    assert down == pytest.approx(ROWS * RATE * 2)
