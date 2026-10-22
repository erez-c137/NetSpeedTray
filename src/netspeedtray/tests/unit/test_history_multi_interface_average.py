"""
The all-interfaces Avg over minute / hour rollups (both readers, summary and hourly profile).

A rollup bucket holds one row per interface. The old aggregate took SUM(avg) across them and weighed it
by SUM(sample_count): the rates do add, but the interfaces' seconds overlap, so SUM(avg) x
SUM(sample_count) exceeds the bucket's real bytes whenever two interfaces moved data in it, and every
"all" Avg read high (the owner's 2.2 history: true 0.0770 Mbps, v8 read 0.0809, v7 0.0790). A bucket is
now its bytes / MAX(sample_count), weighed by MAX(sample_count). A single interface is unchanged.
"""
import random
import shutil
from datetime import datetime, timedelta

import pytest

from netspeedtray.core.db import migrate_v8 as M
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.tests.unit import v8_twin_fixture as F

DAY = 86400
NOW = F.fixed_now()
NOW_TS = int(NOW.timestamp())
# Two minutes of the same local hour, 3 days back (past every reader's raw horizon).
H0 = (NOW_TS - 3 * DAY) // 3600 * 3600
A, B = H0 + 60, H0 + 120

# (ts, nic, avg B/s, seconds): A - Wi-Fi 1000 B/s + vEthernet 1 B/s, the same 60 s; B - Wi-Fi 3000 B/s.
ROWS = [(A, "Wi-Fi", 1000.0, 60), (A, "vEthernet", 1.0, 60), (B, "Wi-Fi", 3000.0, 60)]
TRUE_AVG = (1000 * 60 + 1 * 60 + 3000 * 60) / 120   # 2000.5 B/s over the 120 s measured
OLD_AVG = ((1000 + 1) * 120 + 3000 * 60) / 180      # 1667.3: what SUM(avg) / SUM(count) read


def _minute_rows(rows, exact: bool, bytes_drift: int = 0):
    """v8 net_minute rows (ts, nic, up_avg, down_avg, up_max, down_max, count, up_bytes, down_bytes):
    the download carries the speed, the upload a tenth of it."""
    out = []
    for ts, nic, avg, sc in rows:
        b = (lambda r: int(r * sc) + bytes_drift) if exact else (lambda r: None)
        out.append((ts, nic, avg / 10, avg, avg / 5, avg * 2, sc, b(avg / 10), b(avg)))
    return out


def _write(tmp_path, rows, exact: bool, bytes_drift: int = 0):
    h = F.History(now_ts=NOW_TS, minute=_minute_rows(rows, exact, bytes_drift))
    h.snapshot = None
    F.write_v7(tmp_path / "v7.db", h)
    w = F.V8Writer(tmp_path / "v8.db")
    w.net_tier(S8.NET_MINUTE, h.minute)
    # Raw answers [now - 1 h, inf), minute [10 d back, now - 1 h): the rows are minute-tier rows.
    w.watermarks((NOW_TS - 3600) // 60 * 60, (NOW_TS - 10 * DAY) // 3600 * 3600)
    w.close()
    return F.readers(tmp_path / "v7.db", tmp_path / "v8.db", now=NOW)


@pytest.fixture(params=["v8 exact bytes", "v8 legacy (migrated) rows"])
def two_buckets(request, tmp_path, q_app):
    r7, r8, conns = _write(tmp_path, ROWS, exact=request.param == "v8 exact bytes")
    yield r7, r8
    for c in conns:
        c.close()


WINDOW = (datetime.fromtimestamp(NOW_TS - 5 * DAY), NOW)


@pytest.mark.parametrize("which", ["v7", "v8"])
@pytest.mark.parametrize("iface", [None, "all"])
def test_all_interfaces_avg_is_bytes_over_measured_seconds(two_buckets, which, iface):
    r = dict(zip(("v7", "v8"), two_buckets))[which]
    s = r.summarize_network("download", *WINDOW, iface)
    assert s.avg == pytest.approx(TRUE_AVG) and s.avg != pytest.approx(OLD_AVG)
    assert s.count == 120                     # measured seconds, not 60 + 60 + 60 per-NIC rows
    assert s.max == 6000.0                    # maxima unaffected: the largest per-NIC peak
    assert r.summarize_network("upload", *WINDOW, iface).avg == pytest.approx(TRUE_AVG / 10)


@pytest.mark.parametrize("which", ["v7", "v8"])
def test_hourly_profile_averages_back_to_the_summary(two_buckets, which):
    r = dict(zip(("v7", "v8"), two_buckets))[which]
    hour = datetime.fromtimestamp(H0).hour
    prof = r.network_hourly_profile("download", *WINDOW, None)
    assert prof == {hour: pytest.approx(TRUE_AVG)}
    assert prof[hour] == pytest.approx(r.summarize_network("download", *WINDOW, None).avg)


@pytest.mark.parametrize("which", ["v7", "v8"])
def test_hourly_profile_splits_buckets_by_hour(tmp_path, q_app, which):
    rows = [(A, "Wi-Fi", 1000.0, 60), (A, "vEthernet", 1.0, 60), (B + 3600, "Wi-Fi", 3000.0, 60)]
    r7, r8, conns = _write(tmp_path, rows, exact=True)
    try:
        r = r7 if which == "v7" else r8
        h0, h1 = (datetime.fromtimestamp(t).hour for t in (A, B + 3600))
        prof = r.network_hourly_profile("download", *WINDOW, None)
        assert prof == {h0: pytest.approx(1001.0), h1: pytest.approx(3000.0)}
    finally:
        for c in conns:
            c.close()


@pytest.mark.parametrize("which", ["v7", "v8"])
def test_a_single_interface_is_unchanged(two_buckets, which):
    """Regression guard: one row per bucket reads exactly its stored avg and count, as before."""
    r = dict(zip(("v7", "v8"), two_buckets))[which]
    wifi = r.summarize_network("download", *WINDOW, "Wi-Fi")
    assert (wifi.avg, wifi.count, wifi.max) == (2000.0, 120, 6000.0)
    veth = r.summarize_network("download", *WINDOW, "vEthernet")
    assert (veth.avg, veth.count, veth.max) == (1.0, 60, 2.0)
    assert r.network_hourly_profile("download", *WINDOW, "Wi-Fi") == {datetime.fromtimestamp(H0).hour: 2000.0}


def test_v8_rows_read_their_stored_avg_not_bytes_over_seconds(tmp_path, q_app):
    """A v8 row's sample_count is ROUND(seconds) and its avg the exact time-weighted rate, so bytes /
    sample_count can drift from avg by the rounding. Every row - alone or beside another interface -
    reads its stored avg; stored bytes are volume()'s."""
    _, r8, conns = _write(tmp_path, ROWS, exact=True, bytes_drift=17)
    try:
        assert r8.summarize_network("download", *WINDOW, "Wi-Fi").avg == 2000.0
        assert r8.summarize_network("download", *WINDOW, None).avg == pytest.approx(TRUE_AVG)
    finally:
        for c in conns:
            c.close()


def test_a_standby_gap_minute_shared_by_two_interfaces_stays_at_its_average(tmp_path, q_app):
    """writer_v8 rolls a FLAG_GAP raw row (Modern Standby: 500 MB over a 7259 s interval) into its
    minute with ALL the gap's bytes, avg = bytes / 7259 s and sample_count capped at 60. Beside a
    second interface (vEthernet, 60 B) that minute must read the gap row's own average, as Wi-Fi alone
    does - not 500 MB / 60 s (review of #413: 'all' read 4,168,167 B/s against 35,940 for Wi-Fi)."""
    gap, dt = 500_000_000, 7259
    rows = [(A, "Wi-Fi", 0.0, gap / dt, 0.0, gap / dt, 60, 0, gap),
            (A, "vEthernet", 0.0, 1.0, 0.0, 1.0, 60, 0, 60),
            (B, "Wi-Fi", 0.0, 3000.0, 0.0, 3000.0, 60, 0, 180000)]
    h = F.History(now_ts=NOW_TS, minute=rows)
    h.snapshot = None
    F.write_v7(tmp_path / "v7.db", h)
    w = F.V8Writer(tmp_path / "v8.db")
    w.net_tier(S8.NET_MINUTE, h.minute)
    w.watermarks((NOW_TS - 3600) // 60 * 60, (NOW_TS - 10 * DAY) // 3600 * 3600)
    w.close()
    r7, r8, conns = F.readers(tmp_path / "v7.db", tmp_path / "v8.db", now=NOW)
    try:
        expected = ((gap / dt + 1.0) * 60 + 3000.0 * 60) / 120
        for r in (r7, r8):
            assert r.summarize_network("download", *WINDOW, None).avg == pytest.approx(expected)
            prof = r.network_hourly_profile("download", *WINDOW, None)
            assert prof == {datetime.fromtimestamp(H0).hour: pytest.approx(expected)}
        wifi = r8.summarize_network("download", *WINDOW, "Wi-Fi").avg
        assert wifi == pytest.approx((gap / dt + 3000.0) / 2)
        assert r8.volume(*WINDOW, None, 1.0).down_bytes == gap + 60 + 180000   # volume keeps the bytes
    finally:
        for c in conns:
            c.close()


# --- a real migration: the v7 raw path vs the v8 summary of the folded minutes ----------------------

@pytest.fixture(scope="module")
def migrated_day(tmp_path_factory, q_app):
    """6 h of v7 raw rows, two interfaces with overlapping but different active seconds (the v7 writer
    skips an idle second per NIC), folded into minutes by the real migrator."""
    rng = random.Random(411)
    d = tmp_path_factory.mktemp("multi_iface")
    (d / "orig").mkdir()
    (d / "live").mkdir()
    start = (NOW_TS - 6 * 3600) // 60 * 60
    h = F.History(now_ts=NOW_TS)
    h.snapshot = None
    for t in range(start, NOW_TS - 10):
        if rng.random() < 0.9:
            h.raw.append((t, "Wi-Fi", rng.randint(1, 300), rng.randint(1, 30000)))
        if rng.random() < 0.3:
            h.raw.append((t, "vEthernet", rng.randint(1, 30), rng.randint(1, 3000)))
    F.write_v7(d / "orig" / "speed_history.db", h)
    shutil.copy2(d / "orig" / "speed_history.db", d / "live" / "speed_history.db")
    res = M.migrate(d / "live" / "speed_history.db", now=NOW)
    assert res.status == "migrated", res
    r7, r8, conns = F.readers(d / "orig" / "speed_history.db", d / "live" / "speed_history.db")
    yield h, datetime.fromtimestamp(start), r7, r8
    for c in conns:
        c.close()


@pytest.mark.parametrize("direction, col", [("download", 3), ("upload", 2)])
def test_migrated_summary_matches_the_v7_raw_path(migrated_day, direction, col):
    """v7 reads the <= 24 h window from raw: one sample per distinct second, rates summed across NICs -
    the exact all-interfaces mean. v8 reads the same seconds as folded minute rows. They carry the same
    bytes (avg x count), and v8's count is SUM over minutes of MAX(per-NIC seconds): short of v7's
    distinct seconds only by the seconds vEthernet alone was active (the MAX-vs-union slack)."""
    h, s, r7, r8 = migrated_day
    a = r7.summarize_network(direction, s, NOW, None)
    b = r8.summarize_network(direction, s, NOW, None)
    assert a.tier == "raw" and b.tier == "minute"
    total = sum(r[col] for r in h.raw)
    assert a.avg * a.count == pytest.approx(total, rel=1e-9)
    assert b.avg * b.count == pytest.approx(total, rel=1e-9)

    per_min = {}
    for t, nic, *_ in h.raw:
        per_min.setdefault(t // 60 * 60, {}).setdefault(nic, set()).add(t)
    union = sum(len(set().union(*m.values())) for m in per_min.values())
    longest = sum(max(len(v) for v in m.values()) for m in per_min.values())
    assert a.count == union and b.count == longest
    assert 0 < (union - longest) / union < 0.05      # the slack: small, and the only difference
    assert b.avg == pytest.approx(a.avg * union / longest, rel=1e-9)
    assert b.max == max(r[col] for r in h.raw)       # a minute keeps its NIC's raw peak


@pytest.mark.parametrize("iface", ["Wi-Fi", "vEthernet"])
def test_migrated_single_interface_matches_exactly(migrated_day, iface):
    h, s, r7, r8 = migrated_day
    a = r7.summarize_network("download", s, NOW, iface)
    b = r8.summarize_network("download", s, NOW, iface)
    assert b.count == a.count and b.avg == pytest.approx(a.avg, rel=1e-9) and b.max == a.max
