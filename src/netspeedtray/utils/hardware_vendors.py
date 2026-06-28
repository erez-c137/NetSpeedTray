"""
Hardware vendor detection + the Monitor's vendor-aware graph palette.

CPU vendor comes from the registry (CentralProcessor\\0\\VendorIdentifier), GPU vendor from the
display-adapter DriverDesc — both no-admin, both cached for the process (a machine's silicon doesn't
change at runtime).

The palette solves the brand-collision problem: AMD ships red for both Ryzen and Radeon, Intel ships
blue for both Core and Arc/iGPU — so CPU and GPU would draw the same colour on a same-vendor box (the
Intel-CPU + Intel-iGPU case is very common). We separate them on TWO channels: the GPU always gets a
distinct sibling SHADE *and* a dashed line. Hue + lightness + dash survives even red/green colour-
blindness. These are smart defaults only — the Monitor settings expose colour pickers that override.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional, Tuple

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows
    winreg = None

_logger = logging.getLogger("NetSpeedTray.HardwareVendors")

# Brand hues. CPU = saturated, solid. GPU = a distinct sibling shade, dashed.
_CPU_COLORS = {"intel": "#0071C5", "amd": "#E2231A", "unknown": "#8A8D91"}
_GPU_COLORS = {"nvidia": "#76B900", "amd": "#FF7A45", "intel": "#33C3D6", "unknown": "#B58BFF"}

# matplotlib dash spec for the GPU line (on,off) — readable at 1.5px.
_GPU_DASH = (0, (5, 2))
_CPU_SOLID = "solid"


@lru_cache(maxsize=1)
def cpu_vendor() -> str:
    """'intel' | 'amd' | 'unknown' (registry VendorIdentifier)."""
    if winreg is None:
        return "unknown"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            vid, _ = winreg.QueryValueEx(k, "VendorIdentifier")
        v = str(vid).lower()
        if "intel" in v:
            return "intel"
        if "amd" in v:
            return "amd"
    except Exception as exc:
        _logger.debug("cpu_vendor detection failed: %s", exc)
    return "unknown"


@lru_cache(maxsize=1)
def gpu_vendor() -> str:
    """'nvidia' | 'amd' | 'intel' | 'unknown' from the display-adapter DriverDesc.

    Walks the Display class (GUID 4d36e968…) subkeys and returns the first real adapter's vendor,
    preferring a discrete one — a Microsoft Basic Display Adapter / virtual entry is skipped.
    """
    if winreg is None:
        return "unknown"
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    fallback = "unknown"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as cls:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(cls, i)
                    i += 1
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(cls, sub) as gk:
                        desc, _ = winreg.QueryValueEx(gk, "DriverDesc")
                    v = _classify_gpu(str(desc))
                    if v != "unknown":
                        if v == "nvidia" or v == "amd":
                            return v          # prefer a discrete vendor outright
                        fallback = v          # Intel iGPU — keep, but let a discrete one win
                except Exception:
                    continue
    except Exception as exc:
        _logger.debug("gpu_vendor detection failed: %s", exc)
    return fallback


def _classify_gpu(desc: str) -> str:
    d = desc.lower()
    if any(s in d for s in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla")):
        return "nvidia"
    if any(s in d for s in ("radeon", "amd", "ryzen")):
        return "amd"
    if any(s in d for s in ("intel", "arc", "iris", "uhd graphics", "hd graphics")):
        return "intel"
    return "unknown"


def graph_line_style(role: str, override_color: Optional[str] = None) -> Tuple[str, object]:
    """(color, linestyle) for 'cpu' or 'gpu'. ``override_color`` (from Monitor settings) wins."""
    if role == "gpu":
        color = override_color or _GPU_COLORS.get(gpu_vendor(), _GPU_COLORS["unknown"])
        return color, _GPU_DASH
    color = override_color or _CPU_COLORS.get(cpu_vendor(), _CPU_COLORS["unknown"])
    return color, _CPU_SOLID


def default_color(role: str) -> str:
    """The vendor-default hex for 'cpu' / 'gpu' (for pre-filling the settings colour pickers)."""
    if role == "gpu":
        return _GPU_COLORS.get(gpu_vendor(), _GPU_COLORS["unknown"])
    return _CPU_COLORS.get(cpu_vendor(), _CPU_COLORS["unknown"])
