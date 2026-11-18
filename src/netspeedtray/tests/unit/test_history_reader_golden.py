"""
Golden tests for the history-reader facade (v8 step 2): every WidgetState history read, now delegated
to ``core/db/history_reader.V7HistoryReader``, must return exactly what the pre-facade code returned.

The oracle is ``legacy_v7_reader_oracle.LegacyV7Readers`` - a frozen, verbatim copy of the old
methods - called on the SAME WidgetState, over the SAME database, in the same instant. So a passing
test here means "the facade is behaviour-identical on v7", not merely "the facade returns something".

The fixture database is written through the real ``DatabaseWorker`` (persist batches + the real
maintenance pass that moves raw -> minute -> hour), never by hand-inserting tier rows, so the tiers hold
what production would hold: three eras (hour tier 31-40 days ago, minute tier 1-30 days ago, raw the last
24 h), several interfaces (one that only survives in the hour tier, one that only appears in the last two
hours), multi-day gaps, the full hardware set including fractional latency_*_timeout loss rows, and a
usage_counter row with its counter snapshot.

The whole module runs on a FROZEN clock in a PINNED timezone, so no count below depends on when or
where the suite runs. On the wall clock, day resolution's row total moved with the time of day (where
"now" falls against the day buckets): CI hit 939 at 00:01 UTC against the 1,000 floor (run
43983774035) and #581 lowered the floor to 500. freezegun freezes Python's clocks only - SQLite's
'localtime' (the local-day bins) follows the C runtime's timezone, which freezegun does not touch - so
the zone is pinned at the C level too (``_pinned_timezone``), and no reader under test asks SQLite for
'now' (every bound is an explicit epoch from Python). ``test_day_resolution_at_any_time_of_day``
re-runs the day-boundary comparison at the instants that used to matter. (DST buckets are covered by
test_rc_dst_buckets.py, in a subprocess.)
"""
import contextlib
import ctypes
import os
import math
import random
import sqlite3
import time
import types
from dataclasses import astuple, is_dataclass, replace
from typing import NamedTuple
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from _pytest.timing import Instant
from freezegun import freeze_time

from netspeedtray import constants
from netspeedtray.core import usage_snapshot
from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.tests.unit import legacy_v7_reader_oracle
from netspeedtray.tests.unit.legacy_v7_reader_oracle import LegacyV7Readers as Legacy

DAY = 86400
IFACES_ALL = (None, "all", "Ethernet", "Wi-Fi", "vEthernet (WSL)", "Tailscale", "Ghost")

class Zone(NamedTuple):
    tz: str                  # POSIX TZ syntax, which the Windows C runtime reads too
    std_h: int               # UTC offset in hours; a zone WITHOUT DST (see _pinned_timezone)


# The module's pinned timezone: UTC+9, no DST. Not UTC, so local midnight (the reader's day buckets) and
# UTC midnight (the legacy reader's) stay 9 h apart and the intended local-day divergence is really
# exercised - on CI (a UTC machine) as on a dev box.
JST = Zone("JST-9", 9)
# The module's frozen instant, in UTC: one minute past UTC midnight, the time CI failed at.
ANCHOR_UTC = datetime(2026, 11, 17, 0, 1, 0)
_JAN, _JUL = 1_768_435_200, 1_784_073_600     # 2026-01-15 / 2026-07-15 00:00 UTC


def _offset_h(zone: Zone, anchor_utc: datetime) -> int:
    """The zone's UTC offset - fixed: only DST-free zones are pinned (the pin's self-check proves it)."""
    return zone.std_h


def _local_now(anchor_utc: datetime, zone: Zone = JST) -> datetime:
    """The naive LOCAL "now" for a UTC anchor in the pinned zone - plain arithmetic, so it is right
    even at import time, before the zone is pinned (``.timestamp()`` is only called inside it)."""
    return anchor_utc + timedelta(hours=_offset_h(zone, anchor_utc))


def _tzset() -> None:
    if hasattr(time, "tzset"):
        time.tzset()
    else:   # Windows: Python's time.localtime and SQLite's 'localtime' both read the UCRT's zone
        ctypes.cdll.ucrtbase._tzset()


@contextlib.contextmanager
def _pinned_timezone(zone: Zone = JST):
    old = os.environ.get("TZ")
    os.environ["TZ"] = zone.tz
    _tzset()
    try:
        # Prove the pin took on BOTH sides (Python's localtime, SQLite's 'localtime'), in winter and in
        # summer, or every day-bucket assertion below is about the host zone. DST-free zones only: a
        # PST8PDT case was dropped after the GitHub runner's C runtime reported tm_isdst=1 in July with
        # tm_gmtoff still at standard time (run 44270652400) - DST lives in test_rc_dst_buckets.py.
        c = sqlite3.connect(":memory:")
        try:
            for ts in (_JAN, _JUL):
                lt = time.localtime(ts)
                assert lt.tm_gmtoff == zone.std_h * 3600 and not lt.tm_isdst, (zone.tz, ts, lt)
                assert int(c.execute("SELECT strftime('%H', ?, 'unixepoch', 'localtime')",
                                     (ts,)).fetchone()[0]) == lt.tm_hour, (zone.tz, ts)
        finally:
            c.close()
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        _tzset()


@contextlib.contextmanager
def _frozen_world(anchor_utc: datetime, zone: Zone = JST):
    """Pinned zone + every Python clock frozen at ``anchor_utc``.

    ``tz_offset`` keeps freezegun's ``datetime.now()`` (naive local) consistent with its ``time.time()``
    in the pinned zone. pytest's own timer (``_pytest.timing``, which imports ``time``/``perf_counter``
    by name) and the xdist/execnet plumbing are excluded, or ``--durations`` reports a setup phase of
    ~1.8e9 s (started on the real clock, stopped on the frozen one) - checked below on every entry.
    Trap: inside the freeze, ``time.localtime()`` / ``time.strftime()`` WITHOUT an argument are
    freezegun's, shifted by Python's import-time ``time.timezone`` (the HOST zone on Windows, which has
    no ``time.tzset``), not the pinned zone. Nothing here uses them; always pass a timestamp."""
    started = Instant()
    with _pinned_timezone(zone), freeze_time(anchor_utc, tz_offset=_offset_h(zone, anchor_utc),
                                             ignore=["_pytest.timing"]):
        assert datetime.now() == _local_now(anchor_utc, zone)
        assert int(datetime.now().timestamp()) == int(time.time())
        assert 0 <= started.elapsed().seconds < 600, "pytest's timer is frozen: fix freeze_time(ignore=)"
        yield


@pytest.fixture(scope="module", autouse=True)
def _module_clock():
    with _frozen_world(ANCHOR_UTC):
        yield


# The config the fixture's maintenance pass and the readers see, with the values that shape what the
# tiers hold (retention) and how the readers weigh samples (poll interval) pinned here - so a change to
# the app's DEFAULT_CONFIG does not silently move the exact counts asserted below.
FIXTURE_CONFIG = {**constants.config.defaults.DEFAULT_CONFIG, "keep_data": 365, "update_rate": 1.0}


def _build_fixture_db(db_dir: Path, now: datetime) -> None:
    rng = random.Random(20261019)
    now_ts = int(now.timestamp())
    w = LegacyV7Worker(db_dir / "speed_history.db")
    w._initialize_connection()
    w._check_and_create_schema()

    speed, hw = [], []

    def add_speed(iface, t0, t1, step, scale):
        for t in range(t0, t1, step):
            if rng.random() < 0.08:          # sparse intra-era holes (the writer skips idle seconds)
                continue
            speed.append((t, iface, rng.uniform(0, scale) * 0.3, rng.uniform(0, scale)))

    def add_hw(stat, t0, t1, step, gen):
        for t in range(t0, t1, step):
            if rng.random() < 0.05:
                continue
            hw.append((t, stat, gen()))

    # Hour-tier era: 40..31 days ago, with a whole-day gap 36..35 days ago.
    for a, b in ((40, 36), (35, 31)):
        t0, t1 = now_ts - a * DAY, now_ts - b * DAY
        add_speed("Ethernet", t0, t1, 300, 5e6)
        add_speed("Wi-Fi", t0 + 3600, t1, 450, 2e6)
    add_speed("vEthernet (WSL)", now_ts - 39 * DAY, now_ts - 37 * DAY, 120, 4e5)   # hour tier ONLY
    # Minute-tier era: 30..1 days ago, with a gap 20..15 days ago.
    for a, b in ((30, 20), (15, 1)):
        t0, t1 = now_ts - a * DAY, now_ts - b * DAY
        add_speed("Ethernet", t0, t1, 240, 8e6)
        add_speed("Wi-Fi", t0, t1 - 3 * DAY, 420, 3e6)
    # Raw era: the last 24 h (kept clear of the floored maintenance cutoff), a 3-hour gap 10..7 h ago.
    for a, b in ((23 * 3600, 10 * 3600), (7 * 3600, 5)):
        add_speed("Ethernet", now_ts - a, now_ts - b, 10, 9e6)
        add_speed("Wi-Fi", now_ts - a, now_ts - b, 17, 4e6)
    add_speed("Tailscale", now_ts - 2 * 3600, now_ts - 5, 11, 5e3)              # raw tier ONLY

    pct = lambda: round(rng.uniform(0, 100), 3)
    loss = lambda: rng.choice((0.0, 0.0, 0.0, 0.25, 0.5, 0.75, 1.0))           # 4-echo burst fractions
    rtt = lambda: rng.uniform(1.0, 80.0)
    for (a, b), step in (((40 * DAY, 31 * DAY), 600), ((30 * DAY, 1 * DAY), 300), ((23 * 3600, 5), 30)):
        t0, t1 = now_ts - a, now_ts - b
        for stat in ("cpu", "gpu", "ram"):
            add_hw(stat, t0, t1, step, pct)
        add_hw("vram", t0, t1, step * 2, pct)
        add_hw("cpu_temp", t0, t1, step * 3, lambda: rng.uniform(30, 95))
        add_hw("latency_gw", t0, t1, step, rtt)
        add_hw("latency_gw_timeout", t0, t1, step, loss)
    # The public anchor was only switched on 3 days ago (minute + raw tiers).
    add_hw("latency_anchor", now_ts - 3 * DAY, now_ts - 5, 30, lambda: rng.uniform(8, 200))
    add_hw("latency_anchor_timeout", now_ts - 3 * DAY, now_ts - 5, 30, loss)

    for i in range(0, len(speed), 20000):
        w._persist_speed_batch(speed[i:i + 20000])
    for i in range(0, len(hw), 20000):
        w._persist_hardware_batch(hw[i:i + 20000])
    w._run_maintenance(dict(FIXTURE_CONFIG), now)   # raw->minute->hour, real path

    counters = {"Ethernet": types.SimpleNamespace(bytes_sent=123456789, bytes_recv=987654321)}
    snap = usage_snapshot.encode_snapshot(counters, now_ts - 30, now_ts - 5 * DAY, primary="Ethernet")
    w._persist_usage((5.5e9, 4.25e10, 1.5e9, 2.0e10, "2026-10-01", now_ts - 30, snap))

    # Sanity: the fixture really spans every tier, so a vacuous pass is impossible.
    cur = w.conn.cursor()
    for t in (constants.data.SPEED_TABLE_RAW, constants.data.SPEED_TABLE_MINUTE, constants.data.SPEED_TABLE_HOUR,
              constants.data.HARDWARE_STATS_TABLE_RAW, constants.data.HARDWARE_STATS_TABLE_MINUTE,
              constants.data.HARDWARE_STATS_TABLE_HOUR):
        assert cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] > 100, t
    assert cur.execute(f"SELECT COUNT(*) FROM {constants.data.HARDWARE_STATS_TABLE_MINUTE} "
                       "WHERE stat_type='latency_gw_timeout' AND avg_value NOT IN (0, 1)").fetchone()[0] > 0
    w._close_connection()


def _make_state(db_dir: Path) -> WidgetState:
    cfg = dict(FIXTURE_CONFIG)
    with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=db_dir):
        return WidgetState(cfg, read_only=True)   # never starts the write thread


@pytest.fixture(scope="module")
def ws(q_app, tmp_path_factory, _module_clock):
    db_dir = tmp_path_factory.mktemp("history_reader_golden")
    _build_fixture_db(db_dir, datetime.now())
    state = _make_state(db_dir)
    yield state
    state.cleanup()


@contextlib.contextmanager
def _frozen_clock(ws, now_ts=None):
    """Both readers see ONE "now" for the comparison inside.

    An open-ended window (``end=None``) ends at "now" and a missing ``start`` is 24 h before it; the
    legacy oracle reads ``datetime.now()``, the facade reads its injected ``_clock``. Left on the wall
    clock, a second tick between the two calls slides the window one second and a 30 s sample can cross
    its inclusive start edge (CI, PR #473: hardware_history(latency_anchor, None, None) len 1434 vs
    1435). A whole-second instant, so ``int(now.timestamp())`` is exact on both sides. (The module's
    frozen clock already gives both one instant; this stays as the explicit, local guarantee.)"""
    now_ts = int(time.time()) if now_ts is None else now_ts

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(now_ts, tz)

        @classmethod
        def fromtimestamp(cls, *a, **kw):           # plain datetimes, so _same's type check holds
            return datetime.fromtimestamp(*a, **kw)

    with patch.object(legacy_v7_reader_oracle, "datetime", _FrozenDatetime), \
            patch.object(ws.history_reader, "_clock", lambda: float(now_ts)):
        yield now_ts


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
        assert type(a) is type(b), f"{path}: {type(a)} vs {type(b)}"
        assert len(a) == len(b), f"{path}: len {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{path}[{i}]")
        return
    if isinstance(a, dict) and isinstance(b, dict):
        assert a.keys() == b.keys(), path
        for k in a:
            _same(a[k], b[k], f"{path}[{k!r}]")
        return
    assert type(a) is type(b) and a == b, f"{path}: {a!r} vs {b!r}"


def _windows(now: datetime):
    """(start, end) pairs covering every tier, every tier boundary, gaps, open and future windows."""
    out = [(None, None), (None, now)]
    for d in (timedelta(minutes=5), timedelta(hours=1), timedelta(hours=6), timedelta(hours=12),
              timedelta(hours=24), timedelta(hours=30), timedelta(hours=48), timedelta(days=7),
              timedelta(days=30), timedelta(days=31), timedelta(days=35), timedelta(days=90),
              timedelta(days=400)):
        out.append((now - d, now))
    out += [
        (now - timedelta(hours=10), now - timedelta(hours=7)),     # raw-tier gap
        (now - timedelta(days=19), now - timedelta(days=16)),      # minute-tier gap
        (now - timedelta(days=36), now - timedelta(days=35)),      # hour-tier gap
        (now - timedelta(days=35), now - timedelta(days=25)),      # straddles hour/minute
        (now - timedelta(days=3), now - timedelta(hours=3)),       # straddles minute/raw
        (now - timedelta(days=60), now - timedelta(days=45)),      # before any data
        (now + timedelta(hours=1), now + timedelta(hours=2)),      # future
    ]
    return out


WINDOWS = _windows(_local_now(ANCHOR_UTC))


# --- 1. get_speed_history ---------------------------------------------------------------------------

def _without_legacy_padding(rows, start, end, return_raw):
    """The legacy reader's rows minus its edge padding - the second intended divergence (rc.2 S4):
    the reader returns stored rows only. The oracle padded a bounded window with an all-zero row AT
    ``start`` / ``end`` when the data did not reach them, and filled an empty window with a flat
    all-zero baseline from ``start`` to ``end``; those rows drew unmeasured time as a measured zero."""
    if not (start and end) or not rows:
        return rows
    s_pt = int(start.timestamp()) if return_raw else start
    e_pt = int(end.timestamp()) if return_raw else end
    out = list(rows)
    first = out[0][0] if isinstance(out[0][0], (int, float)) else out[0][0].timestamp()
    if all(not (r[1] or r[2]) for r in out) and int(first) == int(start.timestamp()) and len(out) >= 10:
        return []                                      # the synthetic baseline of an empty window
    if out and out[0][0] == s_pt and not (out[0][1] or out[0][2]):
        out = out[1:]
    if out and out[-1][0] == e_pt and not (out[-1][1] or out[-1][2]):
        out = out[:-1]
    return out


def _expected_local_days(ws, start, end, iface):
    """{local-midnight epoch: (up, down)} for day resolution, from the legacy reader's hour buckets
    (raw + minute + hour tiers, the same tiers and per-bucket peaks as day resolution). Exact when
    the local offset is a whole number of hours, so a UTC hour lies inside one local day."""
    import time as _time
    if any(_time.localtime(t).tm_gmtoff % 3600 for t in (0, 15_552_000)):
        pytest.skip("oracle needs whole-hour UTC offsets (UTC hours inside local days)")
    names = ws.get_distinct_interfaces() if iface in (None, "all") else [iface]
    days = {}
    for name in names:
        peaks = {}
        for ts, up, down in Legacy.get_speed_history(ws, start, end, interface_name=name, return_raw=True,
                                                     resolution="hour", wait_for_flush=False):
            if not (up or down):
                continue
            # Local midnight via the C runtime (mktime, isdst=-1), NOT datetime(...).timestamp(): under
            # freezegun a naive datetime is a FakeDatetime whose timestamp() applies the freeze's FIXED
            # tz_offset, an hour off on the far side of a DST change in any zone that has one.
            day = int(time.mktime(time.localtime(int(ts))[:3] + (0, 0, 0, 0, 0, -1)))
            pu, pd = peaks.get(day, (0.0, 0.0))
            peaks[day] = (max(pu, up), max(pd, down))
        for day, (up, down) in peaks.items():
            su, sd = days.get(day, (0.0, 0.0))
            days[day] = (su + up, sd + down)
    return days


class Compared(NamedTuple):
    rows: int    # legacy-oracle rows compared one by one (non-day) / legacy rows the day windows held
    days: int    # local-day buckets compared value by value (day resolution only)


def _compare_speed_history(ws, windows, resolution, return_raw) -> Compared:
    """Compare every window x interface; return what was compared."""
    checked = days = 0
    for start, end in windows:
        for iface in IFACES_ALL:
            span = (end - start) if (start and end) else None
            # A forced fine resolution over a long window is not a real call (auto would never pick
            # it) and only costs diff time: raw beyond 2 days, minute beyond 10 days (the 10-day
            # hour/minute straddle stays in).
            if resolution == "raw" and (span is None or span > timedelta(days=2)):
                continue
            if resolution == "minute" and (span is None or span > timedelta(days=10)):
                continue
            kw = dict(interface_name=iface, return_raw=return_raw, resolution=resolution, wait_for_flush=False)
            effective = resolution if resolution != "auto" else \
                constants.data.history_period.get_target_resolution(start, end)
            with _frozen_clock(ws):    # the day-resolution oracle below reads the legacy reader too
                old = _without_legacy_padding(Legacy.get_speed_history(ws, start, end, **kw), start, end,
                                              return_raw)
                new = ws.get_speed_history(start, end, **kw)
                want = _expected_local_days(ws, start, end, iface) if effective == "day" else None
            if effective == "day":
                # The one intended divergence (2.2 RC): day buckets now start at LOCAL midnight, not
                # UTC midnight (which moved by an hour across DST). Compared per day against an
                # oracle built from the frozen legacy reader's HOUR buckets, re-binned by local
                # midnight: each interface's peak per local day, summed across interfaces as the
                # reader does for "all". (Zero rows - the legacy edge padding - are skipped.)
                got = {}
                for p in new:
                    if p[1] or p[2]:
                        ts = int(p[0] if return_raw else p[0].timestamp())
                        got[ts] = (p[1], p[2])
                assert set(got) == set(want), f"day buckets ({start},{end},{iface})"
                if any(r[1] or r[2] for r in old):   # the window holds data: the oracle must see it too
                    assert want, f"empty day oracle over data ({start},{end},{iface})"
                days += len(want)
                for day, (u, d) in want.items():
                    assert got[day][0] == pytest.approx(u) and got[day][1] == pytest.approx(d), \
                        (datetime.fromtimestamp(day), iface)
                checked += len(old)
                continue
            _same(new, old, f"speed_history({start},{end},{iface},{resolution},{return_raw})")
            checked += len(old)
    return Compared(checked, days)


_COUNTS_CHANGED = "fixture/retention changed: update the expected counts (the clock is frozen, so they are exact)"

# What each resolution compares at the module's anchor (00:01 UTC, JST). Exact, not a floor: the clock
# is frozen, so these are the same on every run (#581 had lowered a floor to 500 because day
# resolution's row total followed the wall clock - 939 at 00:01 UTC on CI). A change in either number
# means the fixture, the retention config or a reader's bucketing changed.
SPEED_EXPECTED = {
    "auto": Compared(49890, 441),
    "raw": Compared(126507, 0),
    "minute": Compared(46000, 0),
    "hour": Compared(21539, 0),
    "day": Compared(1015, 1070),
}


@pytest.mark.parametrize("resolution", ["auto", "raw", "minute", "hour", "day"])
@pytest.mark.parametrize("return_raw", [False, True])
def test_speed_history_matches_legacy(ws, resolution, return_raw):
    got = _compare_speed_history(ws, WINDOWS, resolution, return_raw)
    assert got == SPEED_EXPECTED[resolution], _COUNTS_CHANGED


# The day comparison at other instants, each on a fresh fixture written and read
# entirely at that instant. Legacy rows move with the time of day by design: a window whose edges sit
# exactly on UTC midnight touches one legacy bucket fewer than the same window a minute later (940 at
# 00:00 UTC). What must not move is the per-day comparison itself, which passes at every instant.
@pytest.mark.parametrize("anchor_utc, zone, expected", [
    (datetime(2026, 11, 17, 0, 0, 0), JST, Compared(940, 1070)),      # exactly UTC midnight: the legacy buckets' edge
    (datetime(2026, 11, 17, 12, 0, 0), JST, Compared(1066, 1066)),
    (datetime(2026, 11, 17, 15, 0, 0), JST, Compared(1066, 940)),     # exactly LOCAL midnight: the new buckets' edge
    (datetime(2026, 11, 17, 23, 59, 0), JST, Compared(1019, 1067)),
], ids=["0000Z", "1200Z", "1500Z-local-midnight", "2359Z"])   # 00:01 UTC: the module anchor
def test_day_resolution_at_any_time_of_day(q_app, tmp_path, anchor_utc, zone, expected):
    with _frozen_world(anchor_utc, zone):
        _build_fixture_db(tmp_path, datetime.now())
        state = _make_state(tmp_path)
        try:
            got = _compare_speed_history(state, _windows(datetime.now()), "day", False)
            assert got == expected, _COUNTS_CHANGED
        finally:
            state.cleanup()


# --- 2. get_hardware_history ------------------------------------------------------------------------

HW_STATS = ("cpu", "gpu", "ram", "vram", "cpu_temp", "latency_gw", "latency_gw_timeout",
            "latency_anchor", "latency_anchor_timeout", "never_recorded")


def test_hardware_history_matches_legacy(ws):
    checked = 0
    for start, end in WINDOWS:
        for stat in HW_STATS:
            with _frozen_clock(ws):
                old = Legacy.get_hardware_history(ws, stat, start, end)
                new = ws.get_hardware_history(stat, start, end)
            _same(new, old, f"hardware_history({stat},{start},{end})")
            checked += len(old)
    assert checked > 1000


# --- 3. summarize_hardware --------------------------------------------------------------------------

def _bounded_windows():
    return [(s, e) for s, e in WINDOWS if s is not None and e is not None]


@pytest.mark.parametrize("poll", [1.0, 5.0])
def test_summarize_hardware_matches_legacy(ws, poll):
    nonempty = 0
    for start, end in _bounded_windows():
        for stat in HW_STATS:
            old = Legacy.summarize_hardware(ws, stat, start, end, poll)
            new = ws.summarize_hardware(stat, start, end, poll)
            _same(new, old, f"summarize_hardware({stat},{start},{end},{poll})")
            nonempty += old.count > 0
    assert nonempty > 50


# --- 4. summarize_network ---------------------------------------------------------------------------

@pytest.mark.parametrize("poll", [2.0])
@pytest.mark.parametrize("direction", ["download", "upload"])
def test_summarize_network_matches_legacy(ws, direction, poll):
    """Identical for every single interface. The all-interfaces aggregate deliberately departs from the
    legacy code in avg and count only: a minute / hour bucket is now its bytes / MAX(sample_count),
    weighed by MAX(sample_count), instead of SUM(avg) weighed by SUM(sample_count), which overstated the
    avg whenever two interfaces moved data in one bucket (test_history_multi_interface_average)."""
    nonempty = changed = 0
    for start, end in _bounded_windows():
        for iface in IFACES_ALL:
            old = Legacy.summarize_network(ws, direction, start, end, iface, poll)
            new = ws.summarize_network(direction, start, end, iface, poll)
            path = f"summarize_network({direction},{start},{end},{iface},{poll})"
            if iface in (None, "all"):
                _same(replace(new, avg=0.0, count=0), replace(old, avg=0.0, count=0), path)
                assert new.count <= old.count, path
                changed += new.count < old.count
            else:
                _same(new, old, path)
            nonempty += old.count > 0
    assert nonempty > 50
    assert changed > 0   # the fixture's overlapping interfaces do reach the aggregate rollup path


# --- 5. get_total_bandwidth_for_period (the data-cap / "data used" totals) ---------------------------

@pytest.mark.parametrize("update_rate", [1.0, 2.0, 10.0, -1.0])   # -1.0: a legacy SMART config value
def test_total_bandwidth_matches_legacy(ws, update_rate):
    ws.config["update_rate"] = update_rate
    try:
        nonzero = 0
        for start, end in WINDOWS:
            end = end or datetime.now()          # callers always pass an end
            for iface in IFACES_ALL:
                old = Legacy.get_total_bandwidth_for_period(ws, start, end, iface)
                new = ws.get_total_bandwidth_for_period(start, end, iface)
                _same(new, old, f"total_bandwidth({start},{end},{iface},{update_rate})")
                nonzero += old[1] > 0
        assert nonzero > 50
    finally:
        ws.config["update_rate"] = FIXTURE_CONFIG["update_rate"]


# --- 6. get_distinct_interfaces ---------------------------------------------------------------------

def test_distinct_interfaces_matches_legacy(ws):
    old = Legacy.get_distinct_interfaces(ws)
    assert old == ["Ethernet", "Tailscale", "Wi-Fi", "vEthernet (WSL)"]   # hour-only + raw-only NICs present
    _same(ws.get_distinct_interfaces(), old)


# --- 7. get_earliest_data_timestamp -----------------------------------------------------------------

def test_earliest_timestamp_matches_legacy(ws):
    old = Legacy.get_earliest_data_timestamp(ws)
    assert old is not None and old < datetime.now() - timedelta(days=39)   # comes from the hour tier
    _same(ws.get_earliest_data_timestamp(), old)


def test_readers_on_an_empty_history_match_legacy(q_app, tmp_path):
    """A fresh install: schema present, no rows anywhere - every reader's empty answer is unchanged."""
    w = LegacyV7Worker(tmp_path / "speed_history.db")
    w._initialize_connection()
    w._check_and_create_schema()
    w._close_connection()
    state = _make_state(tmp_path)
    try:
        now = datetime.now()
        _same(state.get_earliest_data_timestamp(), Legacy.get_earliest_data_timestamp(state))
        _same(state.get_distinct_interfaces(), Legacy.get_distinct_interfaces(state))
        for start, end in ((now - timedelta(hours=1), now), (now - timedelta(days=40), now)):
            # The one divergence (rc.2 S4): no flat zero baseline for an empty window.
            assert state.get_speed_history(start, end, wait_for_flush=False) == []
            legacy = Legacy.get_speed_history(state, start, end, wait_for_flush=False)
            assert legacy and not any(r[1] or r[2] for r in legacy)
            _same(_without_legacy_padding(legacy, start, end, False), [])
            _same(state.get_hardware_history("cpu", start, end), Legacy.get_hardware_history(state, "cpu", start, end))
            _same(state.summarize_network("download", start, end), Legacy.summarize_network(state, "download", start, end))
            _same(state.summarize_hardware("cpu", start, end), Legacy.summarize_hardware(state, "cpu", start, end))
            _same(state.get_total_bandwidth_for_period(start, end),
                  Legacy.get_total_bandwidth_for_period(state, start, end))
    finally:
        state.cleanup()


# --- 8. _try_load_usage (the odometer load: usage_counter + metadata snapshot) -----------------------

_USAGE_FIELDS = ("_usage", "_usage_loaded", "_usage_launch_snapshot")


def _usage_load_both(db_dir: Path):
    """Run the legacy load on one WidgetState and the facade load on another, same DB file."""
    a, b = _make_state(db_dir), _make_state(db_dir)
    try:
        ok_old = Legacy._try_load_usage(a)
        ok_new = b._try_load_usage()
        return ((ok_old, {f: getattr(a, f) for f in _USAGE_FIELDS}),
                (ok_new, {f: getattr(b, f) for f in _USAGE_FIELDS}))
    finally:
        a.cleanup()
        b.cleanup()


def test_usage_load_matches_legacy_on_fixture(ws):
    old, new = _usage_load_both(ws._db_path.parent)
    assert old[0] is True and old[1]["_usage"]["cumulative_down"] == 4.25e10
    assert old[1]["_usage_launch_snapshot"]["counters"] == {"Ethernet": [123456789, 987654321]}
    _same(new, old)


def _fresh_db(db_dir: Path) -> LegacyV7Worker:
    w = LegacyV7Worker(db_dir / "speed_history.db")
    w._initialize_connection()
    w._check_and_create_schema()
    return w


def test_usage_load_matches_legacy_with_no_row(q_app, tmp_path):
    _fresh_db(tmp_path)._close_connection()
    old, new = _usage_load_both(tmp_path)
    assert old[0] is True and old[1]["_usage_launch_snapshot"] == {}
    _same(new, old)


def test_usage_load_matches_legacy_on_corrupt_row_and_snapshot(q_app, tmp_path):
    w = _fresh_db(tmp_path)
    # Negative cumulative, anchors above cumulative, empty period key, a snapshot that is not JSON.
    w._persist_usage((-5.0, 1e9, 1e12, 2e9, "", 1, "{not json"))
    w._close_connection()
    old, new = _usage_load_both(tmp_path)
    assert old[1]["_usage"] == {"cumulative_up": 0.0, "cumulative_down": 1e9,
                                "anchor_up": 0.0, "anchor_down": 1e9, "period_key": ""}
    _same(new, old)


def test_usage_load_matches_legacy_before_the_table_exists(q_app, tmp_path):
    sqlite3.connect(tmp_path / "speed_history.db").close()     # a file with no schema yet
    old, new = _usage_load_both(tmp_path)
    assert old[0] is False and old[1]["_usage_loaded"] is False
    _same(new, old)
