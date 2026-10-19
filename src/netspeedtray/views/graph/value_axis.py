"""
Value (y) axis labels for the speed graphs: as many decimals as the tick step needs, no more.

Why: the old formatter printed int(x) for every tick >= 1 and one fixed decimal below 1, so a
0-2 Mbps axis read "0.0, 0.5, 1, 1, 2" (1.5 shown as "1") and the 0-0.1 Mbps floor scale read
"0.0, 0.0, 0.0, 0.1, 0.1, 0.1". Here the precision comes from the spacing of the ticks actually
drawn, so distinct ticks always get distinct, truthful labels, in the locale's decimal separator.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

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
