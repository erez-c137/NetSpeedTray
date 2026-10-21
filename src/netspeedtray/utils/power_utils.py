"""Battery power helpers for the hover card.

Three small concerns, all of which degrade to silence rather than raise:

1. The battery's *state* (discharging / charging / on AC, and charge percent),
   read with one ``GetSystemPowerStatus`` syscall at hover time. WMI exposes
   neither reliably on every firmware - this IdeaPad-class hardware omits
   ``EstimatedChargeRemaining`` from ``root\\wmi BatteryStatus`` - while the
   kernel API always has the number and costs microseconds. The same call
   carries Windows' own runtime estimate (``BatteryLifeTime``).

2. The packs' design capacity (mWh), summed over every pack in
   ``BatteryStaticData``. Only the fallback for the runtime projection. It is
   a WMI query, so the monitor thread fetches it (``read_design_capacity_mwh``)
   and the hover only reads the cache (``cached_design_capacity_mwh``) - never
   WMI on the GUI thread.

The *rate* (live watts) is NOT read here: StatsMonitorThread already polls the
battery's ``DischargeRate`` for the whole-system power stat and stashes it on
the widget as ``system_power``, so the hover card reuses that reading instead
of opening a second WMI path.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
import time
from collections import namedtuple
from typing import Optional

from netspeedtray.constants import hardware

try:
    import wmi  # pywin32-backed; only present on Windows installs
except ImportError:  # pragma: no cover - exercised on non-Windows CI
    wmi = None

MODE_DISCHARGE = "discharge"
MODE_CHARGE = "charge"
MODE_AC = "ac"
MODE_UNKNOWN = "unknown"   # a battery is present, but the AC line state is unknown
MODE_NONE = "none"         # no battery (or the state could not be read)

# (mode, charge_pct). charge_pct is 0-100 or None when unknown.
PowerState = namedtuple("PowerState", "mode charge_pct")

# SYSTEM_POWER_STATUS.BatteryFlag values (Win32).
_FLAG_CHARGING = 0x08
_FLAG_NO_BATTERY = 0x80
_FLAG_UNKNOWN = 0xFF
_LIFETIME_UNKNOWN = 0xFFFFFFFF


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    # Win32 declares these as BYTE (unsigned): a signed c_byte turns 128 "no system
    # battery" into -128 and 255 "unknown" into -1, which then matched every bit test.
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.wintypes.DWORD),
        ("BatteryFullLifeTime", ctypes.wintypes.DWORD),
    ]


def _power_status_struct():
    """Filled _SYSTEM_POWER_STATUS, or None if the API call failed."""
    try:
        status = _SYSTEM_POWER_STATUS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return status
    except Exception:  # pragma: no cover - ctypes unavailable
        pass
    return None


def read_power_state() -> PowerState:
    """One instantaneous battery state. Never raises - "no battery" is a value."""
    status = _power_status_struct()
    if status is None:
        return PowerState(MODE_NONE, None)

    percent = status.BatteryLifePercent
    charge_pct = percent if 0 <= percent <= 100 else None

    # BatteryFlag (Win32 SYSTEM_POWER_STATUS): 1 high, 2 low, 4 critical, 8 charging,
    # 128 no system battery; 255 alone means "unknown status" and is not a bit set.
    # ACLineStatus: 0 offline, 1 online, 255 unknown. An IdeaPad charging at 72%
    # reports flag 9 = high + charging.
    flags = status.BatteryFlag
    flags_known = flags != _FLAG_UNKNOWN
    if flags_known and flags & _FLAG_NO_BATTERY:
        return PowerState(MODE_NONE, None)       # honored by itself, whatever the percent says
    if flags_known and flags & _FLAG_CHARGING:
        return PowerState(MODE_CHARGE, charge_pct)
    # A known flag that is not "no battery" describes a battery; with the flag unknown, a
    # valid percent is the proof.
    present = flags_known or charge_pct is not None
    if not present:
        return PowerState(MODE_NONE, None)
    if status.ACLineStatus == 1:
        return PowerState(MODE_AC, charge_pct)
    if status.ACLineStatus == 0:
        # Offline: discharging, even when the percent itself reads "unknown" on some firmware.
        return PowerState(MODE_DISCHARGE, charge_pct)
    return PowerState(MODE_UNKNOWN, charge_pct)  # AC line unknown, battery present


def has_battery() -> bool:
    """Whether the machine reports a battery at all (one microsecond syscall).

    The monitor thread asks before its WMI battery poll, so a desktop with the
    hover's battery switch on never pays for a query that can only come back empty."""
    return read_power_state().mode != MODE_NONE


def ac_line_status() -> Optional[int]:
    """The raw ACLineStatus (0 offline, 1 online, 255 unknown), or None if unreadable. Cheap
    enough to ask every poll - the monitor thread uses a change in it to drop its cached
    battery reading, so unplugging flips the card at once instead of up to 8 s later."""
    status = _power_status_struct()
    return None if status is None else int(status.ACLineStatus)


def windows_runtime_hours() -> Optional[float]:
    """Windows' own estimate of the runtime left (BatteryLifeTime), in hours - None on AC, when
    the estimate is unknown (0xFFFFFFFF, e.g. the first seconds after unplugging), or zero."""
    status = _power_status_struct()
    if status is None or status.ACLineStatus != 0:
        return None
    secs = int(status.BatteryLifeTime)
    if secs <= 0 or secs == _LIFETIME_UNKNOWN:
        return None
    return secs / 3600.0


# --- Design capacity (the projection's fallback) ------------------------------

_capacity_lock = threading.Lock()
_design_mwh: Optional[int] = None        # a good value, cached for the process
_capacity_retry_at: float = 0.0          # monotonic time before which a failed read is not retried


def read_design_capacity_mwh() -> Optional[int]:
    """Total design capacity in mWh over every pack in ``BatteryStaticData``.

    Runs WMI - call it from the monitor thread, never the GUI thread (the hover reads
    ``cached_design_capacity_mwh``). A good value is cached for the process; a failure or a
    nonsense reading is retried after ``CAPACITY_RETRY_SEC``, so a WMI hiccup right after resume
    does not cost the projection until restart. None when unavailable - the projection row is
    then omitted rather than guessed.
    """
    global _design_mwh, _capacity_retry_at
    p = hardware.power
    with _capacity_lock:
        if _design_mwh is not None:
            return _design_mwh
        if time.monotonic() < _capacity_retry_at:
            return None
        total = 0
        try:
            if wmi is not None:
                root = wmi.WMI(namespace="root\\wmi")
                for battery in root.query("SELECT DesignedCapacity FROM BatteryStaticData"):
                    capacity = int(getattr(battery, "DesignedCapacity", 0) or 0)
                    if p.DESIGN_CAPACITY_MIN_MWH <= capacity <= p.DESIGN_CAPACITY_MAX_MWH:
                        total += capacity
        except Exception:
            total = 0
        if total > 0:
            _design_mwh = total
            return total
        _capacity_retry_at = time.monotonic() + p.CAPACITY_RETRY_SEC
        return None


def cached_design_capacity_mwh() -> Optional[int]:
    """The capacity the monitor thread already read, or None - never queries WMI."""
    return _design_mwh


def projected_runtime_hours(live_watts: float, charge_pct: int,
                            design_mwh: Optional[int]) -> Optional[float]:
    """Hours of runtime left at the current draw, or None when uncomputable.

    The fallback when Windows has no estimate of its own. Uses design capacity as the
    pack-size estimate - the charged capacity isn't exposed by any query a non-admin process
    can make, so the projection reads a few percent optimistic on a worn pack.
    """
    if live_watts is None or live_watts <= 0 or design_mwh is None:
        return None
    if charge_pct is None or charge_pct <= 0:
        return None
    remaining_mwh = design_mwh * charge_pct / 100.0
    return remaining_mwh / live_watts / 1000.0
