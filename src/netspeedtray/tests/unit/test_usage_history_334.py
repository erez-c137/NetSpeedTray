"""
#334: the "Usage by day / month / year" table and "Back up history".

* ``HistoryReader.daily_volume`` - each day's figure is EXACTLY ``volume`` / ``metered_volume`` over that
  day's own window (same tiers, same watermarks, same scope), the days sum to the whole window, stray
  rows outside a tier's watermark range count nowhere, and pre-2.2 (estimated) days are flagged.
* Local days across DST: 23 h and 25 h days hold exactly their own rows (subprocess with TZ set, as
  ``test_rc_dst_buckets.py``).
* Roll-ups: calendar months, billing periods from the cap's reset day (the odometer's own period rule),
  years; the Today clamp the usage card applies with a cap set.
* The CSV: fixed header, whole bytes, ``estimated`` per row, oldest first.
* The backup: a valid, openable v8 copy that holds the WAL's uncheckpointed rows, made without writing
  to the live file; the live name refused; the backup-API fallback.
"""
import csv
import hashlib
import os
import sqlite3
import subprocess
import sys
import textwrap
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from netspeedtray.core import usage_history as U
from netspeedtray.core.db import history_backup as B
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.db.history_reader import DayVolume, MeteredScope, V7HistoryReader
from netspeedtray.tests.unit import v8_twin_fixture as F

NOW = F.fixed_now()
SRC = str(Path(__file__).resolve().parents[3])


@pytest.fixture(scope="module")
def twin(tmp_path_factory):
    """The shared v8 twin history (raw, v8-native minutes, migrated minutes + hours, watermarks) plus
    two strays the watermarks exclude: a minute row inside raw's range and an hour row inside minute's."""
    d = tmp_path_factory.mktemp("u334")
    h = F.make_history(NOW)
    p = d / "speed_history.db"
    w = F.write_v8(p, h)
    w.net_tier(S8.NET_MINUTE, [(h.wm_raw + 600, "Ethernet", 1.0, 1.0, 1.0, 1.0, 60, 9_999_999, 9_999_999)])
    w.net_tier(S8.NET_HOUR, [(h.wm_minute + 7200, "Ethernet", 1.0, 1.0, 1.0, 1.0, 3600, 7_777_777, 7_777_777)])
    w.close()
    _r7, r8, conns = F.readers(p, p)
    yield h, r8
    for c in conns:
        c.close()


def _day_window(d: DayVolume, days):
    """[the day's local midnight, the next local midnight - 1 s], whether or not that day holds rows."""
    from netspeedtray.core.db.history_reader import _local_day_start
    end = _local_day_start((d.day - date(1970, 1, 1)).days + 1) - 1
    return datetime.fromtimestamp(d.start_ts), datetime.fromtimestamp(end)


@pytest.mark.parametrize("scope", [None, MeteredScope(physical_only=True), MeteredScope(names=("Wi-Fi 3",))])
def test_each_day_equals_the_readers_volume_for_that_day(twin, scope):
    h, r8 = twin
    days = r8.daily_volume(None, NOW, scope)
    assert days and len(days) >= 10
    for d in days:
        v = r8.metered_volume(*_day_window(d, days), scope, 1.0)
        assert (d.up_bytes, d.down_bytes) == (v.up_bytes, v.down_bytes), d.day
        assert d.reconstructed == v.reconstructed, d.day
        assert datetime.fromtimestamp(d.start_ts).time().isoformat() == "00:00:00"
        assert datetime.fromtimestamp(d.start_ts).date() == d.day


def test_days_sum_to_the_window_and_strays_count_nowhere(twin):
    h, r8 = twin
    days = r8.daily_volume(None, NOW, None)
    whole = r8.volume(None, NOW, None, 1.0)
    assert sum(d.up_bytes for d in days) == whole.up_bytes
    assert sum(d.down_bytes for d in days) == whole.down_bytes
    assert 9_999_999 not in [d.down_bytes for d in days] and 7_777_777 not in [d.down_bytes for d in days]
    # A bounded window: the days inside it, and only those.
    start = NOW - timedelta(days=5)
    part = r8.daily_volume(start, NOW, None)
    assert sum(d.down_bytes for d in part) == r8.volume(start, NOW, None, 1.0).down_bytes


def test_pre_2_2_days_are_flagged_estimated(twin):
    h, r8 = twin
    by_day = {d.day: d for d in r8.daily_volume(None, NOW, None)}
    hour_days = {datetime.fromtimestamp(r[0]).date() for r in h.hour}
    raw_only = {datetime.fromtimestamp(t).date() for t, *_ in h.raw} - \
        {datetime.fromtimestamp(r[0]).date() for r in h.minute}
    assert hour_days and all(by_day[d].reconstructed for d in hour_days)        # migrated hours: bytes NULL
    for d in raw_only:
        assert not by_day[d].reconstructed                                       # raw rows: exact bytes


def test_empty_selection_and_v7_backend():
    r = V7HistoryReader(lambda: None, lambda: None)
    assert r.daily_volume(None, NOW, None) is None          # v7 (deferred upgrade): "not available"
    from netspeedtray.core.db.history_reader import V8HistoryReader
    r8 = V8HistoryReader(lambda: (_ for _ in ()).throw(AssertionError("no read")), lambda: None)
    assert r8.daily_volume(None, NOW, MeteredScope(names=())) == []


def test_today_row_equals_the_usage_cards_today(tmp_path):
    """The usage card's Today = metered_volume(local midnight, now) over the metered scope."""
    from netspeedtray.core.db import writer_v8 as W
    from netspeedtray.core.db.history_reader import V8HistoryReader
    now = int(datetime(2026, 10, 21, 12, 0).timestamp())
    p = tmp_path / "h.db"
    c = sqlite3.connect(p)
    S8.create_v8_schema(c)
    c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")
    c.commit()
    w = W.WriterV8(c, version="t", poll_ms=1000, is_physical=lambda n: n == "Ethernet")
    for i in range(0, 20 * 3600, 60):    # yesterday 16:00 -> today 12:00, across midnight
        w.add_net_sample(W.NetSample(ts=now - 20 * 3600 + i, dt_ms=1000, byte_deltas={
            "Ethernet": (100, 1_000), "ProtonVPN": (100, 1_000)}))
    w.flush(now)
    c.close()
    r = V8HistoryReader(lambda: sqlite3.connect(p), lambda: p, clock=lambda: now)
    scope = MeteredScope(physical_only=True)
    now_dt = datetime.fromtimestamp(now)
    midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    card = r.metered_volume(midnight, now_dt, scope, 1.0)
    days = r.daily_volume(None, now_dt, scope)
    today = [d for d in days if d.day == now_dt.date()][0]
    assert (today.up_bytes, today.down_bytes) == (card.up_bytes, card.down_bytes)
    assert today.down_bytes == 12 * 60 * 1_000            # Ethernet only: the VPN is not metered
    assert len(days) == 2


# ------------------------------------------------------------------ DST (subprocess per zone)

_DST_PROBE = textwrap.dedent(r"""
    import sqlite3, sys, time, json
    from datetime import date, datetime, timedelta
    sys.path.insert(0, sys.argv[1])
    from netspeedtray.core.db import schema_v8 as S8
    from netspeedtray.core.db.history_reader import V8HistoryReader
    p = sys.argv[2]
    c = sqlite3.connect(p)
    S8.create_v8_schema(c)
    c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")
    c.execute(f"INSERT INTO {S8.INTERFACES} (iface_id, stable_key, name, is_physical, first_seen, last_seen) "
              "VALUES (1, 'k', 'Ethernet', 1, 0, 0)")
    out = {}
    for y, m, dd in ((2026, 3, 8), (2026, 11, 1)):          # EST5EDT: spring forward / fall back
        a = int((datetime(y, m, dd) - timedelta(days=1)).timestamp())
        b = int((datetime(y, m, dd) + timedelta(days=2)).timestamp())
        # Old days in the HOUR tier with NULL bytes (estimated), one row per real hour: 1 B/s x 3600.
        c.executemany(f"INSERT INTO {S8.NET_HOUR} (ts, iface_id, up_avg, down_avg, up_max, down_max, "
                      "sample_count, up_bytes, down_bytes) VALUES (?, 1, 1, 1, 1, 1, 3600, NULL, NULL)",
                      [(t,) for t in range(a, b, 3600)])
    c.commit()
    now = int(datetime(2026, 12, 1).timestamp())
    r = V8HistoryReader(lambda: c, lambda: p, clock=lambda: now)
    days = r.daily_volume(None, datetime.fromtimestamp(now), None)
    print(json.dumps([[d.day.isoformat(), d.down_bytes, d.reconstructed,
                       datetime.fromtimestamp(d.start_ts).isoformat()] for d in days]))
""")


def test_daily_volume_across_dst_days(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(_DST_PROBE, encoding="utf-8")
    env = dict(os.environ, TZ="EST5EDT")
    out = subprocess.run([sys.executable, str(probe), SRC, str(tmp_path / "dst.db")], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    import json
    rows = {d: (b, rec, start) for d, b, rec, start in json.loads(out.stdout)}
    assert rows["2026-03-08"][0] == 23 * 3600          # the 23 h day: 23 hour rows
    assert rows["2026-11-01"][0] == 25 * 3600          # the 25 h day: 25 hour rows (01:00 twice)
    for d in ("2026-03-07", "2026-03-09", "2026-10-31", "2026-11-02"):
        assert rows[d][0] == 24 * 3600, d
    assert all(rec for _b, rec, _s in rows.values())   # hour rows with NULL bytes: estimated
    assert all(s.endswith("T00:00:00") for _b, _r, s in rows.values())


# ------------------------------------------------------------------ roll-ups, clamp, CSV

def _dv(d: date, up=1.0, down=10.0, rec=False):
    return DayVolume(d, 0, up, down, rec)


def _days(first: date, n: int, **kw):
    return U.day_rows([_dv(first + timedelta(days=i), **kw) for i in range(n)])


def test_calendar_months_and_years():
    days = _days(date(2025, 12, 20), 50)                # 20 Dec 2025 -> 7 Feb 2026
    months = U.month_rows(days)
    assert [m.key for m in months] == ["2025-12", "2026-01", "2026-02"]
    assert [m.down_bytes for m in months] == [120.0, 310.0, 70.0]
    assert months[1].start == date(2026, 1, 1) and months[1].end == date(2026, 1, 31)
    years = U.year_rows(days)
    assert [(y.key, y.down_bytes) for y in years] == [("2025", 120.0), ("2026", 380.0)]
    assert sum(m.down_bytes for m in months) == sum(d.down_bytes for d in days)


def test_billing_periods_follow_the_reset_day():
    days = _days(date(2025, 12, 10), 40)                # 10 Dec -> 18 Jan
    periods = U.month_rows(days, reset_day=15)
    assert [(p.start, p.end) for p in periods] == [
        (date(2025, 11, 15), date(2025, 12, 14)), (date(2025, 12, 15), date(2026, 1, 14)),
        (date(2026, 1, 15), date(2026, 2, 14))]
    assert [p.down_bytes for p in periods] == [50.0, 310.0, 40.0]
    assert periods[1].key == "2025-12-15/2026-01-14" and periods[1].kind == U.KIND_PERIOD


def test_billing_period_matches_the_odometers_period_key():
    from netspeedtray.core.widget_state import WidgetState
    for d in (date(2026, 3, 1), date(2026, 3, 27), date(2026, 3, 28), date(2026, 12, 31)):
        p = U.month_rows(U.day_rows([_dv(d)]), reset_day=28)[0]
        assert p.start.isoformat() == WidgetState._compute_period_key(28, d)
        assert p.start <= d <= p.end


@pytest.mark.parametrize("config, expected", [
    ({}, None),
    ({"data_cap_enabled": True, "data_cap_gb": 100, "data_cap_reset_day": 1}, None),
    ({"data_cap_enabled": True, "data_cap_gb": 100, "data_cap_reset_day": 15}, 15),
    ({"data_cap_enabled": False, "data_cap_gb": 100, "data_cap_reset_day": 15}, None),
    ({"data_cap_enabled": True, "data_cap_gb": 0, "data_cap_reset_day": 15}, None),
    ({"data_cap_enabled": True, "data_cap_gb": 100, "data_cap_reset_day": "x"}, None),
])
def test_billing_reset_day_is_the_usage_cards_rule(config, expected):
    assert U.billing_reset_day(config) == expected


def test_estimated_flag_rolls_up():
    days = U.day_rows([_dv(date(2026, 1, 30), rec=True), _dv(date(2026, 1, 31)), _dv(date(2026, 2, 1))])
    m = U.month_rows(days)
    assert [x.estimated for x in m] == [True, False]
    assert U.year_rows(days)[0].estimated


def test_today_clamp_matches_the_card():
    t = date(2026, 10, 21)
    days = [_dv(t - timedelta(days=1), 5e9, 50e9), _dv(t, 3e9, 30e9)]
    out = U.clamp_day(days, t, (1e9, 40e9))
    assert (out[1].up_bytes, out[1].down_bytes) == (1e9, 30e9)
    assert out[0] == days[0]
    assert U.clamp_day(days, t, None) == days


def test_csv_format(tmp_path):
    rows = U.day_rows([_dv(date(2026, 5, 22), 7e9 + 0.4, 5e9, rec=True), _dv(date(2026, 5, 21), 1.0, 2.0)])
    p = tmp_path / "u.csv"
    U.write_csv(p, list(reversed(rows)))
    raw = p.read_bytes()
    assert raw.startswith(b"date,download_bytes,upload_bytes,total_bytes,estimated\r\n")
    got = list(csv.reader(p.open(encoding="utf-8")))
    assert got[1:] == [["2026-05-21", "2", "1", "3", "false"],
                       ["2026-05-22", "5000000000", "7000000000", "12000000000", "true"]]
    U.write_csv(p, U.month_rows(rows) + U.year_rows(rows))
    keys = [r[0] for r in csv.reader(p.open(encoding="utf-8"))][1:]
    assert set(keys) == {"2026-05", "2026"}
    U.write_csv(p, U.month_rows(rows, reset_day=15))
    assert [r[0] for r in csv.reader(p.open(encoding="utf-8"))][1] == "2026-05-15/2026-06-14"


# ------------------------------------------------------------------ backup

def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


@pytest.fixture()
def live(tmp_path):
    """A v8 history in WAL mode with rows that exist only in the -wal (checkpointing off), and the
    writer's connection still open - the running app's state."""
    d = tmp_path / "app#data"            # a '#' in the path: the read-only URI must stay encoded
    d.mkdir()
    p = d / "speed_history.db"
    h = F.make_history(NOW)
    w = F.write_v8(p, h)
    w.conn.execute("PRAGMA wal_autocheckpoint=0")
    w.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    w.net_raw([(h.now_ts - 1, "Ethernet", 4242, 424242)])          # only in the WAL
    w.conn.commit()
    yield p, w
    w.close()


def test_backup_is_a_valid_v8_copy_with_the_wal_rows(live, tmp_path):
    p, w = live
    assert Path(str(p) + "-wal").stat().st_size > 0
    before = (_digest(p), _digest(Path(str(p) + "-wal")), sorted(x.name for x in p.parent.iterdir()))
    dest = tmp_path / "out" / "NetSpeedTray-history-2026-10-23.db"
    dest.parent.mkdir()
    steps = []
    res = B.backup_history(p, dest, progress=steps.append)
    assert steps == [B.STEP_COPY, B.STEP_VERIFY]
    assert res.path == dest and res.db_version == 8 and res.method == "vacuum_into"
    assert res.size_bytes == dest.stat().st_size > 0
    # Nothing written to the live file or its folder.
    assert (_digest(p), _digest(Path(str(p) + "-wal")), sorted(x.name for x in p.parent.iterdir())) == before
    assert sorted(x.name for x in dest.parent.iterdir()) == [dest.name]   # no temp, no -wal/-shm
    c = sqlite3.connect(dest)
    try:
        assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert set(S8.EXPECTED_TABLES_V8) <= {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        assert c.execute(f"SELECT COUNT(*) FROM {S8.NET_RAW} WHERE down_bytes = 424242").fetchone()[0] == 1
        for t in (S8.NET_RAW, S8.NET_MINUTE, S8.NET_HOUR, "usage_counter"):
            src = w.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            assert c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == src, t
    finally:
        c.close()
    # The copy reads as history: the reader's volume over it equals the live file's.
    _r7, r_copy, conns = F.readers(dest, dest)
    _r7b, r_live, conns2 = F.readers(p, p)
    try:
        assert r_copy.volume(None, NOW, None, 1.0) == r_live.volume(None, NOW, None, 1.0)
    finally:
        for x in conns + conns2:
            x.close()
    # Again over the same name: replaced, still valid.
    B.backup_history(p, dest)
    assert sorted(x.name for x in dest.parent.iterdir()) == [dest.name]


@pytest.mark.parametrize("name", ["speed_history.db", "speed_history.db-wal", "SPEED_HISTORY.db.bak.v8_x"])
def test_backup_refuses_the_live_name(live, name):
    p, _w = live
    before = _digest(p)
    with pytest.raises(B.BackupError):
        B.backup_history(p, p.parent / name)
    assert _digest(p) == before


def test_backup_without_a_history_file(tmp_path):
    with pytest.raises(B.BackupError):
        B.backup_history(tmp_path / "speed_history.db", tmp_path / "x.db")
    assert not (tmp_path / "speed_history.db").exists()      # the read-only open never creates it
    assert list(tmp_path.iterdir()) == []


def test_backup_falls_back_to_the_backup_api(live, tmp_path, monkeypatch):
    p, _w = live
    real_connect = sqlite3.connect

    class _NoVacuumInto:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *a):
            if sql.strip().upper().startswith("VACUUM INTO"):
                raise sqlite3.OperationalError("attempt to write a readonly database")
            return self._c.execute(sql, *a)

        def __getattr__(self, n):
            return getattr(self._c, n)

    def connect(target, *a, **kw):
        conn = real_connect(target, *a, **kw)
        return _NoVacuumInto(conn) if kw.get("uri") and str(target).endswith("mode=ro") and \
            "speed_history" in str(target) else conn

    monkeypatch.setattr(B.sqlite3, "connect", connect)
    dest = tmp_path / "fb.db"
    res = B.backup_history(p, dest)
    assert res.method == "backup_api"
    monkeypatch.undo()
    c = sqlite3.connect(dest)
    try:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert c.execute(f"SELECT COUNT(*) FROM {S8.NET_RAW} WHERE down_bytes = 424242").fetchone()[0] == 1
    finally:
        c.close()
    assert not Path(str(dest) + "-wal").exists()


def test_failed_backup_leaves_nothing(live, tmp_path, monkeypatch):
    p, _w = live
    dest = tmp_path / "keep.db"
    dest.write_bytes(b"old")
    monkeypatch.setattr(B, "_verify", lambda *a: (_ for _ in ()).throw(B.BackupError("bad copy")))
    with pytest.raises(B.BackupError):
        B.backup_history(p, dest)
    assert dest.read_bytes() == b"old"                       # the previous file under that name survives
    assert sorted(x.name for x in tmp_path.iterdir() if x.name.startswith("keep")) == ["keep.db"]


def test_default_name_never_matches_the_apps_backup_matcher():
    n = B.default_backup_name(date(2026, 10, 23))
    assert n == "NetSpeedTray-history-2026-10-23.db"
    assert not n.lower().startswith("speed_history.db")
