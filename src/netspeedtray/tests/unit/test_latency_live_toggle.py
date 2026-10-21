"""
Privacy review 2.2, finding 2: Settings > Connection > Latency must take effect LIVE.

It used to start/skip the probe only at launch (main.py) and the probe never re-read
``latency_enabled``, so turning Latency off kept pinging the router - and the public host, if that
was on - until the next restart. Pinned here:
  * the probe reads the master switch before every echo: off mid-burst -> no further echo, the
    partial burst is not emitted, and the thread ends within the one echo in flight (D2's stop);
  * off between the gateway and the public burst -> nothing goes to the public host, whatever
    ``latency_public_enabled`` says (the stored value is kept, it just does nothing while off);
  * the widget's apply_latency_probe (run from every settings apply) stops the probe and clears
    the live figures when switched off, and starts a fresh one - pings resume - when switched on.
"""
import logging
import threading
import time

import pytest
from PyQt6.QtCore import Qt

from netspeedtray import constants
from netspeedtray.core import latency_probe as LP

GW_IP = "192.168.1.1"
ANCHOR = "1.1.1.1"
LAT = constants.network.latency


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _PingLog:
    """A fake icmp_ping_ms: records (monotonic start, ip) and sleeps ``echo`` seconds per echo."""

    def __init__(self, echo=0.05, reply=3.0):
        self.lock = threading.Lock()
        self.log = []
        self.echo = echo
        self.reply = reply

    def __call__(self, ip, timeout_ms=1000):
        with self.lock:
            self.log.append((time.monotonic(), ip))
        time.sleep(self.echo)
        return self.reply

    def after(self, t):
        with self.lock:
            return [ip for ts, ip in self.log if ts >= t]

    def count(self):
        with self.lock:
            return len(self.log)


@pytest.fixture
def pings(monkeypatch):
    log = _PingLog()
    monkeypatch.setattr(LP, "default_gateway_ip", lambda: GW_IP)
    monkeypatch.setattr(LP, "icmp_ping_ms", log)
    return log


def _wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def test_switching_latency_off_mid_burst_sends_no_further_echo(pings, q_app):
    pings.echo = 0.2
    cfg = {"latency_enabled": True, "latency_public_enabled": True, "latency_public_host": ANCHOR}
    p = LP.LatencyProbe(cfg)
    emitted = []
    p.latency_ready.connect(emitted.append, type=Qt.ConnectionType.DirectConnection)
    p.start()
    try:
        assert _wait_for(lambda: pings.count() >= 2)        # inside the gateway burst
        t_off = time.monotonic()
        cfg["latency_enabled"] = False                     # what ConfigController.update_config does
        assert p.wait(3000), "the probe must end once Latency is switched off"
        joined = time.monotonic() - t_off
        assert pings.after(t_off) == [], f"echo sent after Latency was switched off: {pings.after(t_off)}"
        # On its own (no stop()) the thread notices at the end of its cadence wait; the widget's
        # apply_latency_probe also calls stop(), which ends it at once (test below). What matters for
        # privacy is the line above: not one more echo.
        assert joined < LP.LatencyProbe._CADENCE_SEC + pings.echo + 0.5, f"took {joined:.2f} s"
        assert not emitted, "a burst cut short by the switch must not be emitted as a sample"
        assert cfg["latency_public_enabled"] is True, "the stored public choice is kept"
    finally:
        p.stop()
        p.wait(3000)


def test_switching_off_between_bursts_never_reaches_the_public_host(pings, q_app):
    """The switch flips during the gateway burst's LAST echo: the public burst must not start."""
    cfg = {"latency_enabled": True, "latency_public_enabled": True, "latency_public_host": ANCHOR}
    in_last = threading.Event()
    real = LP.icmp_ping_ms

    def ping(ip, timeout_ms=1000):
        if ip == GW_IP and sum(1 for _, i in pings.log if i == GW_IP) == LAT.BURST_SIZE - 1:
            in_last.set()
            pings.log.append((time.monotonic(), ip))
            time.sleep(0.3)
            return 3.0
        return real(ip, timeout_ms)

    LP.icmp_ping_ms = ping
    try:
        p = LP.LatencyProbe(cfg)
        p.start()
        assert in_last.wait(3.0)
        t_off = time.monotonic()
        cfg["latency_enabled"] = False
        assert p.wait(3000)
        assert ANCHOR not in pings.after(t_off), "public host pinged after Latency was switched off"
        assert ANCHOR not in [ip for _, ip in pings.log], "the public burst must never have started"
    finally:
        LP.icmp_ping_ms = real
        p.stop()
        p.wait(3000)


def test_probe_started_with_latency_off_sends_nothing(pings, q_app):
    p = LP.LatencyProbe({"latency_enabled": False, "latency_public_enabled": True})
    p.start()
    assert p.wait(2000)
    assert pings.count() == 0


# --- the widget follows the switch -----------------------------------------------------------------------

class _Widget:
    """Just enough of NetworkSpeedWidget to run its real latency-switch methods."""

    def __init__(self, config):
        from netspeedtray.views.widget.main import NetworkSpeedWidget as W
        self.config = config
        self.logger = logging.getLogger("test")
        self.latency_probe = None
        self.samples = []
        self.latency_gw = 3.0
        self.latency_loss = 1.5
        self._apply = W.apply_latency_probe.__get__(self)
        self._retire_latency_probe = W._retire_latency_probe.__get__(self)
        self._clear_live_latency = W._clear_live_latency.__get__(self)

    def _on_latency(self, sample):
        self.samples.append(sample)

    def apply(self):
        self._apply()


def test_widget_stops_the_probe_when_switched_off_and_resumes_when_on(pings, q_app):
    cfg = {"latency_enabled": True, "latency_public_enabled": False}
    w = _Widget(cfg)
    w.apply()                                          # startup
    first = w.latency_probe
    assert first is not None and _wait_for(lambda: pings.count() >= 1)

    cfg["latency_enabled"] = False
    t_apply = time.monotonic()
    w.apply()                                          # the settings apply
    assert w.latency_probe is None
    assert first.wait(2000)
    assert time.monotonic() - t_apply < pings.echo + 0.35, "stop() must end the probe within one echo"
    t_off = time.monotonic()
    time.sleep(0.2)
    assert pings.after(t_off) == []
    assert w.latency_gw is None and w.latency_loss is None, "no stale reading after switching off"

    cfg["latency_enabled"] = True
    w.apply()
    second = w.latency_probe
    assert second is not None and second is not first and second.isRunning()
    t_on = time.monotonic()
    assert _wait_for(lambda: GW_IP in pings.after(t_on)), "pings must resume when switched back on"

    w.apply()                                          # an unrelated apply: same probe, no restart
    assert w.latency_probe is second
    second.stop()
    assert second.wait(2000)


def test_widget_never_starts_a_probe_while_off(pings, q_app):
    w = _Widget({"latency_enabled": False})
    w.apply()
    assert w.latency_probe is None
    time.sleep(0.1)
    assert pings.count() == 0
