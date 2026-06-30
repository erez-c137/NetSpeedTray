"""
WindowSummary - tier-aware summary stats. The honesty spine: exact percentiles only from the raw tier;
rollup tiers give weighted-avg + max with percentiles marked UNAVAILABLE; every summary carries coverage.
"""
from netspeedtray.utils import summaries as S


def test_summarize_raw_is_exact():
    s = S.summarize_raw([10, 20, 30, 40, 100], coverage=100.0)
    assert s.exact and s.tier == "raw" and s.count == 5
    assert s.min == 10 and s.max == 100 and s.avg == 40 and s.p50 == 30
    assert 40 < s.p95 <= 100 and s.p99 is not None and s.stddev is not None


def test_summarize_rollup_weighted_avg_no_percentiles():
    s = S.summarize_rollup(avgs=[10, 50], maxes=[20, 90], counts=[1, 3], tier="minute")
    assert not s.exact and s.tier == "minute"
    assert s.avg == (10 * 1 + 50 * 3) / 4          # sample-weighted = 40
    assert s.max == 90
    assert s.min is None and s.p95 is None and s.p99 is None and s.stddev is None   # unavailable
    assert "rollup" in s.note


def test_empty_windows():
    assert S.summarize_raw([]).count == 0
    assert S.summarize_rollup([], []).count == 0


def test_coverage_loss_pctbelow_timeabove():
    assert S.coverage_pct(720, 3600, 5) == 100.0    # 1 sample / 5s over 1h
    assert S.coverage_pct(360, 3600, 5) == 50.0
    assert S.coverage_pct(99999, 3600, 5) == 100.0  # clamped
    assert S.loss_pct(2, 100) == 2.0 and S.loss_pct(0, 0) is None
    assert S.pct_below([10, 20, 30, 40], 25) == 50.0
    assert S.time_above([85, 92, 95, 80], 90, 1.0) == 2.0


def test_peak_offpeak_split():
    from datetime import datetime
    pairs = [(datetime(2026, 6, 28, 20, 0), 100.0), (datetime(2026, 6, 28, 20, 30), 80.0),  # 20h busy
             (datetime(2026, 6, 28, 4, 0), 5.0), (datetime(2026, 6, 28, 4, 30), 15.0)]       # 4h quiet
    po = S.peak_offpeak(pairs)
    assert po["peak_hour"] == 20.0 and po["peak_avg"] == 90.0
    assert po["offpeak_hour"] == 4.0 and po["offpeak_avg"] == 10.0
    # Fewer than two distinct hours -> no split (would be meaningless).
    assert S.peak_offpeak([(datetime(2026, 6, 28, 20, 0), 100.0)]) is None
    assert S.peak_offpeak([]) is None


def test_outage_summary_counts_drop_events():
    from datetime import datetime
    # timeout series (0 = ok, 1 = lost ping): two separate outage runs.
    t = lambda m: datetime(2026, 6, 28, 14, m, 0)
    pairs = [(t(0), 0), (t(1), 1), (t(2), 1), (t(3), 0),      # outage #1: 14:01-14:02
             (t(4), 0), (t(5), 1), (t(6), 0)]                  # outage #2: 14:05
    o = S.outage_summary(pairs)
    assert o["count"] == 2
    assert o["last_start"] == t(5)
    assert o["total_down_seconds"] == 60   # run1 (14:01->14:02) = 60s; run2 (single sample) = 0s


def test_event_runs_handles_trailing_bad():
    from datetime import datetime
    t = lambda m: datetime(2026, 6, 28, 14, m, 0)
    runs = S.event_runs([(t(0), 0), (t(1), 1), (t(2), 1)], lambda v: v >= 0.5)
    assert len(runs) == 1 and runs[0][0] == t(1) and runs[0][1] == t(2)
    assert S.outage_summary([(t(0), 0), (t(1), 0)])["count"] == 0   # clean window -> no events


def test_hourly_profile_accepts_unix_seconds():
    # 72000s = 20:00 UTC-anchored hour-of-day arithmetic (20h); robust to plain unix timestamps.
    prof = S.hourly_profile([(72000.0, 50.0), (72000.0, 70.0)])
    assert prof[20] == 60.0
