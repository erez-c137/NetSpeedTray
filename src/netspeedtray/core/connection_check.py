"""
ConnectionCheck - the on-demand "Check connection" (2.2 D3): a one-shot report you can paste.

A short QThread, separate from the standing LatencyProbe (which keeps running): CHECK_ECHOES (20)
echoes per hop, CHECK_SPACING_MS (200 ms) apart, each with its own CHECK_TIMEOUT_MS timeout, through
the probe's own `icmp_burst`. The router hop always runs; the internet anchor runs ONLY when the caller
passes a host - `anchor_host_for_check` is the one place that decides it (the public probe is on in
Settings, or the user ticked the one-off box in the sheet). The two hops run side by side, so they see
the same moment of the link, and the check takes ~4 s on a healthy one.

Nothing here is persisted. The pure helpers (`hop_stats`, `check_attribution`) carry the math the sheet
shows and the tests pin: loss, min / median / p95 / max RTT, and jitter = the mean absolute difference
between successive RTTs (RFC 3550 / ping-tool convention) - NOT the standard deviation.

Cancel: stop() wakes every wait, and `icmp_burst` consults the pause before every echo, so after stop()
at most the one echo already in flight per hop completes and no new packet leaves.
"""
from __future__ import annotations

import math
import statistics
import threading
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from PyQt6.QtCore import QThread, pyqtSignal

from netspeedtray import constants
from netspeedtray.core import latency_probe as LP


HOP_GATEWAY = LP.HOP_GATEWAY
HOP_ANCHOR = LP.HOP_ANCHOR

# HopStats.loss_level - keys of constants.network.latency.CHECK_LOSS_COLORS (none has no colour).
LOSS_LEVEL_NONE = "none"
LOSS_LEVEL_SOME = "some"
LOSS_LEVEL_HIGH = "high"


@dataclass(frozen=True)
class HopStats:
    """One hop's figures. RTT fields are ms, None when nothing came back (jitter: fewer than 2 replies)."""
    sent: int
    received: int
    lost: int
    loss_pct: float
    min_ms: Optional[float]
    median_ms: Optional[float]
    p95_ms: Optional[float]
    max_ms: Optional[float]
    jitter_ms: Optional[float]

    @property
    def answered(self) -> bool:
        return self.received > 0

    @property
    def verdict(self) -> Optional[str]:
        """The pill's Good / OK / Slow (latency_probe.latency_verdict) on the median RTT alone - loss is
        reported separately (``loss_level``): over 20 echoes one lost ping is 5%, which the pill's rule
        reads as "Slow" for a 2 ms router. None when no echo came back - the sheet says "No reply"
        instead of passing a judgement on silence."""
        if not self.answered:
            return None
        return LP.latency_verdict(self.median_ms, 0.0)

    @property
    def loss_level(self) -> Optional[str]:
        """LOSS_LEVEL_NONE / _SOME (amber) / _HIGH (red, from CHECK_LOSS_HIGH_MIN_LOST lost echoes);
        None when nothing answered."""
        if not self.answered:
            return None
        if self.lost <= 0:
            return LOSS_LEVEL_NONE
        if self.lost >= constants.network.latency.CHECK_LOSS_HIGH_MIN_LOST:
            return LOSS_LEVEL_HIGH
        return LOSS_LEVEL_SOME


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile (numpy's default, which the Stats-detail sheet uses), q in 0-100."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    pos = (len(vals) - 1) * (float(q) / 100.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def jitter(rtts: Sequence[float]) -> Optional[float]:
    """Mean absolute difference between successive RTTs, in arrival order (RFC 3550's interarrival
    jitter idea, as ping-style tools report it). None with fewer than two replies."""
    if len(rtts) < 2:
        return None
    diffs = [abs(float(b) - float(a)) for a, b in zip(rtts, rtts[1:])]
    return sum(diffs) / len(diffs)


def hop_stats(rtts: Sequence[float], sent: int) -> HopStats:
    """Figures for one hop from its successful RTTs (in arrival order) and how many echoes were sent."""
    sent = max(int(sent), len(rtts))
    received = len(rtts)
    lost = sent - received
    return HopStats(
        sent=sent, received=received, lost=lost,
        loss_pct=(lost / sent * 100.0) if sent else 0.0,
        min_ms=float(min(rtts)) if rtts else None,
        median_ms=float(statistics.median(rtts)) if rtts else None,
        p95_ms=percentile(rtts, 95),
        max_ms=float(max(rtts)) if rtts else None,
        jitter_ms=jitter(rtts))


def check_attribution(gw: HopStats, anchor: Optional[HopStats]) -> str:
    """The D1 rule (latency_probe.attribute_loss) on the check's own two hops. A router that answered
    none of its echoes is never blamed for the loss - many routers simply do not answer pings."""
    return LP.attribute_loss(gw.loss_pct, anchor.loss_pct if anchor is not None else None,
                             gw_answered=gw.answered)


def anchor_host_for_check(config: Mapping[str, Any], include_internet: bool) -> Optional[str]:
    """The public host the check may ping, or None. Only when the public probe is on in Settings, or
    the user ticked the sheet's one-off box (never persisted). This is the ONLY gate."""
    if not (bool(config.get("latency_public_enabled", False)) or bool(include_internet)):
        return None
    return str(config.get("latency_public_host", "1.1.1.1") or "1.1.1.1")


class ConnectionCheck(QThread):
    """One check run. Signals carry plain data; the sheet owns all formatting."""

    gateway_resolved = pyqtSignal(object)   # the router IP (str) or None when none was found
    echo_done = pyqtSignal(str, int, object)   # hop, 1-based echo index, rtt ms or None
    check_finished = pyqtSignal(object)     # {hop: HopStats}; NOT emitted for a cancelled run

    def __init__(self, gateway_ip: Optional[str], anchor_host: Optional[str],
                 count: int = constants.network.latency.CHECK_ECHOES,
                 spacing_ms: int = constants.network.latency.CHECK_SPACING_MS,
                 timeout_ms: int = constants.network.latency.CHECK_TIMEOUT_MS,
                 lookup_gateway: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._gateway_ip = gateway_ip
        self._anchor_host = anchor_host
        self._count = int(count)
        self._spacing_ms = int(spacing_ms)
        self._timeout_ms = int(timeout_ms)
        self._lookup_gateway = lookup_gateway
        self._stop_event = threading.Event()

    @property
    def anchor_host(self) -> Optional[str]:
        return self._anchor_host

    @property
    def cancelled(self) -> bool:
        return self._stop_event.is_set()

    def stop(self) -> None:
        self._stop_event.set()

    def _pause(self, seconds: float) -> bool:
        """icmp_burst's wait: wakes at once on stop(); False once stopping (pause(0) = a bare check)."""
        if seconds > 0:
            self._stop_event.wait(seconds)
        return not self._stop_event.is_set()

    def _resolve_gateway(self) -> Optional[str]:
        if self._gateway_ip or not self._lookup_gateway:
            return self._gateway_ip
        # default_gateway_ip() uses WMI, which needs COM initialised on THIS thread (see LatencyProbe.run).
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pythoncom = None
        try:
            return LP.default_gateway_ip()
        finally:
            try:
                if pythoncom is not None:
                    # Free the WMI proxies while this apartment still exists; left to interpreter exit
                    # they are released after CoUninitialize and pywin32 prints a Win32 exception.
                    import gc
                    gc.collect()
                    pythoncom.CoUninitialize()
            except Exception:
                pass

    def _burst(self, hop: str, host: str, out: Dict[str, Any]) -> None:
        def on_echo(i: int, ms: Optional[float]) -> None:
            if not self._stop_event.is_set():
                self.echo_done.emit(hop, i + 1, ms)
        try:
            out[hop] = LP.icmp_burst(host, self._count, self._spacing_ms, self._timeout_ms,
                                     pause=self._pause, on_echo=on_echo)
        except Exception:
            out[hop] = None

    def run(self) -> None:
        gw_ip = self._resolve_gateway()
        if self._stop_event.is_set():
            return
        self.gateway_resolved.emit(gw_ip)
        results: Dict[str, Any] = {}
        helper = None
        if self._anchor_host:
            # The internet hop on a helper thread, so both hops sample the same moment of the link.
            helper = threading.Thread(target=self._burst, args=(HOP_ANCHOR, self._anchor_host, results),
                                      name="ConnectionCheckAnchor", daemon=True)
            helper.start()
        if gw_ip:
            self._burst(HOP_GATEWAY, gw_ip, results)
        if helper is not None:
            helper.join()
        if self._stop_event.is_set():
            return
        out: Dict[str, HopStats] = {}
        gw = results.get(HOP_GATEWAY)
        out[HOP_GATEWAY] = hop_stats(gw.rtts, gw.sent) if gw is not None else hop_stats((), 0)
        if self._anchor_host:
            an = results.get(HOP_ANCHOR)
            out[HOP_ANCHOR] = (hop_stats(an.rtts, an.sent) if an is not None
                               else hop_stats((), self._count))
        self.check_finished.emit(out)
