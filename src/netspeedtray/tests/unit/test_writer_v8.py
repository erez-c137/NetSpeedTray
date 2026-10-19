"""
The v8 writer (database v8 step 6 - dormant: nothing in the app calls it yet).

What a poll becomes on disk (exact bytes over the measured interval, the gap flag, err/drop deltas,
link events, link speed/duplex), the heartbeat + crash semantics of ``app_sessions``, roll-ups that
MOVE data with their watermark in the same transaction, delete-history dropping every cached id, and
Gate 5 (fractional loss survives the roll-ups). The process-kill half is
``tests/integration/test_writer_v8_kill.py``.

Volume is read through BOTH ``V8HistoryReader`` (step 3) and ``_volume`` below - a minimal vendored
copy of the watermark-precedence rule in ``schema_v8`` - so a reader regression and a writer
regression cannot mask each other.
"""
import logging
import os
import sqlite3
import sys
import time
from collections import namedtuple
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.append(os.path.abspath("src"))
from netspeedtray import constants
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.core.db import schema_v8 as S
from netspeedtray.core.db import writer_v8 as W

from netspeedtray.core.db.history_reader import V8HistoryReader

Ctr = namedtuple("Ctr", "bytes_sent bytes_recv packets_sent packets_recv errin errout dropin dropout")
Nic = namedtuple("Nic", "isup duplex speed mtu flags")

NOW = int(time.time())   # the reader judges watermarks against the real clock


def _ctr(sent, recv, errin=0, errout=0, dropin=0, dropout=0):
    return Ctr(sent, recv, 0, 0, errin, errout, dropin, dropout)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "speed_history.db")
    S.create_v8_schema(c)
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")
    c.commit()
    yield c
    c.close()


def _writer(conn, poll_ms=1000, **kw):
    w = W.WriterV8(conn, version="test", poll_ms=poll_ms, **kw)
    return w


def _wm(c, key):
    r = c.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return None if r is None else int(r[0])


def _volume(c, iface=None):
    """Vendored minimal reader: exactly one tier per sub-range, by the watermarks (absent = unbounded).
    Exact bytes only (every row this writer makes has them)."""
    wr, wmn = _wm(c, S.WATERMARK_RAW_KEY), _wm(c, S.WATERMARK_MINUTE_KEY)
    big = 1 << 62
    lo_r = -big if wr is None else wr
    lo_m = -big if wmn is None else wmn
    hi_m = big if wr is None else wr
    hi_h = min(hi_m, big if wmn is None else wmn)
    f, p = ("", ()) if iface is None else (
        " AND iface_id = (SELECT iface_id FROM interfaces WHERE name = ?)", (iface,))
    tot = [0, 0]
    for sql, args in ((f"SELECT SUM(up_bytes), SUM(down_bytes) FROM net_raw WHERE ts >= ?{f}", (lo_r,)),
                      (f"SELECT SUM(up_bytes), SUM(down_bytes) FROM net_minute WHERE ts >= ? AND ts < ?{f}",
                       (lo_m, hi_m)),
                      (f"SELECT SUM(up_bytes), SUM(down_bytes) FROM net_hour WHERE ts < ?{f}", (hi_h,))):
        r = c.execute(sql, args + p).fetchone()
        tot[0] += r[0] or 0
        tot[1] += r[1] or 0
    return tuple(tot)


def _all_rows_volume(c):
    """Plain SUM over every tier: equals _volume only when no row is shadowed or duplicated."""
    return tuple(sum(c.execute(f"SELECT COALESCE(SUM({col}), 0) FROM {t}").fetchone()[0]
                     for t in (S.NET_RAW, S.NET_MINUTE, S.NET_HOUR)) for col in ("up_bytes", "down_bytes"))


def _reader_volume(db_path, conn_factory):
    rc = conn_factory()
    r = V8HistoryReader(lambda: rc, lambda: db_path, logging.getLogger("t"))
    v = r.volume(None, datetime.fromtimestamp(NOW + 3600), None, 1.0)
    rc.close()
    assert not v.reconstructed
    return (round(v.up_bytes), round(v.down_bytes))


def _deltas_like_the_controller(prev, cur, elapsed):
    """The REAL controller code that computes the odometer deltas (what step 7 hands the writer)."""
    from netspeedtray.core.controller import StatsController
    stub = SimpleNamespace(last_interface_counters=prev, _odometer_cap_warned=False,
                           logger=logging.getLogger("t"))
    return StatsController._odometer_byte_deltas(stub, cur, elapsed)


def _feed(w, start_ts, interval_s, n, rng, names=("Wi-Fi 3", "Ethernet")):
    """n polls at interval_s from start_ts. Returns the exact counter delta per name."""
    counters = {nm: _ctr(10_000_000 * (i + 1), 50_000_000 * (i + 1)) for i, nm in enumerate(names)}
    first = dict(counters)
    w.add_net_sample(W.NetSample(start_ts, 0 or 1, {}, counters=counters))   # prime
    t = start_ts
    for _ in range(n):
        t += interval_s
        nxt = {nm: _ctr(c.bytes_sent + rng.randrange(0, 3_000_000), c.bytes_recv + rng.randrange(0, 9_000_000))
               for nm, c in counters.items()}
        deltas = _deltas_like_the_controller(counters, nxt, interval_s)
        w.add_net_sample(W.NetSample(t, int(interval_s * 1000), deltas, counters=nxt))
        counters = nxt
    return {nm: (counters[nm].bytes_sent - first[nm].bytes_sent, counters[nm].bytes_recv - first[nm].bytes_recv)
            for nm in names}


# --------------------------------------------------------------------------------------------
# bytes at capture
# --------------------------------------------------------------------------------------------

def test_volume_is_exact_across_poll_rate_presets_and_rollups(conn, tmp_path):
    """1/2/5/10 s presets, each phase at an age that lands it in a different tier after maintenance:
    the stored volume equals the counter deltas exactly, in every tier and through the reader."""
    import random
    rng = random.Random(8)
    w = _writer(conn)
    w.start_session(NOW - 32 * 86400)
    expected = [0, 0]
    phases = ((1, NOW - 31 * 86400, 900), (2, NOW - 2 * 86400, 600),
              (5, NOW - 3 * 3600, 300), (10, NOW - 3600, 200))
    for preset, start, n in phases:
        w.set_poll_ms(preset * 1000)
        got = _feed(w, start, preset, n, rng)
        for up, down in got.values():
            expected[0] += up
            expected[1] += down
        assert w.flush(start + preset * n)
    # Before maintenance: every byte is in the raw tier, one row per poll per adapter, dt = the preset.
    assert _volume(conn) == tuple(expected) == _all_rows_volume(conn)
    dts = {r[0] for r in conn.execute("SELECT DISTINCT dt_ms FROM net_raw")}
    assert dts == {1000, 2000, 5000, 10000}
    assert conn.execute("SELECT COUNT(*) FROM net_raw WHERE flags & ? != 0", (S.FLAG_GAP,)).fetchone()[0] == 0

    w.run_maintenance(NOW)
    assert conn.execute("SELECT COUNT(*) FROM net_hour").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM net_minute").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM net_raw").fetchone()[0] > 0
    assert _volume(conn) == tuple(expected) == _all_rows_volume(conn)
    # Exact bytes are never NULL in tiers this writer produced.
    for t in (S.NET_MINUTE, S.NET_HOUR):
        assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE up_bytes IS NULL OR down_bytes IS NULL"
                            ).fetchone()[0] == 0
    db = tmp_path / "speed_history.db"
    assert _reader_volume(db, lambda: sqlite3.connect(db)) == tuple(expected)


def test_two_intervals_ending_in_the_same_second_merge_not_drop(conn):
    w = _writer(conn)
    w.add_net_sample(W.NetSample(NOW + 0.10, 600, {"Wi-Fi 3": (100, 1000)}))
    w.add_net_sample(W.NetSample(NOW + 0.95, 850, {"Wi-Fi 3": (7, 70)}))
    assert w.flush(NOW)
    w.add_net_sample(W.NetSample(NOW + 0.99, 40, {"Wi-Fi 3": (1, 1)}))   # same second, next flush
    assert w.flush(NOW + 1)
    assert conn.execute("SELECT ts, up_bytes, down_bytes, dt_ms FROM net_raw").fetchall() == [
        (NOW, 108, 1071, 1490)]


def test_all_zero_and_non_finite_samples_are_not_written(conn):
    w = _writer(conn)
    w.add_net_sample(W.NetSample(NOW, 1000, {"a": (0, 0), "b": (float("nan"), 5), "c": (0.4, 0.4)}))
    w.add_hw_sample(NOW, "cpu", float("inf"))
    assert w.flush(NOW)
    assert conn.execute("SELECT COUNT(*) FROM net_raw").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM hw_raw").fetchone()[0] == 0


@pytest.mark.parametrize("poll_ms, dt_ms, gap", [
    (1000, 1000, False), (1000, 9000, False), (1000, 10000, False), (1000, 10001, True),
    (1000, 3_600_000, True), (2000, 10000, False), (10000, 40000, False), (10000, 50000, False),
    (10000, 50001, True),
])
def test_gap_flag_follows_the_controller_validity_threshold(conn, poll_ms, dt_ms, gap):
    w = _writer(conn, poll_ms=poll_ms)
    w.add_net_sample(W.NetSample(NOW, dt_ms, {"Wi-Fi 3": (dt_ms * 10, dt_ms * 10)}))
    assert w.flush(NOW)
    flags, b, dt = conn.execute("SELECT flags, up_bytes, dt_ms FROM net_raw").fetchone()
    assert bool(flags & S.FLAG_GAP) is gap
    assert (b, dt) == (dt_ms * 10, dt_ms)   # bytes kept either way
    # The compat view's rate is the interval average (10 kB/s here), never bytes-per-second-of-poll.
    assert conn.execute("SELECT upload_bytes_sec FROM speed_history_raw").fetchone()[0] == pytest.approx(10_000)


def test_gap_threshold_is_the_controllers():
    spd = constants.network.speed
    for poll_ms in (500, 1000, 2000, 5000, 10000):
        assert W.gap_threshold_ms(poll_ms) == max(spd.RATE_VALIDITY_MIN_SECONDS, poll_ms / 1000 *
                                                  spd.RATE_VALIDITY_POLL_MULTIPLE) * 1000
    src = Path(__file__).resolve().parents[2] / "core" / "controller.py"
    assert "RATE_VALIDITY_MIN_SECONDS" in src.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------------
# err / drop counters
# --------------------------------------------------------------------------------------------

def test_err_drop_deltas_are_per_interface_metrics_and_clamp_resets(conn):
    w = _writer(conn)
    seq = [  # errin, dropout; bytes go backwards at step 3 (counter reset)
        (0, 0, 1000), (3, 0, 2000), (3, 2, 3000), (1, 2, 10), (4, 5, 20)]
    prev = None
    for i, (ei, do, b) in enumerate(seq):
        c = {"Ethernet": _ctr(b, b, errin=ei, dropout=do)}
        deltas = {} if prev is None else _deltas_like_the_controller(prev, c, 1.0)
        w.add_net_sample(W.NetSample(NOW + i, 1000, deltas, counters=c))
        prev = c
    assert w.flush(NOW + 10)
    iid = conn.execute("SELECT iface_id FROM interfaces WHERE name = 'Ethernet'").fetchone()[0]
    rows = conn.execute(
        "SELECT h.ts - ?, m.name, m.entity, m.unit, h.value FROM hw_raw h JOIN metrics m USING (metric_id) "
        "ORDER BY h.ts, m.name", (NOW,)).fetchall()
    ent = f"iface:{iid}"
    assert rows == [(1, "errin", ent, "count", 3.0), (2, "dropout", ent, "count", 2.0),
                    (4, "dropout", ent, "count", 3.0), (4, "errin", ent, "count", 3.0)]
    # The reset interval: errin 3 -> 1 counts 0, never negative; its bytes row carries the reset flag.
    flags = dict(conn.execute("SELECT ts - ?, flags FROM net_raw", (NOW,)).fetchall())
    assert flags[3] & S.FLAG_COUNTER_RESET and not flags[4] & S.FLAG_COUNTER_RESET
    # Per-entity rows never reach the v7-named compat views (a 2.1.x build would sum them into 'cpu'...).
    assert conn.execute("SELECT COUNT(*) FROM hardware_stats_raw").fetchone()[0] == 0


def test_two_err_deltas_in_one_second_add_up(conn):
    w = _writer(conn)
    w.add_net_sample(W.NetSample(NOW, 1000, {}, counters={"e": _ctr(0, 0, errin=0)}))
    w.add_net_sample(W.NetSample(NOW + 1.1, 500, {}, counters={"e": _ctr(0, 0, errin=2)}))
    assert w.flush(NOW + 2)
    w.add_net_sample(W.NetSample(NOW + 1.9, 500, {}, counters={"e": _ctr(0, 0, errin=7)}))
    assert w.flush(NOW + 3)
    assert conn.execute("SELECT value FROM hw_raw").fetchall() == [(7.0,)]


# --------------------------------------------------------------------------------------------
# link events, link speed / duplex
# --------------------------------------------------------------------------------------------

def test_link_events_are_written_on_change_only(conn):
    w = _writer(conn)
    states = [True, True, True, False, False, True, True]
    for i, up in enumerate(states):
        w.add_net_sample(W.NetSample(NOW + i, 1000, {}, if_stats={"Ethernet": Nic(up, 2, 1000, 1500, "")}))
    assert w.flush(NOW + 10)
    assert conn.execute("SELECT ts - ?, event FROM link_events ORDER BY ts", (NOW,)).fetchall() == [
        (0, 1), (3, 0), (5, 1)]
    # A restart into the same state writes nothing; into a different one, one event.
    w2 = _writer(conn)
    w2.add_net_sample(W.NetSample(NOW + 20, 1000, {}, if_stats={"Ethernet": Nic(True, 2, 1000, 1500, "")}))
    assert w2.flush(NOW + 20)
    assert conn.execute("SELECT COUNT(*) FROM link_events").fetchone()[0] == 3
    w3 = _writer(conn)
    w3.add_net_sample(W.NetSample(NOW + 30, 1000, {}, if_stats={"Ethernet": Nic(False, 2, 0, 1500, "")}))
    assert w3.flush(NOW + 30)
    assert conn.execute("SELECT ts - ?, event FROM link_events ORDER BY ts DESC LIMIT 1", (NOW,)).fetchone() == (30, 0)


def test_link_speed_and_duplex_are_rate_limited_per_interface_metrics(conn):
    w = _writer(conn)
    speeds = {0: 866, 1: 866, 5: 400, 30: 433, 70: 433, 71: 866, 200: 866}
    for t, sp in speeds.items():
        w.add_net_sample(W.NetSample(NOW + t, 1000, {}, if_stats={"Wi-Fi 3": Nic(True, 2, sp, 1500, "")}))
    assert w.flush(NOW + 300)
    iid = conn.execute("SELECT iface_id FROM interfaces").fetchone()[0]
    rows = conn.execute(
        "SELECT h.ts - ?, m.name, m.unit, h.value FROM hw_raw h JOIN metrics m USING (metric_id) "
        "WHERE m.entity = ? ORDER BY h.ts, m.name", (NOW, f"iface:{iid}")).fetchall()
    # First observation writes both; the 5 s / 30 s changes wait out the 60 s interval and the value
    # pending at 70 s (433) is written then; 71 s is again inside the interval -> written at 200 s.
    assert rows == [(0, "link_duplex", "enum", 2.0), (0, "link_speed", "mbps", 866.0),
                    (70, "link_speed", "mbps", 433.0), (200, "link_speed", "mbps", 866.0)]


# --------------------------------------------------------------------------------------------
# sessions: heartbeat + crash
# --------------------------------------------------------------------------------------------

def test_heartbeat_on_every_flush_and_crash_is_bounded_by_the_last_one(conn, tmp_path):
    t0 = NOW - 600
    w = _writer(conn)
    sid = w.start_session(t0)
    for k in (1, 2, 3):
        assert w.flush(t0 + 10 * k)     # idle ticks still heartbeat
    # Crash: no end_session. The next run is a new session; the crashed one keeps its last heartbeat.
    w2 = _writer(conn, poll_ms=2000)
    sid2 = w2.start_session(t0 + 100)
    assert sid2 != sid
    rows = conn.execute("SELECT session_id, started_ts - ?, last_seen_ts - ?, ended_ts, clean_exit, poll_ms "
                        "FROM app_sessions ORDER BY session_id", (t0, t0)).fetchall()
    assert rows == [(sid, 0, 30, None, 0, 1000), (sid2, 100, 100, None, 0, 2000)]
    w2.end_session(t0 + 150)
    assert conn.execute("SELECT last_seen_ts - ?, ended_ts - ?, clean_exit FROM app_sessions WHERE session_id = ?",
                        (t0, t0, sid2)).fetchone() == (150, 150, 1)
    db = tmp_path / "speed_history.db"
    rc = sqlite3.connect(db)
    cov = V8HistoryReader(lambda: rc, lambda: db, logging.getLogger("t")).coverage(
        datetime.fromtimestamp(t0), datetime.fromtimestamp(t0 + 200))
    rc.close()
    assert cov.sessions == ((t0, t0 + 30), (t0 + 100, t0 + 150))


def test_two_instances_starting_in_the_same_second_get_two_sessions(conn):
    a, b = _writer(conn), _writer(conn)
    assert a.start_session(NOW) != b.start_session(NOW)


def test_heartbeat_recreates_a_session_whose_row_is_gone(conn):
    w = _writer(conn)
    w.start_session(NOW)
    conn.execute("DELETE FROM app_sessions")
    conn.commit()
    assert w.flush(NOW + 10)
    assert conn.execute("SELECT started_ts - ?, last_seen_ts - ? FROM app_sessions", (NOW, NOW)).fetchall() == [
        (10, 10)]


# --------------------------------------------------------------------------------------------
# roll-ups
# --------------------------------------------------------------------------------------------

def _dump(c):
    return {t: sorted(c.execute(f"SELECT * FROM {t}").fetchall())
            for t in (S.NET_RAW, S.NET_MINUTE, S.NET_HOUR, S.HW_RAW, S.HW_MINUTE, S.HW_HOUR, S.METADATA)}


def test_rollups_move_rows_write_watermarks_and_are_idempotent(conn):
    w = _writer(conn)
    w.start_session(NOW - 40 * 86400)
    base = NOW - 35 * 86400
    for i in range(0, 3 * 3600, 7):          # 3 h of samples, 35 days old
        w.add_net_sample(W.NetSample(base + i, 7000, {"Wi-Fi 3": (i + 1, 2 * i + 1)}))
        w.add_hw_sample(base + i, "cpu", float(i % 100))
    for i in range(0, 3600, 3):              # 1 h, 2 days old
        w.add_net_sample(W.NetSample(NOW - 2 * 86400 + i, 3000, {"Wi-Fi 3": (5, 9)}))
        w.add_hw_sample(NOW - 2 * 86400 + i, "cpu", 50.0)
    for i in range(0, 600, 1):               # 10 min, fresh
        w.add_net_sample(W.NetSample(NOW - 900 + i, 1000, {"Wi-Fi 3": (1, 1)}))
    assert w.flush(NOW - 1)
    before = _volume(conn)
    out = w.run_maintenance(NOW)
    wr, wmn = _wm(conn, S.WATERMARK_RAW_KEY), _wm(conn, S.WATERMARK_MINUTE_KEY)
    assert wr == (NOW - 86400) // 60 * 60 and wmn == (NOW - 30 * 86400) // 3600 * 3600
    assert _wm(conn, S.HW_WATERMARK_RAW_KEY) == wr and _wm(conn, S.HW_WATERMARK_MINUTE_KEY) == wmn
    assert out["net_raw_moved"] > 0 and out["net_minute_moved"] > 0
    # Moved, not copied: no finer row below its watermark, so a plain SUM over every tier agrees.
    for t, k in ((S.NET_RAW, wr), (S.NET_MINUTE, wmn), (S.HW_RAW, wr), (S.HW_MINUTE, wmn)):
        assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE ts < ?", (k,)).fetchone()[0] == 0
    assert _volume(conn) == before == _all_rows_volume(conn)
    # Rates: minute rows hold the sample-rate stats; hour rows the sample-weighted mean and the max.
    m = conn.execute("SELECT up_bytes, up_avg, up_max, sample_count FROM net_minute ORDER BY ts LIMIT 1 OFFSET 5").fetchone()
    # 20 polls x 3 s: sample_count is the 60 measured SECONDS, avg the time-weighted rate (5 B / 3 s).
    assert m[3] == 60 and m[0] == 20 * 5 and m[1] == pytest.approx(5 / 3) and m[2] == pytest.approx(5 / 3)
    # Hour rows: seconds = per minute MIN(ROUND(sum dt / 1000), 60), summed, capped at 3600.
    per_min = {}
    for i in range(0, 3 * 3600, 7):
        per_min[(base + i) // 60 * 60] = per_min.get((base + i) // 60 * 60, 0) + 7
    per_hour = {}
    for mts, secs in per_min.items():
        per_hour[mts // 3600 * 3600] = per_hour.get(mts // 3600 * 3600, 0) + min(secs, 60)
    assert dict(conn.execute("SELECT ts, sample_count FROM net_hour").fetchall()) == {
        k: min(v, 3600) for k, v in per_hour.items()}
    hw = conn.execute("SELECT SUM(avg_value * sample_count), SUM(sample_count), MIN(min_value), MAX(max_value) "
                      "FROM hw_hour").fetchone()
    assert hw[0] == pytest.approx(sum(float(i % 100) for i in range(0, 3 * 3600, 7))) and hw[2:] == (0.0, 99.0)
    # A second pass moves nothing and changes nothing.
    snap = _dump(conn)
    w.run_maintenance(NOW)
    assert _dump(conn) == snap


def test_late_row_below_the_watermark_merges_into_the_existing_bucket(conn):
    """A clock set back writes a raw row the minute tier already answers for: the next pass merges it
    (bytes add, samples add) - never a second row, never lost."""
    w = _writer(conn)
    w.start_session(NOW - 3 * 86400)
    t = (NOW - 2 * 86400) // 60 * 60
    w.add_net_sample(W.NetSample(t + 10, 1000, {"e": (100, 100)}))
    w.flush(NOW)
    w.run_maintenance(NOW)
    w.add_net_sample(W.NetSample(t + 20, 1000, {"e": (5, 7)}))
    w.flush(NOW)
    w.run_maintenance(NOW)
    assert conn.execute("SELECT ts, up_bytes, down_bytes, sample_count FROM net_minute").fetchall() == [
        (t, 105, 107, 2)]
    assert _volume(conn) == (105, 107) == _all_rows_volume(conn)


def test_covered_ms_comes_from_the_session_union(conn):
    w = _writer(conn)
    t = (NOW - 3 * 86400) // 3600 * 3600
    # Session A [t+30, t+90) crashed (heartbeat at t+90); session B [t+60, t+200) overlaps it.
    conn.execute("INSERT INTO app_sessions (started_ts, last_seen_ts, version, poll_ms) VALUES (?, ?, 'x', 1000)",
                 (t + 30, t + 90))
    conn.execute("INSERT INTO app_sessions (started_ts, last_seen_ts, ended_ts, version, poll_ms, clean_exit) "
                 "VALUES (?, ?, ?, 'x', 1000, 1)", (t + 60, t + 200, t + 200))
    conn.commit()
    for s in (t + 40, t + 61, t + 130, t + 400):
        w.add_net_sample(W.NetSample(s, 1000, {"e": (1, 1)}))
    w.flush(NOW)
    w.run_maintenance(NOW)
    cov = dict(conn.execute("SELECT ts - ?, covered_ms FROM net_minute", (t,)).fetchall())
    # minute 0: [30,60) = 30 s; minute 1: [60,120) = 60 s; minute 2: [120,180) = 60 s; minute 6: none.
    assert cov == {0: 30_000, 60: 60_000, 120: 60_000, 360: None}


def test_cutoff_never_lowers_a_valid_watermark_and_ignores_a_future_one(conn):
    """After a migration the raw watermark is 'now at migration' (the folded minutes end there):
    lowering it to now - 24 h would hide them. A watermark past now + 300 s is a wrong clock."""
    w = _writer(conn)
    w.start_session(NOW - 7200)
    m = (NOW - 3600) // 60 * 60
    conn.execute("INSERT INTO interfaces (iface_id, stable_key, name, first_seen, last_seen) "
                 "VALUES (1, 'e', 'e', 0, 0)")
    conn.execute("INSERT INTO net_minute (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count) "
                 "VALUES (?, 1, 2.0, 2.0, 2.0, 2.0, 60)", (m,))      # a folded (legacy, NULL bytes) minute
    conn.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", (S.WATERMARK_RAW_KEY, str(m + 60)))
    conn.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", (S.HW_WATERMARK_RAW_KEY, str(NOW + 86400)))
    conn.commit()
    w.add_net_sample(W.NetSample(m + 30, 1000, {"e": (9, 9)}))   # first v8 poll, inside the folded minute
    w.add_net_sample(W.NetSample(m + 90, 1000, {"e": (4, 4)}))
    w.flush(NOW)
    w.run_maintenance(NOW)
    assert _wm(conn, S.WATERMARK_RAW_KEY) == m + 60
    assert _wm(conn, S.HW_WATERMARK_RAW_KEY) == (NOW - 86400) // 60 * 60
    # The raw row inside the folded minute was merged into it (legacy row -> bytes stay NULL, the
    # samples count); the later one stays raw.
    assert conn.execute("SELECT sample_count, up_bytes FROM net_minute").fetchall() == [(60, None)]
    assert conn.execute("SELECT ts - ?, up_bytes FROM net_raw", (m,)).fetchall() == [(90, 4)]


def test_retention_prunes_hour_tiers_links_and_old_sessions_only(conn):
    w = _writer(conn)
    old = NOW - 400 * 86400
    conn.execute("INSERT INTO app_sessions (started_ts, last_seen_ts, version, poll_ms) VALUES (?, ?, 'x', 1)",
                 (old, old + 10))
    conn.execute("INSERT INTO link_events (iface_id, ts, event) VALUES (1, ?, 1)", (old,))
    conn.execute("INSERT INTO net_hour (ts, iface_id, up_bytes, down_bytes, up_avg, down_avg, up_max, down_max) "
                 "VALUES (?, 1, 1, 1, 1, 1, 1, 1)", (old // 3600 * 3600,))
    conn.commit()
    w.start_session(NOW)
    out = w.run_maintenance(NOW, retention_days=365)
    assert out["pruned"] == 3
    assert conn.execute("SELECT COUNT(*) FROM app_sessions").fetchone()[0] == 1
    assert w.run_maintenance(NOW, retention_days=constants.data.retention.UNLIMITED_DAYS)["pruned"] == 0


# --------------------------------------------------------------------------------------------
# Gate 5: fractional loss survives the roll-ups
# --------------------------------------------------------------------------------------------

def test_gate5_fractional_loss_is_carried_exactly_through_rollups(conn, tmp_path):
    name = constants.network.latency.STAT_ANCHOR_TIMEOUT
    w = _writer(conn)
    w.start_session(NOW - 40 * 86400)
    vals = [0.0, 0.25, 0.5, 0.75, 1.0, 0.25, 0.0, 0.125]   # dyadic: exact in binary floating point
    base = (NOW - 35 * 86400) // 3600 * 3600
    samples = [(base + i * 5, vals[i % len(vals)]) for i in range(720)]   # one hour at 5 s
    recent = [(NOW - 7200 + i * 5, vals[(i * 3) % len(vals)]) for i in range(240)]
    for ts, v in samples + recent:
        w.add_hw_sample(ts, name, v)
    assert w.flush(NOW)
    assert sorted(v for (v,) in conn.execute("SELECT value FROM hw_raw")) == sorted(v for _, v in samples + recent)
    w.run_maintenance(NOW)
    avg, mn, mx, n = conn.execute("SELECT avg_value, min_value, max_value, sample_count FROM hw_hour").fetchone()
    assert n == 720 and avg == sum(v for _, v in samples) / 720 and (mn, mx) == (0.0, 1.0)
    # Not rounded to 0/1 anywhere, and the unit says what it is.
    assert conn.execute("SELECT unit FROM metrics WHERE name = ?", (name,)).fetchone()[0] == "ratio"
    assert {v for (v,) in conn.execute("SELECT value FROM hw_raw")} >= {0.25, 0.125, 0.75}
    db = tmp_path / "speed_history.db"
    rc = sqlite3.connect(db)
    r = V8HistoryReader(lambda: rc, lambda: db, logging.getLogger("t"))
    pts = r.hardware_history(name, datetime.fromtimestamp(base - 1), datetime.fromtimestamp(base + 3600))
    rc.close()
    assert [v for _, v in pts] == [avg]


# --------------------------------------------------------------------------------------------
# delete all history / caches
# --------------------------------------------------------------------------------------------

def test_delete_history_drops_cached_ids_and_reopens_the_session(conn):
    w = _writer(conn)
    w.start_session(NOW - 100)
    w.add_net_sample(W.NetSample(NOW - 50, 1000, {"ACME-HQ Office VPN": (5, 5)},
                                 counters={"ACME-HQ Office VPN": _ctr(5, 5)}))
    w.add_hw_sample(NOW - 50, "cpu", 10.0)
    w.add_net_sample(W.NetSample(NOW - 40, 1000, {}, if_stats={"ACME-HQ Office VPN": Nic(True, 2, 100, 1, "")}))
    assert w.flush(NOW - 40)
    w.add_net_sample(W.NetSample(NOW - 30, 1000, {"ACME-HQ Office VPN": (9, 9)}))   # pending: discarded
    w.delete_history(now_ts=NOW - 20)
    for t in S.HISTORY_TABLES_V8:
        if t != S.APP_SESSIONS:
            assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
    assert conn.execute("SELECT started_ts - ?, session_id = ? FROM app_sessions", (NOW, w.session_id)).fetchall() == [
        (-20, 1)]
    # The app keeps recording: dimension rows are re-created, never pointed at by stale ids.
    w.add_net_sample(W.NetSample(NOW - 10, 1000, {"ACME-HQ Office VPN": (3, 3)}))
    w.add_hw_sample(NOW - 10, "cpu", 20.0)
    w.add_net_sample(W.NetSample(NOW - 9, 1000, {}, if_stats={"ACME-HQ Office VPN": Nic(True, 2, 100, 1, "")}))
    assert w.flush(NOW)
    assert conn.execute("SELECT COUNT(*) FROM net_raw n JOIN interfaces i USING (iface_id)").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM hw_raw h JOIN metrics m USING (metric_id)").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM link_events l JOIN interfaces i USING (iface_id)").fetchone()[0] == 1
    assert conn.execute("SELECT interface_name FROM speed_history_raw").fetchall() == [("ACME-HQ Office VPN",)]
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM usage_counter").fetchone()[0] == 0   # untouched (never filled)


def test_delete_history_keeps_the_odometer_unless_asked(conn):
    conn.execute("INSERT INTO usage_counter (id, cumulative_up) VALUES (1, 5)")
    conn.commit()
    w = _writer(conn)
    w.delete_history()
    assert conn.execute("SELECT COUNT(*) FROM usage_counter").fetchone()[0] == 1
    w.delete_history(include_usage=True)
    assert conn.execute("SELECT COUNT(*) FROM usage_counter").fetchone()[0] == 0


def test_a_failed_flush_keeps_its_rows_and_forgets_ids_it_created(conn):
    w = _writer(conn)
    w.start_session(NOW)
    conn.execute("CREATE TEMP TRIGGER boom BEFORE INSERT ON main.net_raw BEGIN SELECT RAISE(ABORT, 'disk'); END")
    w.add_net_sample(W.NetSample(NOW + 1, 1000, {"new nic": (5, 6)}))
    assert not w.flush(NOW + 1)
    assert conn.execute("SELECT COUNT(*) FROM interfaces").fetchone()[0] == 0   # rolled back
    conn.execute("DROP TRIGGER temp.boom")
    assert w.flush(NOW + 2)
    assert conn.execute("SELECT i.name, n.up_bytes FROM net_raw n JOIN interfaces i USING (iface_id)").fetchall() == [
        ("new nic", 5)]


# --------------------------------------------------------------------------------------------
# space
# --------------------------------------------------------------------------------------------

def test_incremental_vacuum_returns_pages_without_a_full_vacuum(conn):
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    w = _writer(conn)
    for i in range(30000):
        w.add_hw_sample(NOW - 3 * 86400 + i, "cpu", float(i))
    w.flush(NOW)
    w.run_maintenance(NOW, vacuum_pages=0)   # the move frees pages; nothing reclaimed yet
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    assert free > 50
    assert w.incremental_vacuum(10) == 10
    assert w.incremental_vacuum(None) == free - 10
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0


# --------------------------------------------------------------------------------------------
# the switch-over: one writer, owned by the worker
# --------------------------------------------------------------------------------------------

def test_only_the_database_worker_constructs_the_writer():
    """Step 7: _DB_VERSION is 8 and the DatabaseWorker - on its own thread - is the ONLY place in the app
    that builds a WriterV8 (step-7 note 7). WidgetState may import the NetSample value type to buffer
    polls on the GUI thread; it never touches the writer."""
    assert DatabaseWorker._DB_VERSION == 8
    import re
    root = Path(__file__).resolve().parents[2]
    build = re.compile(r"^(?!\s*#).*\bWriterV8\s*\(", re.M)
    imp = re.compile(r"^(?!\s*#).*\bimport\b.*\bWriterV8\b", re.M)
    files = [p for p in root.rglob("*.py") if "tests" not in p.parts and p.name != "writer_v8.py"]
    builders = sorted(p.name for p in files if build.search(p.read_text(encoding="utf-8", errors="replace")))
    importers = sorted(p.name for p in files if imp.search(p.read_text(encoding="utf-8", errors="replace")))
    assert builders == ["database.py"] and importers == ["database.py"]


# --------------------------------------------------------------------------------------------
# the minute the migration ran: roll-ups merge into an existing (legacy) bucket
# --------------------------------------------------------------------------------------------

def _legacy_volume(c, iface):
    """The step-3 reader's volume rule, vendored: SUM(bytes) where present, else the legacy estimate
    avg x MIN(sample_count, bucket seconds) x 1 s; one tier per sub-range by the watermarks."""
    wr, wmn = _wm(c, S.WATERMARK_RAW_KEY), _wm(c, S.WATERMARK_MINUTE_KEY)
    iid = c.execute("SELECT iface_id FROM interfaces WHERE name = ?", (iface,)).fetchone()[0]
    raw = c.execute("SELECT COALESCE(SUM(up_bytes), 0) FROM net_raw WHERE iface_id = ? AND ts >= ?",
                    (iid, wr)).fetchone()[0]
    mnt = c.execute("SELECT COALESCE(SUM(COALESCE(up_bytes, up_avg * MIN(sample_count, 60))), 0) FROM net_minute "
                    "WHERE iface_id = ? AND ts >= ? AND ts < ?", (iid, wmn, wr)).fetchone()[0]
    hr = c.execute("SELECT COALESCE(SUM(COALESCE(up_bytes, up_avg * MIN(sample_count, 3600))), 0) FROM net_hour "
                   "WHERE iface_id = ? AND ts < ?", (iid, min(wr, wmn))).fetchone()[0]
    return raw + mnt + hr


def test_rows_in_the_migration_minute_merge_into_the_folded_minute(tmp_path):
    """Migrate a v7 fixture; the first v8 polls land in the minute the fold wrote (below the raw
    watermark); the writer's first pass merges them into that row - once, exactly."""
    from netspeedtray.core.db import migrate_v8 as M
    from netspeedtray.tests.integration import v7_fixture as F
    db = tmp_path / "speed_history.db"
    from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
    v7 = LegacyV7Worker(db)
    v7._initialize_connection()
    v7._check_and_create_schema()
    F.populate(v7.conn)
    v7._close_connection()
    wr = F.expected_watermarks()[S.WATERMARK_RAW_KEY]
    mig_now = wr + 30                      # the migration ran half-way through the folded minute
    assert M.migrate(db, now=datetime.fromtimestamp(mig_now)).status == "migrated"
    c = sqlite3.connect(db)
    c.execute("PRAGMA journal_mode = WAL")
    assert _wm(c, S.WATERMARK_RAW_KEY) == wr
    bucket = wr - 60
    iid = c.execute("SELECT iface_id FROM interfaces WHERE name = 'Wi-Fi 3'").fetchone()[0]
    old = c.execute("SELECT up_bytes, up_avg, up_max, sample_count FROM net_minute WHERE ts = ? AND iface_id = ?",
                    (bucket, iid)).fetchone()
    assert old[0] is None and old[3] <= 45          # a folded (legacy) row, room for the new samples
    before = _legacy_volume(c, "Wi-Fi 3")

    w = _writer(c, poll_ms=2000)
    w.start_session(mig_now)
    new = [(bucket + 40 + 2 * k, 1000 + 337 * k) for k in range(8)]   # 2 s polls, in the folded minute
    for ts, b in new:
        w.add_net_sample(W.NetSample(ts, 2000, {"Wi-Fi 3": (b, b)}))
    w.add_net_sample(W.NetSample(wr + 5, 2000, {"Wi-Fi 3": (77, 77)}))  # past the watermark: stays raw
    assert w.flush(mig_now + 10)
    # Below the watermark the raw rows are invisible to readers until the pass...
    assert _legacy_volume(c, "Wi-Fi 3") == before + 77
    w.run_maintenance(mig_now + 20)          # the writer's first pass, right after the migration
    assert _wm(c, S.WATERMARK_RAW_KEY) == wr  # never lowered below the folded minutes
    rows = c.execute("SELECT up_bytes, up_avg, up_max, sample_count FROM net_minute WHERE ts = ? AND iface_id = ?",
                     (bucket, iid)).fetchall()
    assert len(rows) == 1                     # merged, not duplicated, not ignored
    up_bytes, up_avg, up_max, n = rows[0]
    assert up_bytes is None and n == min(old[3] + 2 * len(new), 60)       # stays legacy; n in SECONDS
    assert up_max == max(old[2], max(b * 1000.0 / 2000 for _, b in new))
    assert up_avg * min(n, 60) == pytest.approx(old[1] * min(old[3], 60) + sum(b for _, b in new), rel=1e-12)
    # ...and after it, both parts count exactly once.
    assert _legacy_volume(c, "Wi-Fi 3") == pytest.approx(before + sum(b for _, b in new) + 77, rel=1e-12)
    assert c.execute("SELECT COUNT(*) FROM net_raw WHERE ts < ?", (wr,)).fetchone()[0] == 0
    c.close()
    rc = sqlite3.connect(db)
    r = V8HistoryReader(lambda: rc, lambda: db, logging.getLogger("t"))
    v = r.volume(datetime.fromtimestamp(bucket), datetime.fromtimestamp(wr + 59), "Wi-Fi 3", 1.0)
    assert v.reconstructed
    assert v.up_bytes == pytest.approx(old[1] * old[3] + sum(b for _, b in new) + 77, rel=1e-12)
    pts = r.speed_history(datetime.fromtimestamp(bucket), datetime.fromtimestamp(bucket + 59),
                          "Wi-Fi 3", return_raw=True, resolution="minute")
    assert [p[:2] for p in pts if p[0] == bucket] == [(bucket, up_max)]   # one point, the merged max
    rc.close()


def test_hour_rollup_of_mixed_legacy_and_exact_minutes_keeps_each_part_once(conn):
    w = _writer(conn)
    h = (NOW - 40 * 86400) // 3600 * 3600
    conn.execute("INSERT INTO interfaces (iface_id, stable_key, name, first_seen, last_seen) VALUES (1, 'e', 'e', 0, 0)")
    conn.execute("INSERT INTO net_minute (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count) "
                 "VALUES (?, 1, 10.0, 10.0, 30.0, 30.0, 40)", (h,))           # legacy: est 400 B
    conn.execute("INSERT INTO net_minute (ts, iface_id, up_bytes, down_bytes, up_avg, down_avg, up_max, down_max, "
                 "sample_count) VALUES (?, 1, 1234, 1234, 41.1, 41.1, 90.0, 90.0, 30)", (h + 60,))   # exact
    conn.commit()
    w.run_maintenance(NOW)
    b, avg, mx, n = conn.execute("SELECT up_bytes, up_avg, up_max, sample_count FROM net_hour").fetchone()
    assert b is None and n == 70 and mx == 90.0 and avg * n == pytest.approx(400 + 1234)


def test_a_watermark_up_to_the_migrators_lead_is_honoured(conn):
    """The migrator can stamp the raw watermark up to now + 360 s (end of the minute it ran in, after
    its now + 300 s clamp). The writer must honour that lead, or its first pass would LOWER the
    watermark to now - 24 h and hide the folded minutes."""
    lead = ((NOW + W.FUTURE_SLACK_SECONDS) // 60 + 1) * 60
    assert NOW + W.FUTURE_SLACK_SECONDS < lead <= NOW + 360
    conn.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", (S.WATERMARK_RAW_KEY, str(lead)))
    conn.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", (S.HW_WATERMARK_RAW_KEY, str(NOW + 3600)))
    conn.commit()
    _writer(conn).run_maintenance(NOW)
    assert _wm(conn, S.WATERMARK_RAW_KEY) == lead
    assert _wm(conn, S.HW_WATERMARK_RAW_KEY) == (NOW - 86400) // 60 * 60   # beyond any lead: a wrong clock


# --------------------------------------------------------------------------------------------
# review #365: units of sample_count, legacy merges at slow polls / clock-back, failure safety
# --------------------------------------------------------------------------------------------

def _legacy_minute(conn, ts, avg, n, iface_id=1):
    conn.execute("INSERT OR IGNORE INTO interfaces (iface_id, stable_key, name, first_seen, last_seen) "
                 "VALUES (?, 'e', 'e', 0, 0)", (iface_id,))
    conn.execute("INSERT INTO net_minute (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (ts, iface_id, avg, avg, avg, avg, n))
    conn.commit()


def _est(conn, table, bucket):
    return conn.execute(f"SELECT SUM(COALESCE(up_bytes, up_avg * MIN(sample_count, {bucket}))) FROM {table}"
                        ).fetchone()[0]


@pytest.mark.parametrize("poll_s", [1, 2, 5, 10])
def test_legacy_merge_at_any_poll_rate_keeps_volume_and_a_true_rate(conn, poll_s):
    """A folded minute (30 s at 1000 B/s, estimate 30 000 B) + 30 s more at 1000 B/s from v8 polls of
    poll_s: the merged row estimates 60 000 B through the minute AND the hour roll-up, and its avg is
    still 1000 B/s (sample_count in seconds, not polls)."""
    b = (NOW - 35 * 86400) // 3600 * 3600
    _legacy_minute(conn, b, 1000.0, 30)
    w = _writer(conn, poll_ms=poll_s * 1000)
    w.start_session(b)
    for k in range(30 // poll_s):
        w.add_net_sample(W.NetSample(b + 30 + poll_s * (k + 1) - 1, poll_s * 1000, {"e": (1000 * poll_s,) * 2}))
    assert w.flush(b + 60)
    w.run_maintenance(b + 2 * 86400)            # raw -> minute only
    bytes_, avg, n = conn.execute("SELECT up_bytes, up_avg, sample_count FROM net_minute").fetchone()
    assert bytes_ is None and n == 60 and avg == pytest.approx(1000.0)
    assert _est(conn, "net_minute", 60) == pytest.approx(60_000)
    w.run_maintenance(NOW)                      # minute -> hour
    assert conn.execute("SELECT COUNT(*) FROM net_minute").fetchone()[0] == 0
    assert _est(conn, "net_hour", 3600) == pytest.approx(60_000)
    assert conn.execute("SELECT up_avg FROM net_hour").fetchone()[0] == pytest.approx(1000.0)


def test_clock_set_back_into_a_legacy_minute_keeps_both_parts_through_the_hour(conn):
    """The probe: legacy n=50 at 100 B/s (5 000 B) + 30 one-second rows of 100 B stamped into the same
    minute by a clock set back = 8 000 B. n would be 80 > 60: the cap is applied in the merge AND the
    hour roll-up the same way the reader applies it, so the 8 000 survives both."""
    b = (NOW - 35 * 86400) // 3600 * 3600
    _legacy_minute(conn, b, 100.0, 50)
    w = _writer(conn)
    w.start_session(b)
    for k in range(30):
        w.add_net_sample(W.NetSample(b + k, 1000, {"e": (100, 100)}))
    assert w.flush(b + 60)
    w.run_maintenance(b + 2 * 86400)
    assert _est(conn, "net_minute", 60) == pytest.approx(8000)
    assert conn.execute("SELECT sample_count FROM net_minute").fetchone()[0] == 60
    w.run_maintenance(NOW)
    assert _est(conn, "net_hour", 3600) == pytest.approx(8000)


@pytest.mark.parametrize("poll_s", [2, 5, 10])
def test_exact_rows_at_slow_polls_store_seconds_and_a_true_rate(conn, poll_s):
    b = (NOW - 2 * 86400) // 60 * 60
    w = _writer(conn, poll_ms=poll_s * 1000)
    w.start_session(b - 10)
    for k in range(60 // poll_s):
        w.add_net_sample(W.NetSample(b + poll_s * (k + 1) - 1, poll_s * 1000, {"e": (500 * poll_s,) * 2}))
    assert w.flush(b + 60)
    w.run_maintenance(NOW)
    assert conn.execute("SELECT up_bytes, up_avg, sample_count FROM net_minute").fetchone() == (
        30_000, pytest.approx(500.0), 60)


def test_a_non_sqlite_error_inside_flush_rolls_back_and_keeps_the_rows(conn, monkeypatch):
    w = _writer(conn)
    w.start_session(NOW)
    w.add_net_sample(W.NetSample(NOW + 1, 1000, {"nic": (5, 6)}))
    w.add_hw_sample(NOW + 1, "cpu", 3.0)

    def boom(rows):
        raise ValueError("not a sqlite3.Error")
    monkeypatch.setattr(w, "_write_hw", boom)
    with pytest.raises(ValueError):
        w.flush(NOW + 2)
    assert not conn.in_transaction             # nothing half-written left for the next _begin to commit
    assert w.pending_rows() == 2
    assert conn.execute("SELECT COUNT(*) FROM net_raw").fetchone()[0] == 0
    monkeypatch.undo()
    assert w.flush(NOW + 3)
    assert conn.execute("SELECT COUNT(*) FROM net_raw").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM hw_raw").fetchone()[0] == 1


def test_a_counter_beyond_sqlite_integer_range_is_refused_at_capture(conn):
    """The probe: 2**63 bytes raised OverflowError at bind time - outside sqlite3.Error - and used to
    leave the flush transaction open with its buffers gone."""
    w = _writer(conn)
    w.start_session(NOW)
    w.add_net_sample(W.NetSample(NOW, 1000, {"e": (2 ** 63, 5), "f": (7, 7)}))
    assert w.flush(NOW + 1)
    assert not conn.in_transaction
    assert conn.execute("SELECT up_bytes FROM net_raw").fetchall() == [(7,)]


def test_end_session_rolls_back_on_any_error(conn, monkeypatch):
    w = _writer(conn)
    w.start_session(NOW)
    monkeypatch.setattr(w, "flush", lambda now=None: True)
    real = conn

    class Boom:
        in_transaction = property(lambda self: real.in_transaction)

        def execute(self, sql, *a):
            if sql.startswith("UPDATE"):
                raise KeyError("x")
            return real.execute(sql, *a)

        def commit(self):
            real.commit()

        def rollback(self):
            real.rollback()
    w.conn = Boom()
    with pytest.raises(KeyError):
        w.end_session(NOW + 5)
    assert not real.in_transaction


def test_every_writer_call_off_the_owning_thread_raises(conn):
    import threading
    w = _writer(conn)
    errors = []

    def other():
        for call in (lambda: w.add_hw_sample(NOW, "cpu", 1.0),
                     lambda: w.add_net_sample(W.NetSample(NOW, 1000, {"e": (1, 1)})),
                     lambda: w.flush(NOW), lambda: w.run_maintenance(NOW), lambda: w.delete_history()):
            try:
                call()
            except RuntimeError as e:
                errors.append(str(e))
    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert len(errors) == 5 and w.pending_rows() == 0
    w.bind_to_current_thread()
    assert w.flush(NOW)


def test_begin_commits_a_foreign_open_transaction_with_a_warning(conn, caplog):
    w = _writer(conn)
    conn.execute("INSERT INTO metadata (key, value) VALUES ('foreign', '1')")   # implicit txn, uncommitted
    assert conn.in_transaction
    with caplog.at_level(logging.WARNING):
        assert w.flush(NOW)
    assert "left open" in caplog.text
    other = sqlite3.connect(conn.execute("PRAGMA database_list").fetchone()[2])
    assert other.execute("SELECT value FROM metadata WHERE key = 'foreign'").fetchone() == ("1",)
    other.close()


def test_hour_rollup_applies_the_readers_cap_to_garbled_legacy_minutes(conn):
    """A migrated minute can carry sample_count > 60 (v7 never capped it); the reader estimates it as
    avg x 60. The hour roll-up must estimate each minute the same way AND cap the hour at 3600 s, or
    rolling a full hour of such minutes changes the volume (60 x 60 x 10 B = 36 000 B)."""
    h = (NOW - 40 * 86400) // 3600 * 3600
    for j in range(60):
        _legacy_minute(conn, h + 60 * j, 10.0, 90)
    assert _est(conn, "net_minute", 60) == pytest.approx(36_000)
    _writer(conn).run_maintenance(NOW)
    assert conn.execute("SELECT sample_count FROM net_hour").fetchone()[0] == 3600
    assert _est(conn, "net_hour", 3600) == pytest.approx(36_000)
