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
"""
import math
import random
import sqlite3
import types
from dataclasses import astuple, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from netspeedtray import constants
from netspeedtray.core import usage_snapshot
from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.tests.unit.legacy_v7_reader_oracle import LegacyV7Readers as Legacy

DAY = 86400
IFACES_ALL = (None, "all", "Ethernet", "Wi-Fi", "vEthernet (WSL)", "Tailscale", "Ghost")


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
    w._run_maintenance(dict(constants.config.defaults.DEFAULT_CONFIG), now)   # raw->minute->hour, real path

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
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=db_dir):
        return WidgetState(cfg, read_only=True)   # never starts the write thread


@pytest.fixture(scope="module")
def ws(q_app, tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("history_reader_golden")
    _build_fixture_db(db_dir, datetime.now())
    state = _make_state(db_dir)
    yield state
    state.cleanup()


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


def _windows():
    """(start, end) pairs covering every tier, every tier boundary, gaps, open and future windows."""
    now = datetime.now()
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


WINDOWS = _windows()


# --- 1. get_speed_history ---------------------------------------------------------------------------

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
            day = int(datetime.fromtimestamp(int(ts)).replace(hour=0, minute=0, second=0).timestamp())
            pu, pd = peaks.get(day, (0.0, 0.0))
            peaks[day] = (max(pu, up), max(pd, down))
        for day, (up, down) in peaks.items():
            su, sd = days.get(day, (0.0, 0.0))
            days[day] = (su + up, sd + down)
    return days


@pytest.mark.parametrize("resolution", ["auto", "raw", "minute", "hour", "day"])
@pytest.mark.parametrize("return_raw", [False, True])
def test_speed_history_matches_legacy(ws, resolution, return_raw):
    checked = 0
    for start, end in WINDOWS:
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
            old = Legacy.get_speed_history(ws, start, end, **kw)
            new = ws.get_speed_history(start, end, **kw)
            effective = resolution if resolution != "auto" else \
                constants.data.history_period.get_target_resolution(start, end)
            if effective == "day":
                # The one intended divergence (2.2 RC): day buckets now start at LOCAL midnight, not
                # UTC midnight (which moved by an hour across DST). Compared per day against an
                # oracle built from the frozen legacy reader's HOUR buckets, re-binned by local
                # midnight: each interface's peak per local day, summed across interfaces as the
                # reader does for "all". (Zero rows are the reader's edge padding and are skipped.)
                got = {}
                for p in new:
                    if p[1] or p[2]:
                        ts = int(p[0] if return_raw else p[0].timestamp())
                        got[ts] = (p[1], p[2])
                want = _expected_local_days(ws, start, end, iface)
                assert set(got) == set(want), f"day buckets ({start},{end},{iface})"
                for day, (u, d) in want.items():
                    assert got[day][0] == pytest.approx(u) and got[day][1] == pytest.approx(d), \
                        (datetime.fromtimestamp(day), iface)
                checked += len(old)
                continue
            _same(new, old, f"speed_history({start},{end},{iface},{resolution},{return_raw})")
            checked += len(old)
    assert checked > 1000


# --- 2. get_hardware_history ------------------------------------------------------------------------

HW_STATS = ("cpu", "gpu", "ram", "vram", "cpu_temp", "latency_gw", "latency_gw_timeout",
            "latency_anchor", "latency_anchor_timeout", "never_recorded")


def test_hardware_history_matches_legacy(ws):
    checked = 0
    for start, end in WINDOWS:
        for stat in HW_STATS:
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
    nonempty = 0
    for start, end in _bounded_windows():
        for iface in IFACES_ALL:
            old = Legacy.summarize_network(ws, direction, start, end, iface, poll)
            new = ws.summarize_network(direction, start, end, iface, poll)
            _same(new, old, f"summarize_network({direction},{start},{end},{iface},{poll})")
            nonempty += old.count > 0
    assert nonempty > 50


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
        ws.config["update_rate"] = constants.config.defaults.DEFAULT_CONFIG["update_rate"]


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
            _same(state.get_speed_history(start, end, wait_for_flush=False),
                  Legacy.get_speed_history(state, start, end, wait_for_flush=False))
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
