"""
LatencyProbe — a small background thread that measures network latency.

Privacy-first design (owner decision): it pings the **default gateway** by default — a LAN probe that
never leaves your network — and ONLY pings a public anchor (e.g. 1.1.1.1, for true internet latency)
when the user explicitly opts in. ICMP uses the Windows ICMP helper API (IcmpSendEcho) via ctypes, so
it needs NO admin and spawns no subprocess. It records a timed-out flag per probe so loss% / p99 are
real for the ISP-dispute use-case. Runs on its OWN QThread so a 1 s timeout never blocks the stats
poll. Emits `latency_ready(gw_ms|None, anchor_ms|None, gw_timed_out)`; the controller persists it
(latency_gw / latency_anchor stat_types) and stashes it on the widget for the Monitor's latency pill.
"""
from __future__ import annotations

import ctypes
import socket
import struct
import time
from ctypes import wintypes
from typing import Any, Dict, Optional

from PyQt6.QtCore import QThread, pyqtSignal


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
            dest = struct.unpack("<I", socket.inet_aton(ip))[0]
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
    """Background latency sampler. gw_ms / anchor_ms are ms (None = timeout); gw_timed_out flags loss."""

    latency_ready = pyqtSignal(object, object, bool)

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
        while self._running:
            try:
                gw_ip = self._gateway_ip()
                gw = icmp_ping_ms(gw_ip, self._TIMEOUT_MS) if gw_ip else None
                gw_timed_out = bool(gw_ip) and gw is None

                anchor = None
                if self._config.get("latency_public_enabled", False):
                    host = str(self._config.get("latency_public_host", "1.1.1.1") or "1.1.1.1")
                    anchor = icmp_ping_ms(host, self._TIMEOUT_MS)

                self.latency_ready.emit(gw, anchor, gw_timed_out)
            except Exception:
                pass
            # Sleep in small slices so stop() is responsive.
            slept = 0.0
            while self._running and slept < self._CADENCE_SEC:
                time.sleep(0.2)
                slept += 0.2
