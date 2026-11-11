"""
Update-rate helpers for NetSpeedTray.

The config persists the sampling interval (`update_rate`) in seconds, and since 2.2 the only
valid values are the slider presets in `constants.update_mode.UpdateMode.PRESETS`
(0.5 / 1 / 2 / 5 / 10 s; 0.5 s since 2.2.1, #456). Two helpers, deliberately separate:

- `snap_update_rate()` maps any persisted value onto the preset ladder. The config migration and
  the Settings slider both use it, so the file and the slider can never disagree.
- `resolve_poll_interval_seconds()` turns a live config value into the interval the sampler and
  every seconds-per-sample computation (coverage %, bytes-from-rates totals, buffer sizing) use.
  It is a pure clamp: after load the value is already a preset, so it only guards against garbage.
"""

import logging
import math

from netspeedtray import constants
from netspeedtray.constants.update_mode import UpdateMode

logger = logging.getLogger("NetSpeedTray.TimerUtils")


def _finite_number(value: object):
    """`value` as a float if it is a real, finite number (bools excluded), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def snap_update_rate(update_rate: object) -> float:
    """
    Map a persisted `update_rate` onto the preset ladder (0.5 / 1 / 2 / 5 / 10 s).

    - Non-positive (the retired adaptive preset's -1.0 sentinel, and the older 0 sentinel): **2.0 s**,
      the fixed cadence those users actually sampled at - not the 1 s default.
    - Any other finite number: the nearest preset; a tie goes to the faster (smaller) preset, the
      same rule as the keep_data ladder snap. 10 s stays 10 s; 0.5 s (1.2.4's option, #456) stays
      0.5 s, and a hand-edited sub-second value lands on it.
    - Anything else (non-finite, non-numeric, bool, None): the default.

    Idempotent: a preset maps to itself.
    """
    rate = _finite_number(update_rate)
    if rate is None:
        return float(constants.config.defaults.DEFAULT_UPDATE_RATE)
    if rate <= 0:
        return float(UpdateMode.BALANCED)
    return float(min(UpdateMode.PRESETS, key=lambda preset: abs(preset - rate)))


def graph_sample_seconds(update_rate: object) -> float:
    """Seconds per point of the in-memory graph series (mini graph, Monitor session view): the
    effective poll interval, but never below ``constants.renderer.GRAPH_MIN_SAMPLE_SECONDS`` - at the
    0.5 s preset WidgetState merges each wall-clock second's samples into one point. Every sizing of
    those series (the buffer, the renderer's window) divides by this, not by the poll interval."""
    return max(resolve_poll_interval_seconds(update_rate),
               float(constants.renderer.GRAPH_MIN_SAMPLE_SECONDS))


def resolve_poll_interval_seconds(update_rate: object) -> float:
    """
    The EFFECTIVE sampling interval in seconds for a live config value: the value clamped into
    [fastest preset, slowest preset]. Garbage (non-numeric, non-finite, bool) is the default.

    Examples:
        >>> resolve_poll_interval_seconds(5.0)
        5.0
        >>> resolve_poll_interval_seconds(30)
        10.0
    """
    rate = _finite_number(update_rate)
    if rate is None:
        return float(constants.config.defaults.DEFAULT_UPDATE_RATE)
    return max(UpdateMode.PRESETS[0], min(rate, UpdateMode.PRESETS[-1]))
