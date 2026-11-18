"""
WindowSummary - honest, tier-aware summary statistics over a time window.

The Monitor's pro-stats (inline "avg X (peak Y)", the Stats-detail sheet, and the export) all compute
min/avg/max/p50/p95/p99/stddev over the SELECTED timeline window. The honesty spine (from the
professional design panel): **percentiles/min/stddev are exact only from the RAW tier** (per-second
samples, kept ~24h). The per-minute / per-hour rollups store only avg + max (+ sample count), so beyond
the raw window we return weighted-avg + max and mark percentiles UNAVAILABLE - never fabricate a p95
from minute-buckets, and always carry sample_count + coverage so a figure is admissible as evidence.

Pure functions, no DB/Qt - the stats engine reads the right tier and hands the data here.

No numpy: the widget's hover card reads summarize_hardware (today's battery average), and the widget
must never load numpy (~9 MB USS, ~40 MB commit). The arithmetic is numpy's, step for step - its
pairwise summation for sum/mean/std and its "linear" percentile interpolation - so every figure is
bit-identical to what numpy returned (pinned by test_summaries_match_numpy).
"""
from __future__ import annotations

import math
import operator
from dataclasses import dataclass, asdict
from datetime import timedelta
from functools import reduce
from typing import Dict, List, Optional, Sequence


@dataclass(frozen=True)
class WindowSummary:
    count: int                      # raw samples, or buckets for a rollup tier
    coverage_pct: float             # samples / expected-for-window (0..100)
    tier: str                       # "raw" | "minute" | "hour"
    exact: bool                     # True => percentiles are exact (raw tier)
    avg: Optional[float]
    min: Optional[float]
    max: Optional[float]
    p50: Optional[float]
    p95: Optional[float]
    p99: Optional[float]
    stddev: Optional[float]
    note: str

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


_UNAVAILABLE = "avg+max only (per-minute/hour rollup; exact percentiles need the raw tier, ≤24h)"
_NO_DATA = "no samples in window"


# --- numpy's arithmetic, in pure Python -------------------------------------------------------------

def _pairwise_sum(a: List[float], lo: int, n: int) -> float:
    """numpy's pairwise_sum (float64 add.reduce): sequential below 8 values, 8 interleaved running sums
    up to a block of 128, halves (rounded down to a multiple of 8) above. Sequential adds use
    reduce(operator.add): the builtin sum() of floats is compensated since Python 3.12."""
    if n < 8:
        return reduce(operator.add, a[lo:lo + n], 0.0)
    if n <= 128:
        end = lo + n - n % 8
        r = [reduce(operator.add, a[lo + j + 8:end:8], a[lo + j]) for j in range(8)]
        res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
        return reduce(operator.add, a[end:lo + n], res)
    n2 = n // 2
    n2 -= n2 % 8
    return _pairwise_sum(a, lo, n2) + _pairwise_sum(a, lo + n2, n - n2)


def _sum(a: List[float]) -> float:
    return _pairwise_sum(a, 0, len(a))


def _std(a: List[float], mean: float) -> float:
    """numpy's std (ddof=0): sqrt(sum((x - mean)^2) / n), the sum pairwise."""
    return math.sqrt(_sum([(v - mean) * (v - mean) for v in a]) / len(a))


def _percentile(ordered: List[float], q: float) -> float:
    """numpy.percentile(..., method="linear") of a sorted, NaN-free, non-empty list: virtual index
    (n - 1) * q / 100, numpy's _lerp between its neighbours (from the far end when gamma >= 0.5)."""
    n = len(ordered)
    virtual = (n - 1) * (q / 100)
    if virtual >= n - 1:
        return ordered[-1]
    prev = math.floor(virtual)
    a, b = ordered[prev], ordered[prev + 1]
    gamma = virtual - prev
    diff = b - a
    return b - diff * (1 - gamma) if gamma >= 0.5 else a + diff * gamma


def _finite(values: Sequence[float]) -> List[float]:
    return [v for v in (float(x) for x in values) if math.isfinite(v)]


def _empty(tier: str) -> WindowSummary:
    return WindowSummary(0, 0.0, tier, False, None, None, None, None, None, None, None, _NO_DATA)


def coverage_pct(sample_count: int, window_seconds: float, sample_interval_seconds: float) -> float:
    """What fraction of the window actually had samples - the evidence-admissibility figure."""
    if window_seconds <= 0 or sample_interval_seconds <= 0:
        return 0.0
    expected = window_seconds / sample_interval_seconds
    return float(max(0.0, min(100.0, (sample_count / expected) * 100.0))) if expected > 0 else 0.0


def summarize_raw(values: Sequence[float], coverage: float = 100.0) -> WindowSummary:
    """Exact summary from raw per-sample values (the raw tier, ≤24h)."""
    arr = _finite(values)
    if not arr:
        return _empty("raw")
    n = len(arr)
    mean = _sum(arr) / n
    ordered = sorted(arr)
    return WindowSummary(
        count=n, coverage_pct=round(float(coverage), 1), tier="raw", exact=True,
        avg=mean, min=ordered[0], max=ordered[-1],
        p50=_percentile(ordered, 50), p95=_percentile(ordered, 95),
        p99=_percentile(ordered, 99), stddev=_std(arr, mean),
        note="exact from raw samples")


def summarize_rollup(avgs: Sequence[float], maxes: Sequence[float],
                     counts: Optional[Sequence[float]] = None, tier: str = "minute",
                     coverage: float = 100.0) -> WindowSummary:
    """Summary from a per-minute/hour rollup tier, which stores only avg + max (+ sample count). avg is
    sample-weighted (the honest mean); max is the true peak; min/percentiles/stddev are UNAVAILABLE."""
    a = [float(x) for x in avgs]
    m = [float(x) for x in maxes]
    if not a:
        return _empty(tier)
    c = [float(x) for x in counts] if counts is not None and len(counts) else [1.0] * len(a)
    if len(c) != len(a):
        raise ValueError("counts and avgs differ in length")   # numpy's broadcast error, in words
    total = _sum(c)
    weighted_avg = _sum([x * w for x, w in zip(a, c)]) / total if total > 0 else _sum(a) / len(a)
    peak = math.nan if any(math.isnan(x) for x in m) else max(m)   # numpy's max propagates NaN
    return WindowSummary(
        count=int(total) if counts is not None else len(a), coverage_pct=round(float(coverage), 1),
        tier=tier, exact=False, avg=weighted_avg, min=None, max=peak,
        p50=None, p95=None, p99=None, stddev=None, note=_UNAVAILABLE)


def loss_pct(timeouts: float, total_probes: int) -> Optional[float]:
    """Latency probe loss% = timed-out probes / total - the packet-loss proxy for an ISP dispute.
    Also takes the persisted loss series directly: ``timeouts`` = the sum of per-cycle loss fractions
    (old rows 0/1, burst rows 0-1 in quarter steps) and ``total_probes`` = the number of cycles."""
    if total_probes <= 0:
        return None
    return round((timeouts / total_probes) * 100.0, 2)


def pct_below(values: Sequence[float], threshold: float) -> Optional[float]:
    """Fraction of raw samples below a threshold (e.g. % of time under the advertised plan speed)."""
    arr = _finite(values)
    if not arr:
        return None
    return round(sum(1 for v in arr if v < threshold) / len(arr) * 100.0, 1)


def time_above(values: Sequence[float], threshold: float, sample_interval_seconds: float) -> float:
    """Seconds spent at/above a threshold (e.g. time above the throttle temperature)."""
    arr = _finite(values)
    return float(sum(1 for v in arr if v >= threshold) * max(0.0, sample_interval_seconds))


def hourly_profile(pairs: Sequence) -> Dict[int, float]:
    """Mean value per clock-hour (0..23) from (timestamp, value) pairs. The honest, data-driven basis
    for "your busiest hour" - no assumed ISP peak band, just when this machine was actually busy."""
    sums: Dict[int, float] = {}
    counts: Dict[int, int] = {}
    for ts, v in pairs:
        try:
            fv = float(v)
            if not math.isfinite(fv):
                continue
            hour = ts.hour if hasattr(ts, "hour") else int((float(ts) // 3600) % 24)
        except (TypeError, ValueError):
            continue
        sums[hour] = sums.get(hour, 0.0) + fv
        counts[hour] = counts.get(hour, 0) + 1
    return {h: sums[h] / counts[h] for h in sums if counts[h]}


def event_runs(pairs: Sequence, is_bad, max_gap: Optional[float] = None) -> List[tuple]:
    """Contiguous runs where ``is_bad(value)`` holds, as (start_ts, end_ts) pairs - the basis for
    "internet went bad then good" events you'd otherwise only catch live. ``pairs`` is (timestamp,
    value) in time order; ``is_bad`` is a predicate on the value. With ``max_gap`` (seconds), two rows
    further apart than that never share a run: nothing was recorded between them."""
    return [(r[0], r[1]) for r in _loss_runs(pairs, is_bad, max_gap)]


def _seconds_between(a, b) -> float:
    """``b - a`` in seconds, for datetime or epoch-number timestamps."""
    if hasattr(a, "timestamp") and hasattr(b, "timestamp"):
        return b.timestamp() - a.timestamp()
    return float(b) - float(a)


def _row(r) -> tuple:
    """(ts, value, down, span) from a (ts, value) pair or a 4-field loss row (history_reader.LossRow):
    ``down`` = a rollup bin's estimated lost seconds (None for a raw row / pair), ``span`` = the
    seconds the row stands for (0 for a pair)."""
    if len(r) >= 4:
        return r[0], r[1], r[2], int(r[3] or 0)
    return r[0], r[1], None, 0


def _loss_runs(rows: Sequence, is_bad, max_gap: Optional[float]) -> List[list]:
    """Runs of bad rows as [start, end, end_span, raw_down_s, est_down_s]. A run breaks on a good row,
    or where the next row starts more than ``max_gap`` after the previous row's span ended (nothing
    was recorded in between). Downtime: consecutive raw rows/pairs add the time between them (a run's
    first bad row to its last, as always); a rollup bin adds its own estimate - never its length."""
    runs: List[list] = []
    cur = None
    prev = None   # (ts, span, raw?)
    for r in rows:
        ts, v, down, span = _row(r)
        if (cur is not None and max_gap is not None and prev is not None
                and _seconds_between(prev[0], ts) > prev[1] + max_gap):
            runs.append(cur)
            cur = None
        if bool(is_bad(v)):
            if cur is None:
                cur = [ts, ts, span, 0.0, 0.0]
            else:
                if down is None and prev is not None and prev[2]:
                    cur[3] += max(0.0, _seconds_between(prev[0], ts))
                cur[1], cur[2] = ts, span
            if down is not None:
                cur[4] += max(0.0, float(down))
        elif cur is not None:
            runs.append(cur)
            cur = None
        prev = (ts, span, down is None)
    if cur is not None:
        runs.append(cur)
    return runs


def outage_summary(timeout_rows: Sequence, *more_series: Sequence,
                   max_gap: Optional[float] = None, hop_join: float = 0.0) -> Dict[str, object]:
    """Connection-drop events from the hop-timeout series. A row >= 0.5 is a drop: half or more of
    that cycle's burst lost (2.2+ rows are the lost fraction, 0-1 in quarter steps), or the single lost
    ping of an older 0/1 row - so one unlucky packet in a burst (0.25) is not an outage. Returns
    {count, last_start, total_down_seconds, runs} - an honest outage log, not a fabricated SLA.

    Rows are (ts, value) pairs or loss rows (ts, peak, down, span) from ``loss_history`` - raw cycles
    unbinned, rollup bins as their PEAK (an average hides a short drop) with an estimated lost time.
    ``max_gap``: a run also breaks where no row was recorded for longer than that beyond the previous
    row's span (the app was off - never downtime). ``more_series``: other hops' loss series (the
    internet anchor beside the router). Each hop's runs are built on its own; a run is merged only
    with a DIFFERENT hop's run it overlaps or follows within ``hop_join`` seconds of that run's end -
    the same outage seen on both hops. Runs of one hop are never merged, so adding an empty or clean
    hop never changes the count."""
    def bad(v):
        return v is not None and float(v) >= 0.5

    tagged = []
    for hop, series in enumerate((timeout_rows,) + tuple(more_series)):
        for r in _loss_runs(series, bad, max_gap):
            tagged.append((hop, r))
    tagged.sort(key=lambda t: t[1][0].timestamp() if hasattr(t[1][0], "timestamp") else float(t[1][0]))

    clusters: List[dict] = []
    for hop, (s, e, e_span, raw_down, est) in tagged:
        c = clusters[-1] if clusters else None
        joins = c is not None and any(
            h != hop and _seconds_between(s, reach) >= 0 for h, reach in c["reach"].items())
        if not joins:
            c = {"start": s, "end": e, "reach": {}, "raw": [], "est": {}}
            clusters.append(c)
        elif _seconds_between(c["end"], e) > 0:
            c["end"] = e
        reach = e + timedelta(seconds=e_span + hop_join) if hasattr(e, "timestamp") \
            else float(e) + e_span + hop_join
        old = c["reach"].get(hop)
        c["reach"][hop] = reach if old is None or _seconds_between(old, reach) > 0 else old
        if raw_down > 0:
            c["raw"].append((s, raw_down))
        c["est"][hop] = c["est"].get(hop, 0.0) + est

    runs, total = [], 0.0
    for c in clusters:
        runs.append((c["start"], c["end"]))
        total += _union_seconds(c["raw"]) + max(c["est"].values(), default=0.0)
    return {"count": len(runs), "last_start": runs[-1][0] if runs else None,
            "total_down_seconds": total, "runs": runs}


def _union_seconds(spans: List[tuple]) -> float:
    """Length of the union of (start, seconds) spans - one outage seen on two hops counts once."""
    if not spans:
        return 0.0
    base = spans[0][0]
    iv = sorted((_seconds_between(base, st), _seconds_between(base, st) + d) for st, d in spans)
    total, cs, ce = 0.0, iv[0][0], iv[0][1]
    for a, b in iv[1:]:
        if a > ce:
            total += ce - cs
            cs, ce = a, b
        else:
            ce = max(ce, b)
    return total + (ce - cs)


def peak_offpeak(pairs: Sequence) -> Optional[Dict[str, float]]:
    """Busiest vs quietest clock-hour by mean value. Returns {peak_hour, peak_avg, offpeak_hour,
    offpeak_avg} or None if fewer than two distinct hours have data (a split would be meaningless)."""
    return peak_offpeak_from_profile(hourly_profile(pairs))


def peak_offpeak_from_profile(prof: Optional[Dict[int, float]]) -> Optional[Dict[str, float]]:
    """``peak_offpeak`` from a ready {hour: mean} profile - e.g. the reader's time-weighted
    ``network_hourly_profile``, which averages real samples. Never feed it graph points: those are
    per-bucket peaks, and a peak profile names a busiest hour 1000x the window's average."""
    if not prof or len(prof) < 2:
        return None
    peak_h = max(prof, key=prof.get)
    off_h = min(prof, key=prof.get)
    return {"peak_hour": float(peak_h), "peak_avg": prof[peak_h],
            "offpeak_hour": float(off_h), "offpeak_avg": prof[off_h]}
