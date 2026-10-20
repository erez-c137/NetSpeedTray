"""
The tier-precedence boundary must be robust (PR #357 review):

1. **A stray old row must not hide history.** The writer stamps rows with ``datetime.now()``, so a clock
   set back, a restored VM snapshot or a dead CMOS battery can land ONE raw row days - or decades -
   behind the rest. A boundary of "MIN(timestamp) over the whole finer table" would then make that row
   the floor of raw and hide every minute/hour row above it (measured by the reviewer: a 31-day total of
   26.76 GB read as 0.86 GB after one row dated 2000-01-01). The boundary only considers finer rows
   inside that tier's retention horizon (+ slack), so a stray row costs exactly its own seconds.

2. **One consistent snapshot per read.** A maintenance pass that commits between two statements of
   one read (boundary vs main query, or minute query vs raw query) must not drop or double-count the
   slice it moved. Each reader runs inside one read transaction; the boundary is a subquery of the
   statement it bounds.

The fixture is written the way production writes it - persist batches through ``DatabaseWorker`` and
its real maintenance roll-up - and the stray rows go in through the same persist path afterwards.
"""
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from netspeedtray import constants
from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
from netspeedtray.core.db.history_reader import V7HistoryReader
from netspeedtray.core.widget_state import WidgetState

DAY = 86400
NIC = "Ethernet"
STRAY_UP, STRAY_DOWN = 7.0, 11.0


def _build(db_dir: Path, now: datetime) -> None:
    """10 days of minute history + 20 h of raw + an hour-tier stretch 35-40 days back."""
    rng = random.Random(357)
    now_ts = int(now.timestamp())
    speed, hw = [], []
    for a, b, step in ((40 * DAY, 35 * DAY, 300), (10 * DAY, 20 * 3600, 120), (20 * 3600, 5, 15)):
        for t in range(now_ts - a, now_ts - b, step):
            speed.append((t, NIC, float(rng.randint(1, 500)), float(rng.randint(1, 5000))))
            hw.append((t, "cpu", float(rng.randint(0, 100))))
    w = LegacyV7Worker(db_dir / "speed_history.db")
    w._initialize_connection()
    w._check_and_create_schema()
    w._persist_speed_batch(speed)
    w._persist_hardware_batch(hw)
    w._run_maintenance(dict(constants.config.defaults.DEFAULT_CONFIG), now)
    w._close_connection()


def _add_stray(db_dir: Path, stray_ts: int) -> None:
    w = LegacyV7Worker(db_dir / "speed_history.db")
    w._initialize_connection()
    w._persist_speed_batch([(stray_ts, NIC, STRAY_UP, STRAY_DOWN)])
    w._persist_hardware_batch([(stray_ts, "cpu", 50.0)])
    w._close_connection()


def _state(db_dir: Path) -> WidgetState:
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg["update_rate"] = 1.0
    with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=db_dir):
        return WidgetState(cfg, read_only=True)


STRAYS = {
    "3_days_ago": lambda now: int((now - timedelta(days=3)).timestamp()),
    "year_2000": lambda now: int(datetime(2000, 1, 1, 12, 0, 0).timestamp()),
}


@pytest.fixture(params=sorted(STRAYS))
def clean_and_stray(request, q_app, tmp_path):
    now = datetime.now()
    clean_dir, stray_dir = tmp_path / "clean", tmp_path / "stray"
    for d in (clean_dir, stray_dir):
        d.mkdir()
        _build(d, now)
    stray_ts = STRAYS[request.param](now)
    _add_stray(stray_dir, stray_ts)
    clean, stray = _state(clean_dir), _state(stray_dir)
    yield clean, stray, stray_ts
    clean.cleanup()
    stray.cleanup()


def _assert_superset_plus_one(clean, stray, what):
    """Every bin of the clean series is in the stray series, unchanged; at most one bin is new (the
    stray row's own)."""
    clean_bins, stray_bins = dict((p[0], p[1:]) for p in clean), dict((p[0], p[1:]) for p in stray)
    missing = [k for k in clean_bins if k not in stray_bins]
    assert not missing, f"{what}: {len(missing)} of {len(clean_bins)} bins hidden by one stray row"
    changed = [k for k in clean_bins if stray_bins[k] != clean_bins[k]]
    assert len(changed) <= 1, f"{what}: {len(changed)} bins changed"
    assert len(stray_bins) - len(clean_bins) <= 1, what


@pytest.mark.parametrize("iface", [None, NIC])
@pytest.mark.parametrize("days", [7, 31])
def test_one_stray_raw_row_costs_only_its_own_bytes(clean_and_stray, iface, days):
    clean, stray, stray_ts = clean_and_stray
    now = datetime.now()
    start = now - timedelta(days=days)
    in_window = stray_ts >= int(start.timestamp())
    expected = clean.get_total_bandwidth_for_period(start, now, iface)
    assert expected[1] > 1e6
    if in_window:
        expected = (expected[0] + STRAY_UP, expected[1] + STRAY_DOWN)
    assert stray.get_total_bandwidth_for_period(start, now, iface) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("days, res", [(7, "hour"), (7, "day"), (2, "minute"), (31, "hour")])
def test_one_stray_raw_row_hides_no_graph_bins(clean_and_stray, days, res):
    clean, stray, _ = clean_and_stray
    now = datetime.now()
    start = now - timedelta(days=days)
    c = clean.get_speed_history(start, now, resolution=res, wait_for_flush=False)
    assert len(c) > 5
    _assert_superset_plus_one(c, stray.get_speed_history(start, now, resolution=res, wait_for_flush=False),
                              f"speed {days}d/{res}")


@pytest.mark.parametrize("days", [2, 7, 31])
def test_one_stray_hardware_row_hides_no_cpu_bins(clean_and_stray, days):
    clean, stray, _ = clean_and_stray
    now = datetime.now()
    start = now - timedelta(days=days)
    c = clean.get_hardware_history("cpu", start, now)
    assert len(c) > 20
    _assert_superset_plus_one(c, stray.get_hardware_history("cpu", start, now), f"cpu {days}d")
    cs, ss = clean.summarize_hardware("cpu", start, now, 1.0), stray.summarize_hardware("cpu", start, now, 1.0)
    assert ss.count - cs.count in (0, 1)


@pytest.mark.parametrize("days", [2, 7, 31])
def test_one_stray_raw_row_leaves_the_network_summary_alone(clean_and_stray, days):
    clean, stray, _ = clean_and_stray
    now = datetime.now()
    start = now - timedelta(days=days)
    cs = clean.summarize_network("download", start, now, None, 1.0)
    ss = stray.summarize_network("download", start, now, None, 1.0)
    assert ss.count - cs.count in (0, 1)
    assert ss.max == cs.max


# --- one snapshot per read ---------------------------------------------------------------------------

class _MaintenanceMidRead:
    """A read connection that, right after the read's FIRST SELECT, lets a maintenance pass commit on
    another connection - rolling every raw row (speed and hardware) into minute - before the read's
    remaining statements run. The worst-case interleaving of the hourly roll-up with a Monitor read."""

    def __init__(self, conn: sqlite3.Connection, db_path: Path):
        self._c, self._db_path, self.fired = conn, db_path, False

    def __getattr__(self, name):
        return getattr(self._c, name)

    def _after(self, sql: str) -> None:
        if not self.fired and sql.lstrip().upper().startswith(("SELECT", "WITH")):
            self.fired = True
            w = LegacyV7Worker(self._db_path)
            w._initialize_connection()
            cur = w.conn.cursor()
            later = datetime.now() + timedelta(days=2)
            w._aggregate_raw_to_minute(cur, later)
            w._aggregate_hardware_raw_to_minute(cur, later)
            w.conn.commit()
            w._close_connection()

    def execute(self, sql, *a):
        r = self._c.execute(sql, *a)
        self._after(sql)
        return r

    def cursor(self):
        outer, cur = self, self._c.cursor()

        class _Cur:
            def __getattr__(self, name):
                return getattr(cur, name)

            def execute(self, sql, *a):
                cur.execute(sql, *a)
                outer._after(sql)
                return self

            def fetchall(self):
                return cur.fetchall()

            def fetchone(self):
                return cur.fetchone()
        return _Cur()


@pytest.fixture
def db_dir(q_app, tmp_path):
    _build(tmp_path, datetime.now())
    return tmp_path


def _reader(db_dir: Path, interfere: bool):
    path = db_dir / "speed_history.db"
    conn = sqlite3.connect(path, check_same_thread=False)
    wrapped = _MaintenanceMidRead(conn, path) if interfere else conn
    return V7HistoryReader(lambda: wrapped, lambda: path), conn


@pytest.mark.parametrize("call", ["total", "summarize_network", "summarize_hardware", "speed_history",
                                  "hardware_history"])
def test_a_maintenance_commit_mid_read_changes_nothing(db_dir, call):
    now = datetime.now()
    start = now - timedelta(days=7)
    calls = {
        "total": lambda r: r.total_bandwidth(start, now, None, 1.0),
        "summarize_network": lambda r: r.summarize_network("download", start, now, None, 1.0),
        "summarize_hardware": lambda r: r.summarize_hardware("cpu", start, now, 1.0),
        "speed_history": lambda r: r.speed_history(start, now, None, True, "hour"),
        "hardware_history": lambda r: r.hardware_history("cpu", start, now),
    }
    quiet, qc = _reader(db_dir, interfere=False)
    expected = calls[call](quiet)
    qc.close()
    noisy, nc = _reader(db_dir, interfere=True)
    got = calls[call](noisy)
    nc.close()
    assert got == expected, f"{call}: a roll-up committed mid-read changed the result"
