"""
2.2 scope D1 - internet loss is counted. The public anchor's timeouts become their own persisted series
(`latency_anchor_timeout`), the widget keeps two independent live loss windows, the headline loss
follows the hop the pill headlines, the exporter fills `loss_pct` for `latency_anchor`, and the loss
recipe in DATABASE.md runs against a database written by the app's own write path.

The attribution rule (gateway loss = Wi-Fi/LAN; anchor loss with a clean gateway = beyond the router;
a gateway timeout skips the anchor probe and records it lost) lives in `latency_probe` and is asserted
here once; D3 reuses it.
"""
import csv
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from netspeedtray import constants
from netspeedtray.core import latency_probe as LP


GW_IP = "192.168.1.1"
ANCHOR = "1.1.1.1"
_B = constants.network.latency.BURST_SIZE   # echoes per hop per cycle (2.2 D2)


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _cycles(monkeypatch, config, reply, n, gateway=GW_IP):
    """Drive the REAL _run_loop for `n` cycles. `reply(ip, cycle)` -> ms or None (timeout).
    Returns (the emitted samples, per-cycle lists of the IPs actually pinged)."""
    pinged = [[]]
    got = []

    def fake_ping(ip, timeout_ms=1000):
        pinged[-1].append(ip)
        return reply(ip, len(got))

    monkeypatch.setattr(LP, "default_gateway_ip", lambda: gateway)
    monkeypatch.setattr(LP, "icmp_ping_ms", fake_ping)
    monkeypatch.setattr(LP.time, "sleep", lambda s: None)
    monkeypatch.setattr(LP.LatencyProbe, "_pause", lambda self, s: self._running)   # no real waits
    p = LP.LatencyProbe(config)

    def on_ready(sample):
        got.append(sample)
        if len(got) >= n:
            p._running = False
        else:
            pinged.append([])

    p.latency_ready.connect(on_ready)
    p._running = True
    p._run_loop()
    assert len(got) == n
    return got, pinged


def _one_cycle(monkeypatch, config, replies, gateway=GW_IP):
    """One cycle; `replies` maps ip -> ms (None = timeout)."""
    got, pinged = _cycles(monkeypatch, config, lambda ip, c: replies.get(ip), 1, gateway)
    return got[0], pinged[0]


_PUBLIC_ON = {"latency_public_enabled": True, "latency_public_host": ANCHOR}


# --- the probe ---------------------------------------------------------------------------------------

def test_probe_emits_the_anchor_timeout_flag(monkeypatch):
    s, pinged = _one_cycle(monkeypatch, _PUBLIC_ON, {GW_IP: 3.0, ANCHOR: None})
    assert isinstance(s, LP.LatencySample)
    assert (s.gw_ms, s.gw_timed_out) == (3.0, False)
    assert s.anchor_enabled is True
    assert s.anchor_ms is None and s.anchor_timed_out is True and s.anchor_skipped is False
    assert pinged == [GW_IP] * _B + [ANCHOR] * _B


def test_probe_clean_anchor_is_not_a_timeout(monkeypatch):
    s, _ = _one_cycle(monkeypatch, _PUBLIC_ON, {GW_IP: 3.0, ANCHOR: 14.0})
    assert (s.anchor_ms, s.anchor_timed_out) == (14.0, False)


def test_gateway_timeout_skips_the_anchor_and_records_it_lost(monkeypatch):
    # The router answered on cycle 0, then drops: the link is down, the anchor is skipped + lost.
    got, pinged = _cycles(monkeypatch, _PUBLIC_ON,
                          lambda ip, c: 14.0 if ip == ANCHOR else (3.0 if c == 0 else None), 2)
    assert pinged[1] == [GW_IP] * _B, "the anchor must not be probed when a responsive gateway goes down"
    s = got[1]
    assert s.gw_timed_out is True
    assert s.anchor_skipped is True and s.anchor_timed_out is True and s.anchor_ms is None


def test_silent_gateway_never_skips_the_anchor(monkeypatch):
    """A gateway that never answers ICMP (firewalled CPE, some mesh systems, a full-tunnel VPN's
    virtual gateway) must not pin internet loss at 100%: the anchor is probed normally every cycle."""
    got, pinged = _cycles(monkeypatch, _PUBLIC_ON,
                          lambda ip, c: (None if c in (2, 5) else 14.0) if ip == ANCHOR else None, 8)
    assert all(p == [GW_IP] * _B + [ANCHOR] * _B for p in pinged), pinged
    assert not any(s.anchor_skipped for s in got)
    assert [s.anchor_ms for s in got] == [14.0, 14.0, None, 14.0, 14.0, None, 14.0, 14.0]

    w = _fake_widget()
    _feed(w, got)
    assert [v for k, v in w.widget_state.stats if k == "latency_anchor"] == [14.0] * 6
    assert w.latency_loss_anchor == 25.0, "anchor loss reflects only the anchor's own timeouts"


def test_skip_stops_once_the_gateway_has_been_silent_a_whole_window(monkeypatch):
    """The skip needs a RECENT gateway reply: a router that answered once and then went ICMP-silent
    (a VPN came up) must not suppress the anchor forever."""
    n = constants.network.latency.GATEWAY_REPLY_RECENT_CYCLES
    total = n + 3
    got, pinged = _cycles(monkeypatch, _PUBLIC_ON,
                          lambda ip, c: 14.0 if ip == ANCHOR else (3.0 if c == 0 else None), total)
    skipped = [s.anchor_skipped for s in got]
    assert skipped[1:n + 1] == [True] * n            # within the window after the last reply: skipped
    assert skipped[n + 1:] == [False] * (total - n - 1)
    assert pinged[-1] == [GW_IP] * _B + [ANCHOR] * _B and got[-1].anchor_ms == 14.0


def test_unknown_gateway_still_probes_the_anchor(monkeypatch):
    s, pinged = _one_cycle(monkeypatch, _PUBLIC_ON, {ANCHOR: 14.0}, gateway=None)
    assert pinged == [ANCHOR] * _B
    assert s.gw_timed_out is False and s.anchor_ms == 14.0 and s.anchor_timed_out is False


def test_public_probe_off_never_touches_the_anchor(monkeypatch):
    s, pinged = _one_cycle(monkeypatch, {"latency_public_enabled": False}, {GW_IP: None, ANCHOR: 14.0})
    assert pinged == [GW_IP] * _B
    assert s.anchor_enabled is False and s.anchor_timed_out is False and s.anchor_skipped is False


# --- the attribution rule (D3 reuses it) -------------------------------------------------------------

def test_attribution_rule():
    assert LP.attribute_loss(0.0, 0.0) == LP.LOSS_NONE
    assert LP.attribute_loss(0.0, None) == LP.LOSS_NONE
    assert LP.attribute_loss(0.0, 12.5) == LP.LOSS_BEYOND_ROUTER
    assert LP.attribute_loss(10.0, 10.0) == LP.LOSS_LOCAL
    assert LP.attribute_loss(10.0, None) == LP.LOSS_LOCAL
    assert LP.attribute_loss(10.0, 30.0) == LP.LOSS_LOCAL_AND_BEYOND


def test_headline_hop_rule():
    assert LP.headline_hop(True) == LP.HOP_ANCHOR
    assert LP.headline_hop(False) == LP.HOP_GATEWAY


# --- the widget's live windows + persistence ----------------------------------------------------------

class _RecWS:
    def __init__(self):
        self.stats = []

    def add_hardware_stat(self, stat, value):
        self.stats.append((stat, value))


def _fake_widget():
    return SimpleNamespace(latency_gw=None, latency_anchor=None, latency_loss=None,
                           latency_loss_gw=None, latency_loss_anchor=None, latency_headline_hop=None,
                           latency_headline_ms=None,
                           _latency_loss_windows=LP.LatencyLossWindows(),
                           widget_state=_RecWS())


def _feed(w, samples):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    for s in samples:
        NetworkSpeedWidget._on_latency(w, s)


def _s(gw_ms, anchor_ms, *, public=True, skipped=False):
    gw_to = gw_ms is None
    return LP.LatencySample(gw_ms=gw_ms, anchor_ms=anchor_ms, gw_timed_out=gw_to,
                            anchor_timed_out=public and anchor_ms is None,
                            anchor_enabled=public, anchor_skipped=skipped)


def test_two_live_windows_are_independent():
    w = _fake_widget()
    # 5 cycles: the router answers every time, the anchor loses 2 of 5 (an ISP problem).
    _feed(w, [_s(3.0, 14.0), _s(3.0, None), _s(3.0, None), _s(3.0, 15.0), _s(3.0, 16.0)])
    assert w.latency_loss_gw == 0.0
    assert w.latency_loss_anchor == 40.0
    assert w.latency_loss == 40.0, "with the public probe on, the headline loss is the internet's"

    # Then a Wi-Fi drop: gateway lost -> anchor skipped and counted lost too.
    _feed(w, [_s(None, None, skipped=True)])
    assert w.latency_loss_gw == pytest.approx(16.7)
    assert w.latency_loss_anchor == 50.0


def test_headline_loss_is_the_gateway_when_public_probe_is_off():
    w = _fake_widget()
    _feed(w, [_s(3.0, None, public=False)] * 3 + [_s(None, None, public=False)] * 2)
    assert w.latency_loss_gw == 40.0
    assert w.latency_loss_anchor is None
    assert w.latency_loss == 40.0


def test_no_loss_figure_until_a_window_has_enough_samples():
    """Right after the public probe is switched on, one timeout must not read as 100% loss."""
    need = constants.network.latency.LOSS_MIN_SAMPLES
    w = _fake_widget()
    _feed(w, [_s(3.0, 14.0, public=False)] * 10)
    _feed(w, [_s(3.0, None)])                                    # public just turned on, first ping lost
    assert w.latency_loss_anchor is None and w.latency_loss is None
    _feed(w, [_s(3.0, 14.0)] * (need - 2))
    assert w.latency_loss_anchor is None
    _feed(w, [_s(3.0, 14.0)])
    assert w.latency_loss_anchor == pytest.approx(100.0 / need, abs=0.1)


def test_anchor_timeouts_are_persisted_as_their_own_series():
    w = _fake_widget()
    _feed(w, [_s(3.0, 14.0), _s(3.0, None), _s(None, None, skipped=True)])
    anchor_to = [v for k, v in w.widget_state.stats if k == "latency_anchor_timeout"]
    gw_to = [v for k, v in w.widget_state.stats if k == "latency_gw_timeout"]
    assert anchor_to == [0.0, 1.0, 1.0]
    assert gw_to == [0.0, 0.0, 1.0]


def test_no_anchor_series_when_public_probe_is_off():
    w = _fake_widget()
    _feed(w, [_s(3.0, None, public=False), _s(None, None, public=False)])
    assert not [k for k, _ in w.widget_state.stats if k == "latency_anchor_timeout"]


# --- the Overview pill follows the headline hop -------------------------------------------------------

def _pill(q_app, w):
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    return ov._latency_html(w)


def test_pill_shows_internet_loss_behind_a_healthy_router(q_app):
    w = _fake_widget()
    _feed(w, [_s(3.0, 14.0), _s(3.0, None), _s(3.0, None), _s(3.0, 15.0), _s(3.0, 16.0)])
    html = _pill(q_app, w)
    assert "40% loss" in html, html     # was "0% loss" (router-only) before D1
    assert "Slow" in html


def test_pill_never_shows_router_ms_as_internet_ms(q_app):
    """Public probe on and the anchor has not answered yet: no router 3 ms under "Internet"."""
    w = _fake_widget()
    _feed(w, [_s(3.0, None)] * 5)
    html = _pill(q_app, w)
    assert "3 ms" not in html and "Slow" in html, html


def test_pill_does_not_flicker_on_one_lost_anchor_ping(q_app):
    """One lost ping in a healthy window keeps the last good internet RTT; it is not a red 'Slow'."""
    w = _fake_widget()
    _feed(w, [_s(3.0, 14.0)] * 39 + [_s(3.0, None)])
    html = _pill(q_app, w)
    assert "14 ms" in html and "Slow" not in html, html
    assert "3 ms" not in html


def test_pill_shows_no_loss_until_the_window_has_enough_samples(q_app):
    w = _fake_widget()
    _feed(w, [_s(3.0, 14.0, public=False)] * 10 + [_s(3.0, None)])
    html = _pill(q_app, w)
    assert "loss" not in html and "100%" not in html, html


def test_pill_shows_router_loss_when_public_probe_is_off(q_app):
    w = _fake_widget()
    _feed(w, [_s(3.0, None, public=False)] * 4 + [_s(None, None, public=False)])
    assert "20% loss" in _pill(q_app, w)


# --- the exporter ------------------------------------------------------------------------------------

class _ExportWS:
    def __init__(self, anchor_ms):
        self._anchor_ms = anchor_ms

    def summarize_network(self, direction, start, end, iface, poll):
        from netspeedtray.utils.summaries import summarize_raw
        return summarize_raw([])

    def summarize_hardware(self, stat, start, end, poll):
        from netspeedtray.utils.summaries import summarize_raw
        return summarize_raw({
            "latency_gw": [3.0, 4.0, 3.0, 5.0],
            "latency_gw_timeout": [0.0, 0.0, 0.0, 0.0],
            "latency_anchor": self._anchor_ms,
            "latency_anchor_timeout": [0.0, 1.0, 1.0, 0.0],
        }.get(stat, []))

    def get_speed_history(self, *a, **k):
        return []

    def get_hardware_history(self, stat, start, end):
        return []


def _export(tmp_path, ws):
    from netspeedtray.utils import stats_exporter as SE
    paths = SE.export_window(ws, datetime(2026, 10, 19, 11), datetime(2026, 10, 19, 12),
                             "Last hour", str(tmp_path), "nst")
    with open(paths["summary_csv"], newline="", encoding="utf-8") as f:
        return {r["metric"]: r for r in csv.DictReader(f)}


def test_exporter_fills_loss_pct_for_the_anchor(tmp_path):
    rows = _export(tmp_path, _ExportWS([14.0, 15.0]))
    assert float(rows["latency_anchor"]["loss_pct"]) == pytest.approx(50.0)
    assert float(rows["latency_gw"]["loss_pct"]) == pytest.approx(0.0)


def test_exporter_keeps_a_fully_lost_anchor_row(tmp_path):
    """Internet down for the whole window: no RTT was ever measured, but the loss is the headline."""
    from netspeedtray.utils.summaries import summarize_raw

    class _Dead(_ExportWS):
        def summarize_hardware(self, stat, start, end, poll):
            if stat == "latency_anchor_timeout":
                return summarize_raw([1.0, 1.0, 1.0])
            return super().summarize_hardware(stat, start, end, poll)

    rows = _export(tmp_path, _Dead([]))
    assert float(rows["latency_anchor"]["loss_pct"]) == pytest.approx(100.0)
    assert rows["latency_anchor"]["avg"] == ""


# --- DATABASE.md: the documented loss recipe runs against a real populated database ---------------

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _documented_loss_query() -> str:
    text = (_REPO_ROOT / "DATABASE.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*Internet latency and packet loss[^\n]*\*\*\s*```sql\n(.*?)```", text, re.S)
    assert m, "DATABASE.md lost its latency/loss recipe"
    return m.group(1)


def test_database_md_documents_the_anchor_timeout_series():
    text = (_REPO_ROOT / "DATABASE.md").read_text(encoding="utf-8")
    assert "| `latency_anchor_timeout` |" in text
    assert "latency_anchor_timeout" in _documented_loss_query()


def test_database_md_loss_query_runs_on_a_populated_db(tmp_path):
    from netspeedtray.core.database import DatabaseWorker

    w = DatabaseWorker(tmp_path / "loss.db")
    w._initialize_connection()
    w._check_and_create_schema()

    # Two whole hours of probes, one three days old (rolled up into the minute tier by maintenance) and
    # one from two hours ago (still raw). Each hour: 20 probes every 3 s, router clean except 2, the
    # internet losing 5 (the 2 router losses + 3 beyond the router).
    now = int(time.time())
    hours = [((now - 3 * 86400) // 3600) * 3600, ((now - 2 * 3600) // 3600) * 3600]
    batch = []
    for h in hours:
        for i in range(20):
            ts = h + 60 + 3 * i
            gw_lost = i in (0, 1)
            an_lost = gw_lost or i in (2, 3, 4)
            if not gw_lost:
                batch.append((ts, "latency_gw", 4.0))
            if not an_lost:
                batch.append((ts, "latency_anchor", 20.0))
            batch.append((ts, "latency_gw_timeout", 1.0 if gw_lost else 0.0))
            batch.append((ts, "latency_anchor_timeout", 1.0 if an_lost else 0.0))
    w._persist_hardware_batch(batch)
    w._aggregate_hardware_raw_to_minute(w.conn.cursor(), datetime.now())
    w.conn.commit()

    tiers = {t: w.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("hardware_stats_raw", "hardware_stats_minute")}
    assert tiers["hardware_stats_raw"] > 0 and tiers["hardware_stats_minute"] > 0, tiers
    w.conn.close()

    ro = sqlite3.connect(f"file:{tmp_path / 'loss.db'}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    rows = ro.execute(_documented_loss_query()).fetchall()
    ro.close()
    assert len(rows) == 2, [dict(r) for r in rows]
    for r in rows:
        assert r["internet_ms"] == pytest.approx(20.0)
        assert r["router_loss_pct"] == pytest.approx(10.0)
        assert r["internet_loss_pct"] == pytest.approx(25.0)
