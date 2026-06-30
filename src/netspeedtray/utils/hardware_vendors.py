"""
Hardware vendor detection + the Monitor's vendor-aware graph palette.

CPU vendor comes from the registry (CentralProcessor\\0\\VendorIdentifier), GPU vendor from the
display-adapter DriverDesc - both no-admin, cached for the process. (CPU silicon never changes at
runtime; GPU is cached too - a Thunderbolt eGPU hot-plug or a driver TDR/reinstall mid-session is
knowingly NOT re-detected until restart, which at worst means a slightly-off line colour.)

The palette solves the brand-collision problem: AMD ships red for both Ryzen and Radeon, Intel ships
blue for both Core and Arc/iGPU - so CPU and GPU would draw the same colour on a same-vendor box (the
Intel-CPU + Intel-iGPU case is very common). We separate them on TWO channels: the GPU always gets a
distinct sibling SHADE *and* a dashed line. Hue + lightness + dash survives even red/green colour-
blindness. The GPU shades are theme-aware (the dark hues fail WCAG contrast on the light graph
background, so light mode gets darker siblings). Smart defaults only - Monitor settings expose colour
pickers that override.

On a HYBRID laptop (Intel iGPU + a discrete Nvidia/AMD) the graphed value is max-across-adapters
(usually the idle-busy iGPU), so asserting the discrete brand would be a lie - we return 'unknown'
(neutral) in that case so colour + legend never claim a GPU the data isn't about.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import List, Optional, Tuple

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows
    winreg = None

_logger = logging.getLogger("NetSpeedTray.HardwareVendors")

# CPU = saturated, solid. (These pass WCAG non-text contrast on BOTH graph backgrounds.)
_CPU_COLORS = {"intel": "#0071C5", "amd": "#E2231A", "unknown": "#8A8D91"}
# GPU = a distinct sibling shade, dashed. Dark-bg hues + darker light-bg siblings (>=3.0:1 on white).
_GPU_COLORS_DARK = {"nvidia": "#76B900", "amd": "#FF7A45", "intel": "#33C3D6", "unknown": "#B58BFF"}
_GPU_COLORS_LIGHT = {"nvidia": "#5A8C00", "amd": "#E2571B", "intel": "#1597A8", "unknown": "#7A4FD6"}

_GPU_DASH = (0, (5, 2))      # matplotlib (on, off) - reads at 1.5px
_CPU_SOLID = "solid"

_DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


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
    """'nvidia' | 'amd' | 'intel' | 'unknown'.

    Enumerates real (PCI) display adapters and decides deterministically: a hybrid box (a discrete
    GPU *and* an Intel iGPU) returns 'unknown' (the graphed util is max-across-adapters, so no single
    brand is honest); otherwise the discrete vendor wins, else Intel, else unknown.
    """
    adapters = _enumerate_gpu_adapters()
    discrete = sorted({v for v in adapters if v in ("nvidia", "amd")})
    has_intel = "intel" in adapters
    if discrete and has_intel:
        _logger.debug("Hybrid GPU detected (%s + intel) - using neutral colour.", discrete)
        return "unknown"
    if discrete:
        if len(discrete) > 1:
            _logger.debug("Multiple discrete GPUs %s - picking %s deterministically.", discrete, discrete[0])
        return discrete[0]
    if has_intel:
        return "intel"
    return "unknown"


def _enumerate_gpu_adapters() -> List[str]:
    """Classified vendors of the machine's PCI display adapters (skips virtual/remote/indirect)."""
    if winreg is None:
        return []
    out: List[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS) as cls:
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
                        # Gate to real PCI hardware: ROOT\ / SWD\ entries are Basic Display / indirect
                        # / RDP / virtual drivers that may still carry a vendor-looking DriverDesc.
                        try:
                            mdid, _ = winreg.QueryValueEx(gk, "MatchingDeviceID")
                            if mdid and not str(mdid).upper().startswith("PCI"):
                                continue
                        except FileNotFoundError:
                            pass  # no MatchingDeviceID - keep, best-effort
                        desc, _ = winreg.QueryValueEx(gk, "DriverDesc")
                    v = _classify_gpu(str(desc))
                    if v != "unknown":
                        out.append(v)
                except Exception:
                    continue
    except Exception as exc:
        _logger.debug("gpu adapter enumeration failed: %s", exc)
    return out


def _classify_gpu(desc: str) -> str:
    d = desc.lower()
    if any(s in d for s in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla")):
        return "nvidia"
    if any(s in d for s in ("radeon", "amd", "ryzen")):
        return "amd"
    if any(s in d for s in ("intel", "arc", "iris", "uhd graphics", "hd graphics")):
        return "intel"
    return "unknown"


def graph_line_style(role: str, override_color: Optional[str] = None,
                     is_dark: bool = True) -> Tuple[str, object]:
    """(color, linestyle) for 'cpu' or 'gpu'. ``override_color`` (Monitor settings) wins; the GPU
    colour is theme-aware so it stays legible on the light graph background."""
    if role == "gpu":
        table = _GPU_COLORS_DARK if is_dark else _GPU_COLORS_LIGHT
        color = override_color or table.get(gpu_vendor(), table["unknown"])
        return color, _GPU_DASH
    color = override_color or _CPU_COLORS.get(cpu_vendor(), _CPU_COLORS["unknown"])
    return color, _CPU_SOLID


def default_color(role: str, is_dark: bool = True) -> str:
    """The vendor-default hex for 'cpu' / 'gpu' (for pre-filling the settings colour pickers)."""
    if role == "gpu":
        table = _GPU_COLORS_DARK if is_dark else _GPU_COLORS_LIGHT
        return table.get(gpu_vendor(), table["unknown"])
    return _CPU_COLORS.get(cpu_vendor(), _CPU_COLORS["unknown"])
