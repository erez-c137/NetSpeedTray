"""
Hardware vendor detection + the Monitor's vendor-aware graph palette.

CPU vendor comes from the registry (CentralProcessor\\0\\VendorIdentifier), GPU vendor from the
display-adapter DriverDesc - both no-admin, cached for the process. (CPU silicon never changes at
runtime; GPU is cached too - a Thunderbolt eGPU hot-plug or a driver TDR/reinstall mid-session is
knowingly NOT re-detected until restart, which at worst means a slightly-off line color.)

The palette solves the brand-collision problem: AMD ships red for both Ryzen and Radeon, Intel ships
blue for both Core and Arc/iGPU - so CPU and GPU would draw the same color on a same-vendor box (the
Intel-CPU + Intel-iGPU case is very common). We separate them on TWO channels: the GPU always gets a
distinct sibling SHADE *and* a dashed line. Hue + lightness + dash survives even red/green color-
blindness. The GPU shades are theme-aware (the dark hues fail WCAG contrast on the light graph
background, so light mode gets darker siblings). Smart defaults only - Monitor settings expose color
pickers that override.

On a HYBRID laptop (an Intel or Ryzen iGPU + a discrete Nvidia/AMD) the graphed value is max-across-adapters
(usually the idle-busy iGPU), so asserting the discrete brand would be a lie - we return 'unknown'
(neutral) in that case so color + legend never claim a GPU the data isn't about.
"""
from __future__ import annotations

import colorsys
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

    Enumerates real (PCI) display adapters. Exactly one vendor among them -> that vendor; two or more
    DIFFERENT vendors -> 'unknown'. The graphed util is max-across-adapters, so on a mixed box no single
    brand is honest. That covers Intel iGPU + NVIDIA/AMD laptops AND Ryzen iGPU + NVIDIA ones - the Ryzen
    iGPU's DriverDesc ("AMD Radeon(TM) Graphics") is indistinguishable from a discrete Radeon by name,
    so any vendor mix is treated as hybrid rather than guessing which adapter is integrated.
    """
    vendors = sorted(set(_enumerate_gpu_adapters()))
    if len(vendors) > 1:
        _logger.debug("Mixed GPU vendors %s (hybrid) - using neutral color.", vendors)
        return "unknown"
    if vendors:
        return vendors[0]
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
    color is theme-aware so it stays legible on the light graph background."""
    if role == "gpu":
        table = _GPU_COLORS_DARK if is_dark else _GPU_COLORS_LIGHT
        color = override_color or table.get(gpu_vendor(), table["unknown"])
        return color, _GPU_DASH
    color = override_color or _CPU_COLORS.get(cpu_vendor(), _CPU_COLORS["unknown"])
    return color, _CPU_SOLID


# --- The taskbar widget's CPU/GPU labels ("Brand" label colour) ---------------------------------
# Small glyphs and 9 pt text on the taskbar, so every brand / sibling shade, and every light-taskbar
# shade, clears WCAG's 4.5:1 TEXT contrast on the taskbar it is meant for (reference backgrounds
# #202020 / #2B2B2B / #1C1C1C dark, #EEEEEE / #E8E8E8 / #F3F3F3 light; pinned by
# test_hardware_label_colors.py). The ONE exception is the dark-taskbar neutral pair, kept as the exact
# pre-2.2 colours so unknown-vendor dark-taskbar widgets do not change: CPU #3498DB is 4.5-5.4:1 but
# GPU #E74C3C only 3.7-4.5:1 (4.26:1 on #202020). The pure brand hexes (#0071C5, #E2231A) are only
# ~3:1 on a dark taskbar, hence the lifted dark-taskbar shades and the deepened light ones.
# The GPU wears its OWN brand hue (NVIDIA green, AMD red, Intel blue); only when CPU and GPU share a
# vendor does it switch to a sibling shade (AMD orange, Intel teal) so the two stay apart at a glance.
# Unknown (incl. hybrid laptops, see gpu_vendor) keeps the pre-2.2 neutral blue/red, deepened on a
# light taskbar where the originals measured 2.6-3.4:1. When a neutral would land on the other label's
# hue (AMD CPU + hybrid GPU: red beside red; unknown CPU + Intel GPU: blue beside blue) the GPU takes
# the Monitor's neutral purple instead (6.3 / 5.5-6.0:1).
_LABEL_BRAND = {
    # vendor: (on a dark taskbar, on a light taskbar)
    "intel": ("#4FA8F0", "#0062AD"),
    "amd": ("#FF5A4F", "#C41E1E"),
    "nvidia": ("#76B900", "#447300"),
}
_LABEL_GPU_SIBLING = {"intel": ("#33C3D6", "#00727F"), "amd": ("#FF9A5C", "#A84300")}
_LABEL_NEUTRAL = {"cpu": ("#3498DB", "#1F6AA5"), "gpu": ("#E74C3C", "#B83227")}
_LABEL_NEUTRAL_GPU_ALT = ("#B58BFF", "#6A3FC6")
_MIN_LABEL_HUE_GAP = 25   # degrees; below this CPU and GPU read as the same colour at 9 pt


def _hue_gap(a: str, b: str) -> int:
    def hue(h: str) -> int:
        r, g, b_ = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return round(colorsys.rgb_to_hsv(r, g, b_)[0] * 360)
    d = abs(hue(a) - hue(b)) % 360
    return min(d, 360 - d)


def widget_label_color(role: str, light_taskbar: bool = False) -> str:
    """Brand hex for the widget's 'cpu' / 'gpu' label on a dark (default) or light taskbar."""
    idx = 1 if light_taskbar else 0
    cpu = cpu_vendor()
    cpu_color = _LABEL_BRAND[cpu][idx] if cpu in ("intel", "amd") else _LABEL_NEUTRAL["cpu"][idx]
    if role != "gpu":
        return cpu_color
    gpu = gpu_vendor()
    if gpu == cpu and gpu in _LABEL_GPU_SIBLING:
        return _LABEL_GPU_SIBLING[gpu][idx]
    gpu_color = _LABEL_BRAND.get(gpu, _LABEL_NEUTRAL["gpu"])[idx]
    either_neutral = gpu not in _LABEL_BRAND or cpu not in ("intel", "amd")
    if either_neutral and _hue_gap(cpu_color, gpu_color) < _MIN_LABEL_HUE_GAP:
        return _LABEL_NEUTRAL_GPU_ALT[idx]
    return gpu_color


def default_color(role: str, is_dark: bool = True) -> str:
    """The vendor-default hex for 'cpu' / 'gpu' (for pre-filling the settings color pickers)."""
    if role == "gpu":
        table = _GPU_COLORS_DARK if is_dark else _GPU_COLORS_LIGHT
        return table.get(gpu_vendor(), table["unknown"])
    return _CPU_COLORS.get(cpu_vendor(), _CPU_COLORS["unknown"])
