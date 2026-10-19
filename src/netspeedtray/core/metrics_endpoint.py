"""
Local metrics endpoint (2.2 scope B) - Prometheus text exposition at http://127.0.0.1:PORT/metrics.

Opt-in and OFF by default. When on, a small stdlib HTTP server binds 127.0.0.1 ONLY (never 0.0.0.0,
never ::), refuses any peer that is not loopback, refuses a Host header that is not a loopback name
(so a web page cannot read it through DNS rebinding), answers GET /metrics and nothing else, and is
rate- and size-limited. Nothing leaves the machine.

Threading - the plan's "real plumbing" item. ``stats_ready`` is a Qt signal and every value the
endpoint exports lives on Qt-owned objects (the widget, the controller, WidgetState). The server
thread must never touch them. So:

  * the GUI thread builds an immutable ``MetricsSnapshot`` (``core/metrics_snapshot.py``) on every
    stats tick and hands it to ``SnapshotStore.publish`` - a reference swap under a lock;
  * the server thread only ever calls ``SnapshotStore.current()`` and renders that frozen object with
    the pure ``render_metrics`` below.

This module imports no Qt at all, which is what makes that boundary checkable.
"""
from __future__ import annotations

import errno
import io
import ipaddress
import logging
import math
import select
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterable, List, Optional, Tuple

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.MetricsEndpoint")

_M = constants.network.metrics


# --- the snapshot -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Rates:
    """Bytes per second in each direction. ``displayed`` = what the widget shows (after the spike
    filter); ``measured`` = the raw counter delta over the poll (what history stores)."""
    rx: float
    tx: float
    measured_rx: float
    measured_tx: float


@dataclass(frozen=True)
class LatencyHop:
    hop: str                      # "gateway" / "anchor"
    rtt_ms: Optional[float]       # most recent successful RTT in the live window, None = none
    loss_ratio: Optional[float]   # 0-1 over the live (~2 min) window, None = window not full yet


@dataclass(frozen=True)
class MetricsSnapshot:
    """Everything one scrape reports, frozen. Built on the GUI thread; read on the server thread.
    Every optional field left None is simply not exported (never a fake zero)."""
    version: str
    taken_at: Optional[float] = None                 # wall time the GUI thread built it
    interface_mode: str = "auto"
    aggregate: Optional[Rates] = None
    interfaces: Tuple[Tuple[str, Rates], ...] = ()
    period_bytes: Optional[Tuple[float, float]] = None     # (rx, tx) this billing period - odometer
    lifetime_bytes: Optional[Tuple[float, float]] = None   # (rx, tx) since the odometer began
    period_start: Optional[float] = None             # epoch seconds, local midnight of the reset day
    data_cap_bytes: Optional[float] = None
    data_cap_counts: str = "total"
    latency: Tuple[LatencyHop, ...] = ()
    cpu_ratio: Optional[float] = None
    gpu_ratio: Optional[float] = None
    ram_used_bytes: Optional[float] = None
    ram_total_bytes: Optional[float] = None
    vram_used_bytes: Optional[float] = None
    vram_total_bytes: Optional[float] = None


class SnapshotStore:
    """The one hand-off point between the GUI thread (publish) and the server thread (current)."""

    def __init__(self, initial: Optional[MetricsSnapshot] = None) -> None:
        self._lock = threading.Lock()
        self._snapshot = initial or MetricsSnapshot(version=constants.app.VERSION)

    def publish(self, snapshot: MetricsSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot

    def current(self) -> MetricsSnapshot:
        with self._lock:
            return self._snapshot


# --- Prometheus text exposition (format 0.0.4) ----------------------------------------------------
def _escape_label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _escape_help(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("\n", "\\n")


def _fmt(value: float) -> str:
    v = float(value)
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    if v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return repr(v)


def _labels(pairs: Iterable[Tuple[str, str]]) -> str:
    items = [f'{k}="{_escape_label(v)}"' for k, v in pairs]
    return "{" + ",".join(items) + "}" if items else ""


class _Family:
    def __init__(self, name: str, kind: str, help_text: str) -> None:
        self.name, self.kind, self.help = name, kind, help_text
        self.samples: List[Tuple[Tuple[Tuple[str, str], ...], float]] = []

    def add(self, value: Optional[float], **labels: str) -> None:
        if value is None:
            return
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if math.isnan(v):
            return  # "no reading" is an absent series, not a NaN sample
        self.samples.append((tuple(labels.items()), v))

    def render(self) -> List[str]:
        if not self.samples:
            return []
        out = [f"# HELP {self.name} {_escape_help(self.help)}", f"# TYPE {self.name} {self.kind}"]
        out += [f"{self.name}{_labels(lbl)} {_fmt(v)}" for lbl, v in self.samples]
        return out


_P = "netspeedtray_"
_THIS_PC = ("Counted by NetSpeedTray's data-usage odometer on the monitored interfaces of THIS PC only "
            "- not other devices on your network.")


def render_metrics(snap: MetricsSnapshot, scrape_time: Optional[float] = None) -> str:
    """Render a snapshot as Prometheus text exposition. Pure: no I/O, no Qt, no shared state."""
    now = time.time() if scrape_time is None else scrape_time
    fams: List[_Family] = []

    def fam(name: str, kind: str, help_text: str) -> _Family:
        f = _Family(_P + name, kind, help_text)
        fams.append(f)
        return f

    fam("info", "gauge", "NetSpeedTray build information (always 1).").add(1, version=snap.version)
    fam("scrape_timestamp_seconds", "gauge", "Unix time this response was rendered.").add(now)
    fam("snapshot_timestamp_seconds", "gauge",
        "Unix time NetSpeedTray last refreshed these values (stops advancing while monitoring is "
        "paused or the app is not polling).").add(snap.taken_at)

    mode = snap.interface_mode
    agg = snap.aggregate
    rx = fam("receive_bytes_per_second", "gauge",
             "Download rate the widget shows, across the monitored interfaces (after the spike filter).")
    tx = fam("transmit_bytes_per_second", "gauge",
             "Upload rate the widget shows, across the monitored interfaces (after the spike filter).")
    mrx = fam("receive_measured_bytes_per_second", "gauge",
              "Measured download rate across the monitored interfaces (raw counter delta, no spike filter).")
    mtx = fam("transmit_measured_bytes_per_second", "gauge",
              "Measured upload rate across the monitored interfaces (raw counter delta, no spike filter).")
    if agg is not None:
        rx.add(agg.rx, interface_mode=mode)
        tx.add(agg.tx, interface_mode=mode)
        mrx.add(agg.measured_rx, interface_mode=mode)
        mtx.add(agg.measured_tx, interface_mode=mode)

    irx = fam("interface_receive_bytes_per_second", "gauge",
              "Download rate the widget uses, per monitored interface (after the spike filter).")
    itx = fam("interface_transmit_bytes_per_second", "gauge",
              "Upload rate the widget uses, per monitored interface (after the spike filter).")
    imrx = fam("interface_receive_measured_bytes_per_second", "gauge",
               "Measured download rate per monitored interface (raw counter delta, no spike filter).")
    imtx = fam("interface_transmit_measured_bytes_per_second", "gauge",
               "Measured upload rate per monitored interface (raw counter delta, no spike filter).")
    for name, r in snap.interfaces:
        irx.add(r.rx, interface=name)
        itx.add(r.tx, interface=name)
        imrx.add(r.measured_rx, interface=name)
        imtx.add(r.measured_tx, interface=name)

    if snap.period_bytes is not None:
        fam("period_receive_bytes_total", "counter",
            "Bytes downloaded in the current data-usage period (resets on the reset day). " + _THIS_PC
            ).add(snap.period_bytes[0])
        fam("period_transmit_bytes_total", "counter",
            "Bytes uploaded in the current data-usage period (resets on the reset day). " + _THIS_PC
            ).add(snap.period_bytes[1])
    fam("period_start_timestamp_seconds", "gauge",
        "Unix time the current data-usage period began (local midnight of the reset day)."
        ).add(snap.period_start)
    if snap.lifetime_bytes is not None:
        fam("receive_bytes_total", "counter",
            "Bytes downloaded since the odometer began counting. " + _THIS_PC).add(snap.lifetime_bytes[0])
        fam("transmit_bytes_total", "counter",
            "Bytes uploaded since the odometer began counting. " + _THIS_PC).add(snap.lifetime_bytes[1])
    fam("data_cap_bytes", "gauge",
        "The data cap set in NetSpeedTray, in bytes (1 GB = 10^9 bytes); 'counts' says which "
        "direction counts toward it. Absent when no cap is set.").add(snap.data_cap_bytes,
                                                                     counts=snap.data_cap_counts)

    rtt = fam("latency_rtt_seconds", "gauge",
              "Most recent successful round-trip time per hop within the live window. hop=\"gateway\" is "
              "your router; hop=\"anchor\" is the public server, present only when that probe is enabled.")
    loss = fam("packet_loss_ratio", "gauge",
               "Share of probes lost per hop over the live (~2 minute) window, 0-1.")
    for h in snap.latency:
        rtt.add(None if h.rtt_ms is None else h.rtt_ms / 1000.0, hop=h.hop)
        loss.add(h.loss_ratio, hop=h.hop)

    fam("cpu_utilization_ratio", "gauge", "CPU utilization, 0-1 (only while CPU monitoring is on)."
        ).add(snap.cpu_ratio)
    fam("gpu_utilization_ratio", "gauge", "GPU utilization, 0-1 (only while GPU monitoring is on)."
        ).add(snap.gpu_ratio)
    fam("memory_used_bytes", "gauge", "RAM in use (only while RAM monitoring is on).").add(snap.ram_used_bytes)
    fam("memory_total_bytes", "gauge", "Installed RAM (only while RAM monitoring is on)."
        ).add(snap.ram_total_bytes)
    fam("gpu_memory_used_bytes", "gauge", "Video memory in use (only while VRAM monitoring is on)."
        ).add(snap.vram_used_bytes)
    fam("gpu_memory_total_bytes", "gauge", "Total video memory (only while VRAM monitoring is on)."
        ).add(snap.vram_total_bytes)

    lines: List[str] = []
    for f in fams:
        lines += f.render()
    return "\n".join(lines) + "\n"


# --- the server -----------------------------------------------------------------------------------
def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _host_allowed(host_header: Optional[str], port: int) -> bool:
    """The Host header must name a loopback host (optionally with our port). Blocks DNS rebinding."""
    if not host_header:
        return True  # HTTP/1.0 clients may omit it; the peer check already proved it is local
    h = host_header.strip().lower()
    if h.endswith(f":{port}"):
        h = h[: -len(f":{port}")]
    return h in _M.ALLOWED_HOST_NAMES


class _TokenBucket:
    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate, self._burst, self._clock = float(rate), float(burst), clock
        self._tokens = float(burst)
        self._last = clock()
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            now = self._clock()
            self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


class _DeadlineReader(io.RawIOBase):
    """The handler's socket reads, bounded by ONE deadline for the whole request and woken every
    POLL_INTERVAL_SEC to re-check the server's stop flag.

    A plain socket timeout restarts on every byte, so a local client trickling one byte per second
    held a handler (and a concurrency slot) indefinitely, and stop() - which runs on the GUI thread on
    quit / toggle-off / port change - waited for it (security review of #360: 17.5 s with 5 drips).
    On Windows neither shutdown() nor close() from another thread wakes a blocked recv(), so the
    wait must be select() slices, not a blocking read."""

    def __init__(self, sock: socket.socket, deadline: float, stopping: threading.Event) -> None:
        super().__init__()
        self._sock, self._deadline, self._stopping = sock, deadline, stopping

    def readable(self) -> bool:
        return True

    def readinto(self, buf) -> int:
        while True:
            if self._stopping.is_set():
                raise TimeoutError("metrics endpoint stopping")
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("metrics request deadline")
            ready, _, _ = select.select([self._sock], [], [], min(_M.POLL_INTERVAL_SEC, remaining))
            if ready:
                return self._sock.recv_into(buf)


class _Handler(BaseHTTPRequestHandler):
    server_version = "NetSpeedTray"
    sys_version = ""
    protocol_version = "HTTP/1.0"   # one request per connection: nothing lingers
    timeout = _M.REQUEST_TIMEOUT_SEC

    def version_string(self) -> str:
        return self.server_version   # no Python version in the Server header

    def setup(self) -> None:
        super().setup()
        self.raw_requestline = b""
        srv: "_LoopbackHTTPServer" = self.server  # type: ignore[assignment]
        self.rfile.close()   # replace the makefile() reader with the deadline-bounded one
        self.rfile = io.BufferedReader(_DeadlineReader(
            self.connection, time.monotonic() + _M.REQUEST_TIMEOUT_SEC, srv.stopping))

    # BaseHTTPRequestHandler reads up to 64 KB for the request line; refuse anything longer than a
    # real scrape's well before parsing. Every read below goes through _DeadlineReader.
    def handle_one_request(self) -> None:
        try:
            self.raw_requestline = self.rfile.readline(_M.MAX_REQUEST_LINE + 1)
            if len(self.raw_requestline) > _M.MAX_REQUEST_LINE:
                self.requestline = self.request_version = self.command = ""
                self.send_error(414)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            if self.command == "GET":
                self.do_GET()
            elif self.command == "HEAD":
                self._serve(head=True)
            else:
                self.send_response(405)
                self.send_header("Allow", "GET, HEAD")
                self.send_header("Content-Length", "0")
                self.end_headers()
            self.wfile.flush()
        except (socket.timeout, TimeoutError, OSError, ValueError):
            self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._serve(head=False)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def _from_a_web_page(self) -> bool:
        """A browser request made by a web page (fetch / img / form): it carries Origin or a
        Sec-Fetch-Site other than "none" (typed in the address bar). Scrapers send neither."""
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        return bool(self.headers.get("Origin")) or site not in ("", "none")

    def _serve(self, head: bool) -> None:
        srv: "_LoopbackHTTPServer" = self.server  # type: ignore[assignment]
        # Both checks come BEFORE the rate limit, so neither a rebinding page nor a blind no-cors
        # fetch from any web page can spend the bucket a real scraper needs.
        if (not _host_allowed(self.headers.get("Host"), srv.server_address[1])
                or self._from_a_web_page()):
            self._plain(403, "Forbidden\n")
            return
        if self.path.split("?", 1)[0] != _M.PATH:
            self._plain(404, "Not found. Metrics are at /metrics\n")
            return
        if not srv.bucket.take():
            self._plain(429, "Too many requests\n")
            return
        body = render_metrics(srv.store.current()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", _M.CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _plain(self, code: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # route to our logger, quietly
        logger.debug("metrics endpoint: " + fmt, *args)


class _LoopbackHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer pinned to loopback, with exclusive port ownership and a concurrency cap."""
    # HTTPServer sets SO_REUSEADDR, which on Windows lets a second socket bind a port that is already
    # in use (port hijacking both ways). Never: we want a clean "port in use" error instead.
    allow_reuse_address = False
    allow_reuse_port = False
    # Handler threads are daemons and server_close() does NOT join them: stop() runs on the GUI thread,
    # so it joins them itself with a bounded budget (they exit within POLL_INTERVAL_SEC of `stopping`).
    daemon_threads = True
    block_on_close = False

    def __init__(self, port: int, store: SnapshotStore) -> None:
        self.store = store
        self.bucket = _TokenBucket(_M.RATE_LIMIT_PER_SEC, _M.RATE_LIMIT_BURST)
        self._slots = threading.BoundedSemaphore(_M.MAX_CONCURRENT)
        # Set by MetricsEndpoint.stop(): a handler still waiting for a slow / idle client gives up
        # within POLL_INTERVAL_SEC instead of sitting out REQUEST_TIMEOUT_SEC. (On Windows neither
        # shutdown() nor close() from another thread wakes a recv() blocked on a socket - measured.)
        self.stopping = threading.Event()
        self.handler_threads: set = set()
        self._handlers_lock = threading.Lock()
        super().__init__((_M.HOST, int(port)), _Handler)

    def server_bind(self) -> None:
        excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if excl is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, excl, 1)
        super().server_bind()

    def verify_request(self, request, client_address) -> bool:
        # Bound to 127.0.0.1 so only loopback can connect - checked again anyway (defence in depth).
        if not _is_loopback(client_address[0]):
            logger.warning("Metrics endpoint refused a non-loopback peer.")
            return False
        return True

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)   # over the concurrency cap: drop, don't queue
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        me = threading.current_thread()
        with self._handlers_lock:
            self.handler_threads.add(me)
        try:
            request.settimeout(_M.REQUEST_TIMEOUT_SEC)   # bounds writes; reads use _DeadlineReader
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()
            with self._handlers_lock:
                self.handler_threads.discard(me)

    def join_handlers(self, budget: float) -> None:
        end = time.monotonic() + budget
        with self._handlers_lock:
            threads = list(self.handler_threads)
        for t in threads:
            t.join(max(0.0, end - time.monotonic()))

    def handle_error(self, request, client_address) -> None:
        # socketserver's default prints a traceback to stderr; a dropped scrape is not an app error.
        logger.debug("metrics endpoint: a request ended with an error", exc_info=True)


@dataclass(frozen=True)
class EndpointStatus:
    state: str                    # constants.network.metrics.STATE_*
    port: Optional[int] = None
    error: str = ""               # "in_use" / "denied" / free text; "" when fine

    @property
    def url(self) -> Optional[str]:
        return f"http://{_M.HOST}:{self.port}{_M.PATH}" if self.port else None


def bind_error_kind(exc: OSError) -> str:
    """'in_use' for an occupied port, 'denied' for a reserved/forbidden one, else 'other'."""
    win = getattr(exc, "winerror", None)
    if win == 10048 or exc.errno in (errno.EADDRINUSE,):
        return "in_use"
    if win == 10013 or exc.errno in (errno.EACCES, errno.EPERM):
        return "denied"   # e.g. inside a Hyper-V/WSL excluded port range (netsh excludedportrange)
    return "other"


class MetricsEndpoint:
    """Owns the store and the server. All methods are called from the GUI thread."""

    THREAD_NAME = "NetSpeedTray-metrics"

    def __init__(self, store: Optional[SnapshotStore] = None) -> None:
        self.store = store or SnapshotStore()
        self._server: Optional[_LoopbackHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._status = EndpointStatus(_M.STATE_OFF)
        # The last (enabled, port) that failed, so a live-preview tick does not retry and re-log it.
        self._failed: Optional[Tuple[bool, int]] = None

    @property
    def status(self) -> EndpointStatus:
        return self._status

    @property
    def running(self) -> bool:
        return self._server is not None

    def apply(self, enabled: bool, port: int) -> EndpointStatus:
        """Reconcile with the config: start, stop, or restart on a new port. Idempotent."""
        port = int(port)
        if not enabled:
            self.stop()
            self._failed = None
            return self._status
        if self._server is not None and self._server.server_address[1] == port:
            return self._status
        if self._failed == (True, port):
            return self._status
        self.stop()
        return self._start(port)

    def _start(self, port: int) -> EndpointStatus:
        if not (_M.MIN_PORT <= port <= _M.MAX_PORT):
            self._status = EndpointStatus(_M.STATE_ERROR, port, "other")
            self._failed = (True, port)
            return self._status
        try:
            server = _LoopbackHTTPServer(port, self.store)
        except OSError as e:
            kind = bind_error_kind(e)
            logger.warning("Local metrics endpoint could not listen on %s:%d (%s): %s",
                           _M.HOST, port, kind, e)
            self._status = EndpointStatus(_M.STATE_ERROR, port, kind)
            self._failed = (True, port)
            return self._status
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": _M.POLL_INTERVAL_SEC},
                                  name=self.THREAD_NAME, daemon=True)
        thread.start()
        self._server, self._thread = server, thread
        self._failed = None
        self._status = EndpointStatus(_M.STATE_RUNNING, port)
        logger.info("Local metrics endpoint listening on http://%s:%d%s", _M.HOST, port, _M.PATH)
        return self._status

    def stop(self) -> None:
        """Stop serving and join every thread. Safe to call repeatedly."""
        server, thread = self._server, self._thread
        self._server = self._thread = None
        if server is not None:
            try:
                server.stopping.set()    # every handler gives up within POLL_INTERVAL_SEC
                server.shutdown()        # returns once serve_forever has exited
            finally:
                server.server_close()    # closes the listening socket (handlers are joined below)
            if thread is not None:
                thread.join(_M.STOP_JOIN_TIMEOUT_SEC)
            server.join_handlers(_M.HANDLER_JOIN_BUDGET_SEC)
            logger.info("Local metrics endpoint stopped.")
        self._status = EndpointStatus(_M.STATE_OFF)

    def publish(self, snapshot: MetricsSnapshot) -> None:
        self.store.publish(snapshot)
