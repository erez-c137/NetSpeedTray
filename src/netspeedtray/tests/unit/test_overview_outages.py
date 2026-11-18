"""
The Overview's connection health / drop count over a history window (2.2.1-beta.2).

Two bugs, both on real v8 files (temp DBs - never the live one):

1. Only the ROUTER loss series was read (``latency_gw_timeout``). With the internet ping on
   (``latency_public_enabled``), an ISP outage with the router still answering said "steady".
2. The loss series was read through ``hardware_history``, which AVERAGES each bin (60 s past 6 h,
   3600 s past 30 days) - and the minute/hour tiers' ``avg_value``. A one-cycle drop (1.0 among
   nineteen 0.0 cycles) averages to 0.05, under ``outage_summary``'s 0.5, so drops vanished from
   every window longer than 6 h. The fix reads the per-bin PEAK (raw value / rollup ``max_value``).

Also: rows on either side of a stretch with no rows at all (the app was not running) are never one
outage - the old run builder joined them and counted the hours the app was off as downtime.
"""
from datetime import datetime, timedelta

import pytest

from netspeedtray.core.widget_state import WidgetState
from netspeedtray.tests.unit import v8_twin_fixture as F
from netspeedtray.utils import summaries as S
from netspeedtray.views.monitor.overview.tab import _read_window

NOW = F.fixed_now()
NOW_TS = int(NOW.timestamp())
BASE = NOW_TS // 3600 * 3600
GW, ANCHOR = "latency_gw_timeout", "latency_anchor_timeout"
CYCLE = 3   # the probe's cadence (s)


def _cycles(start_ts: int, minutes: int, drops=()):
    """(ts, loss) at the probe's 3 s cadence; ``drops`` = the ts ranges [a, b) that lost every echo."""
    out = []
    for ts in range(start_ts, start_ts + minutes * 60, CYCLE):
        out.append((ts, 1.0 if any(a <= ts < b for a, b in drops) else 0.0))
    return out


def _reader(tmp_path, hw_raw, hw_minute=(), wm_raw=None):
    h = F.History(now_ts=NOW_TS, wm_raw=wm_raw if wm_raw is not None else BASE - 30 * 3600,
                  wm_minute=BASE - 40 * 86400)
    h.hw_raw.extend(hw_raw)
    h.hw_minute.extend(hw_minute)
    F.write_v8(tmp_path / "speed_history.db", h).close()
    r7, r8, conns = F.readers(tmp_path / "speed_history.db", tmp_path / "speed_history.db", now=NOW)
    return r7, r8, conns


class _WS:
    """WidgetState's real history accessors over a temp-DB reader (no DB worker, no Qt)."""
    get_hardware_history = WidgetState.get_hardware_history
    get_hardware_history_peak = getattr(WidgetState, "get_hardware_history_peak", None)

    def __init__(self, reader):
        self.history_reader = reader
        self.db_worker = object()

    def summarize_network(self, *a):
        return S.summarize_raw([])

    def summarize_hardware(self, *a):
        return S.summarize_raw([])


def _events(reader, public: bool, key="TIMELINE_24_HOURS"):
    series = {"down": [], "up": [], "cpu": [], "gpu": [], "ram": []}
    kw = {"public_enabled": True} if public else {}
    _s, _summ, events, _vol, _cov = _read_window(_WS(reader), key, NOW, None, series, 1.0, True, None, **kw)
    return events


@pytest.fixture
def one_cycle_drop(tmp_path):
    t0 = BASE - 20 * 3600
    rows = [(ts, GW, v) for ts, v in _cycles(t0, 60, drops=[(t0 + 600, t0 + 603)])]
    r7, r8, conns = _reader(tmp_path, rows)
    yield r7, r8
    for c in conns:
        c.close()


@pytest.mark.parametrize("which", [0, 1], ids=["v7", "v8"])
def test_a_one_cycle_drop_is_counted_in_a_24h_window(one_cycle_drop, which):
    ev = _events(one_cycle_drop[which], public=False)
    assert ev["count"] == 1


@pytest.mark.parametrize("which", [0, 1], ids=["v7", "v8"])
def test_graphs_still_read_bin_averages(one_cycle_drop, which):
    """Every other caller of hardware_history (the graphs, the sparklines) is unchanged: AVG per bin."""
    r = one_cycle_drop[which]
    s, e = NOW - timedelta(hours=24), NOW
    assert max(v for _, v in r.hardware_history(GW, s, e)) == pytest.approx(0.05)
    assert max(v for _, v in r.hardware_history(GW, s, e, peak=True)) == 1.0


@pytest.mark.parametrize("which", [0, 1], ids=["v7", "v8"])
def test_peak_reads_the_minute_tier_max_not_its_average(tmp_path, which):
    """Rolled-up history: a minute row of avg 0.05 / max 1.0 is a drop."""
    t0 = BASE - 50 * 3600   # below the raw watermark -> answered by hw_minute
    mins = [(t0 + i * 60, GW, 0.05 if i == 30 else 0.0, 1.0 if i == 30 else 0.0, 20) for i in range(120)]
    r7, r8, conns = _reader(tmp_path, [], hw_minute=mins)
    try:
        ev = _events((r7, r8)[which], public=False, key="TIMELINE_WEEK")
        assert ev["count"] == 1
    finally:
        for c in conns:
            c.close()


def test_drops_either_side_of_time_the_app_was_off_are_two_outages(tmp_path):
    t1 = BASE - 10 * 3600
    t2 = BASE - 7 * 3600            # nothing recorded in between: the app was not running
    rows = [(ts, GW, v) for ts, v in _cycles(t1, 10, drops=[(t1 + 540, t1 + 600)])]
    rows += [(ts, GW, v) for ts, v in _cycles(t2, 10, drops=[(t2, t2 + 60)])]
    r7, r8, conns = _reader(tmp_path, rows)
    try:
        ev = _events(r8, public=False)
        assert ev["count"] == 2
        assert ev["total_down_seconds"] < 300     # not the three hours the app was off
    finally:
        for c in conns:
            c.close()


@pytest.fixture
def internet_down_router_up(tmp_path):
    t0 = BASE - 5 * 3600
    rows = [(ts, GW, v) for ts, v in _cycles(t0, 30)]                         # router fine throughout
    rows += [(ts, ANCHOR, v) for ts, v in _cycles(t0, 30, drops=[(t0 + 600, t0 + 900)])]
    r7, r8, conns = _reader(tmp_path, rows)
    yield r8
    for c in conns:
        c.close()


def test_an_internet_side_drop_counts_when_the_internet_ping_is_on(internet_down_router_up):
    ev = _events(internet_down_router_up, public=True)
    assert ev["count"] == 1
    assert ev["total_down_seconds"] >= 240


def test_internet_ping_off_reads_the_router_only_as_before(internet_down_router_up):
    ev = _events(internet_down_router_up, public=False)
    assert ev["count"] == 0


def test_one_outage_seen_by_both_hops_is_counted_once(tmp_path):
    """The two hops' rows are written a moment apart and can land in different seconds: the same
    outage on both is one drop, not two (nor split into many by the other hop's rows)."""
    t0 = BASE - 3 * 3600
    rows = [(ts, GW, v) for ts, v in _cycles(t0, 30, drops=[(t0 + 600, t0 + 660)])]
    rows += [(ts + 1, ANCHOR, v) for ts, v in _cycles(t0, 30, drops=[(t0 + 600, t0 + 720)])]
    r7, r8, conns = _reader(tmp_path, rows)
    try:
        for key in ("TIMELINE_4_HOURS", "TIMELINE_24_HOURS"):   # 1 s bins and 60 s bins
            assert _events(r8, public=True, key=key)["count"] == 1
    finally:
        for c in conns:
            c.close()


def test_outage_summary_single_series_unchanged():
    """The pure function keeps its old one-series contract (the Stats sheet's per-hop drops)."""
    t = datetime(2026, 11, 18, 12)
    pairs = [(t + timedelta(seconds=3 * i), v) for i, v in enumerate([0, 1, 1, 0, 0.25, 0.5, 0])]
    o = S.outage_summary(pairs)
    assert o["count"] == 2 and o["total_down_seconds"] == 3.0


@pytest.mark.parametrize("which", [0, 1], ids=["v7", "v8"])
def test_stats_sheet_router_drops_read_peaks_too(one_cycle_drop, which):
    """The Stats sheet's per-hop drop count had the same averaging bug."""
    import logging
    from netspeedtray.views.monitor.stats_detail import _load_subjects

    class _SheetWS(_WS):
        def summarize_hardware(self, stat, *a):
            return S.summarize_raw([5.0, 6.0] if stat == "latency_gw" else [0.0, 1.0])

    out = _load_subjects(_SheetWS(one_cycle_drop[which]), [{"key": "latency_gw", "kind": "hw"}],
                         NOW - timedelta(hours=24), NOW, 1.0, logging.getLogger("t"))
    assert out[0]["outage"]["count"] == 1
