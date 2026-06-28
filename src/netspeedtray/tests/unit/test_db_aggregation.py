"""
Tier rollup must not under-count a bucket that straddles the aggregation cutoff.

Regression for the audit blocker: with a raw-second cutoff, a minute bucket spanning it was aggregated
from only its rows < cutoff (then those raw rows deleted); the next maintenance pass re-rolled the
bucket's remainder into the same key, which `INSERT OR IGNORE` silently dropped — losing the second
half of one bucket per cycle, cascading into the hour tier and corrupting exported long-term totals.
The fix floors the cutoff to the bucket boundary so a bucket is only ever rolled up once, complete.
"""
from datetime import datetime, timedelta

import pytest

from netspeedtray import constants
from netspeedtray.core.database import DatabaseWorker


def test_bucket_floored_cutoff_floors_to_boundary():
    now = datetime.fromtimestamp(1_700_000_000 + 86400 + 37)   # 37s past a minute boundary, +24h
    minute = DatabaseWorker._bucket_floored_cutoff(now, timedelta(hours=24), 60)
    hour = DatabaseWorker._bucket_floored_cutoff(now, timedelta(days=1), 3600)
    assert minute % 60 == 0 and hour % 3600 == 0          # always on a bucket edge
    raw_second = int((now - timedelta(hours=24)).timestamp())
    assert minute <= raw_second and raw_second - minute < 60   # floored DOWN, by < one bucket


def _fresh_worker(tmp_path):
    w = DatabaseWorker(tmp_path / "agg.db")
    w._initialize_connection()
    w._check_and_create_schema()
    return w


def test_straddling_minute_bucket_is_not_undercounted(tmp_path):
    w = _fresh_worker(tmp_path)
    cur = w.conn.cursor()
    raw = constants.data.SPEED_TABLE_RAW
    minute = constants.data.SPEED_TABLE_MINUTE

    # One minute bucket M = [t_M, t_M+60) with 10 once-every-6s samples spanning the whole minute.
    t_M = 1_700_000_040                                    # a multiple of 60
    sample_ts = [t_M + 6 * i for i in range(10)]          # t_M .. t_M+54
    cur.executemany(
        f"INSERT INTO {raw} (timestamp, interface_name, upload_bytes_sec, download_bytes_sec) "
        f"VALUES (?, 'Ethernet', 1000, 2000)", [(ts,) for ts in sample_ts])
    w.conn.commit()

    # Run 1: 'now' is 24h + 30s past t_M, so the *unfloored* cutoff (t_M+30) splits the bucket in half —
    # the exact straddle that used to lose data. Run 2: 'now' advances 2 min, so M is wholly in the past.
    now1 = datetime.fromtimestamp(t_M + 86400 + 30)
    now2 = datetime.fromtimestamp(t_M + 86400 + 30 + 120)
    w._aggregate_raw_to_minute(cur, now1); w.conn.commit()
    w._aggregate_raw_to_minute(cur, now2); w.conn.commit()

    cur.execute(f"SELECT timestamp, sample_count FROM {minute} WHERE timestamp = ?", (t_M,))
    rows = cur.fetchall()
    assert len(rows) == 1, "the straddling minute should roll up to exactly one bucket"
    assert rows[0][1] == len(sample_ts), (
        f"bucket M under-counted: {rows[0][1]} of {len(sample_ts)} samples survived the two-pass rollup")

    # And every raw sample has been consumed (none orphaned, none double-counted).
    cur.execute(f"SELECT COUNT(*) FROM {raw} WHERE timestamp >= ? AND timestamp < ?", (t_M, t_M + 60))
    assert cur.fetchone()[0] == 0
    w.conn.close()


def test_hardware_straddling_minute_bucket_is_not_undercounted(tmp_path):
    w = _fresh_worker(tmp_path)
    cur = w.conn.cursor()
    raw = constants.data.HARDWARE_STATS_TABLE_RAW
    minute = constants.data.HARDWARE_STATS_TABLE_MINUTE

    t_M = 1_700_000_040
    sample_ts = [t_M + 6 * i for i in range(10)]
    cur.executemany(
        f"INSERT INTO {raw} (timestamp, stat_type, value) VALUES (?, 'cpu', 50.0)",
        [(ts,) for ts in sample_ts])
    w.conn.commit()

    now1 = datetime.fromtimestamp(t_M + 86400 + 30)
    now2 = datetime.fromtimestamp(t_M + 86400 + 30 + 120)
    w._aggregate_hardware_raw_to_minute(cur, now1); w.conn.commit()
    w._aggregate_hardware_raw_to_minute(cur, now2); w.conn.commit()

    cur.execute(f"SELECT sample_count FROM {minute} WHERE timestamp = ? AND stat_type = 'cpu'", (t_M,))
    rows = cur.fetchall()
    assert len(rows) == 1 and rows[0][0] == len(sample_ts)
    w.conn.close()
