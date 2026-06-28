"""
WindowSummary — honest, tier-aware summary statistics over a time window.

The Monitor's pro-stats (inline "avg X (peak Y)", the Stats-detail sheet, and the export) all compute
min/avg/max/p50/p95/p99/stddev over the SELECTED timeline window. The honesty spine (from the
professional design panel): **percentiles/min/stddev are exact only from the RAW tier** (per-second
samples, kept ~24h). The per-minute / per-hour rollups store only avg + max (+ sample count), so beyond
the raw window we return weighted-avg + max and mark percentiles UNAVAILABLE — never fabricate a p95
from minute-buckets, and always carry sample_count + coverage so a figure is admissible as evidence.

Pure functions, no DB/Qt — the stats engine reads the right tier and hands the data here.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Sequence

import numpy as np


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


def _empty(tier: str) -> WindowSummary:
    return WindowSummary(0, 0.0, tier, False, None, None, None, None, None, None, None, _NO_DATA)


def coverage_pct(sample_count: int, window_seconds: float, sample_interval_seconds: float) -> float:
    """What fraction of the window actually had samples — the evidence-admissibility figure."""
    if window_seconds <= 0 or sample_interval_seconds <= 0:
        return 0.0
    expected = window_seconds / sample_interval_seconds
    return float(max(0.0, min(100.0, (sample_count / expected) * 100.0))) if expected > 0 else 0.0


def summarize_raw(values: Sequence[float], coverage: float = 100.0) -> WindowSummary:
    """Exact summary from raw per-sample values (the raw tier, ≤24h)."""
    arr = np.asarray([float(v) for v in values], dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return _empty("raw")
    return WindowSummary(
        count=int(arr.size), coverage_pct=round(float(coverage), 1), tier="raw", exact=True,
        avg=float(arr.mean()), min=float(arr.min()), max=float(arr.max()),
        p50=float(np.percentile(arr, 50)), p95=float(np.percentile(arr, 95)),
        p99=float(np.percentile(arr, 99)), stddev=float(arr.std()),
        note="exact from raw samples")


def summarize_rollup(avgs: Sequence[float], maxes: Sequence[float],
                     counts: Optional[Sequence[float]] = None, tier: str = "minute",
                     coverage: float = 100.0) -> WindowSummary:
    """Summary from a per-minute/hour rollup tier, which stores only avg + max (+ sample count). avg is
    sample-weighted (the honest mean); max is the true peak; min/percentiles/stddev are UNAVAILABLE."""
    a = np.asarray([float(x) for x in avgs], dtype=float)
    m = np.asarray([float(x) for x in maxes], dtype=float)
    if a.size == 0:
        return _empty(tier)
    c = np.asarray([float(x) for x in counts], dtype=float) if counts is not None and len(counts) else np.ones_like(a)
    total = float(c.sum())
    weighted_avg = float((a * c).sum() / total) if total > 0 else float(a.mean())
    return WindowSummary(
        count=int(total) if counts is not None else int(a.size), coverage_pct=round(float(coverage), 1),
        tier=tier, exact=False, avg=weighted_avg, min=None, max=float(m.max()),
        p50=None, p95=None, p99=None, stddev=None, note=_UNAVAILABLE)


def loss_pct(timeouts: int, total_probes: int) -> Optional[float]:
    """Latency probe loss% = timed-out probes / total — the packet-loss proxy for an ISP dispute."""
    if total_probes <= 0:
        return None
    return round((timeouts / total_probes) * 100.0, 2)


def pct_below(values: Sequence[float], threshold: float) -> Optional[float]:
    """Fraction of raw samples below a threshold (e.g. % of time under the advertised plan speed)."""
    arr = np.asarray([float(v) for v in values], dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return round(float((arr < threshold).mean() * 100.0), 1)


def time_above(values: Sequence[float], threshold: float, sample_interval_seconds: float) -> float:
    """Seconds spent at/above a threshold (e.g. time above the throttle temperature)."""
    arr = np.asarray([float(v) for v in values], dtype=float)
    arr = arr[np.isfinite(arr)]
    return float((arr >= threshold).sum() * max(0.0, sample_interval_seconds))


def hourly_profile(pairs: Sequence) -> Dict[int, float]:
    """Mean value per clock-hour (0..23) from (timestamp, value) pairs. The honest, data-driven basis
    for "your busiest hour" — no assumed ISP peak band, just when this machine was actually busy."""
    sums: Dict[int, float] = {}
    counts: Dict[int, int] = {}
    for ts, v in pairs:
        try:
            fv = float(v)
            if not np.isfinite(fv):
                continue
            hour = ts.hour if hasattr(ts, "hour") else int((float(ts) // 3600) % 24)
        except (TypeError, ValueError):
            continue
        sums[hour] = sums.get(hour, 0.0) + fv
        counts[hour] = counts.get(hour, 0) + 1
    return {h: sums[h] / counts[h] for h in sums if counts[h]}


def peak_offpeak(pairs: Sequence) -> Optional[Dict[str, float]]:
    """Busiest vs quietest clock-hour by mean value. Returns {peak_hour, peak_avg, offpeak_hour,
    offpeak_avg} or None if fewer than two distinct hours have data (a split would be meaningless)."""
    prof = hourly_profile(pairs)
    if len(prof) < 2:
        return None
    peak_h = max(prof, key=prof.get)
    off_h = min(prof, key=prof.get)
    return {"peak_hour": float(peak_h), "peak_avg": prof[peak_h],
            "offpeak_hour": float(off_h), "offpeak_avg": prof[off_h]}
