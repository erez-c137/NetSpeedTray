"""
Data-integrity review C / D (latency probe).

C. The probe cached the default gateway for 60 s and nothing reset it, so after a network switch or a
   resume it kept pinging the OLD router and recorded its silence as 100% loss (~20 cycles, counted
   as a drop). Now: resume and a primary-route change invalidate the cache (re-resolved before the
   next burst); a lost echo from a gateway resolved >= 10 s ago re-checks which gateway is current;
   and a cycle whose gateway changed / was re-resolved records no loss (clean bursts still count).
D. ``except Exception: pass`` around the cycle hid any recurring failure - latency silently dead.
   Now WARNING (with traceback) once, then DEBUG at most once a minute; the thread keeps running.
"""
import logging
import types

import pytest

from netspeedtray.core import controller as ctrl_mod
from netspeedtray.core import latency_probe as LP
from netspeedtray.core.controller import StatsController

OLD_GW, NEW_GW = "192.168.1.1", "10.0.0.1"


class _Net:
    """The fake network: which gateway WMI reports, who answers, a fake monotonic clock."""

    def __init__(self, monkeypatch, gateway=OLD_GW, answering=(OLD_GW,)):
        self.gateway = gateway
        self.answering = set(answering)
        self.t = 1000.0
        self.pinged = []
        self.lookups = 0
        monkeypatch.setattr(LP, "default_gateway_ip", self._lookup)
        monkeypatch.setattr(LP, "icmp_ping_ms", self._ping)
        monkeypatch.setattr(LP.time, "monotonic", lambda: self.t)
        monkeypatch.setattr(LP.LatencyProbe, "_pause", lambda p, s: p._active)

    def _lookup(self):
        self.lookups += 1
        return self.gateway

    def _ping(self, ip, timeout_ms=1000):
        self.pinged.append(ip)
        return 3.0 if ip in self.answering else None


def _cycle(p, net, advance=3.0):
    net.pinged.clear()
    s = p._probe_cycle()
    net.t += advance
    return s


def test_resume_invalidates_the_cached_gateway(monkeypatch):
    net = _Net(monkeypatch)
    p = LP.LatencyProbe({})
    assert _cycle(p, net).gw_lost == 0
    net.gateway, net.answering = NEW_GW, {NEW_GW}      # woke up on another network
    p.invalidate_gateway()                              # what the resume handler calls
    s = _cycle(p, net)
    assert set(net.pinged) == {NEW_GW}, "pinged the cached router after the network changed"
    assert s is not None and s.gw_lost == 0             # a clean burst to the new gateway counts


def test_a_switch_without_an_event_records_no_false_outage(monkeypatch):
    """No resume / route event: the old router goes silent. The first lossy burst re-checks the
    gateway, finds it changed, and records nothing; the next cycle pings the new one. Before: up to
    60 s of 4/4 loss to the old router."""
    net = _Net(monkeypatch)
    p = LP.LatencyProbe({})
    assert _cycle(p, net, advance=LP.LatencyProbe._GATEWAY_RECHECK_ON_LOSS_SEC).gw_lost == 0
    net.gateway, net.answering = NEW_GW, {NEW_GW}
    samples = [_cycle(p, net) for _ in range(5)]
    lossy = [s for s in samples if s.gw_lost]
    assert lossy == [], f"recorded {len(lossy)} cycle(s) of loss from the old router"
    assert samples[0].gw_excluded, "the cycle that pinged the old router does not measure the gateway"
    assert all(not s.gw_excluded and s.gw_sent == 4 and s.gw_lost == 0 for s in samples[1:])


def test_a_real_outage_on_the_same_gateway_is_still_recorded(monkeypatch):
    net = _Net(monkeypatch)
    p = LP.LatencyProbe({})
    _cycle(p, net, advance=LP.LatencyProbe._GATEWAY_RECHECK_ON_LOSS_SEC)
    net.answering = set()                               # same router, now dead
    samples = [_cycle(p, net) for _ in range(5)]
    assert all(s is not None and s.gw_lost == s.gw_sent == 4 for s in samples)
    # The re-check on loss is bounded: one WMI lookup per _GATEWAY_RECHECK_ON_LOSS_SEC, not per cycle.
    assert net.lookups <= 3


def test_an_invalidation_that_finds_the_same_router_records_real_loss(monkeypatch):
    """Review #376 item 4: a route flapping every 2 s during an outage invalidated the gateway each
    time; treating every re-resolve as a change dropped ~3 of 4 cycles - gateway AND anchor loss."""
    net = _Net(monkeypatch, answering=(OLD_GW,))
    p = LP.LatencyProbe({"latency_public_enabled": True, "latency_public_host": "1.1.1.1"})
    _cycle(p, net)
    net.answering = set()                               # the outage: same router, nothing answers
    samples = []
    for _ in range(4):
        p.invalidate_gateway()                          # the route flaps
        samples.append(_cycle(p, net))
    assert all(s is not None and not s.gw_excluded for s in samples)
    assert [s.gw_lost for s in samples] == [4] * 4
    assert [s.anchor_lost for s in samples] == [4] * 4, "anchor loss must always be recorded"


def test_a_gateway_change_still_records_the_anchor(monkeypatch):
    net = _Net(monkeypatch, answering=(OLD_GW, "1.1.1.1"))
    p = LP.LatencyProbe({"latency_public_enabled": True, "latency_public_host": "1.1.1.1"})
    _cycle(p, net, advance=LP.LatencyProbe._GATEWAY_RECHECK_ON_LOSS_SEC)
    net.gateway, net.answering = NEW_GW, {NEW_GW}      # switched; the anchor is unreachable for now
    s = _cycle(p, net)
    assert s.gw_excluded and s.gw_sent == 0 and s.gw_lost == 0
    assert s.anchor_enabled and s.anchor_sent == 4 and s.anchor_lost == 4


def test_an_excluded_gateway_cycle_is_not_persisted_or_windowed():
    w = LP.LatencyLossWindows(min_samples=1)
    w.add(LP.LatencySample(gw_ms=None, anchor_ms=None, gw_timed_out=False, gw_sent=0, gw_lost=0,
                           gw_excluded=True))
    assert len(w.gw) == 0


def test_the_first_lookup_is_not_a_change(monkeypatch):
    """A gateway that never answers ICMP must still record its first cycle (attribution tests)."""
    net = _Net(monkeypatch, answering=())
    p = LP.LatencyProbe({})
    s = _cycle(p, net)
    assert s is not None and s.gw_lost == 4


def test_the_controller_announces_a_primary_route_change(monkeypatch):
    names = iter(["Wi-Fi", "Wi-Fi", "Ethernet"])
    monkeypatch.setattr(ctrl_mod, "get_primary_interface_name", lambda: next(names))
    fired = []
    s = types.SimpleNamespace(primary_interface=None, last_primary_check_time=0.0,
                              logger=logging.getLogger("t"), _last_known_primary=None,
                              _PRIMARY_REFRESH_SEC=StatsController._PRIMARY_REFRESH_SEC,
                              primary_interface_changed=types.SimpleNamespace(emit=lambda: fired.append(1)))
    for _ in range(3):
        StatsController._update_primary_interface_name(s)
        s.last_primary_check_time = -1e9
    assert len(fired) == 2           # None -> Wi-Fi, Wi-Fi -> Ethernet; not the unchanged lookup


def test_the_widget_forwards_route_changes_to_the_probe():
    from netspeedtray.views.widget.main import NetworkSpeedWidget as W
    hits = []
    fake = types.SimpleNamespace(latency_probe=types.SimpleNamespace(invalidate_gateway=lambda: hits.append(1)),
                                 logger=logging.getLogger("t"))
    W.invalidate_latency_gateway(fake)
    fake.latency_probe = None
    W.invalidate_latency_gateway(fake)                 # no probe (Latency off): a no-op
    assert hits == [1]


# --- D: a failing cycle is logged, and the probe lives on ------------------------------------------------

def test_a_failing_cycle_is_logged_once_then_rate_limited_and_the_thread_survives(monkeypatch, caplog):
    clock = [1000.0]
    monkeypatch.setattr(LP.time, "monotonic", lambda: clock[0])
    p = LP.LatencyProbe({})
    calls = []

    def bad_cycle():
        calls.append(1)
        if len(calls) >= 30:
            p.stop()
        raise RuntimeError("WMI went away")

    def pause(s):
        clock[0] += max(0.0, s) + 0.001
        return p._active
    monkeypatch.setattr(p, "_probe_cycle", bad_cycle)
    monkeypatch.setattr(p, "_pause", pause)
    with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.LatencyProbe"):
        p._run_loop()
    assert len(calls) == 30, "the loop must keep cycling after a failure"
    recs = [r for r in caplog.records if r.name == "NetSpeedTray.LatencyProbe"]
    warnings = [r for r in recs if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and warnings[0].exc_info, "first failure: one WARNING with the traceback"
    debugs = [r for r in recs if r.levelno == logging.DEBUG]
    # 30 cycles x 3 s = ~90 s: at most one DEBUG per minute after the warning.
    assert 1 <= len(debugs) <= 2


def test_the_widget_persists_anchor_loss_but_no_gateway_loss_for_an_excluded_cycle():
    from netspeedtray.core.latency_probe import LatencyLossWindows
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    from netspeedtray import constants
    lat = constants.network.latency
    stats = []
    w = types.SimpleNamespace(_latency_loss_windows=LatencyLossWindows(),
                              widget_state=types.SimpleNamespace(add_hardware_stat=lambda k, v: stats.append((k, v))))
    s = LP.LatencySample(gw_ms=None, anchor_ms=None, gw_timed_out=False, anchor_timed_out=True,
                         anchor_enabled=True, gw_sent=0, gw_lost=0, anchor_sent=4, anchor_lost=4,
                         gw_excluded=True)
    NetworkSpeedWidget._on_latency(w, s)
    assert (lat.STAT_ANCHOR_TIMEOUT, 1.0) in stats
    assert not [k for k, _ in stats if k == lat.STAT_GW_TIMEOUT]
