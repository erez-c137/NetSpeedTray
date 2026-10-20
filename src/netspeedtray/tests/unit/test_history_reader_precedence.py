"""
Tier precedence (v8 step 2): the tiers are query-precedence layers, not additive.

Each tier answers only ``[its floor, the finer tiers' MIN(timestamp))`` - raw where it has data, else
minute, else hour. Today's v7 writer MOVES rows down the chain (raw -> minute -> hour, deleting the
source), so the tiers never overlap and the rule changes nothing (test_history_reader_golden.py proves
that on a fixture written by the real DatabaseWorker). It exists for the moment they DO overlap: the
v8 design review measured the original raw->minute + raw->hour fan-out at **2.00x** the true data-cap
total on the owner's database, because every reader SUMmed every tier with no mutual exclusion
(database-v8-final.md 4.1).

So these tests hand-build a database whose coarser tier holds a COPY of data a finer tier still has,
and assert every multi-tier reader returns exactly what it returns on the same history stored
move-only. The legacy oracle is asserted to double-count on the same data, so the tests are red
without the rule.
"""
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from netspeedtray import constants
from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.tests.unit.legacy_v7_reader_oracle import LegacyV7Readers as Legacy

D = constants.data
NIC = "Ethernet"
UP, DOWN = 100.0, 1000.0          # B/s, integer-valued so every expected total is exact
HW = 40.0                         # a constant cpu %, so a duplicated bucket changes AVGs only by count


def _now_parts():
    now = datetime.now()
    now_ts = int(now.timestamp())
    raw_minute = (now_ts - 2 * 3600) // 60 * 60                 # a minute still in raw (2 h ago)
    old_minute = (now_ts - 30 * 3600) // 60 * 60                 # a minute already rolled up (30 h ago)
    # An hour-tier bucket 10 days back. v7 would never hold an hour row that recent; the v8 fan-out
    # (raw -> minute AND raw -> hour) would, with the same span still in minute - the overlap this file
    # is about. It must sit inside the minute tier's retention horizon, because only minute rows inside
    # it bound the hour tier (an older minute row is a stray, see test_history_reader_boundary.py).
    old_hour = (now_ts - 10 * 86400) // 3600 * 3600
    return now, raw_minute, old_minute, old_hour


def _build(db_dir: Path, overlap: bool, parts) -> None:
    """The same history either move-only (overlap=False) or with copies left in the coarser tier.
    Both files take ONE clock reading (``parts``): built a second apart across a minute boundary,
    their buckets used to differ by a minute and the comparisons flaked (CI, PR #388)."""
    _now, raw_minute, old_minute, old_hour = parts
    w = LegacyV7Worker(db_dir / "speed_history.db")
    w._initialize_connection()
    w._check_and_create_schema()
    c = w.conn.cursor()
    # Truth: 60 raw seconds, one older minute bucket, one much older hour bucket.
    c.executemany(f"INSERT INTO {D.SPEED_TABLE_RAW} (timestamp, interface_name, upload_bytes_sec, "
                  "download_bytes_sec) VALUES (?, ?, ?, ?)",
                  [(raw_minute + i, NIC, UP, DOWN) for i in range(60)])
    minute_rows = [(old_minute, NIC, UP / 2, DOWN / 2, UP / 2, DOWN / 2, 60)]
    hour_rows = [(old_hour, NIC, UP / 4, DOWN / 4, UP / 4, DOWN / 4, 3600)]
    hw_raw = [(raw_minute + i, "cpu", HW) for i in range(60)]
    hw_minute = [(old_minute, "cpu", HW / 2, HW / 2, 60)]
    hw_hour = [(old_hour, "cpu", HW / 4, HW / 4, 3600)]
    if overlap:
        # The raw minute ALSO rolled up into minute (copied, not moved) ...
        minute_rows.append((raw_minute, NIC, UP, DOWN, UP, DOWN, 60))
        hw_minute.append((raw_minute, "cpu", HW, HW, 60))
        # ... and the old hour ALSO still present as its 60 minute buckets.
        minute_rows += [(old_hour + 60 * i, NIC, UP / 4, DOWN / 4, UP / 4, DOWN / 4, 60) for i in range(60)]
        hw_minute += [(old_hour + 60 * i, "cpu", HW / 4, HW / 4, 60) for i in range(60)]
    for table, rows in ((D.SPEED_TABLE_MINUTE, minute_rows), (D.SPEED_TABLE_HOUR, hour_rows)):
        c.executemany(f"INSERT INTO {table} (timestamp, interface_name, upload_avg, download_avg, "
                      "upload_max, download_max, sample_count) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    c.executemany(f"INSERT INTO {D.HARDWARE_STATS_TABLE_RAW} (timestamp, stat_type, value) VALUES (?, ?, ?)", hw_raw)
    for table, rows in ((D.HARDWARE_STATS_TABLE_MINUTE, hw_minute), (D.HARDWARE_STATS_TABLE_HOUR, hw_hour)):
        c.executemany(f"INSERT INTO {table} (timestamp, stat_type, avg_value, max_value, sample_count) "
                      "VALUES (?, ?, ?, ?, ?)", rows)
    w.conn.commit()
    w._close_connection()


def _state(db_dir: Path) -> WidgetState:
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg["update_rate"] = 1.0
    with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=db_dir):
        return WidgetState(cfg, read_only=True)


@pytest.fixture
def pair(q_app, tmp_path):
    (tmp_path / "moved").mkdir()
    (tmp_path / "overlap").mkdir()
    parts = _now_parts()
    _build(tmp_path / "moved", overlap=False, parts=parts)
    _build(tmp_path / "overlap", overlap=True, parts=parts)
    moved, overlap = _state(tmp_path / "moved"), _state(tmp_path / "overlap")
    yield moved, overlap
    moved.cleanup()
    overlap.cleanup()


# The true totals of the fixture history (bytes), per window start.
_RAW_BYTES = (60 * UP, 60 * DOWN)                       # 60 s at 1 s polling
_OLD_MINUTE_BYTES = (UP / 2 * 60, DOWN / 2 * 60)
_OLD_HOUR_BYTES = (UP / 4 * 3600, DOWN / 4 * 3600)


@pytest.mark.parametrize("iface", [None, "all", NIC])
def test_data_cap_total_is_1x_when_minute_overlaps_raw(pair, iface):
    """The headline: a minute bucket copied (not moved) out of raw must not be billed twice."""
    moved, overlap = pair
    now = datetime.now()
    # >24 h back so the minute tier is read at all; 26 h reaches the raw minute and its copy but not
    # the older (30 h) minute bucket.
    start = now - timedelta(hours=26)
    truth = moved.get_total_bandwidth_for_period(start, now, iface)
    assert truth == _RAW_BYTES
    # Without precedence the copy is summed on top of the raw seconds: exactly 2.00x.
    legacy = Legacy.get_total_bandwidth_for_period(overlap, start, now, iface)
    assert legacy == (2 * _RAW_BYTES[0], 2 * _RAW_BYTES[1])
    got = overlap.get_total_bandwidth_for_period(start, now, iface)
    assert got == truth, f"data-cap total {got[1] / truth[1]:.2f}x the truth, must be 1.00x"


@pytest.mark.parametrize("iface", [None, NIC])
def test_data_cap_total_is_1x_across_all_three_tiers(pair, iface):
    """Both overlaps at once (minute over raw, minute over hour), over a window reaching every tier:
    each sub-range is answered by exactly one tier, and nothing below a finer tier's floor is lost."""
    moved, overlap = pair
    now = datetime.now()
    start = now - timedelta(days=60)
    expected = tuple(r + m + h for r, m, h in zip(_RAW_BYTES, _OLD_MINUTE_BYTES, _OLD_HOUR_BYTES))
    assert moved.get_total_bandwidth_for_period(start, now, iface) == expected
    legacy = Legacy.get_total_bandwidth_for_period(overlap, start, now, iface)
    assert legacy[1] > expected[1] * 1.5            # the double count precedence exists to stop
    assert overlap.get_total_bandwidth_for_period(start, now, iface) == expected


@pytest.mark.parametrize("days", [2, 60])
@pytest.mark.parametrize("direction", ["download", "upload"])
def test_network_summary_ignores_overlapping_copies(pair, direction, days):
    moved, overlap = pair
    now = datetime.now()
    start = now - timedelta(days=days)
    truth = moved.summarize_network(direction, start, now, None, 1.0)
    assert Legacy.summarize_network(overlap, direction, start, now, None, 1.0) != truth
    assert overlap.summarize_network(direction, start, now, None, 1.0) == truth


@pytest.mark.parametrize("days", [2, 60])
def test_hardware_summary_and_history_ignore_overlapping_copies(pair, days):
    moved, overlap = pair
    now = datetime.now()
    start = now - timedelta(days=days)
    truth = moved.summarize_hardware("cpu", start, now, 1.0)
    assert Legacy.summarize_hardware(overlap, "cpu", start, now, 1.0) != truth
    assert overlap.summarize_hardware("cpu", start, now, 1.0) == truth
    # (The binned series is AVG-based, and a faithful copy of a bucket is its own mean, so the legacy
    # series happens to survive a copy unchanged - no red assertion there, only the invariant.)
    truth_series = moved.get_hardware_history("cpu", start, now)
    assert overlap.get_hardware_history("cpu", start, now) == truth_series


@pytest.mark.parametrize("days, resolutions", [(2, ("minute", "hour", "day")), (60, ("hour", "day"))])
def test_speed_history_ignores_overlapping_copies(pair, days, resolutions):
    """(Peaks are MAX-merged, so the legacy graph was already immune to a copy; this pins that the
    precedence rule does not break it. Minute resolution never reads the hour tier, so over 60 days the
    overlap DB's minute copies would legitimately show finer detail than the move-only hour row.)"""
    moved, overlap = pair
    now = datetime.now()
    start = now - timedelta(days=days)
    for res in resolutions:
        truth = moved.get_speed_history(start, now, resolution=res, wait_for_flush=False)
        assert overlap.get_speed_history(start, now, resolution=res, wait_for_flush=False) == truth, res
