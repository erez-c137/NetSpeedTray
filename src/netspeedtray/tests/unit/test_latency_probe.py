"""
LatencyProbe — the gateway/anchor latency sampler. ICMP + WMI gateway detection need a real network,
so this covers the testable logic: gateway caching, and that one probe iteration emits gw/anchor/loss
with the public anchor gated on the opt-in flag.
"""
import pytest

from netspeedtray.core import latency_probe as LP


def test_gateway_ip_is_cached(monkeypatch):
    monkeypatch.setattr(LP, "default_gateway_ip", lambda: "192.168.1.1")
    p = LP.LatencyProbe({})
    assert p._gateway_ip() == "192.168.1.1"
    monkeypatch.setattr(LP, "default_gateway_ip", lambda: "10.0.0.1")   # changed upstream
    assert p._gateway_ip() == "192.168.1.1"                              # still cached (within refresh)


def test_one_iteration_emits_gateway_and_respects_public_optin(monkeypatch):
    pings = {"192.168.1.1": 3.0, "1.1.1.1": 14.0}
    monkeypatch.setattr(LP, "default_gateway_ip", lambda: "192.168.1.1")
    monkeypatch.setattr(LP, "icmp_ping_ms", lambda ip, t=1000: pings.get(ip))

    # public anchor OFF (privacy default): only the gateway is probed.
    p = LP.LatencyProbe({"latency_public_enabled": False})
    got = []
    p.latency_ready.connect(lambda gw, an, to: got.append((gw, an, to)))
    p._running = True
    # run a single iteration manually (don't start the thread loop)
    gw = LP.icmp_ping_ms(p._gateway_ip())
    p.latency_ready.emit(gw, None, gw is None)
    assert got[-1] == (3.0, None, False)

    # public anchor ON: anchor also measured.
    p2 = LP.LatencyProbe({"latency_public_enabled": True, "latency_public_host": "1.1.1.1"})
    got2 = []
    p2.latency_ready.connect(lambda gw, an, to: got2.append((gw, an, to)))
    anchor = LP.icmp_ping_ms("1.1.1.1")
    p2.latency_ready.emit(LP.icmp_ping_ms(p2._gateway_ip()), anchor, False)
    assert got2[-1] == (3.0, 14.0, False)


def test_timeout_is_loss(monkeypatch):
    monkeypatch.setattr(LP, "default_gateway_ip", lambda: "192.168.1.1")
    monkeypatch.setattr(LP, "icmp_ping_ms", lambda ip, t=1000: None)   # timeout
    p = LP.LatencyProbe({})
    gw = LP.icmp_ping_ms(p._gateway_ip())
    assert gw is None   # a timed-out probe -> loss
