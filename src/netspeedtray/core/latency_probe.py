"""
LatencyProbe - a small background thread that measures network latency.

Privacy-first design (owner decision): it pings the **default gateway** by default - a LAN probe that
never leaves your network - and ONLY pings a public anchor (e.g. 1.1.1.1, for true internet latency)
when the user explicitly opts in. ICMP uses the Windows ICMP helper API (IcmpSendEcho) via ctypes, so
it needs NO admin and spawns no subprocess. Each 3 s cycle sends a BURST of BURST_SIZE (4) echoes per
hop, BURST_SPACING_MS (100 ms) apart, each with its own 1000 ms timeout (~1.3 packets/s of ~80 bytes per
hop); a hop's RTT is the median of the burst's replies and its loss is lost/sent, so loss% resolves
0.6% steps over the ~2 min live window - real for the ISP-dispute use-case. Runs on its OWN QThread so a
timeout never blocks the stats poll. Every wait inside a cycle is interruptible and stop() is checked
before every echo (and before the anchor burst and its DNS resolve), so after stop() at most the one
echo already in flight completes (<= its 1 s timeout) and no new packet is sent. The same holds for
the ``latency_enabled`` master switch, which is read live before every echo: turning Latency off in
Settings ends the probe (gateway and public host alike) without waiting for a restart. Emits `latency_ready(LatencySample)`; the widget persists it (latency_gw / latency_anchor RTTs plus
the latency_gw_timeout / latency_anchor_timeout loss series) and keeps one live loss window per hop for
the Monitor's latency pill.

The attribution rule - which hop a loss belongs to - is written ONCE here (`attribute_loss`) and reused
by every reader that explains a loss (the on-demand connection check, 2.2 D3):
  * loss at the gateway  = your Wi-Fi / LAN;
  * loss on the anchor with a clean gateway = beyond your router (ISP or path);
  * a fully lost gateway burst means the anchor cannot be reached either, so that cycle's anchor
    burst is SKIPPED and recorded as fully lost (BURST_SIZE sent, BURST_SIZE lost). Anchor loss >= gateway loss by construction - but only while the
    gateway has answered within the last GATEWAY_REPLY_RECENT_CYCLES cycles. A gateway that never
    answers ICMP proves nothing about the link, so the anchor is then probed (and attributed) normally.
"""
from __future__ import annotations

import contextlib
import ctypes
import logging
import socket
import statistics
import struct
import sys
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Iterator, Optional, Tuple

from PyQt6.QtCore import QThread, pyqtSignal

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.LatencyProbe")


# --- the sample, the hops, and the attribution rule ------------------------------------------------
HOP_GATEWAY = "gateway"
HOP_ANCHOR = "anchor"

LOSS_NONE = "none"                          # neither hop lost anything
LOSS_LOCAL = "local"                        # the gateway lost it: Wi-Fi / LAN
LOSS_BEYOND_ROUTER = "beyond_router"        # clean gateway, lossy anchor: ISP or path
LOSS_LOCAL_AND_BEYOND = "local_and_beyond"  # both, and the anchor lost more than the gateway explains
LOSS_UNKNOWN = "unknown"                    # the gateway never answered: where the loss is can't be told

VERDICT_GOOD = "good"
VERDICT_OK = "ok"
VERDICT_SLOW = "slow"


@dataclass(frozen=True)
class LatencySample:
    """One probe cycle. RTTs are ms - the MEDIAN of the burst's successful replies (None = no reply at
    all). `*_timed_out` = that hop's whole burst was lost. `*_sent` / `*_lost` count the burst's echoes;
    the persisted loss series is lost / sent. `anchor_enabled` says whether the public anchor was part
    of this cycle (the opt-in); when it was not, the anchor fields are None/False/0 and carry no loss.
    `anchor_skipped` marks the attribution rule's skip: the gateway burst was fully lost, so the anchor
    was not probed and counts as fully lost (`anchor_timed_out` is True, anchor_lost == anchor_sent).

    Counts left as None take the single-echo (pre-burst) shape: 1 sent, lost = the timed-out flag (the
    gateway counts as sent even without one, as before); the anchor 0/0 unless enabled."""
    gw_ms: Optional[float]
    anchor_ms: Optional[float]
    gw_timed_out: bool
    anchor_timed_out: bool = False
    anchor_enabled: bool = False
    anchor_skipped: bool = False
    gw_sent: Optional[int] = None
    gw_lost: Optional[int] = None
    anchor_sent: Optional[int] = None
    anchor_lost: Optional[int] = None
    # The gateway changed during this cycle and its burst lost echoes: those say nothing about the
    # link, so the gateway hop is NOT measured this cycle (no loss recorded, not a window cycle). The
    # anchor burst - when enabled - is still probed and recorded as usual.
    gw_excluded: bool = False

    def __post_init__(self) -> None:
        if self.gw_sent is None:
            object.__setattr__(self, "gw_sent", 1)
        if self.gw_lost is None:
            object.__setattr__(self, "gw_lost", 1 if self.gw_timed_out else 0)
        if self.anchor_sent is None:
            object.__setattr__(self, "anchor_sent", 1 if self.anchor_enabled else 0)
        if self.anchor_lost is None:
            object.__setattr__(self, "anchor_lost",
                               1 if (self.anchor_enabled and self.anchor_timed_out) else 0)

    @property
    def gw_loss_fraction(self) -> float:
        """lost / sent of the gateway burst (0.0 when nothing was sent - no gateway known, as before)."""
        return self.gw_lost / self.gw_sent if self.gw_sent else 0.0

    @property
    def anchor_loss_fraction(self) -> float:
        return self.anchor_lost / self.anchor_sent if self.anchor_sent else 0.0


def headline_hop(anchor_enabled: bool) -> str:
    """The hop a single headline figure describes: the internet anchor when the user opted into the
    public probe, the gateway otherwise."""
    return HOP_ANCHOR if anchor_enabled else HOP_GATEWAY


def attribute_loss(gw_loss_pct: float, anchor_loss_pct: Optional[float],
                   gw_answered: bool = True) -> str:
    """Where the loss happened, from each hop's loss % (anchor None = not probed). Reads the GATEWAY
    first so the internet is never blamed for loss the router already explains (anchor loss includes
    every gateway loss by construction - see the module docstring).

    ``gw_answered`` False = the gateway gave no reply at all over the span measured (many routers,
    mesh nodes and VPN gateways never answer ICMP). Its "loss" then proves nothing about Wi-Fi/LAN, so
    it is never blamed: a clean anchor still reads as no loss, anything else is LOSS_UNKNOWN."""
    gw = float(gw_loss_pct or 0.0)
    an = None if anchor_loss_pct is None else float(anchor_loss_pct)
    if not gw_answered:
        return LOSS_NONE if an is not None and an <= 0 else LOSS_UNKNOWN
    if gw > 0:
        return LOSS_LOCAL_AND_BEYOND if an is not None and an > gw else LOSS_LOCAL
    if an is not None and an > 0:
        return LOSS_BEYOND_ROUTER
    return LOSS_NONE


def latency_verdict(ms: Optional[float], loss_pct: Optional[float]) -> str:
    """Good / OK / Slow for one hop - the rule the Overview pill shows and the connection check reuses.
    No RTT at all reads Slow; otherwise the worse of the ms and the loss decides."""
    lat = constants.network.latency
    loss = float(loss_pct or 0.0)
    if ms is None or loss >= lat.VERDICT_SLOW_LOSS_PCT or ms >= lat.VERDICT_SLOW_MS:
        return VERDICT_SLOW
    if ms >= lat.VERDICT_OK_MS or loss >= lat.VERDICT_OK_LOSS_PCT:
        return VERDICT_OK
    return VERDICT_GOOD


class LatencyLossWindows:
    """The live (~2 min) window per hop: (sent, lost, rtt_ms) per cycle. Independent deques: the gateway
    window takes every cycle, the anchor window only cycles where the anchor was part of the probe (and
    is reset when the user turns the public probe off, so a re-enable starts clean instead of reviving
    stale counts). Loss % = sum(lost) / sum(sent) over the window - 160 probes at 4 per burst, so 0.6%
    steps. It is None until a window holds LOSS_MIN_SAMPLES *cycles* (not probes)."""

    def __init__(self, maxlen: int = constants.network.latency.LIVE_LOSS_WINDOW,
                 min_samples: int = constants.network.latency.LOSS_MIN_SAMPLES) -> None:
        self.gw: Deque[Tuple[int, int, Optional[float]]] = deque(maxlen=maxlen)
        self.anchor: Deque[Tuple[int, int, Optional[float]]] = deque(maxlen=maxlen)
        self._min = max(1, int(min_samples))

    def add(self, sample: LatencySample) -> None:
        if not sample.gw_excluded:
            self.gw.append((int(sample.gw_sent), int(sample.gw_lost), sample.gw_ms))
        if sample.anchor_enabled:
            self.anchor.append((int(sample.anchor_sent), int(sample.anchor_lost), sample.anchor_ms))
        else:
            self.anchor.clear()

    def _pct(self, window) -> Optional[float]:
        if len(window) < self._min:
            return None
        sent = sum(s for s, _, _ in window)
        if sent <= 0:
            return 0.0   # no gateway known for the whole window: nothing was lost (as before bursts)
        return round(sum(lost for _, lost, _ in window) / sent * 100.0, 1)

    @staticmethod
    def _last_ms(window) -> Optional[float]:
        for _, _, ms in reversed(window):
            if ms is not None:
                return ms
        return None

    @property
    def gw_loss(self) -> Optional[float]:
        return self._pct(self.gw)

    @property
    def anchor_loss(self) -> Optional[float]:
        return self._pct(self.anchor)

    def last_ms(self, hop: str) -> Optional[float]:
        """The most recent successful RTT of `hop` still inside its live window (None if none)."""
        return self._last_ms(self.anchor if hop == HOP_ANCHOR else self.gw)


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


def _resolve_ipv4(host: str) -> Optional[str]:
    """A dotted IPv4 for ``host`` (a literal passes straight through), or None if it does not resolve."""
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None


@dataclass(frozen=True)
class BurstResult:
    """One hop's burst: the successful RTTs (ms) and how many echoes it counts as sent."""
    rtts: Tuple[float, ...]
    sent: int

    @property
    def lost(self) -> int:
        return self.sent - len(self.rtts)

    @property
    def ms(self) -> Optional[float]:
        """Median of the replies: stable against one outlier (min would flatter, mean would be dragged)."""
        return float(statistics.median(self.rtts)) if self.rtts else None


def _sleep_pause(seconds: float) -> bool:
    time.sleep(max(0.0, seconds))
    return True


def icmp_burst(host: str, count: int = constants.network.latency.BURST_SIZE,
               spacing_ms: int = constants.network.latency.BURST_SPACING_MS, timeout_ms: int = 1000,
               pause: Callable[[float], bool] = _sleep_pause,
               on_echo: Optional[Callable[[int, Optional[float]], None]] = None) -> Optional[BurstResult]:
    """``count`` echoes to ``host``, ``spacing_ms`` apart, each with its own ``timeout_ms``. ``pause``
    waits between echoes (``pause(0)`` is a bare check) and returns False to abandon the burst (the
    probe is stopping) - then this returns None and nothing is recorded. It is consulted BEFORE the
    DNS resolve and before EVERY echo, the first included, so once stop() is called no further packet
    leaves - in particular none to the public host. The host is resolved once per burst; a name that
    does not resolve counts every echo as lost without sending any (what one failed ping recorded).
    ``on_echo(index, ms_or_None)`` is called after each echo (the connection check's live progress)."""
    if not pause(0):
        return None
    ip = _resolve_ipv4(host)
    if ip is None:
        return BurstResult((), count)
    rtts = []
    for i in range(count):
        if not pause(spacing_ms / 1000.0 if i else 0):
            return None
        ms = icmp_ping_ms(ip, timeout_ms)
        if ms is not None:
            rtts.append(ms)
        if on_echo is not None:
            on_echo(i, ms)
    return BurstResult(tuple(rtts), count)


# The wmi module's (1.5.1, pinned) per-namespace class cache - the attribute _wmi_connection() clears.
# Private: test_wmi_class_cache_attribute_exists_in_the_pinned_wmi fails CI if a bump renames it.
WMI_CLASS_CACHE_ATTR = "_classes_map"
_wmi_cache_missing_logged = False


def preload_wmi() -> Optional[float]:
    """Import the wmi module on the CALLING thread (the GUI thread, at startup); the import time in
    ms, or None when wmi is unavailable. ``import wmi`` itself makes a COM object
    (``wmi.obj = GetObject("winmgmts:")``) that lives as long as the module. Whichever thread imports
    wmi first owns it, and when that is a worker that later calls CoUninitialize (the stats thread,
    the probe, a Check connection's details thread), the object is released at interpreter exit after
    its apartment is gone and pywin32 prints "Win32 exception occurred releasing IUnknown". The GUI
    thread's apartment lasts the whole run. Measured ~11-15 ms once, after the app's own imports."""
    t0 = time.perf_counter()
    try:
        import wmi  # noqa: F401  (already a dependency)
    except Exception:
        logger.debug("wmi not preloaded on the GUI thread.", exc_info=True)
        return None
    return (time.perf_counter() - t0) * 1000.0


@contextlib.contextmanager
def _wmi_connection() -> Iterator[Any]:
    """A ``wmi.WMI()`` connection whose COM proxies are all released by plain reference counting, on
    the calling thread, as soon as the caller drops its results - no garbage collection needed.

    The wmi module caches each class it hands out (``_classes_map``) and every cached class points
    back at its namespace: a reference cycle that holds the SWbemServices / SWbemObject proxies until
    the cyclic GC happens to run - possibly on another thread, or after this thread's
    CoUninitialize. Clearing the cache on the way out breaks that cycle (the only one a query leaves;
    checked live with gc.DEBUG_SAVEALL), so the Check connection's details thread no longer needs a
    whole-heap gc.collect() before CoUninitialize. A failed query (an exception) clears it too."""
    import wmi  # already a dependency
    conn = wmi.WMI()
    try:
        yield conn
    finally:
        global _wmi_cache_missing_logged
        try:
            # vars(): the namespace's __getattr__ forwards unknown names to COM.
            classes = vars(conn).get(WMI_CLASS_CACHE_ATTR)
            if isinstance(classes, dict):
                classes.clear()
            elif not _wmi_cache_missing_logged:
                _wmi_cache_missing_logged = True
                logger.debug("wmi namespace has no %s dict (wmi %s): WMI proxies are left for the "
                             "cyclic GC.", WMI_CLASS_CACHE_ATTR, getattr(sys.modules.get("wmi"),
                                                                       "__version__", "?"))
        except Exception:
            pass


def default_gateway_ip() -> Optional[str]:
    """The default gateway IPv4, via WMI (no admin). None if not found."""
    try:
        with _wmi_connection() as conn:
            for nic in conn.Win32_NetworkAdapterConfiguration(IPEnabled=True):
                gws = getattr(nic, "DefaultIPGateway", None)
                if gws:
                    for g in gws:
                        if g and ":" not in g:   # IPv4 only
                            return str(g)
    except Exception:
        return None
    return None


@dataclass(frozen=True)
class NetworkDetails:
    """The connection the default gateway is on, as the Check connection sheet shows it (#214)."""
    adapter: str = ""
    ipv4: Tuple[str, ...] = ()
    ipv6: Tuple[str, ...] = ()
    gateway: Optional[str] = None
    dns: Tuple[str, ...] = ()


def _strs(values: Any) -> Tuple[str, ...]:
    return tuple(str(v) for v in (values or ()) if v)


def network_details() -> Optional[NetworkDetails]:
    """Adapter, addresses, IPv4 gateway and DNS servers of the adapter that has the default gateway -
    the same adapter, found the same way (the first IP-enabled one with an IPv4 gateway), as
    ``default_gateway_ip``. One read-only WMI query, no admin, nothing sent on the network. Needs COM
    initialised on the calling thread. None when no adapter has an IPv4 gateway (or WMI failed).
    IPv6 link-local addresses (fe80::) are left out: they are not reachable from anywhere else."""
    try:
        with _wmi_connection() as conn:
            for nic in conn.Win32_NetworkAdapterConfiguration(IPEnabled=True):
                gws = [g for g in _strs(getattr(nic, "DefaultIPGateway", None)) if ":" not in g]
                if not gws:
                    continue
                addrs = _strs(getattr(nic, "IPAddress", None))
                return NetworkDetails(
                    adapter=str(getattr(nic, "Description", "") or ""),
                    ipv4=tuple(a for a in addrs if ":" not in a),
                    ipv6=tuple(a for a in addrs if ":" in a and not a.lower().startswith("fe80:")),
                    gateway=gws[0],
                    dns=_strs(getattr(nic, "DNSServerSearchOrder", None)))
    except Exception:
        return None
    return None


class LatencyProbe(QThread):
    """Background latency sampler. Emits one `LatencySample` per cycle."""

    latency_ready = pyqtSignal(object)   # LatencySample

    _CADENCE_SEC = 3.0
    _TIMEOUT_MS = 1000
    _GATEWAY_REFRESH_SEC = 60.0
    # A lost echo from a gateway resolved at least this long ago re-checks which gateway is current
    # (a network switch with no event we hear about); bounded so a real outage costs one WMI query
    # per this many seconds, not one per cycle.
    _GATEWAY_RECHECK_ON_LOSS_SEC = 10.0
    _ERROR_LOG_INTERVAL_SEC = 60.0

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__()
        self._config = config
        self._stop_event = threading.Event()   # set by stop(); every wait in the loop wakes on it
        self._running = True
        self._gateway: Optional[str] = None
        self._gateway_ts = 0.0
        # Cycles since the gateway last answered; None = it has not answered (since it last changed).
        self._gw_cycles_since_reply: Optional[int] = None
        # Set from the GUI thread on resume / a primary-route change: re-resolve before the next burst.
        self._gateway_stale = threading.Event()
        self._gateway_changed = False         # the gateway changed (or was re-resolved) this cycle
        self._errors_logged = 0
        self._last_error_log = 0.0

    @property
    def _running(self) -> bool:
        return not self._stop_event.is_set()

    @_running.setter
    def _running(self, value: bool) -> None:
        if value:
            self._stop_event.clear()
        else:
            self._stop_event.set()

    def stop(self) -> None:
        self._running = False

    def set_config(self, config: Dict[str, Any]) -> None:
        """Point the probe at the widget's current config dict (a settings rollback replaces it)."""
        self._config = config

    def _enabled(self) -> bool:
        """The master switch, read LIVE: Settings > Connection > Latency off ends the probe within the
        echo in flight - same promise as stop() - whatever ``latency_public_enabled`` says."""
        return bool(self._config.get("latency_enabled", True))

    @property
    def _active(self) -> bool:
        """Not stopped AND the master switch is on: the condition every echo is gated on."""
        return self._running and self._enabled()

    @property
    def gateway(self) -> Optional[str]:
        """The default gateway this probe last looked up (None before the first cycle / none found)."""
        return self._gateway

    def _pause(self, seconds: float) -> bool:
        """Wait up to ``seconds``; wakes at once on stop(). Returns False once stopping or once the
        master switch is off (True = go on), so a burst never sends another echo after either."""
        if seconds > 0:
            self._stop_event.wait(seconds)
        return self._active

    def invalidate_gateway(self) -> None:
        """Any thread: the network changed (resume from sleep, a new primary route) - look the default
        gateway up again before the next burst instead of pinging the cached one for up to a minute,
        which recorded the old router's silence as 100% loss."""
        self._gateway_stale.set()

    def _gateway_ip(self, force: bool = False) -> Optional[str]:
        now = time.monotonic()
        if self._gateway_stale.is_set():
            self._gateway_stale.clear()
            force = True
        if force or self._gateway is None or (now - self._gateway_ts) > self._GATEWAY_REFRESH_SEC:
            gw = default_gateway_ip()
            if gw != self._gateway:
                self._gw_cycles_since_reply = None   # a new router has proven nothing yet
            # "Changing" = a DIFFERENT gateway than the one in use (not the probe's very first lookup).
            # A re-resolve that finds the same router changes nothing: its loss is real loss.
            if self._gateway_ts > 0 and gw != self._gateway:
                self._gateway_changed = True
            self._gateway = gw
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

    def _probe_cycle(self) -> Optional[LatencySample]:
        """One cycle: the gateway burst, then - when opted in - the anchor burst. None if stop() cut a
        burst short (a partial burst is not a sample)."""
        lat = constants.network.latency
        self._gateway_changed = False
        gw_excluded = False
        gw_ip = self._gateway_ip()
        if gw_ip:
            gw = icmp_burst(gw_ip, lat.BURST_SIZE, lat.BURST_SPACING_MS, self._TIMEOUT_MS, self._pause)
            if gw is None:
                return None
            if (gw.lost and not self._gateway_changed
                    and time.monotonic() - self._gateway_ts >= self._GATEWAY_RECHECK_ON_LOSS_SEC):
                self._gateway_ip(force=True)   # is the router we pinged still the gateway?
            if gw.lost and self._gateway_changed:
                # Loss measured against a gateway that turned out to be the OLD one (a network switch,
                # waking up) says nothing about the link: the gateway hop is not measured this cycle.
                # The anchor still is. Clean bursts to a new gateway count as usual.
                gw_excluded = True
                gw = BurstResult((), 0)
        else:
            gw = BurstResult((), 0)   # no gateway known: nothing sent, nothing lost (as before bursts)
        gw_timed_out = gw.sent > 0 and not gw.rtts
        if gw.rtts:
            self._gw_cycles_since_reply = 0
        elif self._gw_cycles_since_reply is not None:
            self._gw_cycles_since_reply += 1
        gw_recently_answered = (
            self._gw_cycles_since_reply is not None
            and self._gw_cycles_since_reply <= lat.GATEWAY_REPLY_RECENT_CYCLES)

        anchor: Optional[BurstResult] = None
        anchor_enabled = bool(self._config.get("latency_public_enabled", False))
        anchor_skipped = False
        if anchor_enabled:
            if gw_timed_out and gw_recently_answered:
                # The link is down at the first hop: the anchor cannot answer either. Skip it (probing
                # on only stretches the cycle) and record the whole burst lost - the attribution rule,
                # so anchor loss >= gateway loss. Only for a gateway that has been answering: an
                # ICMP-silent one says nothing about the link.
                anchor_skipped = True
                anchor = BurstResult((), lat.BURST_SIZE)
            else:
                if not self._active:
                    return None   # stopped / switched off after the gateway burst: never start the public one
                host = str(self._config.get("latency_public_host", "1.1.1.1") or "1.1.1.1")
                anchor = icmp_burst(host, lat.BURST_SIZE, lat.BURST_SPACING_MS, self._TIMEOUT_MS,
                                    self._pause)
                if anchor is None:
                    return None

        return LatencySample(
            gw_ms=gw.ms, anchor_ms=anchor.ms if anchor else None, gw_timed_out=gw_timed_out,
            anchor_timed_out=anchor is not None and not anchor.rtts,
            anchor_enabled=anchor_enabled, anchor_skipped=anchor_skipped,
            gw_sent=gw.sent, gw_lost=gw.lost,
            anchor_sent=anchor.sent if anchor else 0, anchor_lost=anchor.lost if anchor else 0,
            gw_excluded=gw_excluded)

    def _log_cycle_error(self, e: BaseException) -> None:
        """A failing cycle is never silent: WARNING (with the traceback) the first time, then DEBUG at
        most once per _ERROR_LOG_INTERVAL_SEC - a recurring failure must not flood the log (#263)."""
        self._errors_logged += 1
        now = time.monotonic()
        if self._errors_logged == 1:
            logger.warning("Latency probe cycle failed; the probe keeps running: %s", e, exc_info=True)
            self._last_error_log = now
        elif now - self._last_error_log >= self._ERROR_LOG_INTERVAL_SEC:
            logger.debug("Latency probe cycle still failing (%d failures so far): %s", self._errors_logged, e)
            self._last_error_log = now

    def _run_loop(self) -> None:
        # Cycle length = max(cadence, time the bursts took): a healthy cycle keeps the 3 s cadence; a
        # dead link (4 gateway timeouts + 3 gaps, anchor skipped) costs one ~4.3 s cycle. Every wait goes
        # through _pause, and stop() is checked before every echo, before the anchor burst and during the
        # cadence wait - the worst case is the one echo in flight (<= the 1 s timeout), inside the
        # widget's 1.5 s shutdown wait, and nothing is sent after stop().
        # Latency switched off in Settings ends the loop (the widget's apply_latency_probe starts a
        # fresh probe when it is switched back on); the check before every echo makes that prompt.
        while self._active:
            started = time.monotonic()
            try:
                sample = self._probe_cycle()
                if sample is not None and self._active:
                    self.latency_ready.emit(sample)
            except Exception as e:
                self._log_cycle_error(e)   # the thread stays alive; the next cycle tries again
            self._pause(self._CADENCE_SEC - (time.monotonic() - started))

