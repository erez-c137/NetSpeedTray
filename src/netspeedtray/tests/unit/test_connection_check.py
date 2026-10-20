"""
2.2 scope D3 - "Check connection", a one-shot report you can paste.

A ConnectionCheck worker (separate from the standing probe) sends CHECK_ECHOES (20) echoes per hop
through latency_probe.icmp_burst; the sheet shows sent / received / lost, loss %, min / median / p95 /
max RTT and jitter (mean absolute successive difference, NOT stddev) per hop, the pill's verdict word,
and one attribution sentence from the D1 rule. The internet hop runs only when the public probe is on
or the user ticks the one-off box. Nothing is persisted. Cancel stops the worker with no further echo.
"""
import re
import statistics
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import connection_check as CC
from netspeedtray.core import latency_probe as LP


GW = "192.168.1.1"
ANCHOR = "1.1.1.1"
LAT = constants.network.latency


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# --- the math, against fixed RTT lists ----------------------------------------------------------------

def test_hop_stats_on_a_fixed_rtt_list():
    st = CC.hop_stats([10.0, 12.0, 11.0, 30.0, 10.0], sent=6)
    assert (st.sent, st.received, st.lost) == (6, 5, 1)
    assert st.loss_pct == pytest.approx(100.0 / 6)
    assert st.min_ms == 10.0 and st.max_ms == 30.0
    assert st.median_ms == 11.0
    # linear-interpolated p95 (numpy's default, as the Stats-detail sheet): sorted [10,10,11,12,30],
    # position 4 * 0.95 = 3.8 -> 12 + 0.8 * (30 - 12)
    assert st.p95_ms == pytest.approx(26.4)
    # jitter = mean |successive difference| in arrival order: (2 + 1 + 19 + 20) / 4
    assert st.jitter_ms == pytest.approx(10.5)


def test_jitter_is_the_mean_successive_difference_not_the_stddev():
    rtts = [10.0, 20.0, 10.0, 20.0]
    assert CC.jitter(rtts) == pytest.approx(10.0)
    assert CC.jitter(rtts) != pytest.approx(statistics.pstdev(rtts))   # 5.0
    # order matters for jitter, never for the distribution
    assert CC.jitter([10.0, 10.0, 20.0, 20.0]) == pytest.approx(10.0 / 3)


def test_twenty_echoes_one_lost_is_five_percent():
    st = CC.hop_stats([2.0] * 19, sent=LAT.CHECK_ECHOES)
    assert st.loss_pct == pytest.approx(5.0) and st.lost == 1


def test_hop_stats_with_no_reply():
    st = CC.hop_stats([], sent=20)
    assert st.loss_pct == 100.0 and not st.answered
    assert st.min_ms is st.median_ms is st.p95_ms is st.max_ms is st.jitter_ms is None
    assert st.verdict is None   # silence gets "No reply", not a judgement


def test_single_reply_has_no_jitter():
    st = CC.hop_stats([7.0], sent=1)
    assert st.jitter_ms is None and st.p95_ms == 7.0


def test_verdict_reuses_the_pill_rule_on_rtt_only():
    """The check's Good / OK / Slow is the pill's RTT rule; loss is reported on its own (below). With
    20 samples one lost ping is 5%, which used to turn a 2 ms router red "Slow"."""
    assert CC.hop_stats([5.0] * 20, 20).verdict == LP.VERDICT_GOOD
    assert CC.hop_stats([60.0] * 20, 20).verdict == LP.VERDICT_OK
    assert CC.hop_stats([5.0] * 19, 20).verdict == LP.VERDICT_GOOD      # 5% loss is not "Slow"
    assert CC.hop_stats([2.0] * 10, 20).verdict == LP.VERDICT_GOOD      # nor is 50%: loss has its own line
    assert CC.hop_stats([200.0] * 20, 20).verdict == LP.VERDICT_SLOW
    assert LP.latency_verdict(None, 0.0) == LP.VERDICT_SLOW


def test_the_pills_long_window_thresholds_are_unchanged():
    assert LP.latency_verdict(5.0, LAT.VERDICT_SLOW_LOSS_PCT) == LP.VERDICT_SLOW
    assert LP.latency_verdict(5.0, LAT.VERDICT_OK_LOSS_PCT) == LP.VERDICT_OK


def test_loss_level_needs_two_lost_pings_for_red():
    assert CC.hop_stats([2.0] * 20, 20).loss_level == CC.LOSS_LEVEL_NONE
    assert CC.hop_stats([2.0] * 19, 20).loss_level == CC.LOSS_LEVEL_SOME     # 1 of 20: amber
    assert CC.hop_stats([2.0] * (20 - LAT.CHECK_LOSS_HIGH_MIN_LOST), 20).loss_level == CC.LOSS_LEVEL_HIGH
    assert LAT.CHECK_LOSS_HIGH_MIN_LOST == 2
    assert CC.hop_stats([], 20).loss_level is None                          # silence: "No reply"


def test_sheet_one_lost_ping_on_a_fast_router_is_good_with_amber_loss(q_app):
    sheet = _finished_sheet(q_app, CC.hop_stats([2.0] * 19, 20))
    status = sheet._cards[CC.HOP_GATEWAY].status.text()
    assert "Good" in status and "Slow" not in status, status
    assert "5% loss" in status, status
    assert LAT.VERDICT_COLORS["slow"] not in status, status                  # nothing red
    assert LAT.CHECK_LOSS_COLORS[CC.LOSS_LEVEL_SOME] in status
    sheet.done(0)


def test_sheet_two_lost_pings_turn_the_loss_red_not_the_verdict(q_app):
    sheet = _finished_sheet(q_app, CC.hop_stats([2.0] * 18, 20))
    status = sheet._cards[CC.HOP_GATEWAY].status.text()
    assert "Good" in status and "10% loss" in status, status
    assert LAT.CHECK_LOSS_COLORS[CC.LOSS_LEVEL_HIGH] in status
    assert f"color:{LAT.VERDICT_COLORS['good']};'>Good" in status.replace(" ", ""), status
    sheet.done(0)


# --- attribution: the four combinations + a router that never answers ---------------------------------

def _st(received, sent=20, ms=5.0):
    return CC.hop_stats([ms] * received, sent)


@pytest.mark.parametrize("gw, anchor, code", [
    (_st(20), _st(20), LP.LOSS_NONE),
    (_st(20), _st(18), LP.LOSS_BEYOND_ROUTER),
    (_st(18), _st(18), LP.LOSS_LOCAL),
    (_st(18), _st(14), LP.LOSS_LOCAL_AND_BEYOND),
    (_st(18), None, LP.LOSS_LOCAL),
    (_st(20), None, LP.LOSS_NONE),
])
def test_attribution_four_combinations(gw, anchor, code):
    assert CC.check_attribution(gw, anchor) == code


@pytest.mark.parametrize("anchor, code", [
    (None, LP.LOSS_UNKNOWN),
    (_st(18), LP.LOSS_UNKNOWN),
    (_st(0), LP.LOSS_UNKNOWN),
    (_st(20), LP.LOSS_NONE),   # the internet answered everything: there is no loss to place
])
def test_a_silent_router_is_never_blamed(anchor, code):
    got = CC.check_attribution(_st(0), anchor)
    assert got == code
    assert got not in (LP.LOSS_LOCAL, LP.LOSS_LOCAL_AND_BEYOND)


def test_attribute_loss_gw_answered_flag_keeps_the_old_default():
    assert LP.attribute_loss(100.0, None) == LP.LOSS_LOCAL                       # unchanged default
    assert LP.attribute_loss(100.0, None, gw_answered=False) == LP.LOSS_UNKNOWN
    assert LP.attribute_loss(100.0, 25.0, gw_answered=False) == LP.LOSS_UNKNOWN


def _finished_sheet(q_app, gw_stats, anchor_stats=None, lang="en_US", public=False, gw_ip=GW):
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    cfg = {"latency_public_enabled": public, "latency_public_host": ANCHOR}
    sheet = ConnectionCheckSheet(cfg, I18nStrings(lang), gateway_ip=gw_ip, app_version="9.9.9",
                                 autostart=False)
    sheet._anchor_host = ANCHOR if anchor_stats is not None else None
    sheet._echoes = {CC.HOP_GATEWAY: []}
    if anchor_stats is not None:
        sheet._echoes[CC.HOP_ANCHOR] = []
    sheet._resolved_gw = gw_ip
    sheet._started_at = datetime(2026, 10, 19, 21, 4, 12)
    sheet._state = "running"
    stats = {CC.HOP_GATEWAY: gw_stats}
    if anchor_stats is not None:
        stats[CC.HOP_ANCHOR] = anchor_stats
    sheet._on_finished(stats)
    return sheet


def test_sheet_sentence_per_combination(q_app):
    i18n = I18nStrings("en_US")
    cases = {
        LP.LOSS_NONE: (_st(20), _st(20)),
        LP.LOSS_BEYOND_ROUTER: (_st(20), _st(18)),
        LP.LOSS_LOCAL: (_st(18), _st(18)),
        LP.LOSS_LOCAL_AND_BEYOND: (_st(18), _st(14)),
        LP.LOSS_UNKNOWN: (_st(0), _st(18)),
    }
    seen = set()
    for code, (gw, an) in cases.items():
        sheet = _finished_sheet(q_app, gw, an)
        assert sheet.attribution_code() == code
        text = sheet._sentence.text()
        assert text and sheet._sentence.isVisibleTo(sheet)
        seen.add(text)
    assert len(seen) == len(cases), "each combination needs its own sentence"
    assert "Wi-Fi" in i18n.CHECK_CONN_VERDICT_LOCAL
    assert "beyond" in i18n.CHECK_CONN_VERDICT_BEYOND


def test_sheet_silent_router_reads_no_reply_and_explains(q_app):
    sheet = _finished_sheet(q_app, _st(0), None)
    assert sheet.attribution_code() == LP.LOSS_UNKNOWN
    card = sheet._cards[CC.HOP_GATEWAY]
    assert "No reply" in card.status.text()
    assert not any(w in card.status.text() for w in ("Slow", "OK", "Good"))
    assert card.note.isVisibleTo(sheet) and "doesn't answer pings" in card.note.text()
    assert sheet._sentence.text() == I18nStrings("en_US").CHECK_CONN_VERDICT_UNKNOWN


# --- the public hop runs ONLY when enabled or ticked (asserted on the IPs actually pinged) -------------

class _PingLog:
    def __init__(self, delay=0.0):
        self.lock = threading.Lock()
        self.log = []                      # (monotonic start, ip)
        self.delay = delay

    def __call__(self, ip, timeout_ms=1000):
        with self.lock:
            self.log.append((time.monotonic(), ip))
        if self.delay:
            time.sleep(self.delay)
        return 4.0

    def ips(self):
        with self.lock:
            return [ip for _, ip in self.log]


@pytest.fixture
def fast_pings(monkeypatch):
    log = _PingLog()
    monkeypatch.setattr(LP, "icmp_ping_ms", log)
    monkeypatch.setattr(LP, "default_gateway_ip", lambda: GW)
    real_pause = CC.ConnectionCheck._pause
    monkeypatch.setattr(CC.ConnectionCheck, "_pause", lambda self, s: real_pause(self, 0))
    return log


def _run_sheet(q_app, config, tick=False, gateway=GW):
    from PyQt6.QtWidgets import QApplication
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    sheet = ConnectionCheckSheet(config, I18nStrings("en_US"), gateway_ip=gateway, autostart=False)
    if tick:
        sheet._tick.setChecked(True)          # the one-off box: starts the check
    else:
        sheet.start_check()
    w = sheet._worker
    assert w.wait(5000)
    QApplication.processEvents()
    return sheet


def test_public_hop_never_runs_when_off_and_not_ticked(q_app, fast_pings):
    sheet = _run_sheet(q_app, {"latency_public_enabled": False, "latency_public_host": ANCHOR})
    assert fast_pings.ips() == [GW] * LAT.CHECK_ECHOES
    assert sheet._tick.isVisibleTo(sheet) and not sheet._cards[CC.HOP_ANCHOR].isVisibleTo(sheet)
    assert CC.HOP_ANCHOR not in sheet._stats


def test_public_hop_runs_when_ticked_and_the_tick_is_not_persisted(q_app, fast_pings):
    cfg = {"latency_public_enabled": False, "latency_public_host": ANCHOR}
    sheet = _run_sheet(q_app, cfg, tick=True)
    ips = fast_pings.ips()
    assert ips.count(GW) == LAT.CHECK_ECHOES and ips.count(ANCHOR) == LAT.CHECK_ECHOES
    assert set(ips) == {GW, ANCHOR}
    assert cfg == {"latency_public_enabled": False, "latency_public_host": ANCHOR}
    assert ANCHOR in sheet._tick.text()          # the label names the host
    sheet.done(0)


def test_public_hop_runs_when_enabled_in_settings_and_no_tick_is_offered(q_app, fast_pings):
    sheet = _run_sheet(q_app, {"latency_public_enabled": True, "latency_public_host": ANCHOR})
    assert set(fast_pings.ips()) == {GW, ANCHOR}
    assert not sheet._tick.isVisibleTo(sheet)


def test_anchor_host_gate():
    assert CC.anchor_host_for_check({}, False) is None
    assert CC.anchor_host_for_check({"latency_public_enabled": False}, False) is None
    assert CC.anchor_host_for_check({"latency_public_host": "9.9.9.9"}, True) == "9.9.9.9"
    assert CC.anchor_host_for_check({"latency_public_enabled": True}, False) == "1.1.1.1"


def test_unticking_mid_check_stops_the_public_pings(q_app, monkeypatch):
    log = _PingLog(delay=0.02)
    monkeypatch.setattr(LP, "icmp_ping_ms", log)
    from PyQt6.QtWidgets import QApplication
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    sheet = ConnectionCheckSheet({"latency_public_enabled": False, "latency_public_host": ANCHOR},
                                 I18nStrings("en_US"), gateway_ip=GW, autostart=False)
    sheet._tick.setChecked(True)
    first = sheet._worker
    deadline = time.monotonic() + 3
    while ANCHOR not in log.ips() and time.monotonic() < deadline:
        time.sleep(0.01)
    sheet._tick.setChecked(False)               # restarts router-only
    t_untick = time.monotonic()
    assert first.wait(2000)
    assert sheet._worker.wait(10000)
    QApplication.processEvents()
    with log.lock:
        late = [ip for t, ip in log.log if t > t_untick + 0.001]
    assert ANCHOR not in late
    assert CC.HOP_ANCHOR not in sheet._stats


# --- copy text: every row -----------------------------------------------------------------------------

def test_copy_text_contains_every_row(q_app):
    gw = CC.hop_stats([1.0, 2.0, 3.0, 2.0] * 5, 20)
    an = CC.hop_stats([14.0, 15.0, 20.0, 14.0] * 4 + [16.0] * 2, 20)
    sheet = _finished_sheet(q_app, gw, an)
    text = sheet.copy_text()
    assert "NetSpeedTray 9.9.9" in text and "2026-10-19 21:04:12" in text
    assert f"Router {GW}" in text and f"Internet {ANCHOR}" in text
    for _field, key, _default in __import__(
            "netspeedtray.views.monitor.connection_check", fromlist=["_CELLS"])._CELLS:
        label = getattr(I18nStrings("en_US"), key)
        assert text.count(f"{label}: ") == 2, (label, text)
    assert "Sent: 20" in text and "Received: 18" in text and "Lost: 2" in text and "Loss: 10%" in text
    assert "[Good]" in text and "[Good, 10% loss]" in text, text   # loss is its own word, not "Slow"
    assert I18nStrings("en_US").CHECK_CONN_VERDICT_BEYOND in text


def test_copy_text_router_only_says_internet_not_checked(q_app):
    sheet = _finished_sheet(q_app, _st(20), None)
    text = sheet.copy_text()
    assert f"Router {GW}" in text and "Internet: not checked" in text


def test_copy_text_is_localised(q_app):
    sheet = _finished_sheet(q_app, CC.hop_stats([1.0, 2.5], 2), None, lang="de_DE")
    text = sheet.copy_text()
    assert "Router" in text and "Jitter: 1,5 ms" in text


# --- cancel stops the worker: no echo after stop -----------------------------------------------------

def test_cancel_stops_the_worker_with_no_further_echoes(q_app, monkeypatch):
    log = _PingLog(delay=0.1)
    monkeypatch.setattr(LP, "icmp_ping_ms", log)
    w = CC.ConnectionCheck(GW, ANCHOR, count=20, spacing_ms=50, timeout_ms=1000)
    finished = []
    w.check_finished.connect(lambda s: finished.append(s), type=Qt.ConnectionType.DirectConnection)
    w.start()
    time.sleep(0.4)
    t_stop = time.monotonic()
    w.stop()
    assert w.wait(3000)
    joined = time.monotonic() - t_stop
    with log.lock:
        late = [(round(t - t_stop, 3), ip) for t, ip in log.log if t >= t_stop]
        before = len(log.log)
    assert not late, f"echo sent after stop(): {late}"
    assert 0 < before < 2 * 20
    assert joined < 0.1 + 0.25, f"worker joined {joined:.2f} s after stop()"
    assert not finished, "a cancelled check must not report results"


def test_closing_the_sheet_stops_the_check(q_app, monkeypatch):
    log = _PingLog(delay=0.05)
    monkeypatch.setattr(LP, "icmp_ping_ms", log)
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    sheet = ConnectionCheckSheet({"latency_public_enabled": True, "latency_public_host": ANCHOR},
                                 I18nStrings("en_US"), gateway_ip=GW)
    w = sheet._worker
    time.sleep(0.3)
    assert w.isRunning() and set(log.ips()) == {GW, ANCHOR}   # really mid-check
    t_close = time.monotonic()
    sheet.reject()
    assert not w.isRunning()
    assert time.monotonic() - t_close < 0.05 + 0.25
    with log.lock:
        assert not [ip for t, ip in log.log if t >= t_close]


def test_stop_button_marks_the_check_stopped(q_app, monkeypatch):
    log = _PingLog(delay=0.05)
    monkeypatch.setattr(LP, "icmp_ping_ms", log)
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    sheet = ConnectionCheckSheet({}, I18nStrings("en_US"), gateway_ip=GW)
    w = sheet._worker
    assert sheet._run_btn.text() == "Stop"
    time.sleep(0.2)
    assert w.isRunning() and log.ips()
    t_click = time.monotonic()
    sheet._run_btn.click()
    assert w.wait(1000)
    with log.lock:
        assert not [ip for t, ip in log.log if t >= t_click]
    assert sheet._state == "stopped" and sheet._run_btn.text() == "Check again"
    assert "Stopped" in sheet._cards[CC.HOP_GATEWAY].status.text()
    sheet.done(0)


def test_burst_reports_each_echo(monkeypatch):
    replies = iter([3.0, None, 5.0])
    monkeypatch.setattr(LP, "icmp_ping_ms", lambda ip, timeout_ms=1000: next(replies))
    seen = []
    r = LP.icmp_burst(GW, 3, 0, pause=lambda s: True, on_echo=lambda i, ms: seen.append((i, ms)))
    assert seen == [(0, 3.0), (1, None), (2, 5.0)] and r.rtts == (3.0, 5.0)


def test_live_progress_fills_in(q_app, fast_pings):
    sheet = _run_sheet(q_app, {"latency_public_enabled": False})
    card = sheet._cards[CC.HOP_GATEWAY]
    assert card.values["sent"].text() == "20" and card.values["received"].text() == "20"
    assert card.values["median_ms"].text() == "4 ms"
    assert "Good" in card.status.text()
    sheet._state = "running"
    sheet._stats = {}
    sheet._echoes[CC.HOP_GATEWAY] = sheet._echoes[CC.HOP_GATEWAY][:12]
    sheet._refresh()
    assert "12 / 20" in card.status.text() and card.values["sent"].text() == "12"


def test_no_router_found(q_app, monkeypatch):
    monkeypatch.setattr(LP, "default_gateway_ip", lambda: None)
    log = _PingLog()
    monkeypatch.setattr(LP, "icmp_ping_ms", log)
    sheet = _run_sheet(q_app, {}, gateway=None)
    assert not log.ips()
    assert "No router found" in sheet._cards[CC.HOP_GATEWAY].status.text()
    assert "Router: No router found" in sheet.copy_text()


def test_probe_exposes_its_gateway(monkeypatch):
    monkeypatch.setattr(LP, "default_gateway_ip", lambda: GW)
    p = LP.LatencyProbe({})
    assert p.gateway is None
    p._gateway_ip()
    assert p.gateway == GW


# --- the Stats-detail "Internet" block follows the headline hop ---------------------------------------

class _HopWS:
    """latency_gw clean (0 loss); latency_anchor lossy (half of every burst lost, one drop)."""
    def __init__(self):
        self._t0 = datetime(2026, 10, 19, 12, 0, 0)
        self.asked = []

    def summarize_network(self, direction, start, end, iface, poll):
        from netspeedtray.utils.summaries import summarize_raw
        return summarize_raw([1.0, 2.0])

    def summarize_hardware(self, stat, start, end, poll):
        from netspeedtray.utils.summaries import summarize_raw
        self.asked.append(stat)
        return summarize_raw({
            "latency_gw": [3.0, 4.0], "latency_anchor": [20.0, 22.0],
            "latency_gw_timeout": [0.0, 0.0], "latency_anchor_timeout": [0.5, 0.0],
        }.get(stat, []))

    def get_speed_history(self, start, end, iface, resolution='auto'):
        return []

    def get_hardware_history(self, stat, start, end):
        self.asked.append(stat)
        if stat == "latency_anchor_timeout":
            return [(self._t0, 0.5), (self._t0 + timedelta(seconds=3), 0.0)]
        if stat == "latency_gw_timeout":
            return [(self._t0, 0.0), (self._t0 + timedelta(seconds=3), 0.0)]
        return [(self._t0, 3.0)]


def _detail_copy(q_app, public: bool):
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    from netspeedtray.views.monitor.stats_detail import StatsDetailSheet
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    cfg = _cfg(latency_public_enabled=public, update_rate=1.0)
    ov = OverviewTab(_MW(), cfg, I18nStrings("en_US"))
    subjects = ov._detail_subjects("network")
    lat = [s for s in subjects if s["unit"] == "ms"][0]
    ws = _HopWS()
    sheet = StatsDetailSheet(ws, subjects, (datetime(2026, 10, 19), datetime(2026, 10, 20), "24 Hours"),
                             cfg, I18nStrings("en_US"))
    return lat["key"], "\n".join(sheet._copy_text_parts), ws.asked


def test_stats_detail_internet_block_follows_the_anchor_when_public_is_on(q_app):
    key, text, asked = _detail_copy(q_app, public=True)
    assert key == "latency_anchor"
    assert "25.0% packet loss" in text and "1 connection drops" in text, text
    assert "latency_gw_timeout" not in asked


def test_stats_detail_internet_block_is_the_gateway_when_public_is_off(q_app):
    key, text, asked = _detail_copy(q_app, public=False)
    assert key == "latency_gw"
    assert "packet loss" not in text and "connection drops" not in text, text
    assert "latency_anchor_timeout" not in asked


# --- privacy.md tells the truth about the probe --------------------------------------------------------

def test_privacy_md_has_a_latency_probe_paragraph():
    text = (Path(__file__).resolve().parents[4] / "privacy.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*Latency probe[^\n]*", text)
    assert m, "privacy.md lost its Latency probe paragraph"
    para = m.group(0)
    for must in ("gateway", "1.3", "Check connection", "not saved", "netspeedtray"):
        assert must in para, must


# --- review follow-ups: a blocked anchor keeps its block; quit/close stop the worker; sheets are freed --

class _BlockedAnchorWS(_HopWS):
    """The anchor never answered: loss rows (every burst lost) but NO RTT rows. The gateway is fine."""
    def __init__(self, anchor_loss=True, gw=True):
        super().__init__()
        self._anchor_loss, self._gw = anchor_loss, gw

    def summarize_hardware(self, stat, start, end, poll):
        from netspeedtray.utils.summaries import summarize_raw
        self.asked.append(stat)
        return summarize_raw({
            "latency_gw": [3.0, 4.0] if self._gw else [],
            "latency_gw_timeout": [0.0, 0.0] if self._gw else [],
            "latency_anchor": [],
            "latency_anchor_timeout": [1.0, 1.0] if self._anchor_loss else [],
        }.get(stat, []))

    def get_hardware_history(self, stat, start, end):
        self.asked.append(stat)
        if stat == "latency_anchor_timeout" and self._anchor_loss:
            return [(self._t0, 1.0), (self._t0 + timedelta(seconds=3), 1.0)]
        if stat == "latency_gw_timeout" and self._gw:
            return [(self._t0, 0.0), (self._t0 + timedelta(seconds=3), 0.0)]
        return []


def _detail_with(q_app, ws):
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    from netspeedtray.views.monitor.stats_detail import StatsDetailSheet
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    cfg = _cfg(latency_public_enabled=True, update_rate=1.0)
    subjects = OverviewTab(_MW(), cfg, I18nStrings("en_US"))._detail_subjects("network")
    sheet = StatsDetailSheet(ws, subjects, (datetime(2026, 10, 19), datetime(2026, 10, 20), "24 Hours"),
                             cfg, I18nStrings("en_US"))
    return "\n".join(sheet._copy_text_parts)


def test_stats_detail_keeps_a_fully_blocked_anchor_block(q_app):
    text = _detail_with(q_app, _BlockedAnchorWS())
    assert "Internet - 24 Hours" in text, text                 # the block is there, under its own name
    assert "100.0% packet loss" in text and "1 connection drops" in text, text
    assert "Average: -" in text and "Peak: -" in text           # no RTT: cells read "-"
    assert "Router - " not in text                              # no silent switch to the gateway


def test_stats_detail_falls_back_to_the_router_only_when_the_anchor_has_no_rows(q_app):
    text = _detail_with(q_app, _BlockedAnchorWS(anchor_loss=False))
    assert "Router - 24 Hours" in text and "Internet - " not in text, text
    assert "Average: 4 ms" in text or "Average: 3 ms" in text, text


def test_stats_detail_drops_the_latency_block_when_no_hop_has_rows(q_app):
    text = _detail_with(q_app, _BlockedAnchorWS(anchor_loss=False, gw=False))
    assert "Internet - " not in text and "Router - " not in text, text


def _drain_deletes():
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QApplication.processEvents()


def test_app_quit_with_the_sheet_open_stops_and_waits_the_worker(q_app, monkeypatch):
    """Tray Exit while the modal sheet runs: exec() returns without done(). aboutToQuit must stop and
    wait the worker - a QThread destroyed while running aborts the whole process."""
    from PyQt6.QtWidgets import QApplication
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    log = _PingLog(delay=0.3)                       # a slow echo, like a dead link's 1 s timeouts
    monkeypatch.setattr(LP, "icmp_ping_ms", log)
    sheet = ConnectionCheckSheet({"latency_public_enabled": True, "latency_public_host": ANCHOR},
                                 I18nStrings("en_US"), gateway_ip=GW)
    w = sheet._worker
    time.sleep(0.1)
    assert w.isRunning()
    t_quit = time.monotonic()
    QApplication.instance().aboutToQuit.emit()
    assert not w.isRunning(), "aboutToQuit must stop and wait the check"
    assert time.monotonic() - t_quit < 0.3 + 0.3
    with log.lock:
        assert not [ip for t, ip in log.log if t >= t_quit]
    _drain_deletes()


def test_a_running_worker_outlives_its_dropped_sheet(q_app, monkeypatch):
    """The sheet's Python wrapper going away mid-check must not destroy the running QThread."""
    import gc
    from netspeedtray.views.monitor import connection_check as V
    log = _PingLog(delay=0.05)
    monkeypatch.setattr(LP, "icmp_ping_ms", log)
    sheet = V.ConnectionCheckSheet({}, I18nStrings("en_US"), gateway_ip=GW)
    w = sheet._worker
    time.sleep(0.1)
    del sheet
    gc.collect()
    _drain_deletes()
    assert w.isRunning()                             # still alive, owned by the registry
    V.stop_all_checks()
    assert not w.isRunning()


def test_monitor_close_stops_the_checks():
    import inspect
    from netspeedtray.views.monitor.window import MonitorWindow
    assert "stop_all_checks()" in inspect.getsource(MonitorWindow.closeEvent)


def test_stop_all_checks_closes_open_sheets(q_app, monkeypatch):
    from netspeedtray.views.monitor import connection_check as V
    log = _PingLog(delay=0.05)
    monkeypatch.setattr(LP, "icmp_ping_ms", log)
    sheet = V.ConnectionCheckSheet({}, I18nStrings("en_US"), gateway_ip=GW)
    sheet.show()
    w = sheet._worker
    V.stop_all_checks()
    assert not w.isRunning() and not sheet.isVisible()
    _drain_deletes()


def test_closed_sheets_and_finished_workers_are_released(q_app, fast_pings):
    from PyQt6 import sip
    from PyQt6.QtCore import Qt as _Qt
    from netspeedtray.views.monitor import connection_check as V
    sheet = V.ConnectionCheckSheet({}, I18nStrings("en_US"), gateway_ip=GW)
    assert sheet.testAttribute(_Qt.WidgetAttribute.WA_DeleteOnClose)
    w = sheet._worker
    assert w.wait(5000)
    _drain_deletes()
    assert sip.isdeleted(w), "a finished worker must be deleteLater()'d"
    sheet.show()
    sheet.done(0)
    _drain_deletes()
    assert sip.isdeleted(sheet), "a closed sheet must be deleted (WA_DeleteOnClose)"
