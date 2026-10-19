"""
Odometer counter snapshot: what lets launch recover the bytes that moved while the app was closed.

The NIC counters (psutil -> GetIfEntry2 InOctets/OutOctets) are cumulative since boot. If the
odometer persists, together with its total, the counter values that total was counted up to, a
later launch in the SAME boot session can add exactly `counter_now - counter_then` - the traffic
the app missed while it was not running (data-usage accuracy audit, finding 3 / item 2).

The snapshot is a JSON value in the `metadata` table, written in the same transaction as the
`usage_counter` row, so the pair is always consistent: a crash before a persist leaves the OLD
total with the OLD snapshot, and the next launch simply recovers from further back.

Boot-session identity is psutil.boot_time() compared with a tolerance (not rounded: a rounding
boundary would flip on psutil's documented ~1 s jitter). See UsageOdometerConstants.

Pure functions only - no Qt, no DB - so the policy is testable on its own.
"""

import json
import logging
import math
from typing import Any, Dict, Mapping, Optional, Tuple

import psutil

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.UsageSnapshot")

_C = constants.network.usage

# persist_usage payload value meaning "delete the stored snapshot" (vs None = leave it untouched).
# Never valid JSON for a snapshot, so it cannot collide with a real one.
CLEAR_SNAPSHOT = ""


def current_boot_time() -> Optional[float]:
    """The boot-session id: psutil.boot_time() (epoch seconds), or None if unreadable."""
    try:
        value = float(psutil.boot_time())
    except Exception:
        return None
    return value if math.isfinite(value) and value > 0 else None


def same_boot(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= _C.BOOT_TIME_TOLERANCE_SEC


def cap_delta(delta: float, elapsed_s: float) -> Tuple[float, bool]:
    """Clamp one direction's byte delta to MAX_REASONABLE_SPEED_BPS x elapsed. Returns
    (value, clipped). A non-positive elapsed allows nothing."""
    ceiling = constants.network.interface.MAX_REASONABLE_SPEED_BPS * max(0.0, elapsed_s)
    if delta > ceiling:
        return ceiling, True
    return delta, False


def encode_snapshot(counters: Mapping[str, Any], sampled_at: float, boot_time: Optional[float],
                    primary: Optional[str] = None) -> Optional[str]:
    """Serialize psutil-style counters ({name: obj with bytes_sent/bytes_recv}) to the stored JSON,
    with the auto-mode primary they were metered on (None outside auto mode).
    None when there is nothing trustworthy to store (no boot id)."""
    if boot_time is None:
        return None
    rows: Dict[str, list] = {}
    for name, c in counters.items():
        try:
            rows[str(name)] = [int(c.bytes_sent), int(c.bytes_recv)]
        except (AttributeError, TypeError, ValueError):
            continue
    data: Dict[str, Any] = {"v": _C.SNAPSHOT_FORMAT_VERSION, "boot_time": float(boot_time),
                            "ts": float(sampled_at), "counters": rows}
    if primary:
        data["primary"] = str(primary)
    return json.dumps(data, separators=(",", ":"))


def decode_snapshot(raw: Optional[str]) -> Dict[str, Any]:
    """Parse and validate the stored JSON. Anything malformed reads as 'no snapshot' ({}), which
    reconciles nothing - the safe direction for a value that would ADD bytes to a data cap."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("v") != _C.SNAPSHOT_FORMAT_VERSION:
            return {}
        boot, ts = float(data["boot_time"]), float(data["ts"])
        if not (math.isfinite(boot) and math.isfinite(ts)):
            return {}
        counters: Dict[str, list] = {}
        for name, pair in dict(data["counters"]).items():
            sent, recv = int(pair[0]), int(pair[1])
            if sent >= 0 and recv >= 0:
                counters[str(name)] = [sent, recv]
        primary = data.get("primary")
        return {"v": data["v"], "boot_time": boot, "ts": ts, "counters": counters,
                "primary": str(primary) if primary else None}
    except (ValueError, TypeError, KeyError, IndexError):
        return {}


def reconcile_deltas(snapshot: Mapping[str, Any], counters: Mapping[str, Any], boot_time: Optional[float],
                     now_wall: float) -> Tuple[Dict[str, Tuple[float, float]], str]:
    """Per-interface bytes that moved between the snapshot and `counters`, or nothing.

    Returns (deltas, reason). Nothing is recovered when there is no snapshot, the boot session
    differs (the counters restarted at boot), or the clock gives no positive elapsed time. Per
    interface, nothing is recovered when it is gone or EITHER counter went backwards (the adapter
    restarted its counters while the app was closed - the delta is unknowable). Each direction is
    capped at MAX_REASONABLE_SPEED_BPS x (now - snapshot time).

    Interface scope (which of these the odometer meters) is the caller's: it aggregates the result
    through the current interface mode, exactly as a live poll's deltas are.
    """
    if not snapshot or not snapshot.get("counters"):
        return {}, "no snapshot"
    if not same_boot(snapshot.get("boot_time"), boot_time):
        return {}, "different boot session"
    elapsed = now_wall - float(snapshot["ts"])
    if elapsed <= 0:
        return {}, "clock went backwards"
    deltas: Dict[str, Tuple[float, float]] = {}
    clipped = False
    for name, (s_sent, s_recv) in snapshot["counters"].items():
        cur = counters.get(name)
        if cur is None:
            continue
        up, down = cur.bytes_sent - s_sent, cur.bytes_recv - s_recv
        if up < 0 or down < 0:
            continue
        up, c1 = cap_delta(float(up), elapsed)
        down, c2 = cap_delta(float(down), elapsed)
        clipped = clipped or c1 or c2
        deltas[name] = (up, down)
    if clipped:
        logger.warning("Launch reconciliation clipped an implausible counter delta to the "
                       "%.0f Gbps ceiling over %.0f s.",
                       constants.network.interface.MAX_REASONABLE_SPEED_BPS * 8 / 1e9, elapsed)
    return deltas, "same boot session"
