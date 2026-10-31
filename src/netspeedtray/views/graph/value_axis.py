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
from matplotlib.ticker import Formatter, MaxNLocator

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
    """Formats y ticks with the precision their spacing needs, in the locale's decimal separator.

    ``scale`` multiplies each tick before it is printed: the speed axes are plotted in Mbps and shown
    in the user's unit (``speed_axis_unit``), so 0.0125 Mbps prints as "12.5" on a Kbps axis."""

    def __init__(self, i18n=None, suffix: str = "", scale: float = 1.0):
        self.i18n = i18n
        self.suffix = suffix
        self.scale = float(scale) if scale and math.isfinite(scale) else 1.0

    def _sep(self) -> str:
        return getattr(self.i18n, "DECIMAL_SEPARATOR", ".") or "."

    def _decimals(self, values: Optional[Iterable[float]] = None) -> int:
        vals = sorted(values if values is not None else (getattr(self, "locs", None) or []))
        return decimals_for_step(_tick_step([v * self.scale for v in vals]))

    def format_ticks(self, values):
        self.set_locs(values)
        d = self._decimals(values)
        return [format_axis_value(v * self.scale, d, self._sep()) + self.suffix for v in values]

    def __call__(self, x, pos=None):
        # Outside a tick pass (no locs to measure a step from), keep the value's own precision.
        vals = sorted(v * self.scale for v in (getattr(self, "locs", None) or []))
        d = decimals_for_step(_tick_step(vals)) if len(vals) > 1 else decimals_for_step(x * self.scale)
        return format_axis_value(x * self.scale, d, self._sep()) + self.suffix


# ----------------------------------------------------------------------------- speed axis unit (S2)
# The speed graphs used to print bare Mbps ticks under a "Download" label, whatever the user chose in
# Settings (#56 asked for MB/s, #59 for bits vs bytes to be unambiguous) - while the hover, one inch
# away, honoured unit_type. The axes are still PLOTTED in Mbps; only the ticks and the label convert.

def speed_axis_unit(top_mbps: float, i18n, unit_type: str = "bits_decimal",
                    force_mega: bool = False) -> Tuple[float, str]:
    """``(scale, unit_label)`` for a speed axis whose top is ``top_mbps``: multiply an Mbps value by
    ``scale`` to get the number to print in ``unit_label``.

    One unit for the whole axis, chosen from its top the way ``format_speed`` picks one from a value:
    bits or bytes and decimal (1000) or binary (1024) from ``unit_type``; kilo at the least
    (``VALUE_AXIS_MIN_UNIT_TIER``), mega at the least with ``force_mega`` (the widget's "always Mbps"
    mode). Always the LONG label ("MB/s", never the short "MB"): an axis with a bare "MB" reads as a
    volume, not a rate."""
    from netspeedtray.utils.helpers import get_unit_labels_for_type   # lazy: helpers imports Qt bits
    units = constants.network.units
    unit_type = unit_type if unit_type in ("bits_decimal", "bits_binary", "bytes_decimal",
                                           "bytes_binary") else "bits_decimal"
    is_binary = unit_type.endswith("_binary")
    is_bytes = unit_type.startswith("bytes")
    step = float(units.KIBI_DIVISOR if is_binary else units.KILO_DIVISOR)
    # Mbps -> the base unit of unit_type (bits/s or bytes/s).
    to_base = units.MEGA_DIVISOR / (units.BITS_PER_BYTE if is_bytes else 1)
    top = max(0.0, float(top_mbps or 0.0)) * to_base if math.isfinite(top_mbps or 0.0) else 0.0
    tier = max(constants.graph.VALUE_AXIS_MIN_UNIT_TIER, 2 if force_mega else 0)
    while tier < 3 and top >= step ** (tier + 1):
        tier += 1
    labels = get_unit_labels_for_type(i18n, unit_type, False)
    return to_base / step ** tier, labels[tier]


class ScaledMaxNLocator(MaxNLocator):
    """MaxNLocator that places ticks on round numbers OF THE DISPLAYED UNIT: an axis plotted in Mbps
    but shown in MB/s gets ticks at 0, 5, 10 MB/s, not at 0, 25, 50 Mbps printed as 3.125, 6.25."""

    def __init__(self, scale: float = 1.0, **kwargs):
        super().__init__(**kwargs)
        self.scale = float(scale) if scale and math.isfinite(scale) and scale > 0 else 1.0

    def tick_values(self, vmin, vmax):
        lo, hi = vmin * self.scale, vmax * self.scale
        ticks = np.asarray(super().tick_values(lo, hi), dtype=float)
        # Labels carry at most VALUE_AXIS_MAX_DECIMALS. On a tiny axis (an idle 0.1 Mbps floor shown
        # in MB/s under "always Mbps" is 0-0.0125) a 2.5-step lands on 0.0025, which prints as
        # "0.003" - the label would not be the tick. Retry without the 2.5 step (1/2/5 x 10^k steps
        # need no extra digit), so every tick is exactly what its label says.
        if ticks.size > 1 and not self._representable(float(np.min(np.diff(ticks)))):
            steps = [s for s in self._steps if not self._is_quarter(s)] or [1, 2, 5, 10]
            ticks = np.asarray(MaxNLocator(nbins=self._nbins, steps=steps, min_n_ticks=self._min_n_ticks,
                                           integer=self._integer, symmetric=self._symmetric,
                                           prune=self._prune).tick_values(lo, hi), dtype=float)
        return ticks / self.scale

    @staticmethod
    def _representable(step: float) -> bool:
        return decimals_for_step(step) < constants.graph.VALUE_AXIS_MAX_DECIMALS or \
            abs(round(step, constants.graph.VALUE_AXIS_MAX_DECIMALS) - step) <= 1e-12

    @staticmethod
    def _is_quarter(step: float) -> bool:
        """A step whose mantissa is 2.5 (2.5, 25, ...) - the only nice step that adds a digit."""
        return step > 0 and abs(step / 10 ** math.floor(math.log10(step)) - 2.5) < 1e-9


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
