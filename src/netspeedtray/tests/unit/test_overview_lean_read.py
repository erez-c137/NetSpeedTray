"""
The Monitor Overview's window reload (2.2.1-beta.2): what it reads, how often, and what it draws.

On the owner's 26 MB v8 database one reload cost 0.4 s (Session) to 1.1 s (All) of CPU, and it ran
every 6 s for every window - 9-18 % of a core while the Overview was open. Four causes, each pinned
here on temp DBs (never the live one):

1. One 6 s cadence for every window, though a 24 h sparkline point is ~13 minutes. Now by window
   length (constants.timers.OVERVIEW_*): Session / short windows 6 s, a day or two 30 s, longer 2 min.
2. Every reload re-read everything: now a reload whose database is unchanged (PRAGMA data_version on
   the reader's connection) reuses the last read, and "All" no longer queues a DB write (the batch
   flush inside get_earliest_data_timestamp) every reload.
3. summarize_network / summarize_hardware fetched every sample into Python for percentiles the
   Overview never shows: the lean read aggregates count / avg / max in SQL. It must give
   summarize_network's figures (parity below, both directions, randomized histories).
4. CORRECTNESS: the sparklines kept only the LAST 120 points (tiles._SPARK_POINTS), so "24 h" drew
   the last ~2 h. Now ~OVERVIEW_SPARK_BINS points spread over the whole window.

Review of #594 (code + UX): every bin of the window is kept, None where nothing was stored (a slept
laptop is a gap at its place, never squeezed out, never zero); the card's peak never reads below the
live rate seen since the last read; the session totals are read on every 6 s request; a link drop
re-reads ~11 s later; a session longer than the in-memory buffer reads its sparklines from the DB;
the data_version token survives a reopened connection; a failed read is never reused; and mutation
guards for the bin rules (peak / mean / whole minutes).
"""
import random
import sqlite3
from datetime import datetime, timedelta

import pytest

from netspeedtray import constants
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.tests.unit import v8_twin_fixture as F
from netspeedtray.views.monitor.overview import tab as T
from netspeedtray.views.monitor.overview.tiles import _SPARK_POINTS

NOW = F.fixed_now()
NOW_TS = int(NOW.timestamp())
DAY = 86400


class _WS:
    """WidgetState's real history accessors over a temp-DB reader (no DB worker, no Qt), counting
    batch flushes."""

    def __init__(self, reader):
        self.history_reader = reader
        self.db_worker = object()
        self.config = {"update_rate": 1.0}
        self.flushes = 0
        self._read_only = False

    def flush_batch(self):
        self.flushes += 1

    def __getattr__(self, name):
        return getattr(WidgetState, name).__get__(self)


def _rel(a, b):
    if a is None or b is None:
        return 0.0 if a is None and b is None else float("inf")
    return abs(a - b) / abs(b) if b else abs(a - b)


def _random_history(seed: int):
    """make_history plus noise the summaries must handle: a third interface, gap rows (flagged, long
    dt), sub-second rows, raw rows sharing a timestamp across interfaces, and minute rows
    that overlap raw in time but sit below the raw watermark."""
    h = F.make_history(NOW, seed=seed)
    rng = random.Random(seed * 7 + 1)
    extra_raw = []
    for t in range(NOW_TS - 20 * 3600, NOW_TS - 5, rng.choice((15, 30, 45))):
        if rng.random() < 0.4:
            extra_raw.append((t, "vEthernet", rng.randint(0, 50), rng.randint(0, 900)))
    return h, extra_raw, rng


def _make_db(tmp_path, seed: int):
    h, extra_raw, rng = _random_history(seed)
    path = tmp_path / f"s{seed}.db"
    w = F.write_v8(path, h)
    w.net_raw(extra_raw)
    # Gap rows (standby): flagged, a long dt - not samples for the summaries.
    w.conn.executemany(f"INSERT OR REPLACE INTO {S8.NET_RAW} (ts, iface_id, up_bytes, down_bytes, dt_ms, flags) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       [(NOW_TS - rng.randint(60, 19 * 3600), w.iface("Ethernet"), 5_000_000, 90_000_000,
                         7_200_000, S8.FLAG_GAP) for _ in range(6)])
    # Short-interval rows (a 0.5 s poll): their rate is bytes over 500 ms, their weight half a second.
    w.conn.executemany(f"INSERT OR REPLACE INTO {S8.NET_RAW} (ts, iface_id, up_bytes, down_bytes, dt_ms) "
                       "VALUES (?, ?, ?, ?, 500)",
                       [(NOW_TS - 7 - 15 * rng.randint(1, 4000), w.iface("Wi-Fi 3"), 10, 20) for _ in range(8)])
    # Hour rows with NULL bytes inside the month (migrated), including a zero sample_count.
    h0 = (NOW_TS - 25 * DAY) // 3600 * 3600   # below the minute watermark (20 d): the hour tier's
    w.net_tier(S8.NET_HOUR, [(t, "Ethernet", float(rng.randint(1, 900)), float(rng.randint(1, 9000)),
                              float(rng.randint(900, 2000)), float(rng.randint(9000, 20000)),
                              0 if i == 3 else rng.randint(1, 3600), None, None)
                             for i, t in enumerate(range(h0, h0 + 30 * 3600, 3600))])
    w.close()
    conn = F.ro_conn(path)
    from netspeedtray.core.db.history_reader import V8HistoryReader
    return V8HistoryReader(lambda: conn, lambda: path, clock=F.clock_at(NOW)), conn


WINDOWS = [timedelta(minutes=30), timedelta(hours=3), timedelta(hours=24), timedelta(hours=30),
           timedelta(days=7), timedelta(days=30), timedelta(days=45)]


def _vals(series):
    return [v for v in series if v is not None]


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_lean_summaries_match_summarize_network(tmp_path, seed):
    """Count / avg / peak of the lean read == summarize_network's, both directions, every window
    length (raw only, raw + minute, + hour, + migrated rows with NULL bytes and a zero count)."""
    from netspeedtray.core.db.history_reader import overview_bin_seconds
    r8, conn = _make_db(tmp_path, seed)
    try:
        for win in WINDOWS:
            start, end = NOW - win, NOW
            b = overview_bin_seconds(int(start.timestamp()), int(end.timestamp()))
            series, lean, ok = r8.overview_window(start, end, b, want_series=True)
            assert ok
            for name, direction in (("down", "download"), ("up", "upload")):
                ref = r8.summarize_network(direction, start, end, None, 1.0)
                got = lean[name]
                assert got.count == ref.count, (win, name)
                assert _rel(got.avg, ref.avg) < 1e-9, (win, name, got.avg, ref.avg)
                assert _rel(got.max, ref.max) < 1e-9, (win, name, got.max, ref.max)
    finally:
        conn.close()


def test_lean_series_is_speed_history_binned_over_the_whole_window(tmp_path):
    """The 24 h sparkline == speed_history at minute resolution (what 24 h read before), each
    epoch-aligned bin its peak, one entry per bin of the window (None where nothing was stored)."""
    from netspeedtray.core.db.history_reader import overview_bin_seconds
    r8, conn = _make_db(tmp_path, 11)
    try:
        start, end = NOW - timedelta(hours=24), NOW
        st, et = int(start.timestamp()), int(end.timestamp())
        b = overview_bin_seconds(st, et)
        series, _, _ok = r8.overview_window(start, end, b)
        rows = r8.speed_history(start, end, None, return_raw=True, resolution="minute")
        bins = {}
        for ts, up, down in rows:
            k = int(ts) // b
            pu, pd = bins.get(k, (0.0, 0.0))
            bins[k] = (max(pu, up), max(pd, down))
        keys = range(st // b, et // b + 1)
        oracle_down = [bins[k][1] if k in bins else None for k in keys]
        oracle_up = [bins[k][0] if k in bins else None for k in keys]
        assert len(series["down"]) == len(oracle_down) <= _SPARK_POINTS
        for got, want in ((series["down"], oracle_down), (series["up"], oracle_up)):
            assert [g is None for g in got] == [w is None for w in want]
            assert all(_rel(g, w) < 1e-9 for g, w in zip(got, want) if w is not None)
        # The fixture's raw history starts 20 h back: the first ~4 h are empty bins, not squeezed out.
        assert series["down"][0] is None and len(_vals(series["down"])) >= 20 * 3600 // b - 1
    finally:
        conn.close()


def _marker_db(tmp_path, gap=None, cpu=None):
    """24 h of one-minute samples: 9 MB/s and CPU 90 % in the window's first hour, 1 kB/s and 10 %
    after, so a sparkline that dropped the start of the window cannot show the marker. ``gap`` =
    (from, to) seconds before now with nothing stored (the PC slept); ``cpu`` overrides the CPU value."""
    h = F.History(now_ts=NOW_TS, wm_raw=NOW_TS - 30 * 3600, wm_minute=NOW_TS - 40 * DAY)
    first = NOW_TS - 24 * 3600 + 120
    for t in range(first, NOW_TS - 5, 60):
        if gap and NOW_TS - gap[0] <= t < NOW_TS - gap[1]:
            continue
        hot = t < first + 3600
        h.raw.append((t, "Ethernet", 500, 9_000_000 if hot else 1_000))
        h.hw_raw.append((t, "cpu", cpu(t) if cpu else (90.0 if hot else 10.0)))
    path = tmp_path / "marker.db"
    F.write_v8(path, h).close()
    return F.readers(path, path, now=NOW)


@pytest.mark.parametrize("which", [0, 1], ids=["v7", "v8"])
def test_24h_sparkline_spans_the_whole_day(tmp_path, which):
    """The tile draws at most _SPARK_POINTS points and kept the LAST ones: a 24 h series of 1,441
    minute points showed only the last 2 h, under a label and scale that claim the day."""
    r7, r8, conns = _marker_db(tmp_path)
    try:
        ws = _WS((r7, r8)[which])
        series = T._read_window(ws, "TIMELINE_24_HOURS", NOW, None, None, 1.0, False, None)[0]
        for key, hot in (("down", 9_000_000.0), ("cpu", 90.0)):
            drawn = _vals(series[key][-_SPARK_POINTS:])     # what Sparkline.set_series keeps
            assert drawn[0] == pytest.approx(hot), f"{key}: the window's first hour is not drawn"
            assert len(series[key]) <= _SPARK_POINTS, key
            assert drawn[-1] < hot / 5, key
    finally:
        for c in conns:
            c.close()


@pytest.mark.parametrize("which", [0, 1], ids=["v7", "v8"])
def test_an_8h_gap_stays_a_gap_at_its_place(tmp_path, which):
    """UX review of #594: bins with no rows were dropped, so the rest stretched across the card - a
    laptop asleep 8 h read as online all day, and spikes moved in time. Every bin of the window is
    there, None (never 0) where nothing was stored, and the network and CPU cards share the axis."""
    from netspeedtray.core.db.history_reader import overview_bin_seconds
    r7, r8, conns = _marker_db(tmp_path, gap=(16 * 3600, 8 * 3600))
    try:
        ws = _WS((r7, r8)[which])
        start = NOW - timedelta(hours=24)
        series = T._read_window(ws, "TIMELINE_24_HOURS", NOW, None, None, 1.0, False, None)[0]
        st, et = int(start.timestamp()), NOW_TS
        b = overview_bin_seconds(st, et)
        n = et // b - st // b + 1
        assert len(series["down"]) == len(series["cpu"]) == n
        for key in ("down", "up", "cpu"):
            s = series[key]
            empty = [i for i, v in enumerate(s) if v is None]
            assert 0 not in [v for v in s if v is not None and v == 0], key
            # Bins entirely inside the gap are None, and they sit 8-16 h before the end.
            inside = [i for i in range(n) if (st // b + i) * b >= et - 16 * 3600 + 60
                      and (st // b + i + 1) * b <= et - 8 * 3600 - 60]
            assert inside and set(inside) <= set(empty), key
            assert s[-2] is not None and s[1] is not None, key
    finally:
        for c in conns:
            c.close()


class _Agg:
    def __init__(self, ts, down, up=1.0):
        self.timestamp, self.download, self.upload = ts, down, up


class _Snap:
    def __init__(self, ts, value):
        self.timestamp, self.value = ts, value


class _Empty:
    def summarize_network(self, *a):
        from netspeedtray.utils.summaries import summarize_raw
        return summarize_raw([])

    summarize_hardware = summarize_network   # read by the pre-fix Overview


def _session_samples(start, n, down, cpu):
    ts = [start + timedelta(seconds=i) for i in range(n)]
    return {"agg": [_Agg(t, down(i)) for i, t in enumerate(ts)],
            "cpu": [_Snap(t, cpu(i)) for i, t in enumerate(ts)], "gpu": [],
            "ram": [_Snap(t, 50.0) for t in ts]}


def test_session_series_is_spread_over_the_whole_session():
    """The Session's in-memory samples (one per poll) are binned across the whole session."""
    start = NOW - timedelta(hours=1)
    samples = _session_samples(start, 3600, lambda i: 9e6 if i < 60 else 1e3, lambda i: 90.0 if i < 60 else 10.0)
    out = T._read_window(_Empty(), "TIMELINE_SESSION", NOW, start, samples, 1.0, False, None)[0]
    assert len(out["down"]) <= constants.timers.OVERVIEW_SPARK_BINS + 2
    assert _vals(out["down"])[0] == 9e6 and _vals(out["down"])[-1] == 1e3
    assert _vals(out["cpu"])[0] == pytest.approx(90.0) and out["gpu"] == []


def test_session_bins_are_peak_for_network_and_mean_for_hardware():
    """Mutation guard (M13): a session bin is the PEAK of its network samples (a 1 s burst must show)
    and the MEAN of its hardware samples."""
    start = NOW - timedelta(hours=1)
    samples = _session_samples(start, 3600, lambda i: 5e6 if i % 100 == 7 else 1e3,
                               lambda i: 30.0 if i % 2 else 10.0)
    out = T._read_window(_Empty(), "TIMELINE_SESSION", NOW, start, samples, 1.0, False, None)[0]
    full = _vals(out["down"])[1:-1]                  # whole bins (31 s) - each holds a 5 MB/s second or not
    assert max(full) == 5e6 and all(v in (5e6, 1e3) for v in full)
    assert all(abs(v - 20.0) < 1.0 for v in _vals(out["cpu"])[1:-1])


def test_session_bins_stay_put_between_reloads():
    """The Session's bins are ts // bin, like the SQL path: six seconds later every sample lands in
    the same absolute bin, so the line doesn't shift each reload."""
    from netspeedtray.core.db.history_reader import overview_bin_seconds
    start = NOW - timedelta(minutes=50)
    samples = _session_samples(start, 3000, lambda i: 7e6 if i == 1234 else 1e3, lambda i: 10.0)
    for later in (0, 6, 12):
        now = NOW + timedelta(seconds=later)
        out = T._read_window(_Empty(), "TIMELINE_SESSION", now, start, samples, 1.0, False, None)[0]
        st, et = int(start.timestamp()), int(now.timestamp())
        b = overview_bin_seconds(st, et)
        spike = out["down"].index(7e6)
        assert st // b + spike == (int(start.timestamp()) + 1234) // b


# --- 1. cadence -------------------------------------------------------------------------------------

@pytest.mark.parametrize("key, expected", [
    ("TIMELINE_30_MIN", 6000), ("TIMELINE_4_HOURS", 6000),
    ("TIMELINE_12_HOURS", 30000), ("TIMELINE_24_HOURS", 30000), ("TIMELINE_48_HOURS", 30000),
    ("TIMELINE_WEEK", 90000), ("TIMELINE_MONTH", 90000), ("TIMELINE_ALL", 90000),
])
def test_reload_cadence_follows_the_window(key, expected):
    assert T._reload_interval_ms(key, NOW) == expected


@pytest.mark.parametrize("hours, expected", [(0.5, 6000), (3.9, 6000), (20, 30000), (72, 90000)])
def test_session_cadence_follows_the_session_length(hours, expected):
    assert T._reload_interval_ms("TIMELINE_SESSION", NOW, NOW - timedelta(hours=hours)) == expected


def test_long_reload_stays_below_the_read_connection_idle_limit():
    """WidgetState closes a thread's read connection after 120 s idle; the Overview's reader asks
    every 6 s and re-reads long windows every 90 s, so its connection is never pruned between reads."""
    assert constants.timers.OVERVIEW_LONG_RELOAD_MS < WidgetState._READ_CONN_IDLE_SEC * 1000
    assert constants.timers.OVERVIEW_SHORT_RELOAD_MS < WidgetState._READ_CONN_IDLE_SEC * 1000


class _CountingWS:
    """Counts every DB read; ``token`` is what get_history_data_version reports."""

    def __init__(self):
        self.token = (1, 1)
        self.reads = 0
        self.volume_reads = 0
        self.earliest_calls = []
        self.fail = False

    def get_history_data_version(self):
        return self.token

    def get_overview_window(self, start, end, b, want_series=True):
        from netspeedtray.core.db.history_reader import LeanSummary
        self.reads += 1
        return ({"down": [float(self.reads)], "up": [], "cpu": [], "gpu": [], "ram": []} if want_series else None,
                {"down": LeanSummary(1, 2.0, 3.0), "up": LeanSummary(1, 1.0, 1.0)}, not self.fail)

    def get_earliest_data_timestamp(self, flush=True):
        self.earliest_calls.append(flush)
        return NOW - timedelta(days=60)

    def get_history_volume_for_period(self, start, end, iface=None):
        from netspeedtray.core.db.history_reader import VolumeResult
        self.volume_reads += 1
        return VolumeResult(float(self.volume_reads), 0.0, 0.0, 0.0, False)


def _read(ws, cache, epoch=1, key="TIMELINE_24_HOURS", interval_ms=0, force=False, session=None):
    return T._read_window(ws, key, NOW, session, None, 1.0, False, None, cache=cache, epoch=epoch,
                          interval_ms=interval_ms, force=force)


def test_window_is_reread_only_when_its_interval_has_passed():
    now = [1000.0]
    ws, cache = _CountingWS(), T._ReadCache(clock=lambda: now[0])
    for step in range(5):                           # the 6 s requests within one 30 s interval
        ws.token = (1, step)                        # ... with writes in between
        _read(ws, cache, interval_ms=30000)
        now[0] += 6
    assert ws.reads == 1
    _read(ws, cache, interval_ms=30000)             # 30 s: due, and the DB changed
    assert ws.reads == 2
    _read(ws, cache, interval_ms=30000, force=True)  # a forced re-read (link change)
    assert ws.reads == 3


def test_unchanged_database_is_not_read_again():
    ws, cache = _CountingWS(), T._ReadCache()
    first = _read(ws, cache)
    assert ws.reads == 1
    second = _read(ws, cache)
    assert second[:3] == first[:3] and ws.reads == 1, "re-read a database nothing had written to"
    assert first[5] is True and second[5] is False   # fresh / reused
    ws.token = (1, 2)                               # the DB worker committed
    _read(ws, cache)
    assert ws.reads == 2
    _read(ws, cache, epoch=2)                       # range change / shown again / history deleted
    assert ws.reads == 3
    _read(ws, cache, epoch=2, key="TIMELINE_WEEK")  # another window
    assert ws.reads == 4
    ws.token = None                                 # unknown: always read
    _read(ws, cache, epoch=2, key="TIMELINE_WEEK")
    _read(ws, cache, epoch=2, key="TIMELINE_WEEK")
    assert ws.reads == 6


def test_an_unchanged_read_is_reused_only_for_a_while():
    now = [1000.0]
    ws, cache = _CountingWS(), T._ReadCache(clock=lambda: now[0])
    _read(ws, cache)
    now[0] += constants.timers.OVERVIEW_REUSE_MAX_AGE_MS / 1000.0 - 1
    _read(ws, cache)
    assert ws.reads == 1
    now[0] += 2
    _read(ws, cache)
    assert ws.reads == 2


def test_a_failed_read_is_never_reused():
    """A read the reader swallowed an error in (empty figures) is shown once, not kept as the
    window's state for the next minute and a half."""
    ws, cache = _CountingWS(), T._ReadCache()
    ws.fail = True
    _read(ws, cache, interval_ms=90000)
    _read(ws, cache, interval_ms=90000)
    assert ws.reads == 2
    ws.fail = False
    _read(ws, cache, interval_ms=90000)
    _read(ws, cache, interval_ms=90000)
    assert ws.reads == 3


def test_a_failing_v8_read_reports_it(tmp_path):
    r7, r8, conns = _marker_db(tmp_path)
    try:
        def broken():
            raise sqlite3.OperationalError("disk I/O error")
        r8._conn = broken
        series, summ, ok = r8.overview_window(NOW - timedelta(hours=24), NOW, 780)
        assert ok is False and summ["down"].count == 0
    finally:
        for c in conns:
            c.close()


def test_session_totals_are_read_on_every_request():
    """UX review of #594: the strip's session ↓/↑ totals rode inside the cached window read, so on a
    long window they froze for up to two minutes. They are read on every 6 s request."""
    ws, cache = _CountingWS(), T._ReadCache()
    session = NOW - timedelta(hours=2)
    vols = [_read(ws, cache, key="TIMELINE_WEEK", interval_ms=90000, session=session)[3] for _ in range(3)]
    assert ws.reads == 1 and ws.volume_reads == 3
    assert [v.up_bytes for v in vols] == [1.0, 2.0, 3.0]


def test_all_reloads_never_flush_and_resolve_the_earliest_once(tmp_path):
    """get_earliest_data_timestamp() queues the pending batch on the DB worker (a write) - it ran on
    every 6 s reload of "All". The Overview now reads the earliest sample without the flush, once
    per epoch."""
    r7, r8, conns = _marker_db(tmp_path)
    try:
        ws, cache = _WS(r8), T._ReadCache()
        for _ in range(3):
            T._read_window(ws, "TIMELINE_ALL", NOW, None, None, 1.0, False, None, cache=cache, epoch=1)
        assert ws.flushes == 0
        cws, cache2 = _CountingWS(), T._ReadCache()
        for i, epoch in enumerate((1, 1, 1, 2)):
            cws.token = (1, i)                      # a write between every reload: always re-read
            _read(cws, cache2, epoch=epoch, key="TIMELINE_ALL")
        assert cws.reads == 4
        assert cws.earliest_calls == [False, False]
    finally:
        for c in conns:
            c.close()


# --- data_version token --------------------------------------------------------------------------

def test_data_version_changes_only_when_another_connection_commits(tmp_path):
    r7, r8, conns = _marker_db(tmp_path)
    try:
        a = r8.data_version()
        assert a is not None and r8.data_version() == a
        rw = sqlite3.connect(tmp_path / "marker.db")
        rw.execute(f"INSERT INTO {S8.HW_RAW} (ts, metric_id, value) SELECT ?, metric_id, 1.0 FROM "
                   f"{S8.METRICS} LIMIT 1", (NOW_TS + 1,))
        rw.commit()
        rw.close()
        assert r8.data_version() != a
    finally:
        for c in conns:
            c.close()


def test_a_reopened_connection_never_matches_an_old_token(tmp_path):
    """Code review of #594 (mutation M12): (id(conn), data_version) collided 200/200 when the read
    connection was closed (idle pruning) and reopened with a commit in between - CPython reused the
    id and the new connection's counter restarted at the same value. The token keeps the connection
    object itself."""
    from netspeedtray.core.db.history_reader import V8HistoryReader
    r7, _r8, conns = _marker_db(tmp_path)
    for c in conns:
        c.close()
    path = tmp_path / "marker.db"
    rw = sqlite3.connect(path)
    holder = {}

    def conn():
        if holder.get("c") is None:
            holder["c"] = F.ro_conn(path)
        return holder["c"]

    r8 = V8HistoryReader(conn, lambda: path, clock=F.clock_at(NOW))
    try:
        for i in range(50):
            before = r8.data_version()
            holder.pop("c").close()                 # pruned
            rw.execute(f"INSERT INTO {S8.HW_RAW} (ts, metric_id, value) SELECT ?, metric_id, 1.0 FROM "
                       f"{S8.METRICS} LIMIT 1", (NOW_TS + 10 + i,))
            rw.commit()
            after = r8.data_version()               # a new connection, after the write
            assert after != before, f"collision on round {i}"
    finally:
        rw.close()
        if holder.get("c") is not None:
            holder["c"].close()


# --- mutation guards ---------------------------------------------------------------------------

def test_bin_length_rounds_up_to_whole_minutes():
    """Mutation guard (M8): a bin over a minute is a whole number of minutes, so "All"'s bins (and
    the drawn shape) only move when the window grows by a minute per point."""
    from netspeedtray.core.db.history_reader import overview_bin_seconds
    assert overview_bin_seconds(0, 86400) == 780            # 733 s -> 13 min
    assert overview_bin_seconds(0, 7 * 86400) % 60 == 0
    assert overview_bin_seconds(0, 30 * 60) == 16           # short windows keep second bins
    for span in (3 * 3600 + 7, 86400 + 1, 400 * 86400 + 59):
        b = overview_bin_seconds(0, span)
        assert b % 60 == 0 and span // b + 2 <= _SPARK_POINTS


def test_hardware_bins_are_the_mean(tmp_path):
    """Mutation guard (M10): a hardware sparkline bin is the mean of the values in it, not the peak."""
    r7, r8, conns = _marker_db(tmp_path, cpu=lambda t: 30.0 if (t // 60) % 2 else 10.0)
    try:
        series = r8.overview_window(NOW - timedelta(hours=24), NOW, 780)[0]
        inner = _vals(series["cpu"])[1:-1]
        assert inner and all(abs(v - 20.0) < 2.0 for v in inner), inner[:5]
    finally:
        for c in conns:
            c.close()


# --- tab behaviour --------------------------------------------------------------------------------

def _tab(ws=None, **mw_attrs):
    from netspeedtray.tests.unit.test_overview_tiles import _HeldWS, _overview_on
    return _overview_on(ws or _HeldWS(), **mw_attrs)


def test_peak_never_reads_below_what_was_just_seen_live(q_app):
    """UX review of #594: between reloads (up to 90 s) a new live peak was higher than the card's
    "Peak". The card's peak, scale and the sparkline's last bin follow the live maximum until a read
    made after those seconds were stored replaces it."""
    from netspeedtray.utils.summaries import summarize_raw
    ov = _tab()
    try:
        ov._series = {"down": [1e5, None, 2e5], "up": [1e4, 1e4, 1e4], "cpu": [], "gpu": [], "ram": []}
        ov._win_summ = {"down": summarize_raw([1e5, 2e5]), "up": summarize_raw([1e4])}
        ov._main_widget.download_speed = 80.0          # Mbps = 1e7 B/s, far above the window peak
        ov._render()
        sub = ov._hero._sub.text()
        assert "Peak  ↓ 80.0 Mbps" in sub, sub
        assert ov._hero._spark._series[-1] == pytest.approx(1e7)
        assert ov._hero._spark._scale_label.startswith("80.0")
        ov._main_widget.download_speed = 1.0           # the burst ended: the peak holds
        ov._render()
        assert "Peak  ↓ 80.0 Mbps" in ov._hero._sub.text()
        # A fresh read made long after the burst holds it (it was stored): the hold is released.
        import time as _t
        res = (ov._series, ov._win_summ, {}, None, None, True, _t.monotonic() + 60)
        ov._on_window_loaded(ov._load_seq, res)
        assert "Peak  ↓ 1.6 Mbps" in ov._hero._sub.text(), ov._hero._sub.text()
    finally:
        ov.teardown()


def test_a_reused_read_keeps_the_live_peak(q_app):
    from netspeedtray.utils.summaries import summarize_raw
    import time as _t
    ov = _tab()
    try:
        ov._win_summ = {"down": summarize_raw([1e5]), "up": summarize_raw([1e4])}
        ov._main_widget.download_speed = 80.0
        ov._render()
        res = (ov._series, ov._win_summ, {}, None, None, False, _t.monotonic() + 60)   # reused
        ov._on_window_loaded(ov._load_seq, res)
        assert "Peak  ↓ 80.0 Mbps" in ov._hero._sub.text()
    finally:
        ov.teardown()


def test_a_link_change_rereads_soon_after(q_app):
    """UX review of #594: after a drop, "steady" stayed green until the next long-window re-read.
    A change of latency_link_lost (either way) arms one forced re-read ~11 s later."""
    ov = _tab()
    try:
        ov._main_widget.latency_link_lost = False
        ov._render()
        assert not ov._link_timer.isActive()           # the first observation is not a change
        ov._main_widget.latency_link_lost = True
        ov._render()
        assert ov._link_timer.isActive()
        assert ov._link_timer.interval() == constants.timers.OVERVIEW_LINK_CHANGE_RELOAD_MS
        ov._link_timer.stop()
        ov._main_widget.latency_link_lost = False      # back
        ov._render()
        assert ov._link_timer.isActive()
    finally:
        ov.teardown()


def test_a_session_longer_than_the_buffer_is_read_from_the_db(q_app):
    """UX review of #594: the Session sparkline covered only the in-memory buffer (~83 min) while
    avg/peak covered the whole session. A session that began before the buffer's oldest sample
    reads its sparklines from the database."""
    from netspeedtray.tests.unit.test_overview_tiles import _HeldWS, _overview_on, _spin
    long_ws = _HeldWS()
    ov = _overview_on(long_ws, period=0, session_start=datetime.now() - timedelta(hours=3))
    ov._period_index = 0
    try:
        ov._reload_window()
        _spin(lambda: ov._win_summ is not None)
        assert long_ws.speed_calls == 1                # the DB series
    finally:
        ov.teardown()
    short_ws = _HeldWS()
    ov = _overview_on(short_ws, period=0, session_start=datetime.now() - timedelta(seconds=20))
    ov._period_index = 0
    try:
        ov._reload_window()
        _spin(lambda: ov._win_summ is not None)
        assert short_ws.speed_calls == 0               # the in-memory buffer covers it
    finally:
        ov.teardown()


def test_activating_the_monitor_reasks_when_stale(q_app):
    from PyQt6.QtCore import QEvent
    from netspeedtray.tests.unit.test_overview_tiles import _HeldWS, _spin
    ws = _HeldWS()
    ov = _tab(ws)
    try:
        ov._watch_window()
        ov._reload_window()
        _spin(lambda: ov._win_summ is not None and not ov._load_busy)
        before = ov._last_request_at
        ov.eventFilter(ov._filtered_window, QEvent(QEvent.Type.WindowActivate))
        assert ov._last_request_at == before           # fresh: nothing to do
        ov._last_request_at -= 60
        ov.eventFilter(ov._filtered_window, QEvent(QEvent.Type.WindowActivate))
        assert ov._last_request_at > before - 60
    finally:
        ov.teardown()


def test_history_delete_reloads_at_once(q_app):
    from PyQt6.QtCore import QObject, pyqtSignal
    from netspeedtray.tests.unit.test_overview_tiles import _HeldWS, _overview_on, _spin

    class _Sig(QObject):
        history_deleted = pyqtSignal(str)

    sig = _Sig()
    ws = _HeldWS()
    ws.history_deleted = sig.history_deleted
    ov = _overview_on(ws)
    try:
        ov._reload_window()
        _spin(lambda: ov._win_summ is not None and not ov._load_busy)
        seq, calls = ov._load_seq, ws.speed_calls
        sig.history_deleted.emit("done")
        assert ov._load_seq == seq + 1
        _spin(lambda: ws.speed_calls == calls + 1)
    finally:
        ov.teardown()


def test_sparkline_breaks_at_gaps_and_paints(q_app):
    """None is a gap: the sparkline paints runs separately (no crash, nothing drawn as zero)."""
    from netspeedtray.views.monitor.overview.tiles import Sparkline
    sp = Sparkline("#42B883")
    sp.resize(200, 60)
    assert Sparkline._runs([1.0, None, None, 2.0, 3.0, None, 4.0]) == [[0], [3, 4], [6]]
    for series in ([None, None], [1.0, None, 2.0], [None, 5.0, 6.0, None], [3.0]):
        sp.set_dual(series, series, "#4287F5", scale_label="1 Mbps", soft_last=True)
        sp.grab()
        sp.set_series(series, soft_last=True)
        sp.grab()


# --- follow-ups from the final review of #594 -------------------------------------------------------

def _hosted(ws, period=2):
    """The tab inside a real top-level window, shown for real (no isVisible patch), so Qt delivers
    its own show / hide / window-state events."""
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    from netspeedtray.tests.unit.test_overview_tiles import _overview_on
    ov = _overview_on(ws, period=period)
    del ov.isVisible                      # _overview_on patches it; this test wants the real one
    host = QWidget()
    QVBoxLayout(host).addWidget(ov)
    host.resize(900, 700)
    return host, ov


def _settle(ov):
    from netspeedtray.tests.unit.test_overview_tiles import _spin
    from PyQt6.QtWidgets import QApplication
    _spin(lambda: ov._win_summ is not None and not ov._load_busy and not ov._load_dirty)
    for _ in range(20):
        QApplication.processEvents()


def _count_requests(ov):
    """Every read the tab asks the reader for, with the tab method that asked."""
    import traceback
    asked = []

    def note(*_a):
        names = [f.name for f in traceback.extract_stack()]
        asked.append(next((n for n in reversed(names)
                           if n in ("eventFilter", "showEvent", "_on_window_loaded", "<lambda>")), "?"))
    ov._load_requested.connect(note)
    return asked


def test_restoring_the_minimized_monitor_asks_for_one_read(q_app):
    """Final review of #594: Qt delivers WindowStateChange to the window's event filter BEFORE the
    restore's spontaneous show (QWidget::event sends it while handling that state change). The filter
    asked for a read (the 6 s staleness check passed), then showEvent asked again: coalesced, but rerun
    after the first landed - a second session-volume + token read. showEvent alone handles a restore."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from netspeedtray.tests.unit.test_overview_tiles import _HeldWS
    ws = _HeldWS()
    host, ov = _hosted(ws)
    try:
        host.show()
        _settle(ov)
        asked = _count_requests(ov)
        host.setWindowState(Qt.WindowState.WindowMinimized)
        QApplication.processEvents()
        assert not ov._hist_timer.isActive()           # minimized: the 6 s asks pause (hideEvent)
        assert asked == []
        ov._last_request_at -= 60                      # stale, so the old filter path would fire
        host.setWindowState(Qt.WindowState.WindowNoState)
        _settle(ov)
        assert asked == ["showEvent"], asked
        assert ov._hist_timer.isActive()
    finally:
        ov.teardown()
        host.close()
        host.deleteLater()


def test_hiding_the_overview_stops_every_timer(q_app):
    """Final review of #594: nothing pinned that hideEvent stops the 11 s link timer and the history
    timer - leaving Overview (or minimizing) with either running would read the DB while unseen."""
    from netspeedtray.tests.unit.test_overview_tiles import _HeldWS
    host, ov = _hosted(_HeldWS())
    try:
        host.show()
        _settle(ov)
        ov._link_timer.start()
        assert ov._timer.isActive() and ov._hist_timer.isActive() and ov._link_timer.isActive()
        host.hide()
        assert not ov._timer.isActive()
        assert not ov._hist_timer.isActive()
        assert not ov._link_timer.isActive()
    finally:
        ov.teardown()
        host.close()
        host.deleteLater()


def test_a_link_change_ends_in_a_forced_reread(q_app):
    """End to end through the tab (the history reader mocked): a latency_link_lost change arms the
    single-shot, and its timeout re-reads the window although the window's cadence (24 h: 30 s) would
    reuse the last read."""
    from netspeedtray.tests.unit.test_overview_tiles import _HeldWS, _spin
    ws = _HeldWS()
    host, ov = _hosted(ws, period=2)
    try:
        host.show()
        _settle(ov)
        calls = ws.speed_calls
        assert calls >= 1
        ov._reload_window()                            # an ordinary ask is reused: cadence not due
        _settle(ov)
        assert ws.speed_calls == calls
        ov._link_timer.setInterval(30)                 # 11 s in the app; the wiring is what is tested
        ov._main_widget.latency_link_lost = False
        ov._render()
        ov._main_widget.latency_link_lost = True
        ov._render()
        assert ov._link_timer.isActive()
        _spin(lambda: ws.speed_calls == calls + 1)
        _settle(ov)
        assert ws.speed_calls == calls + 1
        assert not ov._force_reload                    # consumed by that read
    finally:
        ov.teardown()
        host.close()
        host.deleteLater()


def test_a_gap_paints_no_pixels(q_app):
    """Final review of #594: the paint test above only proves None doesn't crash - drawing None as 0
    would pass it. Render a series with a gap in the middle: a column inside the gap is empty, columns
    on both sides are not (line + fill)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage
    from netspeedtray.views.monitor.overview.tiles import Sparkline
    w, h = 294, 60                          # pad 2 + 29 steps of 10 px
    series = [5.0 + (i % 3) for i in range(30)]
    for i in range(10, 20):
        series[i] = None                    # bins 10-19 measured nothing: x 102..192 must stay empty
    for paint in ("single", "dual"):
        sp = Sparkline("#42B883")
        sp.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        sp.resize(w, h)
        if paint == "single":
            sp.set_series(series)
        else:
            sp.set_dual(series, [v / 2 if v is not None else None for v in series], "#4287F5")
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        sp.render(img)

        def inked(x):
            return sum(1 for y in range(h) if img.pixelColor(x, y).alpha() > 0)

        assert inked(147) == 0, (paint, inked(147))          # mid-gap
        assert inked(120) == 0 and inked(175) == 0, paint    # anywhere well inside it
        assert inked(50) > 3, (paint, inked(50))             # before: line + fill
        assert inked(250) > 3, (paint, inked(250))           # after
