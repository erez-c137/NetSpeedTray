"""Battery power helpers for the hover card.

Two small concerns, both of which degrade to silence rather than raise:

1. The battery's *state* (discharging / charging / on AC, and charge percent),
   read with one ``GetSystemPowerStatus`` syscall at hover time. WMI exposes
   neither reliably on every firmware - this IdeaPad-class hardware omits
   ``EstimatedChargeRemaining`` from ``root\\wmi BatteryStatus`` - while the
   kernel API always has the number and costs microseconds.

2. The pack's design capacity (mWh), read once per process from
   ``BatteryStaticData``. Needed only for the runtime projection; on machines
   that hide the class, the projection row is simply omitted.

The *rate* (live watts) is NOT read here: StatsMonitorThread already polls the
battery's ``DischargeRate`` for the whole-system power stat and stashes it on
the widget as ``system_power``, so the hover card reuses that reading instead
of opening a second WMI path.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
from collections import namedtuple
from typing import Optional, Tuple

from netspeedtray.constants import hardware

try:
    import wmi  # pywin32-backed; only present on Windows installs
except ImportError:  # pragma: no cover - exercised on non-Windows CI
    wmi = None

MODE_DISCHARGE = "discharge"
MODE_CHARGE = "charge"
MODE_AC = "ac"
MODE_NONE = "none"

# (mode, charge_pct). charge_pct is 0-100 or None when unknown (255 = "no battery").
PowerState = namedtuple("PowerState", "mode charge_pct")

# SYSTEM_POWER_STATUS.BatteryFlag values (Win32).
_FLAG_CHARGING = 0x08
_FLAG_NO_BATTERY = 0x80
_FLAG_UNKNOWN = 0xFF


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
    # reports flag 9 = high + charging. The charging bit outranks AC, and "no
    # battery" is only honored when the percent ALSO reads unknown - a valid
    # percent is proof of a battery.
    flags = status.BatteryFlag
    if flags == _FLAG_UNKNOWN:
        flags = 0                       # no bits to read; fall back to the AC line
    if flags & _FLAG_CHARGING:
        return PowerState(MODE_CHARGE, charge_pct)
    if charge_pct is None and (flags & _FLAG_NO_BATTERY or status.BatteryFlag == _FLAG_UNKNOWN):
        return PowerState(MODE_NONE, None)
    if status.ACLineStatus == 1:
        return PowerState(MODE_AC, charge_pct)
    if status.ACLineStatus == 0:
        # Offline with no no-battery flag: discharging, even when the percent
        # itself reads "unknown" (255) on some firmware.
        return PowerState(MODE_DISCHARGE, charge_pct)
    return PowerState(MODE_NONE, None)  # ACLineStatus unknown and not charging


def has_battery() -> bool:
    """Whether the machine reports a battery at all (one microsecond syscall).

    The monitor thread asks before its WMI battery poll, so a desktop with the
    hover's battery switch on never pays for a query that can only come back empty."""
    return read_power_state().mode != MODE_NONE


# --- Design capacity (for the runtime projection) ----------------------------

_capacity_lock = threading.Lock()
_design_mwh: Optional[int] = None  # None = untried; negative = known unavailable

_UNAVAILABLE = -1


def read_design_capacity_mwh() -> Optional[int]:
    """Design capacity in mWh from ``BatteryStaticData``, cached for the process.

    None when the class is missing or reports nothing - the projection row is
    omitted rather than guessed.
    """
    global _design_mwh
    if _design_mwh is not None:
        return None if _design_mwh == _UNAVAILABLE else _design_mwh
    value = _UNAVAILABLE
    try:
        if wmi is not None:
            root = wmi.WMI(namespace="root\\wmi")
            for battery in root.query("SELECT DesignedCapacity FROM BatteryStaticData"):
                capacity = int(getattr(battery, "DesignedCapacity", 0) or 0)
                p = hardware.power
                if p.DESIGN_CAPACITY_MIN_MWH <= capacity <= p.DESIGN_CAPACITY_MAX_MWH:
                    value = capacity
                    break
    except Exception:
        pass
    with _capacity_lock:
        _design_mwh = value
    return None if value == _UNAVAILABLE else value


def projected_runtime_hours(live_watts: float, charge_pct: int,
                            design_mwh: Optional[int]) -> Optional[float]:
    """Hours of runtime left at the current draw, or None when uncomputable.

    Uses design capacity as the pack-size estimate - the charged capacity isn't
    exposed by any query a non-admin process can make, so the projection reads
    a few percent optimistic on a worn pack. Good enough for a hover glance.
    """
    if live_watts is None or live_watts <= 0 or design_mwh is None:
        return None
    if charge_pct is None or charge_pct <= 0:
        return None
    remaining_mwh = design_mwh * charge_pct / 100.0
    return remaining_mwh / live_watts / 1000.0
