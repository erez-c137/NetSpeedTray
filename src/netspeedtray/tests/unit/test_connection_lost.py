"""
A dead link reads as a lost connection (rc.2 VM session, O9). With the uplink cable pulled, Check
connection showed 20 sent / 0 received / 100% and then said "Your router doesn't answer pings - some
never do. That alone is not packet loss.", and the Overview pill kept "Router: Good · 0 ms" for ~2 min
before going blank.

Three cases look alike from a router's zero replies and must read differently:
  * a router that has never answered pings      -> the old "doesn't answer pings" note (unchanged);
  * a router that answered before and now doesn't -> "No connection" + "the connection to it is lost";
  * no link / adapter down / no default gateway  -> "No connection" + "check the cable or Wi-Fi".
"""
from types import SimpleNamespace

import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import connection_check as CC
from netspeedtray.core import latency_probe as LP

GW = "192.168.1.1"
_B = constants.network.latency.BURST_SIZE
SLOW_COLOR = constants.network.latency.VERDICT_COLORS["slow"]


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Nothing here may read the machine's real adapters or WMI."""
    monkeypatch.setattr(LP, "network_details", lambda: None)
    monkeypatch.setattr(LP, "link_state", lambda gw: LP.LINK_UNKNOWN)


# --- the standing probe --------------------------------------------------------------------------------

def _drive(monkeypatch, script, link=lambda gw: LP.LINK_UNKNOWN):
    """Run the REAL _run_loop over ``script``: one (gateway or None, reply ms or None) per cycle. The
    gateway lookup answers from the CURRENT cycle's entry (the probe re-resolves on loss)."""
    got = []
    cur = [0]
    monkeypatch.setattr(LP, "default_gateway_ip", lambda: script[min(cur[0], len(script) - 1)][0])
    monkeypatch.setattr(LP, "icmp_ping_ms", lambda ip, timeout_ms=1000: script[cur[0]][1])
    monkeypatch.setattr(LP, "link_state", link)
    monkeypatch.setattr(LP.LatencyProbe, "_pause", lambda self, s: self._running)
    # Every cycle may re-resolve (the 10 s re-check after a loss would otherwise never come due).
    monkeypatch.setattr(LP.LatencyProbe, "_GATEWAY_RECHECK_ON_LOSS_SEC", 0.0)
    p = LP.LatencyProbe({})

    def on_ready(sample):
        got.append(sample)
        cur[0] += 1
        if cur[0] >= len(script):
            p._running = False

    p.latency_ready.connect(on_ready)
    p._running = True
    p._run_loop()
    return got, p


def test_pulled_cable_is_a_lost_connection_from_the_first_dead_burst(monkeypatch):
    # 3 good cycles, then the cable goes: the next burst is dead and Windows drops the route.
    script = [(GW, 1.0)] * 3 + [(GW, None)] + [(None, None)] * 3
    got, p = _drive(monkeypatch, script)
    assert [s.gw_link_lost for s in got] == [False] * 3 + [True] * 4
    assert p.gateway is None and p.gateway_answered is True


def test_the_connection_comes_back_when_the_router_answers_again(monkeypatch):
    script = [(GW, 1.0)] * 2 + [(GW, None), (None, None), (GW, 1.0)]
    got, p = _drive(monkeypatch, script)
    assert [s.gw_link_lost for s in got] == [False, False, True, True, False]


def test_a_router_that_never_answers_is_never_a_lost_connection(monkeypatch):
    got, p = _drive(monkeypatch, [(GW, None)] * 5)
    assert not any(s.gw_link_lost for s in got)
    assert p.gateway_answered is False


def test_a_silent_router_whose_adapter_has_no_link_is_a_lost_connection(monkeypatch):
    got, _ = _drive(monkeypatch, [(GW, None)] * 3, link=lambda gw: LP.LINK_DOWN)
    assert all(s.gw_link_lost for s in got)


def test_an_answering_internet_hop_is_never_a_lost_connection(monkeypatch):
    """Public probe on, silent router, adapter state claims down: 1.1.1.1 answered, so it is not."""
    got = []
    monkeypatch.setattr(LP, "default_gateway_ip", lambda: GW)
    monkeypatch.setattr(LP, "icmp_ping_ms", lambda ip, timeout_ms=1000: None if ip == GW else 9.0)
    monkeypatch.setattr(LP, "link_state", lambda gw: LP.LINK_DOWN)
    monkeypatch.setattr(LP.LatencyProbe, "_pause", lambda self, s: self._running)
    p = LP.LatencyProbe({"latency_public_enabled": True, "latency_public_host": "1.1.1.1"})
    sample = p._probe_cycle()
    assert sample.anchor_ms == 9.0 and sample.gw_link_lost is False


def test_no_gateway_from_the_start(monkeypatch):
    got, _ = _drive(monkeypatch, [(None, None)] * 3)
    assert not any(s.gw_link_lost for s in got)          # can't tell: say nothing, as before
    got, _ = _drive(monkeypatch, [(None, None)] * 3, link=lambda gw: LP.LINK_DOWN)
    assert all(s.gw_link_lost for s in got)              # no adapter has a link at all


def test_switching_to_a_silent_router_is_not_a_lost_connection(monkeypatch):
    """A new gateway has proven nothing: an ICMP-silent one must not inherit the old one's record."""
    script = [(GW, 1.0)] * 2 + [("10.0.0.1", None)] * 4
    got, p = _drive(monkeypatch, script)
    assert not any(s.gw_link_lost for s in got)
    assert p.gateway == "10.0.0.1" and p.gateway_answered is False


# --- link_state (psutil adapter state; nothing is sent) ----------------------------------------------

def _fake_psutil(monkeypatch, ifaces):
    """ifaces: {name: (isup, [(ip, netmask)])}"""
    import socket
    import psutil
    monkeypatch.setattr(psutil, "net_if_stats",
                        lambda: {n: SimpleNamespace(isup=up) for n, (up, _) in ifaces.items()})
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {
        n: [SimpleNamespace(family=socket.AF_INET, address=a, netmask=m) for a, m in addrs]
        for n, (_, addrs) in ifaces.items()})


@pytest.mark.parametrize("ifaces, gw, want", [
    ({"Ethernet": (False, [("192.168.1.20", "255.255.255.0")]),
      "vEthernet (Default Switch)": (True, [("172.20.0.1", "255.255.240.0")])}, GW, LP.LINK_DOWN),
    ({"Ethernet": (True, [("192.168.1.20", "255.255.255.0")])}, GW, LP.LINK_UP),
    ({"PPPoE": (True, [("100.64.3.4", "255.255.255.255")])}, GW, LP.LINK_UNKNOWN),   # off-subnet gw
    ({"Ethernet": (False, []), "Loopback Pseudo-Interface 1": (True, [("127.0.0.1", "255.0.0.0")])},
     None, LP.LINK_DOWN),
    ({"Ethernet": (False, []), "VirtualBox Host-Only": (True, [("192.168.56.1", "255.255.255.0")])},
     None, LP.LINK_UNKNOWN),
])
def test_link_state(monkeypatch, ifaces, gw, want):
    monkeypatch.undo()   # drop the autouse stub: this one tests the real function
    _fake_psutil(monkeypatch, ifaces)
    assert LP.link_state(gw) == want


def test_link_state_never_raises(monkeypatch):
    monkeypatch.undo()
    import psutil
    monkeypatch.setattr(psutil, "net_if_stats", lambda: 1 / 0)
    assert LP.link_state(GW) == LP.LINK_UNKNOWN


# --- the Overview pill ----------------------------------------------------------------------------------

def test_the_pill_says_no_connection_at_once_not_the_windows_stale_good(q_app):
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    mw = SimpleNamespace(_latency_loss_windows=LP.LatencyLossWindows(), widget_state=None)
    good = LP.LatencySample(gw_ms=0.4, anchor_ms=None, gw_timed_out=False, gw_sent=_B, gw_lost=0)
    for _ in range(10):
        NetworkSpeedWidget._on_latency(mw, good)
    assert "Router: Good" in ov._latency_html(mw)
    dead = LP.LatencySample(gw_ms=None, anchor_ms=None, gw_timed_out=True, gw_sent=_B, gw_lost=_B,
                            gw_link_lost=True)
    NetworkSpeedWidget._on_latency(mw, dead)
    html = ov._latency_html(mw)
    assert "Router: No connection" in html and SLOW_COLOR in html
    assert "Good" not in html and "ms" not in html
    gone = LP.LatencySample(gw_ms=None, anchor_ms=None, gw_timed_out=False, gw_sent=0, gw_lost=0,
                            gw_link_lost=True)   # the route is gone: nothing even sent
    for _ in range(60):                          # well past the ~2 min live window
        NetworkSpeedWidget._on_latency(mw, gone)
    assert "Router: No connection" in ov._latency_html(mw)
    NetworkSpeedWidget._on_latency(mw, good)     # back
    assert "Router: Good" in ov._latency_html(mw)


def test_the_pill_ignores_a_non_bool_flag(q_app):
    """A stand-in main widget (MagicMock) must not read as a lost connection."""
    from unittest.mock import MagicMock
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    mw = SimpleNamespace(latency_gw=12.0, latency_anchor=None, latency_loss=0.0,
                         latency_link_lost=MagicMock())
    assert "Good" in ov._latency_html(mw)


# --- the Check connection sheet -------------------------------------------------------------------------

def _st(received, sent=20, ms=5.0):
    return CC.hop_stats([ms] * received, sent)


def _sheet(q_app, gw_stats, gw_ip=GW, answered=False, link=LP.LINK_UNKNOWN):
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    sheet = ConnectionCheckSheet({}, I18nStrings("en_US"), gateway_ip=gw_ip, autostart=False,
                                 gateway_answered=answered)
    sheet._echoes = {CC.HOP_GATEWAY: []}
    sheet._resolved_gw = gw_ip
    sheet._state = "running"
    sheet._on_link(link)
    sheet._on_finished({CC.HOP_GATEWAY: gw_stats})
    return sheet


def test_connection_lost_reasons():
    dead, live = _st(0), _st(20)
    assert CC.connection_lost(dead, True, LP.LINK_UNKNOWN, answered_before=False) is None
    assert CC.connection_lost(dead, True, LP.LINK_UNKNOWN, answered_before=True) == CC.LOST_ROUTER
    assert CC.connection_lost(dead, True, LP.LINK_DOWN, answered_before=False) == CC.LOST_NO_LINK
    assert CC.connection_lost(_st(0, sent=0), False, LP.LINK_DOWN, False) == CC.LOST_NO_LINK
    assert CC.connection_lost(_st(0, sent=0), False, LP.LINK_UNKNOWN, True) == CC.LOST_ROUTER
    assert CC.connection_lost(_st(0, sent=0), False, LP.LINK_UNKNOWN, False) is None
    assert CC.connection_lost(live, True, LP.LINK_DOWN, True) is None   # replies beat adapter state
    # The internet answered: the router merely stopped answering ICMP - the connection works.
    assert CC.connection_lost(dead, True, LP.LINK_UNKNOWN, True, anchor=live) is None
    assert CC.connection_lost(dead, True, LP.LINK_DOWN, True, anchor=live) is None
    assert CC.connection_lost(dead, True, LP.LINK_UNKNOWN, True, anchor=_st(0)) == CC.LOST_ROUTER
    assert LP.attribute_loss(100.0, None, gw_answered=False, connection_lost=True) == LP.LOSS_NO_CONNECTION


def test_sheet_router_that_answered_before_reads_as_a_lost_connection(q_app):
    sheet = _sheet(q_app, _st(0), answered=True)
    card = sheet._cards[CC.HOP_GATEWAY]
    assert "No connection" in card.status.text() and SLOW_COLOR in card.status.text()
    assert "No reply" not in card.status.text()
    assert card.note.isVisibleTo(sheet) and "connection to it is lost" in card.note.text()
    assert "doesn't answer pings" not in card.note.text()
    assert sheet.attribution_code() == LP.LOSS_NO_CONNECTION
    i18n = I18nStrings("en_US")
    assert sheet._sentence.text() == i18n.CHECK_CONN_VERDICT_NO_CONNECTION
    assert "not packet loss" not in sheet._sentence.text()
    text = sheet.copy_text()
    assert "[No connection]" in text and i18n.CHECK_CONN_ROUTER_LOST in text
    assert "No reply" not in text


def test_sheet_no_link_says_check_the_cable(q_app):
    sheet = _sheet(q_app, _st(0), link=LP.LINK_DOWN)
    card = sheet._cards[CC.HOP_GATEWAY]
    assert "No connection" in card.status.text()
    assert "cable or Wi-Fi" in card.note.text() and card.note.isVisibleTo(sheet)
    assert sheet.attribution_code() == LP.LOSS_NO_CONNECTION


def test_sheet_no_router_and_no_link(q_app):
    sheet = _sheet(q_app, _st(0, sent=0), gw_ip=None, link=LP.LINK_DOWN)
    card = sheet._cards[CC.HOP_GATEWAY]
    assert "No connection" in card.status.text() and "No router found" not in card.status.text()
    assert "Router: No connection" in sheet.copy_text()


def test_sheet_never_answering_router_keeps_the_old_explanation(q_app):
    sheet = _sheet(q_app, _st(0))
    card = sheet._cards[CC.HOP_GATEWAY]
    assert "No reply" in card.status.text()
    assert "doesn't answer pings" in card.note.text()
    assert sheet.attribution_code() == LP.LOSS_UNKNOWN


def test_a_different_router_than_the_probe_saw_answer_proves_nothing(q_app):
    """The probe's record belongs to ITS router. Opened while the probe had no route (the old router,
    which was answering, had vanished), the check finds a NEW router that never answers ICMP: that
    is the old "doesn't answer pings" note, not "your router was answering a moment ago"."""
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    sheet = ConnectionCheckSheet({}, I18nStrings("en_US"), gateway_ip=None, autostart=False,
                                 gateway_answered=True)
    sheet._echoes = {CC.HOP_GATEWAY: []}
    sheet._resolved_gw = "10.0.0.1"          # the check looked the router up itself
    sheet._state = "running"
    sheet._on_link(LP.LINK_UNKNOWN)
    sheet._on_finished({CC.HOP_GATEWAY: _st(0)})
    assert sheet.lost_reason() is None
    assert sheet.attribution_code() == LP.LOSS_UNKNOWN
    # The same router the probe saw answering keeps the PR's behaviour.
    assert _sheet(q_app, _st(0), answered=True).lost_reason() == CC.LOST_ROUTER


def test_the_worker_reports_the_link_after_the_bursts(q_app, monkeypatch):
    """End to end through the real worker: a dead cable (0 replies, adapter down)."""
    from PyQt6.QtWidgets import QApplication
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    monkeypatch.setattr(LP, "icmp_ping_ms", lambda ip, timeout_ms=1000: None)
    monkeypatch.setattr(LP, "link_state", lambda gw: LP.LINK_DOWN if gw == GW else LP.LINK_UNKNOWN)
    real_pause = CC.ConnectionCheck._pause
    monkeypatch.setattr(CC.ConnectionCheck, "_pause", lambda self, s: real_pause(self, 0))
    sheet = ConnectionCheckSheet({}, I18nStrings("en_US"), gateway_ip=GW, autostart=False)
    sheet.start_check()
    assert sheet._worker.wait(5000)
    QApplication.processEvents()
    assert sheet._state == "done"
    assert sheet.lost_reason() == CC.LOST_NO_LINK
    assert "No connection" in sheet._cards[CC.HOP_GATEWAY].status.text()


def test_open_connection_check_passes_what_the_standing_probe_saw(q_app, monkeypatch):
    from netspeedtray.views.monitor import connection_check as view
    seen = {}

    class _Sheet:
        def __init__(self, config, i18n, **kw):
            seen.update(kw)

        def exec(self):
            return 0

    monkeypatch.setattr(view, "ConnectionCheckSheet", _Sheet)
    probe = SimpleNamespace(gateway=GW, gateway_answered=True)
    view.open_connection_check(SimpleNamespace(latency_probe=probe), {}, I18nStrings("en_US"))
    assert seen["gateway_ip"] == GW and seen["gateway_answered"] is True
