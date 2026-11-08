"""
utils/summaries is pure Python (the widget's hover card reads summarize_hardware, and the widget must
not load numpy) - and every figure is BIT-IDENTICAL to the numpy implementation it replaced, kept here
as the reference (main's code, 2.2.0-rc.6): numpy's pairwise sum / mean / std and its "linear"
percentile, on random sizes either side of every pairwise-sum boundary (8, 128, halves), with NaN, inf,
negatives, ties and None-free ints.
"""
import math
import random

import numpy as np
import pytest

from netspeedtray.utils import summaries as S


# --- main's numpy versions ------------------------------------------------------------------------------

def np_summarize_raw(values, coverage=100.0):
    arr = np.asarray([float(v) for v in values], dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return S._empty("raw")
    return S.WindowSummary(
        count=int(arr.size), coverage_pct=round(float(coverage), 1), tier="raw", exact=True,
        avg=float(arr.mean()), min=float(arr.min()), max=float(arr.max()),
        p50=float(np.percentile(arr, 50)), p95=float(np.percentile(arr, 95)),
        p99=float(np.percentile(arr, 99)), stddev=float(arr.std()),
        note="exact from raw samples")


def np_summarize_rollup(avgs, maxes, counts=None, tier="minute", coverage=100.0):
    a = np.asarray([float(x) for x in avgs], dtype=float)
    m = np.asarray([float(x) for x in maxes], dtype=float)
    if a.size == 0:
        return S._empty(tier)
    c = np.asarray([float(x) for x in counts], dtype=float) if counts is not None and len(counts) else np.ones_like(a)
    total = float(c.sum())
    weighted_avg = float((a * c).sum() / total) if total > 0 else float(a.mean())
    return S.WindowSummary(
        count=int(total) if counts is not None else int(a.size), coverage_pct=round(float(coverage), 1),
        tier=tier, exact=False, avg=weighted_avg, min=None, max=float(m.max()),
        p50=None, p95=None, p99=None, stddev=None, note=S._UNAVAILABLE)


def np_pct_below(values, threshold):
    arr = np.asarray([float(v) for v in values], dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return round(float((arr < threshold).mean() * 100.0), 1)


def np_time_above(values, threshold, interval):
    arr = np.asarray([float(v) for v in values], dtype=float)
    arr = arr[np.isfinite(arr)]
    return float((arr >= threshold).sum() * max(0.0, interval))


SIZES = list(range(0, 40)) + [63, 64, 65, 127, 128, 129, 130, 255, 256, 257, 1000, 1023, 1024, 1025, 3600,
                              8191, 8192, 8193, 20011, 86400]


def _values(n, rng):
    kind = rng.choice(["uniform", "wide", "ints", "ties", "neg", "dirty"])
    if kind == "uniform":
        return [rng.uniform(0, 100) for _ in range(n)]
    if kind == "wide":
        return [rng.uniform(-1, 1) * 10 ** rng.randint(-3, 12) for _ in range(n)]
    if kind == "ints":
        return [rng.randint(0, 5000) for _ in range(n)]
    if kind == "ties":
        return [rng.choice([0.0, 1.5, 7.25]) for _ in range(n)]
    if kind == "neg":
        return [rng.gauss(0, 1e5) for _ in range(n)]
    v = [rng.uniform(0, 1e6) for _ in range(n)]
    for _ in range(min(n, 3)):
        v[rng.randrange(n)] = rng.choice([math.nan, math.inf, -math.inf])
    return v


def _same(a, b):
    """Bit-identical summaries (NaN == NaN)."""
    for fa, fb in zip(a.as_dict().values(), b.as_dict().values()):
        if isinstance(fa, float) and isinstance(fb, float) and math.isnan(fa) and math.isnan(fb):
            continue
        assert fa == fb and type(fa) is type(fb), (fa, fb)


@pytest.mark.parametrize("n", SIZES)
def test_summarize_raw_is_bit_identical_to_numpy(n):
    rng = random.Random(n)
    for _ in range(3 if n < 5000 else 1):
        v = _values(n, rng)
        _same(S.summarize_raw(v, 87.25), np_summarize_raw(v, 87.25))


@pytest.mark.parametrize("n", [1, 2, 7, 8, 9, 100, 129, 1440, 8193])
def test_summarize_rollup_is_bit_identical_to_numpy(n):
    rng = random.Random(n)
    for counts in (None, [], [rng.randint(1, 60) for _ in range(n)], [0] * n):
        avgs = [rng.uniform(0, 1e6) for _ in range(n)]
        maxes = [a * rng.uniform(1, 3) for a in avgs]
        _same(S.summarize_rollup(avgs, maxes, counts, "hour", 12.0),
              np_summarize_rollup(avgs, maxes, counts, "hour", 12.0))
    _same(S.summarize_rollup([], []), np_summarize_rollup([], []))


def test_rollup_max_propagates_nan_like_numpy():
    _same(S.summarize_rollup([1.0, 2.0], [3.0, math.nan]), np_summarize_rollup([1.0, 2.0], [3.0, math.nan]))


@pytest.mark.parametrize("n", [0, 1, 5, 129, 3600])
def test_pct_below_and_time_above_match_numpy(n):
    rng = random.Random(n)
    v = _values(n, rng)
    for thr in (0.0, 50.0, 1e5):
        assert S.pct_below(v, thr) == np_pct_below(v, thr)
        assert S.time_above(v, thr, 2.0) == np_time_above(v, thr, 2.0)
        assert S.time_above(v, thr, -1.0) == np_time_above(v, thr, -1.0)
