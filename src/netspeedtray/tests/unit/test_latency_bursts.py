"""
2.2 scope D2 - bursts. Each 3 s cycle sends a burst of BURST_SIZE (4) echoes per hop, spaced
BURST_SPACING_MS (100 ms); the sample carries the MEDIAN RTT plus sent/lost per hop; the persisted
latency_*_timeout series become lost/sent per cycle (0, .25, .5, .75, 1); the live loss is
sum(lost)/sum(sent) over the last 40 cycles, so it resolves 0.6% steps.

Gate 5: the loss series keep one meaning across old (0/1, one echo per cycle) and new (fractional)
rows - every reader is asserted here against BOTH shapes.
"""
import csv
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt

from netspeedtray import constants
from netspeedtray.core import latency_probe as LP
from netspeedtray.utils import summaries as S


GW_IP = "192.168.1.1"
ANCHOR = "1.1.1.1"
LAT = constants.network.latency
_PUBLIC_ON = {"latency_public_enabled": True, "latency_public_host": ANCHOR}


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# --- a probe driven through the REAL _run_loop on a fake clock ------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 1000.0


def _drive(monkeypatch, config, reply, n, gateway=GW_IP):
    """Run `n` real cycles. `reply(ip, cycle, k)` -> ms or None (k = echo index within that hop's burst
    this cycle). A reply advances the fake clock by its RTT, a timeout by the 1000 ms timeout, a pause by
    its length. Returns (samples, pings as (t, ip, cycle), pauses)."""
    clock = _Clock()
    got, pings, pauses = [], [], []

    def fake_ping(ip, timeout_ms=1000):
        cycle = len(got)
        k = sum(1 for _, i, c in pings if i == ip and c == cycle)
        pings.append((clock.t, ip, cycle))
        r = reply(ip, cycle, k)
        clock.t += (timeout_ms / 1000.0) if r is None else r / 1000.0
        return r

    def fake_pause(self, seconds):
        pauses.append(round(seconds, 6))
        clock.t += max(0.0, seconds)
        return self._running

    monkeypatch.setattr(LP, "default_gateway_ip", lambda: gateway)
    monkeypatch.setattr(LP, "icmp_ping_ms", fake_ping)
    monkeypatch.setattr(LP.time, "monotonic", lambda: clock.t)
    monkeypatch.setattr(LP.LatencyProbe, "_pause", fake_pause)
    p = LP.LatencyProbe(config)

    def on_ready(sample):
        got.append(sample)
        if len(got) >= n:
            p._running = False

    p.latency_ready.connect(on_ready)
    p._running = True
    p._run_loop()
    assert len(got) == n
    return got, pings, pauses


def _cycle_starts(pings):
    starts = {}
    for t, _, c in pings:
        starts.setdefault(c, t)
    return [starts[c] for c in sorted(starts)]


# --- the burst ------------------------------------------------------------------------------------------

def test_burst_sends_four_spaced_echoes_per_hop(monkeypatch):
    got, pings, pauses = _drive(monkeypatch, _PUBLIC_ON, lambda ip, c, k: 3.0 if ip == GW_IP else 14.0, 1)
    assert LAT.BURST_SIZE == 4 and LAT.BURST_SPACING_MS == 100
    assert [ip for _, ip, _ in pings] == [GW_IP] * 4 + [ANCHOR] * 4
    assert pauses.count(0.1) == 6, pauses          # 3 gaps inside each hop's burst
    gw_t = [t for t, ip, _ in pings if ip == GW_IP]
    assert all(b - a >= 0.1 for a, b in zip(gw_t, gw_t[1:]))
    s = got[0]
    assert (s.gw_sent, s.gw_lost, s.anchor_sent, s.anchor_lost) == (4, 0, 4, 0)


def test_public_probe_off_bursts_only_the_gateway(monkeypatch):
    got, pings, _ = _drive(monkeypatch, {"latency_public_enabled": False}, lambda ip, c, k: 3.0, 1)
    assert [ip for _, ip, _ in pings] == [GW_IP] * 4
    s = got[0]
    assert (s.gw_sent, s.gw_lost) == (4, 0)
    assert (s.anchor_sent, s.anchor_lost) == (0, 0) and s.anchor_enabled is False


def test_ms_is_the_median_of_the_successful_rtts(monkeypatch):
    gw = [3.0, 50.0, 4.0, 5.0]           # one outlier: median 4.5, not the mean 15.5 or the min 3
    an = [None, 20.0, 30.0, 100.0]       # one lost: median of the three replies = 30
    got, _, _ = _drive(monkeypatch, _PUBLIC_ON, lambda ip, c, k: (gw if ip == GW_IP else an)[k], 1)
    s = got[0]
    assert s.gw_ms == pytest.approx(4.5)
    assert s.anchor_ms == pytest.approx(30.0)
    assert (s.anchor_sent, s.anchor_lost) == (4, 1)
    assert s.anchor_timed_out is False, "a partly-lost burst still measured the hop"


def test_fully_lost_burst_is_a_timeout(monkeypatch):
    got, _, _ = _drive(monkeypatch, {"latency_public_enabled": False}, lambda ip, c, k: None, 1)
    s = got[0]
    assert s.gw_ms is None and s.gw_timed_out is True and (s.gw_sent, s.gw_lost) == (4, 4)


# --- the cycle budget -----------------------------------------------------------------------------------

def test_healthy_cycle_keeps_the_3s_cadence(monkeypatch):
    got, pings, _ = _drive(monkeypatch, _PUBLIC_ON, lambda ip, c, k: 3.0 if ip == GW_IP else 14.0, 3)
    starts = _cycle_starts(pings)
    assert [round(b - a, 6) for a, b in zip(starts, starts[1:])] == [3.0, 3.0]


def test_dead_link_costs_one_bounded_cycle_and_records_four_of_four_lost(monkeypatch):
    """The router answered on cycle 0, then the link dies. A dead cycle = 4 gateway timeouts + 3 gaps
    (~4.3 s), the anchor is skipped (not pinged) and recorded 4/4 lost - not 8 timeouts (~8.3 s)."""
    budget = LAT.BURST_SIZE * 1.0 + (LAT.BURST_SIZE - 1) * LAT.BURST_SPACING_MS / 1000.0
    got, pings, _ = _drive(monkeypatch, _PUBLIC_ON,
                           lambda ip, c, k: (3.0 if ip == GW_IP else 14.0) if c == 0 else None, 3)
    starts = _cycle_starts(pings)
    dead = starts[2] - starts[1]
    assert dead == pytest.approx(max(3.0, budget)), (dead, budget)
    assert [ip for _, ip, c in pings if c == 1] == [GW_IP] * 4, "a dead gateway must not reach the anchor"
    s = got[1]
    assert (s.gw_sent, s.gw_lost, s.anchor_sent, s.anchor_lost) == (4, 4, 4, 4)
    assert s.anchor_skipped is True and s.gw_timed_out is True and s.anchor_timed_out is True


def test_stop_interrupts_a_pause_promptly():
    p = LP.LatencyProbe({})
    threading.Timer(0.1, p.stop).start()
    t0 = time.monotonic()
    assert p._pause(5.0) is False
    assert time.monotonic() - t0 < 0.6


def test_shutdown_is_bounded_mid_burst_on_a_dead_link(monkeypatch, q_app):
    """The real thread on a dead link with an ICMP-silent gateway (so both hops burst: 8 timeouts a
    cycle). stop() must return within one in-flight echo, not after the burst or the cycle."""
    echo = 0.2

    def slow_timeout(ip, timeout_ms=1000):
        time.sleep(echo)
        return None

    monkeypatch.setattr(LP, "default_gateway_ip", lambda: GW_IP)
    monkeypatch.setattr(LP, "icmp_ping_ms", slow_timeout)
    p = LP.LatencyProbe(_PUBLIC_ON)
    emitted = []
    p.latency_ready.connect(lambda s: emitted.append(s), type=Qt.ConnectionType.DirectConnection)
    p.start()
    time.sleep(0.5)                 # mid-way through the gateway burst
    t0 = time.monotonic()
    p.stop()
    assert p.wait(3000)
    assert time.monotonic() - t0 < echo + 0.35
    assert not emitted, "a burst cut short by stop() must not be emitted as a sample"


def test_stop_between_bursts_never_reaches_the_public_host(monkeypatch, q_app):
    """stop() lands during the gateway burst's last echo (a real 1 s timeout, mocked). The anchor burst
    must not start - no packet to the public host after stop() - and the thread must join well inside
    the widget's 1.5 s shutdown wait, i.e. within that one in-flight echo."""
    lock = threading.Lock()
    log = []                                  # (monotonic start, ip)
    gw_echoes = [0]

    def ping(ip, timeout_ms=1000):
        with lock:
            log.append((time.monotonic(), ip))
        if ip == GW_IP:
            gw_echoes[0] += 1
            if gw_echoes[0] == LAT.BURST_SIZE:
                time.sleep(timeout_ms / 1000.0)   # the burst's last echo times out: a full 1 s
                return None
            return 3.0
        time.sleep(timeout_ms / 1000.0)
        return None

    monkeypatch.setattr(LP, "default_gateway_ip", lambda: GW_IP)
    monkeypatch.setattr(LP, "icmp_ping_ms", ping)
    p = LP.LatencyProbe(_PUBLIC_ON)
    p.start()
    deadline = time.monotonic() + 3.0
    while gw_echoes[0] < LAT.BURST_SIZE and time.monotonic() < deadline:
        time.sleep(0.01)
    assert gw_echoes[0] == LAT.BURST_SIZE
    time.sleep(0.2)                           # mid-way through the in-flight 1 s echo
    t_stop = time.monotonic()
    p.stop()
    assert p.wait(5000)
    joined = time.monotonic() - t_stop
    with lock:
        late = [(round(t - t_stop, 2), ip) for t, ip in log if t >= t_stop]
    assert not [ip for _, ip in late if ip == ANCHOR], f"echo to the public host after stop(): {late}"
    assert joined < 1.2, f"thread joined {joined:.2f} s after stop() (the widget waits 1.5 s)"


# --- the live windows: sum(lost) / sum(sent)--------------------------------------------------------------

def _burst(gw_lost=0, an_lost=None, *, gw_ms=3.0, an_ms=14.0):
    n = LAT.BURST_SIZE
    public = an_lost is not None
    return LP.LatencySample(
        gw_ms=None if gw_lost == n else gw_ms, anchor_ms=(None if an_lost == n else an_ms) if public else None,
        gw_timed_out=gw_lost == n, anchor_timed_out=public and an_lost == n, anchor_enabled=public,
        gw_sent=n, gw_lost=gw_lost, anchor_sent=n if public else 0, anchor_lost=an_lost or 0)


def test_live_loss_resolves_0_6_percent_steps():
    w = LP.LatencyLossWindows()
    for _ in range(LAT.LIVE_LOSS_WINDOW - 1):
        w.add(_burst(0, 0))
    w.add(_burst(1, 1))
    assert w.gw_loss == 0.6 and w.anchor_loss == 0.6     # 1 of 160 probes
    w.add(_burst(1, 2))
    assert w.gw_loss == pytest.approx(1.2) and w.anchor_loss == pytest.approx(1.9)


def test_live_loss_is_lost_over_sent_not_lost_cycles():
    w = LP.LatencyLossWindows()
    for _ in range(4):
        w.add(_burst(1, 2))         # every cycle loses some, none loses all
    w.add(_burst(0, 0))
    assert w.gw_loss == pytest.approx(20.0)   # 4 / 20 probes
    assert w.anchor_loss == pytest.approx(40.0)


def test_loss_min_samples_still_counts_cycles_not_probes():
    w = LP.LatencyLossWindows()
    for _ in range(LAT.LOSS_MIN_SAMPLES - 1):
        w.add(_burst(1))
    assert w.gw_loss is None, "4 cycles = 16 probes is still not enough cycles"
    w.add(_burst(1))
    assert w.gw_loss == pytest.approx(25.0)


def test_old_single_echo_samples_still_count_as_one_probe():
    """A LatencySample built without counts (one echo per cycle, the D1 shape) is 1 sent, 0/1 lost."""
    s = LP.LatencySample(gw_ms=None, anchor_ms=None, gw_timed_out=True, anchor_timed_out=True,
                         anchor_enabled=True)
    assert (s.gw_sent, s.gw_lost, s.anchor_sent, s.anchor_lost) == (1, 1, 1, 1)


# --- persistence: the fraction lost per cycle --------------------------------------------------------------

class _RecWS:
    def __init__(self):
        self.stats = []

    def add_hardware_stat(self, stat, value):
        self.stats.append((stat, value))


def _fake_widget():
    return SimpleNamespace(latency_gw=None, latency_anchor=None, latency_loss=None,
                           latency_loss_gw=None, latency_loss_anchor=None, latency_headline_hop=None,
                           latency_headline_ms=None, _latency_loss_windows=LP.LatencyLossWindows(),
                           widget_state=_RecWS())


def _feed(w, samples):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    for s in samples:
        NetworkSpeedWidget._on_latency(w, s)


def test_timeout_series_persist_the_fraction_lost_per_cycle():
    w = _fake_widget()
    _feed(w, [_burst(0, 0), _burst(1, 2), _burst(2, 3), _burst(3, 4), _burst(4, 4)])
    gw = [v for k, v in w.widget_state.stats if k == LAT.STAT_GW_TIMEOUT]
    an = [v for k, v in w.widget_state.stats if k == LAT.STAT_ANCHOR_TIMEOUT]
    assert gw == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert an == [0.0, 0.5, 0.75, 1.0, 1.0]


# --- Gate 5: every reader on a mix of old 0/1 rows and new fractional rows -----------------------------

def _t(m):
    return datetime(2026, 10, 19, 14, 0, 0) + timedelta(minutes=m)


def test_outage_summary_on_mixed_rows_half_a_burst_is_a_drop_a_quarter_is_not():
    vals = [0, 1, 0, 0.25, 0, 0.5, 0.75, 0, 0.25, 0.25, 0]   # old 0/1 rows, then new bursts
    o = S.outage_summary([(_t(i), v) for i, v in enumerate(vals)])
    assert o["count"] == 2
    assert o["runs"] == [(_t(1), _t(1)), (_t(5), _t(6))]
    assert S.outage_summary([(_t(i), 0.25) for i in range(10)])["count"] == 0


def test_loss_pct_on_fractional_rows():
    rows = [1.0, 0.0, 0.25, 0.75, 0.0, 0.5, 0.0, 0.0]          # mixed shapes, 8 cycles
    assert S.loss_pct(sum(rows), len(rows)) == pytest.approx(31.25)


def test_exporter_loss_pct_on_mixed_rows(tmp_path):
    from netspeedtray.utils import stats_exporter as SE
    from netspeedtray.utils.summaries import summarize_raw

    class _WS:
        def summarize_network(self, *a):
            return summarize_raw([])

        def summarize_hardware(self, stat, *a):
            return summarize_raw({"latency_gw": [3.0, 4.0, 4.5, 5.0],
                                  "latency_gw_timeout": [1.0, 0.0, 0.25, 0.75, 0.0]}.get(stat, []))

        def get_speed_history(self, *a, **k):
            return []

        def get_hardware_history(self, *a):
            return []

    paths = SE.export_window(_WS(), datetime(2026, 10, 19, 11), datetime(2026, 10, 19, 12),
                             "Last hour", str(tmp_path), "nst")
    with open(paths["summary_csv"], newline="", encoding="utf-8") as f:
        rows = {r["metric"]: r for r in csv.DictReader(f)}
    assert float(rows["latency_gw"]["loss_pct"]) == pytest.approx(40.0)


def _pill(w, lang="en_US"):
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    return OverviewTab(_MW(), _cfg(), I18nStrings(lang))._latency_html(w)


def test_pill_sub_one_percent_loss_uses_the_locale_decimal_separator(q_app):
    w = _fake_widget()
    _feed(w, [_burst(0, 0)] * (LAT.LIVE_LOSS_WINDOW - 1) + [_burst(0, 1)])
    html = _pill(w, "de_DE")
    assert "0,6% Verlust" in html and "0.6" not in html, html


def test_pill_on_fractional_loss_below_one_percent_is_good_and_says_so(q_app):
    w = _fake_widget()
    _feed(w, [_burst(0, 0)] * (LAT.LIVE_LOSS_WINDOW - 1) + [_burst(0, 1)])
    html = _pill(w)
    assert "Good" in html and "0.6% loss" in html, html   # not "1% loss" under a Good verdict


def test_pill_on_fractional_loss_crosses_the_ok_threshold(q_app):
    w = _fake_widget()
    _feed(w, [_burst(0, 0)] * (LAT.LIVE_LOSS_WINDOW - 2) + [_burst(0, 1)] * 2)
    html = _pill(w)
    assert "OK" in html and "1% loss" in html, html


def test_pill_on_partly_lost_bursts(q_app):
    w = _fake_widget()
    _feed(w, [_burst(0, 1)] * 10)           # a quarter of every burst lost, every reply 14 ms
    html = _pill(w)
    assert "Slow" in html and "25% loss" in html and "14 ms" in html, html


_REPO_ROOT = Path(__file__).resolve().parents[4]


def _documented_loss_query() -> str:
    text = (_REPO_ROOT / "DATABASE.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*Internet latency and packet loss[^\n]*\*\*\s*```sql\n(.*?)```", text, re.S)
    assert m, "DATABASE.md lost its latency/loss recipe"
    return m.group(1)


def test_database_md_documents_the_burst_fraction():
    text = (_REPO_ROOT / "DATABASE.md").read_text(encoding="utf-8")
    row = next(l for l in text.splitlines() if l.startswith("| `latency_gw_timeout` |"))
    assert "burst" in row and "0.25" in row, row
    assert "1.3 packets" in text


def test_database_md_recipe_and_rollup_on_mixed_rows(tmp_path):
    """Old 0/1 rows and new fractional rows in one database - in the same hour and across tiers. The
    rollup AVG and the documented recipe both read a per-cycle loss rate from either shape."""
    from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker

    w = LegacyV7Worker(tmp_path / "mixed.db")
    w._initialize_connection()
    w._check_and_create_schema()
    now = int(time.time())
    old_hour = ((now - 3 * 86400) // 3600) * 3600       # rolled into the minute tier
    new_hour = ((now - 2 * 3600) // 3600) * 3600        # still raw
    batch = []
    # Each hour: 10 old-shape cycles (one echo: 1 lost) then 10 bursts (two cycles at 0.5, one at 0.25,
    # one at 0.75 on the anchor side). Router loss = (1 + 0.5 + 0.5) / 20 = 10%;
    # internet loss = (1 + 0.5 + 0.5 + 0.25 + 0.75) / 20 = 15%.
    for h in (old_hour, new_hour):
        for i in range(20):
            ts = h + 60 + 3 * i
            if i < 10:
                g = 1.0 if i == 0 else 0.0
                a = g
            else:
                g = 0.5 if i in (10, 11) else 0.0
                a = {10: 0.5, 11: 0.5, 12: 0.25, 13: 0.75}.get(i, 0.0)
            if g < 1.0:
                batch.append((ts, "latency_gw", 4.0))
            if a < 1.0:
                batch.append((ts, "latency_anchor", 20.0))
            batch.append((ts, LAT.STAT_GW_TIMEOUT, g))
            batch.append((ts, LAT.STAT_ANCHOR_TIMEOUT, a))
    w._persist_hardware_batch(batch)
    w._aggregate_hardware_raw_to_minute(w.conn.cursor(), datetime.now())
    w.conn.commit()
    minute = w.conn.execute(
        "SELECT SUM(avg_value * sample_count) / SUM(sample_count) FROM hardware_stats_minute "
        "WHERE stat_type = ?", (LAT.STAT_GW_TIMEOUT,)).fetchone()[0]
    assert minute == pytest.approx(0.10), "the rollup AVG must keep the per-cycle rate"
    w.conn.close()
    # The documented recipe reads the v8 tables: run it on this v7 history as 2.2 sees it, migrated.
    from netspeedtray.core.db import migrate_v8
    assert migrate_v8.migrate(tmp_path / "mixed.db").status == "migrated"

    ro = sqlite3.connect(f"file:{tmp_path / 'mixed.db'}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    rows = ro.execute(_documented_loss_query()).fetchall()
    ro.close()
    assert len(rows) == 2, [dict(r) for r in rows]
    for r in rows:
        assert r["router_loss_pct"] == pytest.approx(10.0)
        assert r["internet_loss_pct"] == pytest.approx(15.0)
        assert 0.0 <= r["router_loss_pct"] <= r["internet_loss_pct"] <= 100.0
