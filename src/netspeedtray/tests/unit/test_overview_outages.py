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
def test_a_two_cycle_drop_is_counted_in_a_24h_window(make, which):
    t0 = BASE - 20 * 3600
    r = make(which, **_gw(_cycles(t0, 60, drops=[(t0 + 600, t0 + 606)])))
    assert _events(r, public=False)["count"] == 1


@BOTH
def test_graphs_still_read_bin_averages_and_loss_rows_are_unbinned(make, which):
    """Every other caller of hardware_history (graphs, sparklines) is unchanged: AVG per bin."""
    t0 = BASE - 20 * 3600
    r = make(which, **_gw(_cycles(t0, 60, drops=[(t0 + 600, t0 + 603)])))
    s, e = NOW - timedelta(hours=24), NOW
    assert max(v for _, v in r.hardware_history(GW, s, e)) == pytest.approx(0.05)
    rows = r.loss_history([GW], s, e)[GW]
    assert all(x.span == 1 and x.down is None for x in rows) and max(x.peak for x in rows) == 1.0
    if which == 1:   # v8 reads only the bad cycle and the clean cycles either side
        assert [(int(x.ts.timestamp()) - t0, x.peak) for x in rows] == [(597, 0.0), (600, 1.0), (603, 0.0)]
    else:            # v7 (an upgrade still deferred) reads every cycle
        assert len(rows) == 1200


@BOTH
def test_minute_tier_reads_max_and_estimates_the_lost_time(make, which):
    """Rolled-up history: a minute row of avg 0.2 / max 1.0 / 20 samples is a drop of ~four cycles."""
    t0 = BASE - 50 * 3600   # below the raw watermark -> answered by hw_minute
    mins = [(t0 + i * 60, GW, 0.2 if i == 30 else 0.0, 1.0 if i == 30 else 0.0, 20) for i in range(120)]
    r = make(which, hw_minute=mins)
    ev = _events(r, public=False, key="TIMELINE_WEEK")
    assert ev["count"] == 1
    assert ev["total_down_seconds"] == pytest.approx(12.0)


# --- per-bin peaks must not chain short drops into a long outage (review MAJOR 2) -----------------

@BOTH
def test_a_short_drop_every_minute_is_not_one_half_hour_outage_in_the_raw_tier(make, which):
    """One 6 s drop in each of 30 consecutive minutes: 30 drops, 3 minutes down - never "1 drop,
    29 min down" (60 s peak bins chained them)."""
    t0 = BASE - 20 * 3600
    r = make(which, **_gw(_cycles(t0, 30, drops=[(t0 + i * 60 + 30, t0 + i * 60 + 36) for i in range(30)])))
    ev = _sheet(r, NOW - timedelta(hours=24), NOW)
    assert ev["count"] == 30
    assert ev["total_down_seconds"] == pytest.approx(180.0)


@BOTH
def test_coalesced_minute_bins_claim_only_their_estimated_downtime(make, which):
    """Rolled up, the same pattern is one run of 30 bad minutes (the tier cannot tell the cycles
    apart) - but its downtime is the lost cycles (30 x 4 x 3 s), not 29 minutes."""
    t0 = BASE - 50 * 3600
    mins = [(t0 + i * 60, GW, 0.2, 1.0, 20) for i in range(30)]
    r = make(which, hw_minute=mins)
    ev = _sheet(r, NOW - timedelta(days=7), NOW)
    assert ev["count"] == 1
    assert ev["total_down_seconds"] == pytest.approx(360.0)


def test_coalesced_bins_as_loss_rows_in_the_pure_function():
    T = datetime(2026, 11, 18, 12)
    rows = [(T + timedelta(seconds=60 * i), 1.0, 3.0, 60) for i in range(30)]
    o = S.outage_summary(rows, max_gap=GAP)
    assert o["count"] == 1 and o["total_down_seconds"] == pytest.approx(90.0)


@BOTH
def test_4h_and_24h_windows_agree_inside_the_raw_tier(make, which):
    t0 = BASE - 3 * 3600
    drops = [(t0 + 300, t0 + 309), (t0 + 900, t0 + 906), (t0 + 960, t0 + 966), (t0 + 2000, t0 + 2060)]
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
    # two minutes estimated (2 x 20 cycles x 3 s, capped at 60 s each) + the raw run's 20 cycles
    assert ev["total_down_seconds"] == pytest.approx(120.0 + 60.0)


@BOTH
def test_the_hour_tier_is_not_read_for_drops(make, which):
    """Hour rows inflate and chain into multi-day drops: loss_history never reads them."""
    t0 = BASE - 60 * 86400   # below the minute watermark -> only hw_hour could answer
    hours = [(t0, GW, 0.01, 1.0, 1200), (t0 + 3600, GW, 0.0, 0.0, 1200), (t0 + 7200, GW, 0.01, 1.0, 1200)]
    r = make(which, hw_hour=hours)
    assert r.loss_history([GW], NOW - timedelta(days=90), NOW)[GW] == []


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
    """With the internet ping on, the internet series alone decides (the router lost 60 s of the
    same outage; it changes nothing). The cross-hop union itself is pinned by the pure tests."""
    t0 = BASE - 3 * 3600
    rows = [(ts, GW, v) for ts, v in _cycles(t0, 30, drops=[(t0 + 600, t0 + 660)])]
    rows += [(ts + 1, ANCHOR, v) for ts, v in _cycles(t0, 30, drops=[(t0 + 600, t0 + 720)])]
    r = make(which, hw_raw=rows)
    for key in ("TIMELINE_4_HOURS", "TIMELINE_24_HOURS"):
        ev = _events(r, public=True, key=key)
        assert ev["count"] == 1
        assert ev["total_down_seconds"] == pytest.approx(120.0)   # anchor: [601, 721]


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
    r = make(which, **_gw(_cycles(t0, 60, drops=[(t0 + 600, t0 + 606)])))
    assert _sheet(r, NOW - timedelta(hours=24), NOW)["count"] == 1


# --- native spans: rollups are never re-binned by window length (second review, MAJOR 1) ---------

def _alternating_minutes(t0, n=60):
    """A bad minute (four lost cycles), a clean minute, a bad minute ... - n minutes."""
    return [(t0 + i * 60, GW, 0.2 if i % 2 == 0 else 0.0, 1.0 if i % 2 == 0 else 0.0, 20) for i in range(n)]


@BOTH
def test_the_all_window_keeps_minute_rows_at_their_own_span(make, which):
    """Past 30 days the window bin is an hour: minute rows re-binned to hours chained every bad
    minute of an hour into one drop (the owner's history: 327 drops read as 10, one of 55 h)."""
    t0 = BASE - 20 * 86400
    r = make(which, hw_minute=_alternating_minutes(t0))
    ev = _sheet(r, NOW - timedelta(days=90), NOW)
    assert ev["count"] == 30
    assert ev["total_down_seconds"] == pytest.approx(30 * 12.0)


@BOTH
def test_a_month_window_of_30_days_and_an_hour_reads_like_29_days(make, which):
    """Naive local 'now - 30 d' spans 30 d + 1 h across a DST change: past the 30-day bin switch."""
    t0 = BASE - 20 * 86400
    r = make(which, hw_minute=_alternating_minutes(t0))
    a = _sheet(r, NOW - timedelta(days=29), NOW)
    b = _sheet(r, NOW - timedelta(days=30, hours=1), NOW)
    assert a["count"] == b["count"] == 30
    assert a["total_down_seconds"] == b["total_down_seconds"]


# --- downtime rules (second review: mutation gaps, raw/rollup consistency, cross-hop union) ------

def _lr(sec, peak, down=None, span=1):
    from netspeedtray.core.db.history_reader import LossRow
    return LossRow(T0 + timedelta(seconds=sec), float(peak), down, span)


@BOTH
def test_a_rollup_rows_estimate_is_capped_at_its_span(make, which):
    """21 cycles fit in a minute at a 3 s cadence: 21 x 3 s = 63 s must read as the minute's 60 s."""
    t0 = BASE - 50 * 3600
    mins = [(t0 - 60, GW, 0.0, 0.0, 20), (t0, GW, 1.0, 1.0, 21), (t0 + 60, GW, 0.0, 0.0, 20)]
    r = make(which, hw_minute=mins)
    rows = r.loss_history([GW], NOW - timedelta(days=7), NOW)[GW]
    assert [x.down for x in rows] == [0.0, 60.0, 0.0]
    assert _sheet(r, NOW - timedelta(days=7), NOW)["total_down_seconds"] == pytest.approx(60.0)


def test_one_lost_raw_cycle_reads_like_one_rolled_up():
    """Raw: first bad row to last plus one cycle - one lost cycle is 3 s, as in a rollup row."""
    raw = [_lr(0, 0), _lr(3, 1), _lr(6, 0)]
    assert S.outage_summary(raw, max_gap=GAP, raw_cycle=CYCLE)["total_down_seconds"] == 3.0
    rolled = [_lr(0, 1.0, 3.0, 60)]
    assert S.outage_summary(rolled, max_gap=GAP, raw_cycle=CYCLE)["total_down_seconds"] == 3.0
    long = [_lr(0, 0)] + [_lr(3 * i, 1) for i in range(1, 21)] + [_lr(63, 0)]
    assert S.outage_summary(long, max_gap=GAP, raw_cycle=CYCLE)["total_down_seconds"] == 60.0


def test_both_hops_losing_the_same_minute_is_not_counted_twice():
    gw = [_lr(0, 1.0, 3.0, 60)]
    anchor = [_lr(0, 1.0, 3.0, 60)]
    o = S.outage_summary(gw, anchor, max_gap=GAP, hop_join=JOIN, raw_cycle=CYCLE)
    assert o["count"] == 1 and o["total_down_seconds"] == 3.0


def test_a_rollup_run_joined_to_a_later_raw_run_of_the_other_hop_adds_both():
    """Router lost 3 s in the minute at 0; the internet then lost two raw cycles at 65-68 s (inside
    the minute's span + the 10 s join): one outage, 3 s + 6 s down - the union, not the larger."""
    gw = [_lr(0, 1.0, 3.0, 60), _lr(60, 0.0, 0.0, 60)]
    anchor = [_lr(62, 0), _lr(65, 1), _lr(68, 1), _lr(71, 0)]
    o = S.outage_summary(gw, anchor, max_gap=GAP, hop_join=JOIN, raw_cycle=CYCLE)
    assert o["count"] == 1
    assert o["total_down_seconds"] == pytest.approx(9.0)


def test_the_hop_join_includes_its_boundary():
    """Router run ends at 3 s (1 s span): a drop on the other hop starting exactly at 3 + 1 + 10 s
    is the same outage; one second later it is not."""
    gw = [_lr(0, 0), _lr(3, 1), _lr(6, 0)]
    anchor = [_lr(11, 0), _lr(14, 1), _lr(17, 0)]
    assert S.outage_summary(gw, anchor, max_gap=GAP, hop_join=JOIN)["count"] == 1
    late = [_lr(12, 0), _lr(15, 1), _lr(18, 0)]
    assert S.outage_summary(gw, late, max_gap=GAP, hop_join=JOIN)["count"] == 2


# --- the bad-rows read gives exactly the full read's outages (second review, MAJOR 2) ------------

def _full_read(r, stats, start, end):
    """Reference: EVERY row of every tier at its native span, inside the range that reader's own
    tier rule gives the tier (v8: watermarks; v7: below each finer tier's earliest in-horizon row)."""
    from netspeedtray.core.db.history_reader import LossRow, V8HistoryReader, _loss_down_sql
    from netspeedtray.core.db import schema_v8 as S8
    st, et = int(start.timestamp()), int(end.timestamp())
    v8 = isinstance(r, V8HistoryReader)
    if v8:
        bounds, tables, ts = r._tier_bounds(r._HW, "h"), r._HW, "ts"
        key = f"h.metric_id IN (SELECT metric_id FROM {S8.METRICS} WHERE name = ? AND entity = '')"
    else:
        bounds, tables, ts = r._bounds(r._HW_TIERS), r._HW_TIERS, "timestamp"
        key = "h.stat_type = ?"
    out = {}
    for stat in stats:
        rows = []
        for k, native in enumerate((1, 60)):   # the hour tier is never read for drops
            peak = "h.value" if k == 0 else "h.max_value"
            down = "NULL" if k == 0 else _loss_down_sql("h.avg_value", "h.sample_count", native)
            q = (f"SELECT h.{ts}, {peak}, {down} FROM {tables[k]} h WHERE {key} "
                 f"AND h.{ts} BETWEEN ? AND ?{bounds[k][0]}")
            for t, p, d in r._conn().execute(q, (stat, st, et, *bounds[k][1])):
                rows.append(LossRow(datetime.fromtimestamp(t), float(p), d, native))
        out[stat] = sorted(rows, key=lambda x: x.ts.timestamp())   # naive times repeat at a DST fall-back
    return out


def _random_history(seed):
    import random
    rnd = random.Random(seed)
    wm_raw, wm_min = BASE - 6 * 3600, BASE - 3 * 86400   # small tiers: the test stays fast
    raw, mins, hours = [], [], []

    def loss():
        x = rnd.random()
        return 1.0 if x < 0.06 else (0.5 if x < 0.08 else (0.25 if x < 0.12 else 0.0))

    for hop in (GW, ANCHOR):
        t = wm_raw - 900                      # strays below the raw watermark (not rolled up yet)
        while t < NOW_TS - 5:
            if rnd.random() < 0.002:
                t += rnd.randint(60, 4000)    # the app was off
            raw.append((t + (hop == ANCHOR), hop, loss()))
            t += CYCLE
        for m in range(wm_min - 7200, wm_raw + 120, 60):   # incl. strays past each tier's range
            if rnd.random() < 0.03:
                continue
            bad = rnd.random() < 0.15
            mins.append((m, hop, rnd.random() * 0.1 if bad else 0.0,
                         1.0 if bad else rnd.choice([0.0, 0.25]), rnd.choice([19, 20, 21])))
        for h in range(BASE - 80 * 86400, wm_min + 3600, 3600):
            if rnd.random() < 0.05:
                continue
            bad = rnd.random() < 0.3
            hours.append((h, hop, rnd.random() * 0.05 if bad else 0.0, 1.0 if bad else 0.25, 1200))
    return dict(hw_raw=raw, hw_minute=mins, hw_hour=hours, wm_raw=wm_raw)


def test_a_neighbour_is_looked_up_only_inside_its_tiers_range(make):
    """A raw row below the raw watermark (rolled up, not deleted yet) is not part of the series:
    it must not become the clean neighbour that splits a drop running from the last rolled-up
    minute into the first raw cycle (v8 watermarks; the v7 file has none)."""
    wm = WM_RAW_DEFAULT
    mins = [(wm - 120, GW, 0.0, 0.0, 20), (wm - 60, GW, 1.0, 1.0, 20)]
    raw = [(wm - 3, GW, 0.0), (wm, GW, 1.0), (wm + 3, GW, 1.0), (wm + 6, GW, 0.0)]
    r = make(1, hw_minute=mins, hw_raw=raw)
    assert _sheet(r, NOW - timedelta(days=7), NOW)["count"] == 1


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_bad_rows_read_matches_the_full_read(tmp_path, seed):
    r7, r8, conns = _db(tmp_path, **_random_history(seed))
    try:
        windows = [timedelta(hours=2), timedelta(hours=24), timedelta(days=7),
                   timedelta(days=30, hours=1), timedelta(days=90)]
        for w in windows:
            s = NOW - w
            for stats in ([GW], [GW, ANCHOR]):
                for r in (r7, r8):
                    ref = _full_read(r, stats, s, NOW)
                    got_rows = r.loss_history(stats, s, NOW)
                    for mc in (0, 2):   # every run, and the Monitor's 2-cycle bar
                        kw = dict(max_gap=GAP, hop_join=JOIN, raw_cycle=CYCLE, min_cycles=mc,
                                  min_rollup_cycles=2 * mc)   # the Monitor's 2 raw / 4 per minute
                        want = S.outage_summary(*(ref[x] for x in stats), **kw)
                        assert want["count"] > 0
                        got = S.outage_summary(*(got_rows[x] for x in stats), **kw)
                        assert (got["count"], got["runs"]) == (want["count"], want["runs"]), (w, stats, r, mc)
                        assert got["total_down_seconds"] == pytest.approx(want["total_down_seconds"])
                    if r is r8:   # v8 reads only bad rows + neighbours; v7 reads in full
                        assert sum(len(v) for v in got_rows.values()) < sum(len(v) for v in ref.values())
    finally:
        for c in conns:
            c.close()


# --- a drop needs at least 2 lost cycles (OUTAGE_MIN_CYCLES, owner decision on #587) -------------

def _raw_drop(which, make, bad_offsets):
    """30 min of clean cycles from t0 with the cycles at ``bad_offsets`` (s) lost."""
    t0 = BASE - 3 * 3600
    rows = [(ts, GW, 1.0 if ts - t0 in bad_offsets else 0.0) for ts, _ in _cycles(t0, 30)]
    return make(which, hw_raw=rows)


@BOTH
def test_one_lost_cycle_is_a_blip_two_in_a_row_are_a_drop(make, which):
    one = _raw_drop(which, make, {600})
    assert _events(one, public=False)["count"] == 0
    assert _sheet(one, NOW - timedelta(hours=24), NOW)["count"] == 0
    two = _raw_drop(which, make, {600, 603})
    ev = _events(two, public=False)
    assert ev["count"] == 1 and ev["total_down_seconds"] == pytest.approx(6.0)
    assert _sheet(two, NOW - timedelta(hours=24), NOW)["count"] == 1


@BOTH
def test_two_blips_with_a_clean_cycle_between_are_no_drop(make, which):
    r = _raw_drop(which, make, {600, 606})
    ev = _events(r, public=False)
    assert ev["count"] == 0 and ev["total_down_seconds"] == 0.0


@BOTH
@pytest.mark.parametrize("raw_bad, drops", [(1, 0), (2, 1)], ids=["1+1-cycles", "1+2-cycles"])
def test_a_run_across_the_raw_minute_boundary(make, which, raw_bad, drops):
    """The last rolled-up minute lost one cycle and the raw tier carries on: the stretches are
    not added up across the boundary (a rollup cannot say its cycle was the minute's last), so
    1 + 1 is no drop; 1 + 2 is one - with both parts' downtime."""
    wm = WM_RAW_DEFAULT
    mins = [(wm - 120, GW, 0.0, 0.0, 20), (wm - 60, GW, 0.05, 1.0, 20)]
    raw = [(wm + 3 * i, GW, 1.0 if i < raw_bad else 0.0) for i in range(10)]
    r = make(which, hw_minute=mins, **_gw([(t, v) for t, _, v in raw]))
    ev = _events(r, public=False, key="TIMELINE_WEEK")
    assert ev["count"] == drops
    assert ev["total_down_seconds"] == pytest.approx(3.0 + 3.0 * raw_bad if drops else 0.0)


def test_a_blip_on_the_other_hop_neither_counts_nor_bridges_two_drops():
    gw = _ser([0, 1, 1, 0, 0, 0, 1, 1, 0])               # drops at 3-6 s and 18-21 s
    anchor = _ser([0, 0, 0, 0, 1, 0, 0, 0, 0], off=1)     # one lost cycle at 13 s
    o = S.outage_summary(gw, anchor, max_gap=GAP, hop_join=JOIN, raw_cycle=CYCLE, min_cycles=2)
    assert o["count"] == 2 and o["total_down_seconds"] == 12.0
    one_each = _ser([0, 1, 0, 0]), _ser([0, 1, 0, 0], off=1)   # the same blip on both hops
    assert S.outage_summary(*one_each, max_gap=GAP, hop_join=JOIN, raw_cycle=CYCLE,
                            min_cycles=2)["count"] == 0


# --- final round (#587): minute rows need 4 lost cycles, hour rows never count, the internet hop
# decides when it is on, and "last" says which day ------------------------------------------------

@BOTH
@pytest.mark.parametrize("avg, drops", [(0.1, 0), (0.15, 0), (0.2, 1)], ids=["2", "3", "4"])
def test_a_minute_row_needs_four_estimated_lost_cycles(make, which, avg, drops):
    """avg x 20 samples = 2 / 3 / 4 lost cycles: a minute row counts from 4 (>= 12 s of it lost) -
    where two consecutive raw cycles more likely than not were among them."""
    t0 = BASE - 50 * 3600
    mins = [(t0 - 60, GW, 0.0, 0.0, 20), (t0, GW, avg, 1.0, 20), (t0 + 60, GW, 0.0, 0.0, 20)]
    r = make(which, hw_minute=mins)
    assert _sheet(r, NOW - timedelta(days=7), NOW)["count"] == drops


# The owner's 2026-10-21 12:31-12:42 outage as the minute tier keeps it: (avg, samples) per minute -
# the probe slows while echoes time out, so the samples fall to 7-8 a minute.
_OCT21 = [(0.0, 20), (0.088, 17), (1.0, 15), (1.0, 15), (1.0, 12), (1.0, 7), (1.0, 8), (1.0, 7), (1.0, 8),
          (1.0, 7), (1.0, 8), (1.0, 7), (0.105, 19), (0.0, 20), (0.0, 20), (0.039, 19), (0.0, 20)]


@BOTH
def test_a_real_ten_minute_outage_in_the_minute_tier_is_one_drop(make, which):
    t0 = BASE - 50 * 3600
    mins = [(t0 + 60 * i, GW, a, (1.0 if a >= 0.08 else (0.75 if a else 0.0)), n) for i, (a, n) in enumerate(_OCT21)]
    r = make(which, hw_minute=mins)
    ev = _sheet(r, NOW - timedelta(days=7), NOW)
    assert ev["count"] == 1
    assert ev["runs"][0][0] == datetime.fromtimestamp(t0 + 60)          # 12:31 ... 12:42
    assert ev["runs"][0][1] == datetime.fromtimestamp(t0 + 60 * 12)
    lost = sum(min(a * n * 3.0, 60.0) for a, n in _OCT21[1:13])
    assert ev["total_down_seconds"] == pytest.approx(lost)


@BOTH
def test_hour_rows_never_count_as_drops(make, which):
    """Hour rows inflate (~1.8x the same time as minutes) and chain into multi-day drops: the drop
    count covers minute-or-finer data only (about the last 30 days)."""
    t0 = BASE - 60 * 86400
    hours = [(t0 + 3600 * i, GW, 0.5, 1.0, 1200) for i in range(48)]   # two days of heavy loss
    r = make(which, hw_hour=hours)
    ev = _sheet(r, NOW - timedelta(days=90), NOW)
    assert ev["count"] == 0 and ev["total_down_seconds"] == 0.0


def test_raw_cycle_zero_never_makes_a_rollup_row_infinite():
    rows = [_lr(0, 1.0, 30.0, 60)]
    assert S.outage_summary(rows, min_cycles=2, min_rollup_cycles=4)["count"] == 0


@pytest.fixture
def router_and_internet(make):
    """Router: a 30 s drop at +600 s. Internet: a 60 s drop at +1200 s (the router answered)."""
    t0 = BASE - 3 * 3600
    rows = [(ts, GW, v) for ts, v in _cycles(t0, 30, drops=[(t0 + 600, t0 + 630)])]
    rows += [(ts + 1, ANCHOR, v) for ts, v in _cycles(t0, 30, drops=[(t0 + 1200, t0 + 1260)])]
    return make(1, hw_raw=rows)


def test_with_the_internet_ping_on_only_the_internet_series_counts(router_and_internet):
    ev = _events(router_and_internet, public=True)
    assert ev["count"] == 1 and ev["total_down_seconds"] == pytest.approx(60.0)
    assert _events(router_and_internet, public=False)["count"] == 1   # router only: its own drop


def test_the_overview_matches_the_stats_sheets_internet_block(router_and_internet):
    import logging
    from netspeedtray.views.monitor.stats_detail import _load_subjects
    s, e = NOW - timedelta(hours=24), NOW
    ov = _events(router_and_internet, public=True)
    blocks = _load_subjects(_WS(router_and_internet), [{"key": "latency_anchor", "kind": "hw"},
                                                       {"key": "latency_gw", "kind": "hw"}],
                            s, e, 1.0, logging.getLogger("t"))
    internet, router = blocks[0]["outage"], blocks[1]["outage"]
    assert (ov["count"], ov["total_down_seconds"], ov["last_start"]) == \
        (internet["count"], internet["total_down_seconds"], internet["last_start"])
    assert router["count"] == 1 and router["last_start"] != internet["last_start"]   # its own count


def test_last_drop_shows_a_date_when_it_was_not_today():
    from netspeedtray.views.monitor.overview.tab import drop_time_label
    now = datetime(2026, 11, 18, 10, 0)
    assert drop_time_label(datetime(2026, 11, 18, 8, 40), now, "en_US") == "08:40"
    assert drop_time_label(datetime(2026, 10, 11, 16, 48), now, "en_US") == "10/11 16:48"
    de = drop_time_label(datetime(2026, 10, 11, 16, 48), now, "de_DE")
    assert de.startswith("11.10") and de.endswith("16:48") and "2026" not in de and "26 " not in de


def test_red_means_a_recent_drop(q_app):
    from types import SimpleNamespace
    from netspeedtray import constants
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    colors = constants.network.latency.VERDICT_COLORS
    mw = SimpleNamespace(system_power=None, latency_gw=12.0, latency_anchor=None)
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    now = datetime.now()
    ov._latency_events = {"count": 5, "last_start": now - timedelta(hours=2)}
    assert colors["slow"] in ov._context_right_text(mw)
    ov._latency_events = {"count": 5, "last_start": now - timedelta(days=3)}
    txt = ov._context_right_text(mw)
    assert colors["ok"] in txt and colors["slow"] not in txt
    ov._latency_events = {"count": 1, "last_start": now - timedelta(minutes=5)}
    assert colors["slow"] in ov._context_right_text(mw)
    ov._latency_events = {"count": 0, "last_start": None}
    assert colors["good"] in ov._context_right_text(mw)
