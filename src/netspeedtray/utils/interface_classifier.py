"""
Adapter classification: is this network interface a real adapter, or a virtual one?

ONE classifier, used for two things that must agree:
  * the Settings interface picker, which groups adapters into "physical" and "virtual" so a user can
    tell which entry is their actual connection, and
  * the ``all_physical`` interface mode, which sums only the physical adapters.

The 2.1 code answered this with a name keyword list alone, and it missed Tailscale, WireGuard,
NordLynx, ZeroTier and Hyper-V's vEthernet (data-usage accuracy audit, finding 5), so all_physical
counted a VPN's bytes on top of the real NIC that carried them. Names are a weak signal - "Ethernet 3"
is a VirtualBox host-only adapter on the dev machine - so the OS is asked first:

  ``GetIfTable2`` (iphlpapi, no admin, one call for every interface) gives each adapter's
  ``HardwareInterface`` flag (set by NDIS for adapters backed by a real device) and its IANA ifType
  (53 = proprietary virtual, 131 = tunnel, 23 = PPP, 24 = loopback).

Rules, in order:
  1. Loopback and the IPv6 transition shims (Teredo, ISATAP, 6to4, IP-HTTPS) are ``pseudo``.
  2. A hit in the user's ``excluded_interfaces`` (the 2.1 list, matched on the friendly name), the
     built-in name keywords, or the strong description keywords is ``virtual``. These veto the
     hardware flag: a Bluetooth PAN adapter is a hardware device, but it's a tether, not the line.
  3. With OS data: a tunnel / virtual / PPP ifType is ``virtual``; a hardware interface is
     ``physical`` - even if its description says "Virtual" (an SR-IOV virtual function, e.g. Azure's
     "Mellanox ConnectX-4 Lx Virtual Ethernet Adapter", is the VM's real path); otherwise a soft
     description keyword or a cleared hardware flag makes it ``virtual``. This is what catches a VPN
     client whose name and description match no keyword.
  4. Without OS data (off Windows, or a name only seen in the history DB): ``physical``.

The table is read once and cached. A name it doesn't know (a VPN that just came up) re-reads it at
once, throttled to one read per ``CLASSIFIER_UNKNOWN_REREAD_SEC``; an answer made without OS data is
never cached, so a new tunnel can't be pinned as physical by its first poll.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.InterfaceClassifier")

_IF = constants.network.interface


@dataclass(frozen=True)
class AdapterInfo:
    """What the OS says about one interface. ``name`` is the friendly name (psutil's key)."""
    name: str
    description: str = ""
    if_type: Optional[int] = None
    hardware: Optional[bool] = None


def _any_in(text: str, keywords: Iterable[str]) -> bool:
    return any(kw and kw in text for kw in keywords)


# Why an adapter got its kind. KEYWORD and TYPE are positive evidence of a VPN / tunnel; HARDWARE
# (the OS flag) and DEFAULT are not, which is what all_physical's "nothing physical" fallback needs.
REASON_PSEUDO = "pseudo"
REASON_KEYWORD = "keyword"
REASON_TYPE = "type"
REASON_HARDWARE = "hardware"
REASON_DEFAULT = "default"


def classify_interface_detailed(name: str, info: Optional[AdapterInfo],
                                exclusions: Iterable[str] = ()) -> Tuple[str, str]:
    """Pure classification of one interface: ``(kind, reason)``."""
    lname = (name or "").lower()
    desc = (info.description if info else "").lower()
    if_type = info.if_type if info else None

    # 1. pseudo-interfaces
    if if_type == _IF.IF_TYPE_SOFTWARE_LOOPBACK or _any_in(lname, _IF.PSEUDO_NAME_KEYWORDS):
        return _IF.KIND_PSEUDO, REASON_PSEUDO

    # 2. keyword vetoes: the user's list + ours, on the name; the strong list on the description
    user = [str(k).lower() for k in (exclusions or ())]
    if (_any_in(lname, user) or _any_in(lname, _IF.VIRTUAL_NAME_KEYWORDS)
            or _any_in(desc, _IF.VIRTUAL_DESCRIPTION_KEYWORDS)):
        return _IF.KIND_VIRTUAL, REASON_KEYWORD

    # 3. the OS signals
    if info is not None:
        if if_type in _IF.VIRTUAL_IF_TYPES:
            return _IF.KIND_VIRTUAL, REASON_TYPE
        if info.hardware is True:
            return _IF.KIND_PHYSICAL, REASON_HARDWARE
        if _any_in(desc, _IF.VIRTUAL_DESCRIPTION_SOFT_KEYWORDS):
            return _IF.KIND_VIRTUAL, REASON_KEYWORD
        if info.hardware is False:
            return _IF.KIND_VIRTUAL, REASON_HARDWARE

    # 4. nothing says otherwise
    return _IF.KIND_PHYSICAL, REASON_DEFAULT


def classify_interface(name: str, info: Optional[AdapterInfo],
                       exclusions: Iterable[str] = ()) -> str:
    """Pure classification of one interface. Returns a ``constants.network.interface.KIND_*``."""
    return classify_interface_detailed(name, info, exclusions)[0]


# --- the OS read (GetIfTable2) ------------------------------------------------------------------

_U64 = ctypes.c_uint64


class _MIB_IF_ROW2(ctypes.Structure):
    """netioapi.h MIB_IF_ROW2 (1352 bytes on x64). Only the leading fields are named; the 18
    trailing ULONG64 octet/packet counters are kept for the size."""
    _fields_ = [
        ("InterfaceLuid", _U64), ("InterfaceIndex", ctypes.c_ulong), ("InterfaceGuid", ctypes.c_ubyte * 16),
        ("Alias", ctypes.c_wchar * 257), ("Description", ctypes.c_wchar * 257),
        ("PhysicalAddressLength", ctypes.c_ulong), ("PhysicalAddress", ctypes.c_ubyte * 32),
        ("PermanentPhysicalAddress", ctypes.c_ubyte * 32), ("Mtu", ctypes.c_ulong), ("Type", ctypes.c_ulong),
        ("TunnelType", ctypes.c_int), ("MediaType", ctypes.c_int), ("PhysicalMediumType", ctypes.c_int),
        ("AccessType", ctypes.c_int), ("DirectionType", ctypes.c_int),
        ("InterfaceAndOperStatusFlags", ctypes.c_ubyte),
        ("OperStatus", ctypes.c_int), ("AdminStatus", ctypes.c_int), ("MediaConnectState", ctypes.c_int),
        ("NetworkGuid", ctypes.c_ubyte * 16), ("ConnectionType", ctypes.c_int),
        ("TransmitLinkSpeed", _U64), ("ReceiveLinkSpeed", _U64),
    ] + [(f"_counter{i}", _U64) for i in range(18)]


class _MIB_IF_TABLE2(ctypes.Structure):
    _fields_ = [("NumEntries", ctypes.c_ulong), ("Table", _MIB_IF_ROW2 * 1)]


# InterfaceAndOperStatusFlags bit-field (BOOLEAN : 1 each, LSB first).
_FLAG_HARDWARE_INTERFACE = 0x01
_FLAG_FILTER_INTERFACE = 0x02


def read_os_adapters() -> Dict[str, AdapterInfo]:
    """Every interface Windows knows, keyed by friendly name. ``{}`` off Windows or on any failure.
    NDIS filter rows (``Wi-Fi-WFP Native MAC Layer LightWeight Filter-0000`` and friends) are
    skipped - they mirror a real adapter and are never a psutil key."""
    if sys.platform != "win32":
        return {}
    lib = ctypes.WinDLL("iphlpapi")
    lib.GetIfTable2.argtypes = [ctypes.POINTER(ctypes.POINTER(_MIB_IF_TABLE2))]
    lib.GetIfTable2.restype = ctypes.c_ulong
    lib.FreeMibTable.argtypes = [ctypes.c_void_p]
    lib.FreeMibTable.restype = None
    table = ctypes.POINTER(_MIB_IF_TABLE2)()
    rc = lib.GetIfTable2(ctypes.byref(table))
    if rc != 0 or not table:
        logger.debug("GetIfTable2 failed (rc=%s); classifying by name only.", rc)
        return {}
    try:
        n = table.contents.NumEntries
        rows = ctypes.cast(ctypes.addressof(table.contents.Table), ctypes.POINTER(_MIB_IF_ROW2 * n)).contents
        out: Dict[str, AdapterInfo] = {}
        for r in rows:
            flags = r.InterfaceAndOperStatusFlags
            if flags & _FLAG_FILTER_INTERFACE or not r.Alias:
                continue
            out.setdefault(r.Alias, AdapterInfo(
                name=r.Alias, description=r.Description, if_type=int(r.Type),
                hardware=bool(flags & _FLAG_HARDWARE_INTERFACE)))
        return out
    finally:
        lib.FreeMibTable(table)


# --- the cached classifier -----------------------------------------------------------------------

class InterfaceClassifier:
    """Caches the OS table and per-name results. GUI-thread use (controller + Settings)."""

    def __init__(self, loader: Callable[[], Dict[str, AdapterInfo]] = read_os_adapters,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._loader = loader
        self._clock = clock
        self._table: Optional[Dict[str, AdapterInfo]] = None
        self._loaded_at = 0.0
        self._cache: Dict[tuple, str] = {}

    def _load(self) -> None:
        try:
            self._table = dict(self._loader() or {})
        except Exception as e:   # never let classification break a poll
            logger.debug("Adapter table read failed (%s); classifying by name only.", e)
            self._table = {}
        self._loaded_at = self._clock()
        self._cache.clear()

    def _detailed(self, name: str, exclusions: Iterable[str]) -> Tuple[str, str]:
        if self._table is None:
            self._load()
        elif (name not in self._table
              and self._clock() - self._loaded_at >= _IF.CLASSIFIER_UNKNOWN_REREAD_SEC):
            # A name the table doesn't know: most likely an adapter that just came up (a VPN
            # connecting). Re-read now rather than guess from its name for a long window.
            self._load()
        excl = tuple(exclusions or ())
        key = (name, excl)
        hit = self._cache.get(key)
        if hit is None:
            info = self._table.get(name)
            hit = classify_interface_detailed(name, info, excl)
            if info is not None:
                # Only an answer backed by OS data is cached. A name-only guess ("home" looks
                # physical) is recomputed, so the next table read can correct it.
                self._cache[key] = hit
        return hit

    def kind(self, name: str, exclusions: Iterable[str] = ()) -> str:
        return self._detailed(name, exclusions)[0]

    def is_physical(self, name: str, exclusions: Iterable[str] = ()) -> bool:
        return self.kind(name, exclusions) == _IF.KIND_PHYSICAL

    def fallback_eligible(self, name: str, exclusions: Iterable[str] = ()) -> bool:
        """May all_physical's "nothing is physical" fallback sum this adapter? Only if it is not
        pseudo and not KNOWN to be a VPN/tunnel (keyword or tunnel/PPP/virtual ifType) - i.e. it is
        virtual only because the OS doesn't flag it as hardware (a VM guest NIC, say)."""
        kind, reason = self._detailed(name, exclusions)
        return kind != _IF.KIND_PSEUDO and reason not in (REASON_KEYWORD, REASON_TYPE)


_default: Optional[InterfaceClassifier] = None


def get_classifier() -> InterfaceClassifier:
    """The process-wide classifier (one OS table cache for the controller and the picker)."""
    global _default
    if _default is None:
        _default = InterfaceClassifier()
    return _default


# --- picker grouping ------------------------------------------------------------------------------

@dataclass
class InterfaceGroups:
    """The picker's two sections. ``primary`` (the routing interface) is first in its section."""
    physical: List[str] = field(default_factory=list)
    virtual: List[str] = field(default_factory=list)
    primary: Optional[str] = None


def group_interfaces(names: Iterable[str], kind_of: Callable[[str], Optional[str]],
                     primary: Optional[str] = None) -> InterfaceGroups:
    """Split ``names`` into physical / virtual (pseudo goes with virtual - a loopback that the
    history DB remembers is still selectable, just not presented as a real adapter)."""
    uniq = sorted(set(n for n in names if n), key=str.casefold)
    if primary not in uniq:
        primary = None
    groups = InterfaceGroups(primary=primary)
    for n in uniq:
        (groups.physical if kind_of(n) == _IF.KIND_PHYSICAL else groups.virtual).append(n)
    if primary is not None:
        bucket = groups.physical if primary in groups.physical else groups.virtual
        bucket.remove(primary)
        bucket.insert(0, primary)
    return groups
