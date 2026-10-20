"""
End to end: a v7 file -> ``migrate_v8.migrate`` -> ``V8HistoryReader`` on the result, against
``V7HistoryReader`` on the original. The migrator stamps the watermarks (#359); this proves the reader
reads them the way the migrator means them.

They must agree everywhere except the two documented differences, each asserted explicitly:

1. **The last-24 h fold** (owner default 2): the network raw tier is folded into minute rows, so
   per-second network detail is gone - raw-resolution graphs and <= 24 h network summaries are empty
   on v8, and summaries read exactly like the original after v7's OWN raw -> minute roll-up (per
   interface: same avg / max / count, coverage counted in minute buckets; the all-interfaces aggregate
   takes v7's minute-tier weighting). A window opening mid-minute loses that minute's folded seconds
   (the bucket is stamped at its start). Graphs at minute / hour / day resolution are unchanged from
   minute-aligned starts (a minute's max IS its raw max).
2. **Volume:** v7 bills ``avg x 60 / 3600``; v8 the legacy estimate ``avg x sample_count x 1 s``,
   flagged ``reconstructed``. For the folded raw rows that estimate is exactly v7's raw sum.

Hardware raw rows are not folded, so every hardware read agrees exactly.
"""
import shutil
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from netspeedtray.core.db import migrate_v8 as M
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri
from netspeedtray.tests.unit import v8_twin_fixture as F
from netspeedtray.tests.unit.test_history_reader_v8_parity import _same

DAY = 86400
NOW = F.fixed_now()   # one clock for the history, the migration and the readers (v8_twin_fixture)


@pytest.fixture(scope="module")
def migrated(tmp_path_factory, q_app):
    d = tmp_path_factory.mktemp("e2e")
    (d / "orig").mkdir()
    (d / "live").mkdir()
    h = F.make_history(NOW)
    # A v7 file never holds bytes: the v8-native minutes of the twin fixture are plain v7 rows here.
    h.minute = [r[:7] + (None, None) for r in h.minute]
    # One raw row 2 minutes ahead of the clock (inside migrate_v8.FUTURE_SLACK_SECONDS, so it moves the
    # watermark): the stamped raw watermark then deterministically lies in the reader's future - the
    # shape a migration that ran at the end of a minute leaves. Outside every test window.
    h.raw.append((h.now_ts + 120, "Ethernet", 3, 4))
    F.write_v7(d / "orig" / "speed_history.db", h)
    shutil.copy2(d / "orig" / "speed_history.db", d / "live" / "speed_history.db")
    res = M.migrate(d / "live" / "speed_history.db", now=NOW)
    assert res.status == "migrated", res
    r7, r8, conns = F.readers(d / "orig" / "speed_history.db", d / "live" / "speed_history.db")
    yield h, r7, r8, d, res
    for c in conns:
        c.close()


def _windows(h):
    """Starts minute-aligned: a folded minute is stamped at its START, so a window opening mid-minute
    loses that minute's folded seconds (pinned separately by test_a_mid_minute_window_start...)."""
    now = datetime.fromtimestamp(h.now_ts)
    m = lambda dt: datetime.fromtimestamp(int(dt.timestamp()) // 60 * 60)
    out = [(m(now - dl), now) for dl in (timedelta(hours=6), timedelta(hours=20), timedelta(hours=26),
                                         timedelta(days=3), timedelta(days=11), timedelta(days=38),
                                         timedelta(days=90))]
    return out + [(m(now - timedelta(days=9)), m(now - timedelta(days=4))),
                  (m(now - timedelta(days=39)), m(now - timedelta(days=36)))]


def test_the_migrator_stamps_watermarks_the_reader_accepts(migrated):
    """The network raw watermark is the END of the newest folded minute - up to a minute past the
    migration's clock (+ FUTURE_SLACK_SECONDS clamp). The reader's allowed lead must cover it, or a
    freshly migrated file would read through the weaker fallback."""
    h, _, _, d, res = migrated
    assert S8.WATERMARK_MAX_LEAD_SECONDS >= M.FUTURE_SLACK_SECONDS + 60
    c = sqlite3.connect(sqlite_ro_uri(d / "live" / "speed_history.db"), uri=True)
    wm = dict(c.execute("SELECT key, CAST(value AS INTEGER) FROM metadata WHERE key IN (?, ?, ?, ?)",
                        S8.WATERMARK_KEYS).fetchall())
    c.close()
    assert set(wm) == set(S8.WATERMARK_KEYS)
    assert wm[S8.WATERMARK_RAW_KEY] == max(r[0] for r in h.raw) // 60 * 60 + 60
    assert wm[S8.HW_WATERMARK_RAW_KEY] == max(r[0] for r in h.hw_minute) // 60 * 60 + 60
    assert wm[S8.WATERMARK_MINUTE_KEY] == wm[S8.HW_WATERMARK_MINUTE_KEY] == max(r[0] for r in h.hour) // 3600 * 3600 + 3600
    for v in wm.values():
        assert 0 < v <= int(NOW.timestamp()) + S8.WATERMARK_MAX_LEAD_SECONDS


@pytest.mark.parametrize("resolution", ["minute", "hour", "day"])
@pytest.mark.parametrize("iface", [None, "Ethernet", "Wi-Fi 3"])
def test_graphs_agree_at_rollup_resolutions(migrated, resolution, iface):
    h, r7, r8, _, _ = migrated
    for s, e in _windows(h):
        _same(r7.speed_history(s, e, iface, True, resolution), r8.speed_history(s, e, iface, True, resolution),
              f"{s}..{e} {resolution}")


def test_raw_resolution_network_detail_is_folded_away(migrated):
    """Difference 1: the per-second network rows are now minute rows."""
    h, r7, r8, _, _ = migrated
    now = datetime.fromtimestamp(h.now_ts)
    s = now - timedelta(hours=6)
    assert any(p[2] for p in r7.speed_history(s, now, None, True, "raw"))
    assert not any(p[1] or p[2] for p in r8.speed_history(s, now, None, True, "raw"))
    assert r7.summarize_network("download", s, now).count > 0 and r8.summarize_network("download", s, now).count == 0


def test_a_mid_minute_window_start_loses_only_that_minutes_folded_seconds(migrated):
    """Difference 1, at the window edge: the folded bucket [m, m + 60) is stamped m, so a window that
    opens at m + 30 no longer sees the seconds [m + 30, m + 60) on v8. Exactly that, nothing more."""
    h, r7, r8, _, _ = migrated
    now = datetime.fromtimestamp(h.now_ts)
    m = (h.now_ts - 6 * 3600) // 60 * 60
    s = datetime.fromtimestamp(m + 30)
    lost = sum(r[3] for r in h.raw if m + 30 <= r[0] < m + 60)
    assert lost > 0
    assert r7.total_bandwidth(s, now, None, 1.0)[1] - r8.total_bandwidth(s, now, None, 1.0)[1] == pytest.approx(lost)


@pytest.fixture(scope="module")
def v7_rolled(migrated):
    """The ORIGINAL v7 file after v7's own maintenance rolled every network raw row into minutes - what
    the fold does, done by the code that defined the minute tier. (Hardware raw is left alone, as the
    migrator leaves it.)"""
    from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
    from netspeedtray.core.db.history_reader import V7HistoryReader
    h, _, _, d, _ = migrated
    path = d / "rolled" / "speed_history.db"
    path.parent.mkdir()
    shutil.copy2(d / "orig" / "speed_history.db", path)
    w = LegacyV7Worker(path)
    w._initialize_connection()
    w._aggregate_raw_to_minute(w.conn.cursor(), NOW + timedelta(days=2))
    w.conn.commit()
    w._close_connection()
    conn = sqlite3.connect(sqlite_ro_uri(path), uri=True, check_same_thread=False)
    yield V7HistoryReader(lambda: conn, lambda: path, clock=F.clock_at(NOW))
    conn.close()


@pytest.mark.parametrize("direction", ["download", "upload"])
@pytest.mark.parametrize("iface", [None, "Ethernet", "Wi-Fi 3"])
def test_network_summaries_read_like_v7_after_its_own_rollup(migrated, v7_rolled, direction, iface):
    """Difference 1, for summaries: the migrated file summarizes EXACTLY like the original after v7's own
    raw -> minute roll-up (coverage included). Against the un-rolled original, a single interface keeps
    avg / max / count over > 24 h and only the coverage arithmetic changes (minute buckets x 60 s instead
    of samples x poll); the all-interfaces aggregate also takes v7's minute-tier sample weighting
    (per-adapter sample counts summed per bucket) - max is unchanged."""
    h, r7, r8, _, _ = migrated
    for s, e in _windows(h):
        b = r8.summarize_network(direction, s, e, iface)
        _same(v7_rolled.summarize_network(direction, s, e, iface), b, f"rolled {s}..{e}")
        if (e - s).total_seconds() <= 24 * 3600:
            continue
        a = r7.summarize_network(direction, s, e, iface)
        if iface is None:
            assert a.max == b.max, f"{s}..{e}"
        else:
            _same(replace(a, coverage_pct=0.0), replace(b, coverage_pct=0.0), f"{s}..{e}")


def test_hardware_agrees_exactly(migrated):
    h, r7, r8, _, _ = migrated
    now = datetime.fromtimestamp(h.now_ts)
    for s, e in _windows(h) + [(now - timedelta(minutes=30), now)]:
        _same(r7.hardware_history("cpu", s, e), r8.hardware_history("cpu", s, e), f"{s}..{e}")
        for poll in (1.0, 5.0):
            _same(r7.summarize_hardware("cpu", s, e, poll), r8.summarize_hardware("cpu", s, e, poll), f"{s}..{e}")


def test_interfaces_earliest_and_odometer_agree(migrated):
    _, r7, r8, _, _ = migrated
    assert r8.distinct_interfaces() == r7.distinct_interfaces()
    assert r8.earliest_timestamp() == r7.earliest_timestamp()
    assert r8.load_usage() == r7.load_usage()


@pytest.mark.parametrize("iface", [None, "Ethernet"])
def test_volume_differs_only_by_the_documented_estimate(migrated, iface):
    """Difference 2, value by value. Folded raw rows: avg x n x 1 s == v7's SUM(rate) x 1 s exactly."""
    h, r7, r8, _, _ = migrated
    nic = iface
    for s, e in _windows(h):
        st, et = int(s.timestamp()), int(e.timestamp())
        pick = lambda rows: [r for r in rows if st <= r[0] <= et and (nic is None or r[1] == nic)]
        raw = sum(r[3] for r in pick(h.raw))
        est = sum(r[3] * min(r[6], 60) for r in pick(h.minute)) + sum(r[3] * min(r[6], 3600) for r in pick(h.hour))
        v7_rollup = (sum(r[3] * 60 for r in pick(h.minute)) if st < h.now_ts - DAY else 0) + \
                    (sum(r[3] * 3600 for r in pick(h.hour)) if st < h.now_ts - 30 * DAY else 0)
        assert r7.total_bandwidth(s, e, iface, 1.0)[1] == pytest.approx(raw + v7_rollup, rel=1e-12)
        vol = r8.volume(s, e, iface, 1.0)
        assert vol.down_bytes == pytest.approx(raw + est, rel=1e-12), f"{s}..{e}"
        assert vol.exact_down_bytes == 0 and vol.reconstructed is bool(raw + est)


def test_a_clock_set_back_after_migration_hides_nothing(migrated, tmp_path):
    """The first v8 write lands BEHIND the stamped raw watermark (clock set back 25.5 h). Read with the
    migrator's watermarks it is ignored and every read is unchanged; read through the fallback (as the
    reader did when it rejected the migrator's slightly-future stamp) it would become the floor of raw
    and hide the folded minutes above it."""
    h, _, r8, d, _ = migrated
    path = tmp_path / "speed_history.db"
    shutil.copy2(d / "live" / "speed_history.db", path)
    c = sqlite3.connect(path)
    iid = c.execute(f"SELECT iface_id FROM {S8.INTERFACES} WHERE name = 'Ethernet'").fetchone()[0]
    c.execute(f"INSERT INTO {S8.NET_RAW} (ts, iface_id, up_bytes, down_bytes, dt_ms) VALUES (?, ?, 7, 11, 1000)",
              (h.now_ts - int(25.5 * 3600), iid))
    c.commit()
    c.close()
    _, stray, conns = F.readers(path, path)
    now = datetime.fromtimestamp(h.now_ts)
    for s, e in _windows(h):
        assert stray.total_bandwidth(s, e, None, 1.0) == r8.total_bandwidth(s, e, None, 1.0)
        _same(stray.speed_history(s, e, None, True, "hour"), r8.speed_history(s, e, None, True, "hour"))
    for c in conns:
        c.close()
