"""
LatencyProbe - a small background thread that measures network latency.

Privacy-first design (owner decision): it pings the **default gateway** by default - a LAN probe that
never leaves your network - and ONLY pings a public anchor (e.g. 1.1.1.1, for true internet latency)
when the user explicitly opts in. ICMP uses the Windows ICMP helper API (IcmpSendEcho) via ctypes, so
it needs NO admin and spawns no subprocess. It records a timed-out flag per probe so loss% / p99 are
real for the ISP-dispute use-case. Runs on its OWN QThread so a 1 s timeout never blocks the stats
poll. Emits `latency_ready(LatencySample)`; the widget persists it (latency_gw / latency_anchor RTTs plus
the latency_gw_timeout / latency_anchor_timeout loss series) and keeps one live loss window per hop for
the Monitor's latency pill.

The attribution rule - which hop a loss belongs to - is written ONCE here (`attribute_loss`) and reused
by every reader that explains a loss (the on-demand connection check, 2.2 D3):
  * loss at the gateway  = your Wi-Fi / LAN;
  * loss on the anchor with a clean gateway = beyond your router (ISP or path);
  * a gateway timeout means the anchor cannot be reached either, so that cycle's anchor probe is
    SKIPPED and recorded as lost. Anchor loss >= gateway loss by construction.
"""
from __future__ import annotations

import ctypes
import socket
import struct
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from netspeedtray import constants


# --- the sample, the hops, and the attribution rule ------------------------------------------------
HOP_GATEWAY = "gateway"
HOP_ANCHOR = "anchor"

LOSS_NONE = "none"                          # neither hop lost anything
LOSS_LOCAL = "local"                        # the gateway lost it: Wi-Fi / LAN
LOSS_BEYOND_ROUTER = "beyond_router"        # clean gateway, lossy anchor: ISP or path
LOSS_LOCAL_AND_BEYOND = "local_and_beyond"  # both, and the anchor lost more than the gateway explains


@dataclass(frozen=True)
class LatencySample:
    """One probe cycle. RTTs are ms (None = no reply). `anchor_enabled` says whether the public anchor
    was part of this cycle (the opt-in); when it was not, the anchor fields are None/False and carry no
    loss. `anchor_skipped` marks the attribution rule's skip: the gateway timed out, so the anchor was
    not probed and counts as lost (`anchor_timed_out` is True too). 2.2 D2 adds per-hop sent/lost
    counts to this shape for the burst probe."""
    gw_ms: Optional[float]
    anchor_ms: Optional[float]
    gw_timed_out: bool
    anchor_timed_out: bool = False
    anchor_enabled: bool = False
    anchor_skipped: bool = False


def headline_hop(anchor_enabled: bool) -> str:
    """The hop a single headline figure describes: the internet anchor when the user opted into the
    public probe, the gateway otherwise."""
    return HOP_ANCHOR if anchor_enabled else HOP_GATEWAY


def attribute_loss(gw_loss_pct: float, anchor_loss_pct: Optional[float]) -> str:
    """Where the loss happened, from each hop's loss % (anchor None = not probed). Reads the GATEWAY
    first so the internet is never blamed for loss the router already explains (anchor loss includes
    every gateway loss by construction - see the module docstring)."""
    gw = float(gw_loss_pct or 0.0)
    an = None if anchor_loss_pct is None else float(anchor_loss_pct)
    if gw > 0:
        return LOSS_LOCAL_AND_BEYOND if an is not None and an > gw else LOSS_LOCAL
    if an is not None and an > 0:
        return LOSS_BEYOND_ROUTER
    return LOSS_NONE


class LatencyLossWindows:
    """The live (~2 min) loss window per hop. Independent deques: the gateway window takes every cycle,
    the anchor window only cycles where the anchor was part of the probe (and is reset when the user
    turns the public probe off, so a re-enable starts clean instead of reviving stale flags)."""

    def __init__(self, maxlen: int = constants.network.latency.LIVE_LOSS_WINDOW) -> None:
        self.gw: Deque[bool] = deque(maxlen=maxlen)
        self.anchor: Deque[bool] = deque(maxlen=maxlen)

    def add(self, sample: LatencySample) -> None:
        self.gw.append(bool(sample.gw_timed_out))
        if sample.anchor_enabled:
            self.anchor.append(bool(sample.anchor_timed_out))
        else:
            self.anchor.clear()

    @staticmethod
    def _pct(window: Deque[bool]) -> float:
        return round(sum(window) / len(window) * 100.0, 1) if window else 0.0

    @property
    def gw_loss(self) -> float:
        return self._pct(self.gw)

    @property
    def anchor_loss(self) -> float:
        return self._pct(self.anchor)


# --- Windows ICMP helper (no admin, no subprocess) -------------------------------------------------
class _IP_OPTION_INFORMATION(ctypes.Structure):
    _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte),
                ("Flags", ctypes.c_ubyte), ("OptionsSize", ctypes.c_ubyte),
                ("OptionsData", ctypes.c_void_p)]


class _ICMP_ECHO_REPLY(ctypes.Structure):
    _fields_ = [("Address", ctypes.c_uint32), ("Status", ctypes.c_uint32),
                ("RoundTripTime", ctypes.c_uint32), ("DataSize", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort), ("Data", ctypes.c_void_p),
                ("Options", _IP_OPTION_INFORMATION)]


_iphlp = None


def _iphlpapi():
    """Bind iphlpapi once with correct 64-bit-safe signatures (HANDLE truncation is the classic bug)."""
    global _iphlp
    if _iphlp is None:
        lib = ctypes.windll.iphlpapi
        lib.IcmpCreateFile.restype = wintypes.HANDLE
        lib.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
        lib.IcmpSendEcho.restype = wintypes.DWORD
        lib.IcmpSendEcho.argtypes = [wintypes.HANDLE, ctypes.c_uint32, ctypes.c_char_p, wintypes.WORD,
                                     ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
        _iphlp = lib
    return _iphlp


def icmp_ping_ms(ip: str, timeout_ms: int = 1000) -> Optional[float]:
    """One ICMP echo to ``ip``. Returns round-trip ms, or None on timeout / unreachable / error."""
    try:
        lib = _iphlpapi()
        handle = lib.IcmpCreateFile()
        if not handle or handle == wintypes.HANDLE(-1).value:
            return None
        try:
            try:
                packed = socket.inet_aton(ip)                          # a dotted IPv4 literal
            except OSError:
                packed = socket.inet_aton(socket.gethostbyname(ip))    # resolve a hostname (e.g. cloudflare.com)
            dest = struct.unpack("<I", packed)[0]
            data = b"netspeedtray"
            reply_size = ctypes.sizeof(_ICMP_ECHO_REPLY) + len(data) + 8
            reply_buf = ctypes.create_string_buffer(reply_size)
            n = lib.IcmpSendEcho(handle, dest, data, len(data), None, reply_buf, reply_size, int(timeout_ms))
            if n == 0:
                return None   # timeout / unreachable (this is the "loss" case)
            reply = ctypes.cast(reply_buf, ctypes.POINTER(_ICMP_ECHO_REPLY)).contents
            if reply.Status != 0:   # 0 == IP_SUCCESS
                return None
            return float(reply.RoundTripTime)
        finally:
            lib.IcmpCloseHandle(handle)
    except Exception:
        return None


def default_gateway_ip() -> Optional[str]:
    """The default gateway IPv4, via WMI (no admin). None if not found."""
    try:
        import wmi  # already a dependency
        for nic in wmi.WMI().Win32_NetworkAdapterConfiguration(IPEnabled=True):
            gws = getattr(nic, "DefaultIPGateway", None)
            if gws:
                for g in gws:
                    if g and ":" not in g:   # IPv4 only
                        return str(g)
    except Exception:
        return None
    return None


class LatencyProbe(QThread):
    """Background latency sampler. Emits one `LatencySample` per cycle."""

    latency_ready = pyqtSignal(object)   # LatencySample

    _CADENCE_SEC = 3.0
    _TIMEOUT_MS = 1000
    _GATEWAY_REFRESH_SEC = 60.0

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__()
        self._config = config
        self._running = True
        self._gateway: Optional[str] = None
        self._gateway_ts = 0.0

    def stop(self) -> None:
        self._running = False

    def _gateway_ip(self) -> Optional[str]:
        now = time.monotonic()
        if self._gateway is None or (now - self._gateway_ts) > self._GATEWAY_REFRESH_SEC:
            self._gateway = default_gateway_ip()
            self._gateway_ts = now
        return self._gateway

    def run(self) -> None:
        # This thread calls wmi.WMI() (via default_gateway_ip) which needs COM initialized PER THREAD -
        # without it, wmi raises "CoInitialize has not been called", default_gateway_ip swallows it, and
        # the gateway probe (the privacy-preserving default) silently never produces a reading. Mirror
        # StatsMonitorThread, which inits COM on its own thread for the same reason.
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pythoncom = None
        try:
            self._run_loop()
        finally:
            try:
                if pythoncom is not None:
                    pythoncom.CoUninitialize()
            except Exception:
                pass

    def _run_loop(self) -> None:
        while self._running:
            try:
                gw_ip = self._gateway_ip()
                gw = icmp_ping_ms(gw_ip, self._TIMEOUT_MS) if gw_ip else None
                gw_timed_out = bool(gw_ip) and gw is None

                anchor = None
                anchor_enabled = bool(self._config.get("latency_public_enabled", False))
                anchor_skipped = False
                if anchor_enabled:
                    if gw_timed_out:
                        # The link is down at the first hop: the anchor cannot answer either. Skip it
                        # (probing on only stretches the cycle) and record it lost - the attribution
                        # rule, so anchor loss >= gateway loss.
                        anchor_skipped = True
                    else:
                        host = str(self._config.get("latency_public_host", "1.1.1.1") or "1.1.1.1")
                        anchor = icmp_ping_ms(host, self._TIMEOUT_MS)

                self.latency_ready.emit(LatencySample(
                    gw_ms=gw, anchor_ms=anchor, gw_timed_out=gw_timed_out,
                    anchor_timed_out=anchor_enabled and anchor is None,
                    anchor_enabled=anchor_enabled, anchor_skipped=anchor_skipped))
            except Exception:
                pass
            # Sleep in small slices so stop() is responsive.
            slept = 0.0
            while self._running and slept < self._CADENCE_SEC:
                time.sleep(0.2)
                slept += 0.2
