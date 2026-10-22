"""
Static per-adapter GPU facts from DXGI (``IDXGIAdapter1::GetDesc1``), read with ctypes - no new
dependency, no device creation, nothing written.

Used to decide ONCE (at query init and on every query rebuild) whether this machine has a GPU with
real dedicated video memory. A usage reading cannot answer that: a discrete GPU that is idle at
startup (a hybrid laptop's Arc/Radeon, an idle eGPU, a desktop Radeon with the monitor on the
motherboard) reports ~0 Dedicated Usage, which would read as "integrated" and flip mid-session.
``DedicatedVideoMemory`` is a property of the adapter, not of its load, so it never flips.

The descriptor also carries the adapter LUID - the same LUID that names the PDH
``\\GPU Adapter Memory(luid_0xHIGH_0xLOW_phys_N)`` instances - the software flag that identifies
the Microsoft Basic Render Driver, and ``SharedSystemMemory``: the shared GPU memory budget Windows
gives the adapter (what Task Manager shows as "Shared GPU memory").
"""
from __future__ import annotations

import ctypes
import logging
import re
import sys
from ctypes import wintypes
from typing import Iterable, List, NamedTuple, Optional, Tuple

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.GpuAdapters")

DXGI_ADAPTER_FLAG_SOFTWARE = 0x2
MICROSOFT_VENDOR_ID = 0x1414          # Microsoft Basic Render Driver / WARP
_DXGI_ERROR_NOT_FOUND = 0x887A0002
_MAX_ADAPTERS = 16

# luid_0x00000000_0x0000D9C1_phys_0 (also the prefix of \GPU Engine instances)
_PDH_LUID = re.compile(r"luid_0x([0-9a-f]+)_0x([0-9a-f]+)", re.IGNORECASE)


class AdapterInfo(NamedTuple):
    name: str
    vendor_id: int
    luid: Tuple[int, int]            # (HighPart, LowPart) as unsigned ints, matching PDH names
    dedicated_video_bytes: int
    shared_system_bytes: int
    software: bool                   # Basic Render Driver / WARP


def pdh_instance_luid(instance: object) -> Optional[Tuple[int, int]]:
    """(high, low) LUID parsed from a PDH GPU instance name, or None."""
    m = _PDH_LUID.search(instance) if isinstance(instance, str) else None
    return (int(m.group(1), 16), int(m.group(2), 16)) if m else None


def hardware_adapters(adapters: Iterable[AdapterInfo]) -> List[AdapterInfo]:
    """Real GPUs only: drops the Basic Render Driver / WARP (software flag or Microsoft vendor id)."""
    return [a for a in adapters if not a.software and a.vendor_id != MICROSOFT_VENDOR_ID]


def find_adapter(adapters: Optional[Iterable[AdapterInfo]], name: object) -> Optional[AdapterInfo]:
    """The real adapter whose DXGI Description is ``name`` (case and surrounding space ignored), or
    None: an empty name (Automatic), no DXGI facts, or that GPU is not in this machine right now.
    Two identical cards share a name; the first DXGI enumerates (the primary) wins."""
    wanted = str(name or "").strip().casefold()
    if not wanted or adapters is None:
        return None
    for a in hardware_adapters(adapters):
        if a.name.strip().casefold() == wanted:
            return a
    return None


def is_integrated(adapter: AdapterInfo) -> bool:
    """An integrated GPU by the same static DXGI fact the shared-VRAM rule uses: less dedicated video
    memory than constants.gpu.DEDICATED_VIDEO_MEMORY_MIN_MIB (an iGPU's BIOS stolen chunk)."""
    return adapter.dedicated_video_bytes / (1024.0 * 1024.0) < constants.gpu.DEDICATED_VIDEO_MEMORY_MIN_MIB


def same_maker_siblings(adapters: Optional[Iterable[AdapterInfo]], adapter: AdapterInfo) -> List[AdapterInfo]:
    """The OTHER real adapters from ``adapter``'s maker (an Intel iGPU beside an Arc, an APU beside a
    Radeon): the case where a vendor-only sensor match cannot say which card a reading is from."""
    return [a for a in hardware_adapters(adapters or []) if a.vendor_id == adapter.vendor_id and a != adapter]


def sensor_matches_vendor(text: object, vendor_id: int) -> bool:
    """Whether a sensor's hardware id / name / identifier names the maker ``vendor_id`` (see
    constants.gpu.VENDOR_SENSOR_TOKENS). An unknown vendor matches nothing."""
    tokens = constants.gpu.VENDOR_SENSOR_TOKENS.get(int(vendor_id), ())
    low = str(text or "").casefold()
    return any(t in low for t in tokens)


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", wintypes.WCHAR * 128),
                ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT),
                ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT),
                ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _LUID), ("Flags", wintypes.UINT)]


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


# {770aae78-f26f-4dba-a829-253c83d1b387}
_IID_IDXGIFactory1 = _GUID(0x770AAE78, 0xF26F, 0x4DBA,
                           (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))

# COM vtable slots (IUnknown 0-2, IDXGIObject 3-6, IDXGIFactory 7-11, IDXGIFactory1 12-13;
# IDXGIAdapter 7-9, IDXGIAdapter1 10).
_RELEASE = 2
_FACTORY1_ENUM_ADAPTERS1 = 12
_ADAPTER1_GET_DESC1 = 10


def _vcall(obj: ctypes.c_void_p, slot: int, restype, *args_types):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *args_types)(vtbl[slot])


def _release(obj: ctypes.c_void_p) -> None:
    try:
        if obj:
            _vcall(obj, _RELEASE, ctypes.c_ulong)(obj)
    except Exception:
        pass


def enumerate_adapters() -> Optional[List[AdapterInfo]]:
    """Every DXGI adapter, or None when DXGI is unavailable (non-Windows, a failed call)."""
    if sys.platform != "win32":
        return None
    factory = ctypes.c_void_p()
    try:
        dxgi = ctypes.WinDLL("dxgi.dll")
        create = dxgi.CreateDXGIFactory1
        create.restype = ctypes.c_long
        create.argtypes = [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        hr = create(ctypes.byref(_IID_IDXGIFactory1), ctypes.byref(factory))
        if hr < 0 or not factory:
            logger.debug("CreateDXGIFactory1 failed: 0x%08X", hr & 0xFFFFFFFF)
            return None
        enum = _vcall(factory, _FACTORY1_ENUM_ADAPTERS1, ctypes.c_long,
                      wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))
        out: List[AdapterInfo] = []
        for i in range(_MAX_ADAPTERS):
            adapter = ctypes.c_void_p()
            hr = enum(factory, i, ctypes.byref(adapter))
            if (hr & 0xFFFFFFFF) == _DXGI_ERROR_NOT_FOUND:
                break
            if hr < 0 or not adapter:
                logger.debug("EnumAdapters1(%d) failed: 0x%08X", i, hr & 0xFFFFFFFF)
                return None
            try:
                desc = _DXGI_ADAPTER_DESC1()
                hr = _vcall(adapter, _ADAPTER1_GET_DESC1, ctypes.c_long,
                            ctypes.POINTER(_DXGI_ADAPTER_DESC1))(adapter, ctypes.byref(desc))
                if hr < 0:
                    logger.debug("GetDesc1(%d) failed: 0x%08X", i, hr & 0xFFFFFFFF)
                    return None
                out.append(AdapterInfo(
                    name=desc.Description, vendor_id=int(desc.VendorId),
                    luid=(int(desc.AdapterLuid.HighPart) & 0xFFFFFFFF, int(desc.AdapterLuid.LowPart)),
                    dedicated_video_bytes=int(desc.DedicatedVideoMemory),
                    shared_system_bytes=int(desc.SharedSystemMemory),
                    software=bool(desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)))
            finally:
                _release(adapter)
        return out
    except Exception as e:
        logger.debug("DXGI adapter enumeration unavailable: %s", e)
        return None
    finally:
        _release(factory)
