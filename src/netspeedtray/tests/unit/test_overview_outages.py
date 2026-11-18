"""
The Overview's connection health / drop count over a history window (2.2.1-beta.2, #587).

Bugs, on real v8 files (temp DBs - never the live one), read with both the v7 and the v8 reader:

1. Only the ROUTER loss series was read (``latency_gw_timeout``). With the internet ping on
   (``latency_public_enabled``), an ISP outage with the router still answering said "steady".
2. The loss series was read through ``hardware_history``, which AVERAGES each bin (60 s past 6 h,
   3600 s past 30 days) - and the minute/hour tiers' ``avg_value``. A one-cycle drop (1.0 among
   nineteen 0.0 cycles) averages to 0.05, under ``outage_summary``'s 0.5, so drops vanished from
   every window longer than 6 h.
3. Rows on either side of a stretch with no rows at all (the app was not running) were one outage,
   and the hours the app was off counted as downtime.

The fix reads ``loss_history``: raw cycles UNBINNED (binning them to per-minute peaks chained one 3 s
drop per minute into "1 drop, 29 min down"), rollup bins as their peak plus an ESTIMATED lost time
(lost cycles x the cadence - never the bin's length). Hops are merged only across hops (review of
#587: merging by the gap distance also joined one hop's own separate drops).
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
WM_RAW_DEFAULT = BASE - 30 * 3600
GAP, JOIN = 30, 10.0   # constants.network.latency.OUTAGE_GAP_SLACK_SEC / OUTAGE_HOP_JOIN_SEC


def _cycles(start_ts: int, minutes: int, drops=()):
    """(ts, loss) at the probe's 3 s cadence; ``drops`` = the ts ranges [a, b) that lost every echo."""
    out = []
    for ts in range(start_ts, start_ts + minutes * 60, CYCLE):
        out.append((ts, 1.0 if any(a <= ts < b for a, b in drops) else 0.0))
    return out


def _db(tmp_path, hw_raw=(), hw_minute=(), hw_hour=(), wm_raw=WM_RAW_DEFAULT):
    h = F.History(now_ts=NOW_TS, wm_raw=wm_raw, wm_minute=BASE - 40 * 86400)
    h.hw_raw.extend(hw_raw)
    h.hw_minute.extend(hw_minute)
    h.hw_hour.extend(hw_hour)
    F.write_v8(tmp_path / "speed_history.db", h).close()
    return F.readers(tmp_path / "speed_history.db", tmp_path / "speed_history.db", now=NOW)


@pytest.fixture
def make(tmp_path):
    """make(which, **rows) -> the v7 (0) or v8 (1) reader over a fresh temp file."""
    opened = []

    def _make(which, **kw):
        d = tmp_path / f"db{len(opened)}"
        d.mkdir()
        r7, r8, conns = _db(d, **kw)
        opened.extend(conns)
        return (r7, r8)[which]

    yield _make
    for c in opened:
        c.close()


BOTH = pytest.mark.parametrize("which", [0, 1], ids=["v7", "v8"])


class _WS:
    """WidgetState's real history accessors over a temp-DB reader (no DB worker, no Qt)."""
    get_hardware_history = WidgetState.get_hardware_history
    get_loss_history = getattr(WidgetState, "get_loss_history", None)

    def __init__(self, reader):
        self.history_reader = reader
        self.db_worker = object()

    def summarize_network(self, *a):
        return S.summarize_raw([])

    def summarize_hardware(self, stat, *a):
        return S.summarize_raw([5.0, 6.0] if stat == "latency_gw" else [0.0, 1.0])


def _events(reader, public: bool, key="TIMELINE_24_HOURS"):
    series = {"down": [], "up": [], "cpu": [], "gpu": [], "ram": []}
    kw = {"public_enabled": True} if public else {}
    _s, _summ, events, _vol, _cov = _read_window(_WS(reader), key, NOW, None, series, 1.0, True, None, **kw)
    return events


def _sheet(reader, start, end):
    """The Stats sheet's router-block drops over [start, end]."""
    import logging
    from netspeedtray.views.monitor.stats_detail import _load_subjects
    out = _load_subjects(_WS(reader), [{"key": "latency_gw", "kind": "hw"}], start, end, 1.0,
                         logging.getLogger("t"))
    return out[0]["outage"]


def _gw(rows):
    return {"hw_raw": [(ts, GW, v) for ts, v in rows]}


# --- averaging ------------------------------------------------------------------------------------

@BOTH
def test_a_one_cycle_drop_is_counted_in_a_24h_window(make, which):
    t0 = BASE - 20 * 3600
    r = make(which, **_gw(_cycles(t0, 60, drops=[(t0 + 600, t0 + 603)])))
    assert _events(r, public=False)["count"] == 1


@BOTH
def test_graphs_still_read_bin_averages_and_loss_rows_are_unbinned(make, which):
    """Every other caller of hardware_history (graphs, sparklines) is unchanged: AVG per bin."""
    t0 = BASE - 20 * 3600
    r = make(which, **_gw(_cycles(t0, 60, drops=[(t0 + 600, t0 + 603)])))
    s, e = NOW - timedelta(hours=24), NOW
    assert max(v for _, v in r.hardware_history(GW, s, e)) == pytest.approx(0.05)
    rows = r.loss_history([GW], s, e)[GW]
    assert len(rows) == 1200 and all(x.span == 1 and x.down is None for x in rows)
    assert max(x.peak for x in rows) == 1.0


@BOTH
def test_minute_tier_reads_max_and_estimates_the_lost_time(make, which):
    """Rolled-up history: a minute row of avg 0.05 / max 1.0 / 20 samples is a drop of ~one cycle."""
    t0 = BASE - 50 * 3600   # below the raw watermark -> answered by hw_minute
    mins = [(t0 + i * 60, GW, 0.05 if i == 30 else 0.0, 1.0 if i == 30 else 0.0, 20) for i in range(120)]
    r = make(which, hw_minute=mins)
    ev = _events(r, public=False, key="TIMELINE_WEEK")
    assert ev["count"] == 1
    assert ev["total_down_seconds"] == pytest.approx(3.0)


# --- per-bin peaks must not chain short drops into a long outage (review MAJOR 2) -----------------

@BOTH
def test_a_short_drop_every_minute_is_not_one_half_hour_outage_in_the_raw_tier(make, which):
    """One 3 s drop in each of 30 consecutive minutes: 30 drops, seconds down - never "1 drop,
    29 min down" (60 s peak bins chained them)."""
    t0 = BASE - 20 * 3600
    r = make(which, **_gw(_cycles(t0, 30, drops=[(t0 + i * 60 + 30, t0 + i * 60 + 33) for i in range(30)])))
    ev = _sheet(r, NOW - timedelta(hours=24), NOW)
    assert ev["count"] == 30
    assert ev["total_down_seconds"] < 120


@BOTH
def test_coalesced_minute_bins_claim_only_their_estimated_downtime(make, which):
    """Rolled up, the same pattern is one run of 30 bad minutes (the tier cannot tell the cycles
    apart) - but its downtime is the lost cycles (30 x 3 s), not 29 minutes."""
    t0 = BASE - 50 * 3600
    mins = [(t0 + i * 60, GW, 0.05, 1.0, 20) for i in range(30)]
    r = make(which, hw_minute=mins)
    ev = _sheet(r, NOW - timedelta(days=7), NOW)
    assert ev["count"] == 1
    assert ev["total_down_seconds"] == pytest.approx(90.0)


def test_coalesced_bins_as_loss_rows_in_the_pure_function():
    T = datetime(2026, 11, 18, 12)
    rows = [(T + timedelta(seconds=60 * i), 1.0, 3.0, 60) for i in range(30)]
    o = S.outage_summary(rows, max_gap=GAP)
    assert o["count"] == 1 and o["total_down_seconds"] == pytest.approx(90.0)


@BOTH
def test_4h_and_24h_windows_agree_inside_the_raw_tier(make, which):
    t0 = BASE - 3 * 3600
    drops = [(t0 + 300, t0 + 309), (t0 + 900, t0 + 903), (t0 + 960, t0 + 963), (t0 + 2000, t0 + 2060)]
    r = make(which, **_gw(_cycles(t0, 60, drops=drops)))
    four, day = _events(r, False, "TIMELINE_4_HOURS"), _events(r, False, "TIMELINE_24_HOURS")
    assert four["count"] == day["count"] == 4
    assert four["total_down_seconds"] == day["total_down_seconds"]


@BOTH
def test_the_week_window_joins_a_drop_across_the_raw_minute_boundary(make, which):
    """An outage that started in rolled-up minutes and went on into raw cycles is one drop."""
    wm = WM_RAW_DEFAULT
    mins = [(wm - 600 + i * 60, GW, 0.0 if i < 8 else 1.0, 0.0 if i < 8 else 1.0, 20) for i in range(10)]
    raw = _cycles(wm, 10, drops=[(wm, wm + 60)])
    r = make(which, hw_minute=mins, **_gw(raw))
    ev = _events(r, public=False, key="TIMELINE_WEEK")
    assert ev["count"] == 1
    # two minutes estimated (2 x 20 cycles x 3 s, capped at 60 s each) + the raw run's 57 s
    assert ev["total_down_seconds"] == pytest.approx(120.0 + 57.0)


@BOTH
def test_the_all_window_hour_bins_keep_separate_drops_apart(make, which):
    """3600 s bins: bad 10:00, clean 11:00, bad 12:00 -> two drops."""
    t0 = BASE - 60 * 86400   # below the minute watermark -> answered by hw_hour
    hours = [(t0, GW, 0.01, 1.0, 1200), (t0 + 3600, GW, 0.0, 0.0, 1200), (t0 + 7200, GW, 0.01, 1.0, 1200)]
    r = make(which, hw_hour=hours)
    start = NOW - timedelta(days=90)
    loss = r.loss_history([GW], start, NOW)[GW]
    assert {x.span for x in loss} == {3600}
    ev = S.outage_summary(loss, max_gap=GAP)
    assert ev["count"] == 2
    assert ev["total_down_seconds"] == pytest.approx(2 * 0.01 * 1200 * 3)


# --- time the app was off ------------------------------------------------------------------------

@BOTH
def test_drops_either_side_of_time_the_app_was_off_are_two_outages(make, which):
    t1 = BASE - 10 * 3600
    t2 = BASE - 7 * 3600            # nothing recorded in between: the app was not running
    rows = _cycles(t1, 10, drops=[(t1 + 540, t1 + 600)]) + _cycles(t2, 10, drops=[(t2, t2 + 60)])
    r = make(which, **_gw(rows))
    ev = _events(r, public=False)
    assert ev["count"] == 2
    assert ev["total_down_seconds"] < 300     # not the three hours the app was off


def test_max_gap_splits_a_single_series():
    T = datetime(2026, 11, 18, 12)
    pairs = [(T, 1.0), (T + timedelta(seconds=3), 1.0), (T + timedelta(seconds=200), 1.0)]
    assert S.outage_summary(pairs)["count"] == 1                     # old contract, no max_gap
    o = S.outage_summary(pairs, max_gap=GAP)
    assert o["count"] == 2 and o["total_down_seconds"] == 3.0


# --- internet-side drops (the anchor) ------------------------------------------------------------

@pytest.fixture
def internet_down_router_up(make):
    t0 = BASE - 5 * 3600
    rows = [(ts, GW, v) for ts, v in _cycles(t0, 30)]                         # router fine throughout
    rows += [(ts, ANCHOR, v) for ts, v in _cycles(t0, 30, drops=[(t0 + 600, t0 + 900)])]
    return make(1, hw_raw=rows)


def test_an_internet_side_drop_counts_when_the_internet_ping_is_on(internet_down_router_up):
    ev = _events(internet_down_router_up, public=True)
    assert ev["count"] == 1
    assert ev["total_down_seconds"] >= 240


def test_internet_ping_off_reads_the_router_only_as_before(internet_down_router_up):
    assert _events(internet_down_router_up, public=False)["count"] == 0


@BOTH
def test_one_outage_seen_by_both_hops_is_counted_once(make, which):
    """The two hops' rows are written a moment apart and can land in different seconds: the same
    outage on both is one drop, and its downtime is their union, not their sum."""
    t0 = BASE - 3 * 3600
    rows = [(ts, GW, v) for ts, v in _cycles(t0, 30, drops=[(t0 + 600, t0 + 660)])]
    rows += [(ts + 1, ANCHOR, v) for ts, v in _cycles(t0, 30, drops=[(t0 + 600, t0 + 720)])]
    r = make(which, hw_raw=rows)
    for key in ("TIMELINE_4_HOURS", "TIMELINE_24_HOURS"):
        ev = _events(r, public=True, key=key)
        assert ev["count"] == 1
        assert ev["total_down_seconds"] == pytest.approx(118.0)   # [600, 657] u [601, 718]


# --- cross-hop merge only (review MAJOR 1) ---------------------------------------------------------

T0 = datetime(2026, 11, 18, 12)


def _ser(vals, step=3, off=0):
    return [(T0 + timedelta(seconds=off + step * i), float(v)) for i, v in enumerate(vals)]


@pytest.mark.parametrize("anchor", [[], _ser([0] * 12)], ids=["empty", "clean"])
def test_an_empty_or_clean_anchor_never_changes_the_router_count(anchor):
    gw = _ser([0, 1, 0, 0, 0, 0, 0, 1, 0])   # drop, 5 clean cycles (15 s), drop -> two drops
    alone = S.outage_summary(gw, max_gap=GAP, hop_join=JOIN)
    both = S.outage_summary(gw, anchor, max_gap=GAP, hop_join=JOIN)
    assert alone["count"] == both["count"] == 2


def test_same_hop_runs_within_the_join_distance_stay_apart():
    gw = _ser([0, 1, 0, 0, 1, 0])            # two drops 9 s apart, inside the 10 s join
    o = S.outage_summary(gw, _ser([0] * 6, off=1), max_gap=GAP, hop_join=JOIN)
    assert o["count"] == 2


def test_merging_never_counts_clean_time_as_down():
    gw = _ser([0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0])   # two one-cycle drops 27 s apart
    o = S.outage_summary(gw, [], max_gap=GAP, hop_join=JOIN)
    assert o["count"] == 2 and o["total_down_seconds"] == 0.0


def test_hour_bin_pairs_keep_drops_hours_apart():
    gw = _ser([1, 0, 1], step=3600)
    assert S.outage_summary(gw, max_gap=3630)["count"] == 2
    assert S.outage_summary(gw, [], max_gap=3630, hop_join=JOIN)["count"] == 2


def test_hops_merge_when_one_drop_follows_the_other_within_the_join():
    gw = _ser([0, 1, 1, 0, 0, 0, 0, 0])
    anchor = _ser([0, 0, 0, 0, 1, 1, 0, 0], off=1)     # starts 7 s after the router's ended
    assert S.outage_summary(gw, anchor, max_gap=GAP, hop_join=JOIN)["count"] == 1
    far = _ser([0, 0, 0, 0, 0, 0, 1, 0], off=1)         # 13 s after: a separate drop
    assert S.outage_summary(gw, far, max_gap=GAP, hop_join=JOIN)["count"] == 2


def test_outage_summary_single_series_unchanged():
    """The pure function keeps its old one-series contract."""
    pairs = [(T0 + timedelta(seconds=3 * i), v) for i, v in enumerate([0, 1, 1, 0, 0.25, 0.5, 0])]
    o = S.outage_summary(pairs)
    assert o["count"] == 2 and o["total_down_seconds"] == 3.0


# --- the Stats sheet ------------------------------------------------------------------------------

@BOTH
def test_stats_sheet_router_drops_count_a_short_drop_in_24h(make, which):
    t0 = BASE - 20 * 3600
    r = make(which, **_gw(_cycles(t0, 60, drops=[(t0 + 600, t0 + 603)])))
    assert _sheet(r, NOW - timedelta(hours=24), NOW)["count"] == 1
