"""
v8 reader backend (database v8 step 3, dormant): ``V8HistoryReader`` against ``V7HistoryReader``.

The same logical history is written into a v7 file and a v8 file (``v8_twin_fixture``). The two backends
must AGREE on everything the schema change does not make more exact - graph series at every resolution,
hardware series, window summaries, interface list, earliest timestamp, the odometer - and differ only
in **volume**, where v8 is more exact. Every difference is asserted explicitly, value by value:

* raw tier: v7 = SUM(rate) x the caller's poll interval; v8 = SUM(bytes). Equal at a 1 s capture.
* v8-native rollup rows (bytes stored): v8 = the bytes, exactly. v7 = avg x bucket seconds.
* migrated rollup rows (bytes NULL): v8 = the legacy estimate avg x sample_count x 1 s, flagged
  ``reconstructed``. v7 = avg x bucket seconds - the over-count on partly idle buckets (design 5:
  measured x1.53 on the owner's hour tier), which v8 does not carry forward.

Then the watermark contract (``schema_v8.WATERMARK_KEYS``): boundaries, stray rows, copies, missing
and invalid watermarks, and one consistent snapshot per read.
"""
import math
from dataclasses import astuple, is_dataclass, replace
from datetime import datetime, timedelta

import pytest

from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.tests.unit import v8_twin_fixture as F

DAY = 86400
# The one clock every history, window and reader in this module derives from (v8_twin_fixture).
NOW = F.fixed_now()


def _same(a, b, path="result"):
    """Deep equality; floats to 1e-9 relative (SUM order is the only legitimate source of drift)."""
    if is_dataclass(a) and is_dataclass(b):
        assert type(a) is type(b), path
        return _same(astuple(a), astuple(b), path)
    if isinstance(a, float) or isinstance(b, float):
        assert isinstance(a, (int, float)) and isinstance(b, (int, float)), f"{path}: {a!r} vs {b!r}"
        if math.isnan(a) and math.isnan(b):
            return
        assert a == pytest.approx(b, rel=1e-9, abs=1e-9), f"{path}: {a!r} vs {b!r}"
        return
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        assert len(a) == len(b), f"{path}: len {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{path}[{i}]")
        return
    if isinstance(a, dict) and isinstance(b, dict):
        assert a.keys() == b.keys(), path
        for k in a:
            _same(a[k], b[k], f"{path}[{k!r}]")
        return
    assert a == b, f"{path}: {a!r} vs {b!r}"


@pytest.fixture(scope="module")
def twin(tmp_path_factory, q_app):
    d = tmp_path_factory.mktemp("twin")
    h = F.make_history(NOW)
    F.write_v7(d / "v7.db", h)
    F.write_v8(d / "v8.db", h).close()
    r7, r8, conns = F.readers(d / "v7.db", d / "v8.db")
    yield h, r7, r8
    for c in conns:
        c.close()


def _windows(now):
    out = [(None, None), (None, now)]
    for dl in (timedelta(minutes=5), timedelta(hours=6), timedelta(hours=20), timedelta(hours=26),
               timedelta(hours=30), timedelta(days=3), timedelta(days=7), timedelta(days=11),
               timedelta(days=31), timedelta(days=38), timedelta(days=90)):
        out.append((now - dl, now))
    out += [(now - timedelta(days=9), now - timedelta(days=4)),       # inside the migrated minutes
            (now - timedelta(hours=27), now - timedelta(hours=26)),   # inside the v8-native minutes
            (now - timedelta(days=39), now - timedelta(days=36)),     # inside the hour tier
            (now - timedelta(days=60), now - timedelta(days=45)),     # before any data
            (now + timedelta(hours=1), now + timedelta(hours=2))]     # future
    return out


WINDOWS = _windows(NOW)
IFACES = [None, "all", "Ethernet", "Wi-Fi 3", "no such adapter"]


# --- where the backends must agree ---------------------------------------------------------------

@pytest.mark.parametrize("resolution", ["auto", "raw", "minute", "hour", "day"])
@pytest.mark.parametrize("iface", IFACES)
def test_speed_history_agrees(twin, resolution, iface):
    _, r7, r8 = twin
    nonempty = 0
    for s, e in WINDOWS:
        for return_raw in (False, True):
            a = r7.speed_history(s, e, iface, return_raw, resolution)
            b = r8.speed_history(s, e, iface, return_raw, resolution)
            _same(a, b, f"{s}..{e} {resolution} {iface} raw={return_raw}")
            nonempty += any(p[1] or p[2] for p in a)
    if iface != "no such adapter":
        assert nonempty > 5, "fixture too thin to prove anything"


def test_hardware_history_agrees_and_ignores_per_entity_rows(twin):
    _, r7, r8 = twin
    for s, e in WINDOWS:
        a, b = r7.hardware_history("cpu", s, e), r8.hardware_history("cpu", s, e)
        _same(a, b, f"cpu {s}..{e}")
        assert all(v < 999.0 for _, v in b), "a per-entity ('core0') row leaked into the aggregate series"
    assert r8.hardware_history("gpu", None, None) == r7.hardware_history("gpu", None, None) == []


def _clipped_seconds(h, st, et, nic):
    """Seconds of the window's raw rows that lie BEFORE the window. A raw row is the END of its measured
    interval ``[ts - dt, ts]`` (#366: v8 clips a raw row's weight to the window), so a 1 s row stamped
    exactly at the window start measured nothing inside it: v8 gives it 0 s of coverage, v7 counts it as
    one sample x the poll interval. Computed from the logical history, not from either reader."""
    stamps = {t for t, n, _, _ in h.raw if st <= t <= et and (nic is None or n == nic)}
    return sum(1 - min(1, t - st) for t in stamps)


def _assert_summaries_agree(h, r7, r8, windows, direction, iface, poll):
    """v8 == v7 read at the capture interval (1 s rows), value by value - except, exactly, the start-edge
    clip. Returns how many windows the clip applied to (so a caller can prove it exercised both cases)."""
    nic = None if iface in (None, "all") else iface
    clipped = 0
    for s, e in windows:
        a, b = r7.summarize_network(direction, s, e, iface, 1.0), r8.summarize_network(direction, s, e, iface, poll)
        win = (e - s).total_seconds()
        clip = _clipped_seconds(h, int(s.timestamp()), int(e.timestamp()), nic)
        if clip:
            clipped += 1
            # Only a raw-path window can reach a raw row at its start (the twin's raw tier is 20 h).
            assert a.tier == b.tier == "raw", f"{s}..{e}"
            assert a.coverage_pct == round(a.count / win * 100.0, 1), f"v7 coverage {s}..{e}"
            a = replace(a, coverage_pct=round((a.count - clip) / win * 100.0, 1))
        _same(a, b, f"{direction} {iface} {s}..{e} clip={clip}")
    return clipped


@pytest.mark.parametrize("direction", ["download", "upload"])
@pytest.mark.parametrize("iface", [None, "Ethernet", "Wi-Fi 3"])
@pytest.mark.parametrize("poll", [1.0, 2.0])
@pytest.mark.parametrize("offset_s", [0, 7])
def test_network_summary_agrees(twin, direction, iface, poll, offset_s):
    """v8 == v7 read at the interval the twin was actually captured at (1 s rows). v8 ignores the
    caller's ``poll``: a raw row's weight in a rollup summary, and its share of coverage, are its STORED
    measured seconds (dt_ms / 1000, step-7 note 8 - the unit net-tier sample_count already has), so a
    poll-rate change cannot rescale the summary of history captured at another rate. v7 can only
    multiply its row count by the live rate.

    The one other difference is the start-edge clip (``_clipped_seconds``). Both cases are pinned, not
    left to the clock: at offset 0 the windows open exactly on the twin's 15 s raw grid (every raw-path
    window has a row at its start), at +7 s none does. Before the shared clock (``F.fixed_now()``), the
    seconds between this module's import and the fixture build chose the case - a 1-in-15 flake."""
    h, r7, r8 = twin
    shift = timedelta(seconds=offset_s)
    clipped = _assert_summaries_agree(h, r7, r8, [(s + shift, e + shift) for s, e in WINDOWS[2:]],
                                      direction, iface, poll)
    assert (clipped > 0) is (offset_s == 0), clipped


# Fixed instants, independent of when the suite runs: the windows (5 min .. 90 d back) cross midnight,
# an hour boundary, and the October DST change (EU + Israel, 2026-10-25) in local time.
_FIXED_CLOCKS = {
    "23:21 (passed)": datetime(2026, 10, 19, 23, 21, 4, 329611),
    "just after midnight": datetime(2026, 10, 20, 0, 0, 30, 500000),
    "00:12 (failed)": datetime(2026, 10, 20, 0, 12, 7, 125000),
    "hour boundary": datetime(2026, 10, 20, 14, 2, 11, 900000),
    "after DST end": datetime(2026, 10, 25, 4, 30, 0),
}


@pytest.mark.parametrize("clock", sorted(_FIXED_CLOCKS))
def test_network_summary_agrees_at_fixed_clocks(tmp_path, q_app, clock):
    """The same contract on twins built (and read) at fixed instants either side of midnight and of an
    hour: nothing in the summaries' window / tier / watermark arithmetic depends on the time of day."""
    now = _FIXED_CLOCKS[clock]
    h = F.make_history(now)
    F.write_v7(tmp_path / "v7.db", h)
    F.write_v8(tmp_path / "v8.db", h).close()
    r7, r8, conns = F.readers(tmp_path / "v7.db", tmp_path / "v8.db", now=now)
    try:
        for offset_s in (0, 7):
            shift = timedelta(seconds=offset_s)
            windows = [(s + shift, e + shift) for s, e in _windows(now)[2:]]
            for iface in (None, "Ethernet"):
                for direction in ("download", "upload"):
                    clipped = _assert_summaries_agree(h, r7, r8, windows, direction, iface, 2.0)
                    assert (clipped > 0) is (offset_s == 0), (offset_s, clipped)
    finally:
        for c in conns:
            c.close()


@pytest.mark.parametrize("poll", [1.0, 5.0])
def test_hardware_summary_agrees(twin, poll):
    _, r7, r8 = twin
    for s, e in WINDOWS[2:]:
        _same(r7.summarize_hardware("cpu", s, e, poll), r8.summarize_hardware("cpu", s, e, poll), f"cpu {s}..{e}")


def test_interfaces_earliest_and_odometer_agree(twin):
    h, r7, r8 = twin
    assert r8.distinct_interfaces() == r7.distinct_interfaces() == sorted(F.NICS)
    assert r8.earliest_timestamp() == r7.earliest_timestamp() == datetime.fromtimestamp(h.hour[0][0])
    u7, u8 = r7.load_usage(), r8.load_usage()
    assert u8 == u7 and u8.counter_row == h.usage_row[1:6] and u8.snapshot


# --- where v8 is more exact: volume, each difference asserted ------------------------------------

def _expected(h, start_ts, end_ts, nic=None):
    """(v7 total, v8 total, v8 exact part, reconstructed?) for [start, end], computed from the logical
    history, not from either reader."""
    def pick(rows):
        return [r for r in rows if start_ts <= r[0] <= end_ts and (nic is None or r[1] == nic)]
    raw, minute, hour = pick(h.raw), pick(h.minute), pick(h.hour)
    now = h.now_ts
    v7 = [0.0, 0.0]
    v8 = [0.0, 0.0]
    exact = [0.0, 0.0]
    for _, _, u, d in raw:                                   # 1 s samples: rate == bytes
        for i, x in enumerate((u, d)):
            v7[i] += x
            v8[i] += x
            exact[i] += x
    recon = False
    for rows, bucket, v7_reads in ((minute, 60, start_ts < now - DAY), (hour, 3600, start_ts < now - 30 * DAY)):
        for r in rows:
            avg, n, by = (r[2], r[3]), r[6], (r[7], r[8])
            for i in range(2):
                if v7_reads:
                    v7[i] += avg[i] * bucket                 # v7: avg x bucket, partly idle or not
                if by[i] is not None:
                    v8[i] += by[i]
                    exact[i] += by[i]
                else:
                    v8[i] += avg[i] * min(n, bucket)         # legacy estimate: one sample = one second
                    recon = True
    return tuple(v7), tuple(v8), tuple(exact), recon


@pytest.mark.parametrize("iface", [None, "Ethernet", "Wi-Fi 3"])
def test_volume_differs_only_where_v8_is_more_exact(twin, iface):
    h, r7, r8 = twin
    now = NOW
    nic = None if iface in (None, "all") else iface
    seen = set()
    for s, e in WINDOWS[1:]:
        s_ts = int(s.timestamp()) if s else 0
        e_ts = int(e.timestamp())
        x7, x8, exact, recon = _expected(h, s_ts, e_ts, nic)
        got7 = r7.total_bandwidth(s, e, iface, 1.0)
        got8 = r8.total_bandwidth(s, e, iface, 1.0)
        assert got7 == pytest.approx(x7, rel=1e-12), f"v7 {s}..{e}"
        assert got8 == pytest.approx(x8, rel=1e-12), f"v8 {s}..{e}"
        vol = r8.volume(s, e, iface, 1.0)
        assert (vol.up_bytes, vol.down_bytes) == pytest.approx(x8, rel=1e-12)
        assert (vol.exact_up_bytes, vol.exact_down_bytes) == pytest.approx(exact, rel=1e-12)
        assert vol.reconstructed is recon, f"{s}..{e}"
        # v7 can never claim exactness: every v7 byte is a rate x interval reconstruction (D5).
        v7vol = r7.volume(s, e, iface, 1.0)
        assert (v7vol.up_bytes, v7vol.down_bytes) == pytest.approx(x7, rel=1e-12)
        assert v7vol.reconstructed is True and v7vol.exact_down_bytes == 0.0
        if x7 == pytest.approx(x8):
            seen.add("equal")
        else:
            seen.add("differs")
    assert seen == {"equal", "differs"}


def test_raw_only_window_is_identical_and_exact(twin):
    """<= 20 h: raw tier only - both backends report the same bytes, and v8 calls them exact."""
    h, r7, r8 = twin
    now = NOW
    s = datetime.fromtimestamp(h.now_ts) - timedelta(hours=20)
    assert r7.total_bandwidth(s, now, None, 1.0) == pytest.approx(r8.total_bandwidth(s, now, None, 1.0), rel=1e-12)
    vol = r8.volume(s, now, None, 1.0)
    assert vol.reconstructed is False and vol.down_bytes == vol.exact_down_bytes > 0


def test_native_minutes_are_exact_where_v7_overcounts(twin):
    """The 3 h of v8-native minutes (20 x 2 s samples, 40 s of each 60 s bucket): v8 returns the stored
    bytes, v7 bills the idle 20 s as if they moved data (x1.5), and the legacy estimate would have
    under-counted the 2 s samples (x0.5) - which is why stored bytes beat any estimate."""
    h, r7, r8 = twin
    now = datetime.fromtimestamp(h.now_ts)   # the fixture's clock: the module fixture predates this test
    s, e = now - timedelta(hours=28, minutes=1), now - timedelta(hours=24, minutes=30)
    native = [r for r in h.minute if r[7] is not None and int(s.timestamp()) <= r[0] <= int(e.timestamp())]
    assert len(native) == 2 * 180
    true_down = sum(r[8] for r in native)
    vol = r8.volume(s, e, None, 1.0)
    assert vol.down_bytes == true_down and vol.exact_down_bytes == true_down and vol.reconstructed is False
    assert r7.total_bandwidth(s, e, None, 1.0)[1] == pytest.approx(true_down * 60 / 40)
    assert sum(r[3] * r[6] for r in native) == pytest.approx(true_down / F.NATIVE_SAMPLE_SECONDS)


def test_migrated_minutes_use_the_legacy_estimate_not_avg_x_60(twin):
    h, r7, r8 = twin
    now = datetime.fromtimestamp(h.now_ts)
    s, e = now - timedelta(days=9), now - timedelta(days=4)
    rows = [r for r in h.minute if r[7] is None and int(s.timestamp()) <= r[0] <= int(e.timestamp())]
    assert rows and any(r[6] < 60 for r in rows)
    est = sum(r[3] * r[6] for r in rows)
    vol = r8.volume(s, e, None, 1.0)
    assert vol.down_bytes == pytest.approx(est) and vol.exact_down_bytes == 0 and vol.reconstructed
    assert r7.total_bandwidth(s, e, None, 1.0)[1] == pytest.approx(sum(r[3] * 60 for r in rows))
    assert r7.total_bandwidth(s, e, None, 1.0)[1] > vol.down_bytes


def test_v8_raw_volume_ignores_the_live_poll_rate(tmp_path, q_app):
    """v7 scales raw rows by the CURRENT poll interval (a 2 s capture read at 1 s is half the bytes; the
    SMART-mode 2x under-report). v8 stores bytes over a measured dt_ms, so the caller's rate is moot."""
    now = NOW
    h = F.make_history(now)
    for name, dt in (("v8_1s.db", 1000), ("v8_2s.db", 2000)):
        w = F.V8Writer(tmp_path / name)
        w.net_raw(h.raw, dt_ms=dt)   # same rates; at 2 s each row holds twice the bytes
        w.close()
    F.write_v7(tmp_path / "v7.db", F.History(now_ts=h.now_ts, raw=h.raw))
    r7, r8_1, c1 = F.readers(tmp_path / "v7.db", tmp_path / "v8_1s.db")
    _, r8_2, c2 = F.readers(tmp_path / "v7.db", tmp_path / "v8_2s.db")
    s = now - timedelta(hours=20)
    true_2s = (sum(r[2] for r in h.raw) * 2.0, sum(r[3] for r in h.raw) * 2.0)
    for poll in (1.0, 2.0, 10.0):
        assert r8_2.total_bandwidth(s, now, None, poll) == pytest.approx(true_2s)
        assert r7.total_bandwidth(s, now, None, poll) == pytest.approx((true_2s[0] * poll / 2, true_2s[1] * poll / 2))
    # ... while the rates (graph, summaries) are the same B/s v7 stores, whatever the capture interval;
    # coverage counts the seconds each row MEASURED, so the 2 s file is v7 read at its true 2 s rate.
    for r8, capture_s in ((r8_1, 1.0), (r8_2, 2.0)):
        _same(r8.speed_history(s, now, None, True, "raw"), r7.speed_history(s, now, None, True, "raw"))
        _same(r8.summarize_network("download", s, now, None, 1.0),
              r7.summarize_network("download", s, now, None, capture_s))
    for c in c1 + c2:
        c.close()


def test_empty_v8_file_reads_like_an_empty_v7_file(tmp_path, q_app):
    h = F.History(now_ts=int(NOW.timestamp()))
    F.write_v7(tmp_path / "v7.db", h)
    F.V8Writer(tmp_path / "v8.db").close()
    # usage row identical on both (write_v7 inserts h.usage_row; mirror it)
    import sqlite3
    c = sqlite3.connect(tmp_path / "v8.db")
    c.execute("INSERT INTO usage_counter (id, cumulative_up, cumulative_down, anchor_up, anchor_down, period_key, "
              "updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?)", h.usage_row)
    c.commit()
    c.close()
    r7, r8, conns = F.readers(tmp_path / "v7.db", tmp_path / "v8.db")
    now = NOW
    for s, e in WINDOWS:
        for res in ("auto", "raw", "minute", "hour"):
            _same(r7.speed_history(s, e, None, False, res), r8.speed_history(s, e, None, False, res))
        _same(r7.hardware_history("cpu", s, e), r8.hardware_history("cpu", s, e))
    s = now - timedelta(days=40)
    _same(r7.summarize_network("download", s, now), r8.summarize_network("download", s, now))
    _same(r7.summarize_hardware("cpu", s, now), r8.summarize_hardware("cpu", s, now))
    assert r8.total_bandwidth(s, now, None, 1.0) == r7.total_bandwidth(s, now, None, 1.0) == (0.0, 0.0)
    assert r8.volume(s, now, None, 1.0).reconstructed is False
    assert r8.distinct_interfaces() == r7.distinct_interfaces() == []
    assert r8.earliest_timestamp() is None and r7.earliest_timestamp() is None
    assert r8.load_usage() == r7.load_usage()
    for c in conns:
        c.close()


# --- the watermark contract ----------------------------------------------------------------------

def _all_reads(r, now):
    out = {}
    for days in (2, 7, 31, 45):
        s = now - timedelta(days=days)
        out[f"total{days}"] = r.total_bandwidth(s, now, None, 1.0)
        out[f"total{days}e"] = r.total_bandwidth(s, now, "Ethernet", 1.0)
        out[f"sumnet{days}"] = r.summarize_network("download", s, now, None, 1.0)
        out[f"sumhw{days}"] = r.summarize_hardware("cpu", s, now, 1.0)
        out[f"hw{days}"] = r.hardware_history("cpu", s, now)
        for res in ("minute", "hour", "day"):
            out[f"speed{days}{res}"] = r.speed_history(s, now, None, True, res)
    out["speedraw"] = r.speed_history(now - timedelta(hours=23), now, None, True, "raw")
    return out


def _v8(tmp_path, name, h, watermarks=True, mutate=None):
    w = F.write_v8(tmp_path / name, h, watermarks=watermarks)
    if mutate:
        mutate(w)
    w.close()
    _, r8, conns = F.readers(tmp_path / name, tmp_path / name)
    return r8, conns


@pytest.fixture
def hist():
    return F.make_history(NOW)


def test_missing_watermarks_fall_back_to_the_horizon_rule(tmp_path, hist):
    """No overlap and no strays: with or without watermarks, every read is the same."""
    a, ca = _v8(tmp_path, "wm.db", hist)
    b, cb = _v8(tmp_path, "nowm.db", hist, watermarks=False)
    now = NOW
    _same(_all_reads(a, now), _all_reads(b, now))
    for c in ca + cb:
        c.close()


@pytest.mark.parametrize("bad", ["abc", "", "-5", "0", "1.7e9", " 1760000000", "far_future"])
def test_invalid_watermarks_are_ignored(tmp_path, hist, bad):
    """A watermark the reader cannot trust is no claim at all: same as absent. A future one matters most -
    taken at face value it would declare every live raw row 'already rolled up' and blank the graph."""
    val = str(hist.now_ts + 30 * DAY) if bad == "far_future" else bad
    ref, cr = _v8(tmp_path, "nowm.db", hist, watermarks=False)
    got, cg = _v8(tmp_path, "bad.db", hist, mutate=lambda w: w.watermarks(val, val))
    now = NOW
    _same(_all_reads(ref, now), _all_reads(got, now))
    for c in cr + cg:
        c.close()


def test_each_tier_answers_exactly_from_its_watermark(tmp_path, hist):
    """Boundary rows: raw at W_r counts, raw at W_r - 1 is ignored; minute at W_r - 60 counts, minute at
    W_r is ignored; minute at W_m counts, hour at W_m is ignored, hour at W_m - 3600 counts."""
    wr, wm = hist.wm_raw, hist.wm_minute
    nic = "Ethernet"

    def edges(w):
        w.net_raw([(wr, nic, 1, 1000), (wr - 1, nic, 1, 2000)])
        w.net_tier(S8.NET_MINUTE, [(wr - 60, nic, 1.0, 30.0, 1.0, 30.0, 10, None, None),
                                   (wr, nic, 1.0, 50000.0, 1.0, 50000.0, 60, 1, 3000000),
                                   (wm, nic, 1.0, 70.0, 1.0, 70.0, 10, None, None)])
        w.net_tier(S8.NET_HOUR, [(wm, nic, 1.0, 9e6, 1.0, 9e6, 3600, None, None),
                                 (wm - 3600, nic, 1.0, 11.0, 1.0, 11.0, 100, None, None)])
    base, cb = _v8(tmp_path, "base.db", hist)
    got, cg = _v8(tmp_path, "edges.db", hist, mutate=edges)
    now = NOW
    s = now - timedelta(days=60)
    b, g = base.total_bandwidth(s, now, None, 1.0), got.total_bandwidth(s, now, None, 1.0)
    assert g[1] - b[1] == pytest.approx(1000 + 30 * 10 + 70 * 10 + 11 * 100)
    for c in cb + cg:
        c.close()


@pytest.mark.parametrize("moved", ["network", "hardware"])
def test_each_family_reads_only_its_own_watermarks(tmp_path, hist, moved):
    """The families roll up independently (#359: the migration folds network raw, leaves hardware raw in
    place). Moving one family's raw watermark to now - claiming its whole raw tier rolled up - must
    blank only THAT family's raw reads; the other family reads exactly as before."""
    now_floor = hist.now_ts // 60 * 60
    if moved == "network":
        wm = dict(raw=now_floor, minute=hist.wm_minute, hw_raw=hist.wm_raw, hw_minute=hist.wm_minute)
    else:
        wm = dict(raw=hist.wm_raw, minute=hist.wm_minute, hw_raw=now_floor, hw_minute=hist.wm_minute)
    clean, c1 = _v8(tmp_path, "clean.db", hist)
    got, c2 = _v8(tmp_path, "moved.db", hist, mutate=lambda w: w.watermarks(**wm))
    now = NOW
    s = now - timedelta(hours=20)
    net = lambda r: (r.total_bandwidth(s, now, None, 1.0), r.speed_history(s, now, None, True, "raw"),
                     r.summarize_network("download", s, now, None, 1.0))
    hw = lambda r: (r.hardware_history("cpu", s, now), r.summarize_hardware("cpu", s, now, 1.0))
    kept = hw if moved == "network" else net
    _same(kept(clean), kept(got))
    if moved == "network":
        assert got.total_bandwidth(s, now, None, 1.0)[1] < 0.01 * clean.total_bandwidth(s, now, None, 1.0)[1]
    else:
        assert len(got.hardware_history("cpu", s, now)) <= 5 < len(clean.hardware_history("cpu", s, now))
    for c in c1 + c2:
        c.close()


@pytest.mark.parametrize("stray_at", ["25.5h", "3d", "year_2000"])
def test_a_stray_raw_row_below_the_watermark_hides_nothing(tmp_path, hist, stray_at):
    """A raw row stamped behind W_r after the pass (clock set back) is, by the contract, 'rolled up' -
    the reader ignores it until the next pass rolls it, so it neither double-counts nor bounds minute.
    Without a watermark, the step-2 fallback is weaker: a stray INSIDE raw's horizon (25.5 h back)
    becomes the floor of raw and hides the minute rows above it - the case the watermark exists for."""
    ts = {"25.5h": hist.now_ts - int(25.5 * 3600), "3d": hist.now_ts - 3 * DAY,
          "year_2000": int(datetime(2000, 1, 1, 12).timestamp())}[stray_at]

    def stray(w):
        w.net_raw([(ts, "Ethernet", 7, 11)])
        w.hw_raw([(ts, "cpu", 50.0)])
    clean, c1 = _v8(tmp_path, "clean.db", hist)
    got, c2 = _v8(tmp_path, "stray.db", hist, mutate=stray)
    now = NOW
    _same(_all_reads(clean, now), _all_reads(got, now))
    if stray_at == "25.5h":
        fb, c3 = _v8(tmp_path, "stray_nowm.db", hist, watermarks=False, mutate=stray)
        s = now - timedelta(days=2)
        lost = clean.total_bandwidth(s, now, None, 1.0)[1] - fb.total_bandwidth(s, now, None, 1.0)[1]
        hidden = sum(r[8] for r in hist.minute if r[7] is not None and r[0] >= ts)
        assert lost == pytest.approx(hidden - 11) and hidden > 0
        for c in c3:
            c.close()
    for c in c1 + c2:
        c.close()


def test_copies_above_a_watermark_are_never_billed_twice(tmp_path, hist):
    """A coarser row at/above the finer tier's watermark is a copy (the v8 fan-out, a re-run pass): a
    minute copy of raw-covered time and an hour copy of minute-covered time change nothing."""
    def copies(w):
        t = hist.raw[0][0] // 60 * 60 + 60
        w.net_tier(S8.NET_MINUTE, [(t, "Ethernet", 500.0, 5000.0, 900.0, 9000.0, 60, 30000, 300000)])
        w.hw_tier(S8.HW_MINUTE, [(t, "cpu", 50.0, 90.0, 60)])
        t2 = hist.minute[-1][0] // 3600 * 3600
        w.net_tier(S8.NET_HOUR, [(t2, "Wi-Fi 3", 500.0, 5000.0, 900.0, 9000.0, 3600, None, None)])
        w.hw_tier(S8.HW_HOUR, [(t2, "cpu", 50.0, 90.0, 3600)])
    clean, c1 = _v8(tmp_path, "clean.db", hist)
    got, c2 = _v8(tmp_path, "copies.db", hist, mutate=copies)
    now = NOW
    _same(_all_reads(clean, now), _all_reads(got, now))
    for c in c1 + c2:
        c.close()


# --- one snapshot per read -----------------------------------------------------------------------

class _RollupMidRead:
    """A read connection that, right after the read's FIRST SELECT, lets a v8 roll-up commit on another
    connection - every raw row into minute, with the watermark moved in the same transaction, exactly as
    the contract demands - before the read's remaining statements run."""

    def __init__(self, conn, path):
        self._c, self._path, self.fired = conn, path, False

    def __getattr__(self, name):
        return getattr(self._c, name)

    def _after(self, sql):
        if not self.fired and sql.lstrip().upper().startswith(("SELECT", "WITH")):
            self.fired = True
            import sqlite3
            w = sqlite3.connect(self._path, isolation_level=None)
            cut = int(NOW.timestamp()) // 60 * 60
            w.execute("BEGIN IMMEDIATE")
            w.execute(f"""INSERT OR IGNORE INTO {S8.NET_MINUTE} (ts, iface_id, up_bytes, down_bytes, up_avg,
                          down_avg, up_max, down_max, sample_count)
                          SELECT ts / 60 * 60, iface_id, SUM(up_bytes), SUM(down_bytes),
                                 AVG(up_bytes * 1000.0 / dt_ms), AVG(down_bytes * 1000.0 / dt_ms),
                                 MAX(up_bytes * 1000.0 / dt_ms), MAX(down_bytes * 1000.0 / dt_ms), COUNT(*)
                          FROM {S8.NET_RAW} WHERE ts < ? GROUP BY ts / 60 * 60, iface_id""", (cut,))
            w.execute(f"""INSERT OR IGNORE INTO {S8.HW_MINUTE} (ts, metric_id, avg_value, max_value, sample_count)
                          SELECT ts / 60 * 60, metric_id, AVG(value), MAX(value), COUNT(*)
                          FROM {S8.HW_RAW} WHERE ts < ? GROUP BY ts / 60 * 60, metric_id""", (cut,))
            w.execute(f"DELETE FROM {S8.NET_RAW} WHERE ts < ?", (cut,))
            w.execute(f"DELETE FROM {S8.HW_RAW} WHERE ts < ?", (cut,))
            w.executemany("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                          [(S8.WATERMARK_RAW_KEY, str(cut)), (S8.HW_WATERMARK_RAW_KEY, str(cut))])
            w.execute("COMMIT")
            w.close()

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


@pytest.mark.parametrize("call", ["total", "volume", "summarize_network", "summarize_hardware", "speed_history",
                                  "hardware_history", "coverage"])
def test_a_rollup_commit_mid_read_changes_nothing(tmp_path, hist, call):
    import sqlite3
    from netspeedtray.core.db.history_reader import V8HistoryReader
    path = tmp_path / "v8.db"
    F.write_v8(path, hist).close()
    now = NOW
    start = now - timedelta(days=3)
    calls = {
        "total": lambda r: r.total_bandwidth(start, now, None, 1.0),
        "volume": lambda r: r.volume(start, now, None, 1.0),
        "summarize_network": lambda r: r.summarize_network("download", start, now, None, 1.0),
        "summarize_hardware": lambda r: r.summarize_hardware("cpu", start, now, 1.0),
        "speed_history": lambda r: r.speed_history(start, now, None, True, "hour"),
        "hardware_history": lambda r: r.hardware_history("cpu", start, now),
        "coverage": lambda r: r.coverage(start, now),
    }
    quiet = sqlite3.connect(path, check_same_thread=False)
    expected = calls[call](V8HistoryReader(lambda: quiet, lambda: path, clock=F.clock_at(NOW)))
    quiet.close()
    raw_conn = sqlite3.connect(path, check_same_thread=False)
    noisy = _RollupMidRead(raw_conn, path)
    got = calls[call](V8HistoryReader(lambda: noisy, lambda: path, clock=F.clock_at(NOW)))
    assert noisy.fired
    raw_conn.close()
    _same(got, expected, call)
    if call == "total":   # and the roll-up really moved the data (the test would be vacuous otherwise)
        r = V8HistoryReader(lambda: sqlite3.connect(path), lambda: path, clock=F.clock_at(NOW))
        assert r.volume(start, now, None, 1.0).down_bytes == pytest.approx(expected[1])
