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


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_lean_summaries_match_summarize_network(tmp_path, seed):
    """Count / avg / peak of the lean read == summarize_network's, both directions, every window
    length (raw only, raw + minute, + hour, + migrated rows with NULL bytes and a zero count)."""
    r8, conn = _make_db(tmp_path, seed)
    try:
        for win in WINDOWS:
            start, end = NOW - win, NOW
            from netspeedtray.core.db.history_reader import overview_bin_seconds
            b = overview_bin_seconds(int(start.timestamp()), int(end.timestamp()))
            series, lean = r8.overview_window(start, end, b, want_series=True)
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
    epoch-aligned bin its peak - all of it, not the last 120 minutes."""
    r8, conn = _make_db(tmp_path, 11)
    try:
        start, end = NOW - timedelta(hours=24), NOW
        from netspeedtray.core.db.history_reader import overview_bin_seconds
        b = overview_bin_seconds(int(start.timestamp()), int(end.timestamp()))
        series, _ = r8.overview_window(start, end, b)
        rows = r8.speed_history(start, end, None, return_raw=True, resolution="minute")
        bins = {}
        for ts, up, down in rows:
            k = int(ts) // b
            pu, pd = bins.get(k, (0.0, 0.0))
            bins[k] = (max(pu, up), max(pd, down))
        oracle_down = [bins[k][1] for k in sorted(bins)]
        oracle_up = [bins[k][0] for k in sorted(bins)]
        assert len(series["down"]) == len(oracle_down) <= _SPARK_POINTS
        assert all(_rel(a, e) < 1e-9 for a, e in zip(series["down"], oracle_down))
        assert all(_rel(a, e) < 1e-9 for a, e in zip(series["up"], oracle_up))
        # The fixture's raw history starts 20 h back: the first points are ~20 h old, not 2 h.
        assert len(series["down"]) >= 20 * 3600 // b - 1
    finally:
        conn.close()


def _marker_db(tmp_path):
    """24 h of one-minute samples: 9 MB/s and CPU 90 % in the window's first hour, 1 kB/s and 10 %
    after, so a sparkline that dropped the start of the window cannot show the marker."""
    h = F.History(now_ts=NOW_TS, wm_raw=NOW_TS - 30 * 3600, wm_minute=NOW_TS - 40 * DAY)
    first = NOW_TS - 24 * 3600 + 120
    for t in range(first, NOW_TS - 5, 60):
        hot = t < first + 3600
        h.raw.append((t, "Ethernet", 500, 9_000_000 if hot else 1_000))
        h.hw_raw.append((t, "cpu", 90.0 if hot else 10.0))
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
            drawn = series[key][-_SPARK_POINTS:]            # what Sparkline.set_series keeps
            assert drawn[0] == pytest.approx(hot), f"{key}: the window's first hour is not drawn"
            assert len(series[key]) <= _SPARK_POINTS, key
            assert drawn[-1] < hot / 5, key
    finally:
        for c in conns:
            c.close()


def test_session_series_is_spread_over_the_whole_session():
    """The Session's in-memory series (one value per poll) is downsampled across all of it."""
    n = 3600
    series = {"down": [9e6] * 60 + [1e3] * (n - 60), "up": [1.0] * n,
              "cpu": [90.0] * 60 + [10.0] * (n - 60), "gpu": [], "ram": [50.0] * n}

    class _Empty:
        def summarize_network(self, *a):
            from netspeedtray.utils.summaries import summarize_raw
            return summarize_raw([])

        summarize_hardware = summarize_network   # read by the pre-fix Overview

    out = T._read_window(_Empty(), "TIMELINE_SESSION", NOW, NOW - timedelta(hours=1), series, 1.0,
                         False, None)[0]
    assert len(out["down"]) <= constants.timers.OVERVIEW_SPARK_BINS and out["down"][0] == 9e6 and out["down"][-1] == 1e3
    assert out["cpu"][0] == pytest.approx(90.0) and out["gpu"] == []


# --- 1. cadence -------------------------------------------------------------------------------------

@pytest.mark.parametrize("key, expected", [
    ("TIMELINE_SESSION", 6000), ("TIMELINE_30_MIN", 6000), ("TIMELINE_4_HOURS", 6000),
    ("TIMELINE_12_HOURS", 30000), ("TIMELINE_24_HOURS", 30000), ("TIMELINE_48_HOURS", 30000),
    ("TIMELINE_WEEK", 120000), ("TIMELINE_MONTH", 120000), ("TIMELINE_ALL", 120000),
])
def test_reload_cadence_follows_the_window(key, expected):
    assert T._reload_interval_ms(key, NOW) == expected


@pytest.mark.parametrize("index, expected", [(0, 6000), (2, 30000), (3, 120000), (5, 120000)])
def test_overview_timer_uses_the_window_cadence(q_app, index, expected):
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    from netspeedtray.constants.i18n import I18nStrings
    ov = T.OverviewTab(_MW(), _cfg(history_period_slider_value=index, history_period_chosen=True),
                       I18nStrings("en_US"))
    try:
        assert ov._hist_timer.interval() == expected
    finally:
        ov.teardown()


# --- 2. reuse an unchanged read; no flush on All ---------------------------------------------------

class _CountingWS:
    """Counts every DB read; ``token`` is what get_history_data_version reports."""

    def __init__(self):
        self.token = (1, 1)
        self.reads = 0
        self.earliest_calls = []

    def get_history_data_version(self):
        return self.token

    def get_overview_window(self, start, end, b, want_series=True):
        from netspeedtray.core.db.history_reader import LeanSummary
        self.reads += 1
        return ({"down": [float(self.reads)], "up": [], "cpu": [], "gpu": [], "ram": []} if want_series else None,
                {"down": LeanSummary(1, 2.0, 3.0), "up": LeanSummary(1, 1.0, 1.0)})

    def get_earliest_data_timestamp(self, flush=True):
        self.earliest_calls.append(flush)
        return NOW - timedelta(days=60)


def test_unchanged_database_is_not_read_again():
    ws, cache = _CountingWS(), T._ReadCache()
    read = lambda epoch=1, key="TIMELINE_24_HOURS": T._read_window(
        ws, key, NOW, None, None, 1.0, False, None, cache=cache, epoch=epoch)
    first = read()
    assert ws.reads == 1
    assert read() == first and ws.reads == 1, "re-read a database nothing had written to"
    ws.token = (1, 2)                               # the DB worker committed
    read()
    assert ws.reads == 2
    read(epoch=2)                                   # range change / shown again / history deleted
    assert ws.reads == 3
    read(epoch=2, key="TIMELINE_WEEK")              # another window
    assert ws.reads == 4
    ws.token = None                                 # unknown: always read
    read(epoch=2, key="TIMELINE_WEEK")
    read(epoch=2, key="TIMELINE_WEEK")
    assert ws.reads == 6


def test_an_unchanged_read_is_reused_only_for_a_while():
    now = [1000.0]
    ws, cache = _CountingWS(), T._ReadCache(clock=lambda: now[0])
    read = lambda: T._read_window(ws, "TIMELINE_24_HOURS", NOW, None, None, 1.0, False, None,
                                  cache=cache, epoch=1)
    read()
    now[0] += constants.timers.OVERVIEW_REUSE_MAX_AGE_MS / 1000.0 - 1
    read()
    assert ws.reads == 1
    now[0] += 2
    read()
    assert ws.reads == 2


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
            T._read_window(cws, "TIMELINE_ALL", NOW, None, None, 1.0, False, None, cache=cache2, epoch=epoch)
        assert cws.reads == 4
        assert cws.earliest_calls == [False, False]
    finally:
        for c in conns:
            c.close()


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
