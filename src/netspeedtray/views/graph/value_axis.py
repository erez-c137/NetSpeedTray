"""
Value (y) axis labels for the speed graphs: as many decimals as the tick step needs, no more.

Why: the old formatter printed int(x) for every tick >= 1 and one fixed decimal below 1, so a
0-2 Mbps axis read "0.0, 0.5, 1, 1, 2" (1.5 shown as "1") and the 0-0.1 Mbps floor scale read
"0.0, 0.0, 0.0, 0.1, 0.1, 0.1". Here the precision comes from the spacing of the ticks actually
drawn, so distinct ticks always get distinct, truthful labels, in the locale's decimal separator.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
from matplotlib.ticker import Formatter

from netspeedtray import constants


def decimals_for_step(step: float) -> int:
    """Fewest decimals (capped) that represent multiples of ``step`` exactly: 0.5 -> 1, 0.02 -> 2, 5 -> 0."""
    max_dec = constants.graph.VALUE_AXIS_MAX_DECIMALS
    step = abs(step)
    if not math.isfinite(step) or step == 0:
        return 0
    for d in range(max_dec + 1):
        if abs(round(step, d) - step) <= 1e-9 * max(1.0, step):
            return d
    return max_dec


def _tick_step(values: Sequence[float]) -> float:
    gaps = [abs(b - a) for a, b in zip(values, values[1:]) if abs(b - a) > 0]
    return min(gaps) if gaps else 0.0


def format_axis_value(value: float, decimals: int, decimal_separator: str = ".") -> str:
    """``value`` to ``decimals`` places with trailing zeros (and a bare point) dropped."""
    s = f"{value:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    if decimal_separator and decimal_separator != ".":
        s = s.replace(".", decimal_separator)
    return s


class ValueAxisFormatter(Formatter):
    """Formats y ticks with the precision their spacing needs, in the locale's decimal separator."""

    def __init__(self, i18n=None, suffix: str = ""):
        self.i18n = i18n
        self.suffix = suffix

    def _sep(self) -> str:
        return getattr(self.i18n, "DECIMAL_SEPARATOR", ".") or "."

    def _decimals(self, values: Optional[Iterable[float]] = None) -> int:
        vals = sorted(values if values is not None else (getattr(self, "locs", None) or []))
        return decimals_for_step(_tick_step(vals))

    def format_ticks(self, values):
        self.set_locs(values)
        d = self._decimals(values)
        return [format_axis_value(v, d, self._sep()) + self.suffix for v in values]

    def __call__(self, x, pos=None):
        # Outside a tick pass (no locs to measure a step from), keep the value's own precision.
        vals = sorted(getattr(self, "locs", None) or [])
        d = decimals_for_step(_tick_step(vals)) if len(vals) > 1 else decimals_for_step(x)
        return format_axis_value(x, d, self._sep()) + self.suffix


# ----------------------------------------------------------------------------- long-window clipping
# 2.2 scope C: two speed tests in a month scaled the axis to ~950 Mbps and squashed a month of 5-25
# Mbps traffic into the bottom 3%. On long windows the axis may instead scale to a high percentile of
# the points actually plotted (there is no throughput histogram; plan scope A defers it). The clipped
# points stay on the line - matplotlib only clips the drawing, so the hover still reads their true
# value - and the renderer marks them on the top edge and states the true peak.

def clipped_axis_max(values: Iterable[float], period_key: Optional[str]) -> Tuple[float, bool]:
    """The value the speed axis should scale to, and whether that is a percentile clip.

    Returns ``(max, False)`` unless ``period_key`` is a long window, there are enough points for a
    percentile to mean something, the percentile is above zero, and the max exceeds it by
    ``VALUE_AXIS_CLIP_TRIGGER`` x. Then ``(percentile x VALUE_AXIS_CLIP_HEADROOM, True)`` - never
    below the percentile itself.
    """
    g = constants.graph
    arr = np.asarray(list(values) if not isinstance(values, np.ndarray) else values, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return 0.0, False
    peak = float(arr.max())
    if period_key not in g.VALUE_AXIS_CLIP_PERIODS or arr.size < g.VALUE_AXIS_CLIP_MIN_POINTS:
        return peak, False
    pct = float(np.percentile(arr, g.VALUE_AXIS_CLIP_PERCENTILE))
    if pct <= 0 or peak <= pct * g.VALUE_AXIS_CLIP_TRIGGER:
        return peak, False
    return pct * max(1.0, g.VALUE_AXIS_CLIP_HEADROOM), True


def clipped_runs(xs: Sequence[float], ys: Sequence[float], top: float) -> List[Tuple[float, float]]:
    """``(x, y)`` of the highest point in each contiguous run of points above ``top``.

    One marker per run, not per point: an interpolated spike is several points above the edge, and
    a row of carets for one event would read as several events.
    """
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    if x.size == 0 or x.size != y.size:
        return []
    above = np.isfinite(y) & (y > top)
    if not above.any():
        return []
    idx = np.flatnonzero(above)
    runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
    out = []
    for run in runs:
        i = int(run[np.argmax(y[run])])
        out.append((float(x[i]), float(y[i])))
    return out
