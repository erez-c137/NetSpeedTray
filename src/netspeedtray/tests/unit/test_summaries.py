"""
WindowSummary — tier-aware summary stats. The honesty spine: exact percentiles only from the raw tier;
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
