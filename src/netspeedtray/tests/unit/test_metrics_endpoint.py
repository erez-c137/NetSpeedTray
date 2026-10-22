"""
Local metrics endpoint (2.2 scope B): exposition format, honesty of the values, and the server's
safety properties (loopback only, off by default, port-in-use, start/stop, no thread left behind,
no Qt-owned object read from the server thread).

Every server test binds a free ephemeral port on 127.0.0.1; nothing here touches %APPDATA% or
starts the app.
"""
import http.client
import logging
import re
import socket
import threading
import time
import types
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.core import metrics_endpoint as me
from netspeedtray.core.metrics_endpoint import (LatencyHop, MetricsEndpoint, MetricsSnapshot, Rates,
                                                SnapshotStore, render_metrics)
from netspeedtray.core.metrics_snapshot import build_snapshot
from netspeedtray.core.widget_state import WidgetState

M = constants.network.metrics


# --- a small, strict parser for Prometheus text format 0.0.4 ------------------------------------
_NAME = r"[a-zA-Z_:][a-zA-Z0-9_:]*"
_LABEL = r'[a-zA-Z_][a-zA-Z0-9_]*="(?:[^"\\\n]|\\\\|\\"|\\n)*"'
_SAMPLE = re.compile(rf"^({_NAME})(\{{(?:{_LABEL})(?:,{_LABEL})*\}})?\s(\S+)$")
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\\n]|\\\\|\\"|\\n)*)"')


def parse_exposition(text):
    """{name: {"type", "help", "samples": [(labels_dict, value)]}}. Raises AssertionError on anything
    a Prometheus scraper would reject (or that breaks the conventions we promise)."""
    assert text.endswith("\n"), "exposition must end with a newline"
    fams, current = {}, None
    for line in text.rstrip("\n").split("\n"):
        assert line, "no blank lines"
        if line.startswith("# HELP "):
            name, _, help_text = line[7:].partition(" ")
            assert re.fullmatch(_NAME, name) and name not in fams, line
            fams[name] = {"help": help_text, "type": None, "samples": []}
            current = name
        elif line.startswith("# TYPE "):
            name, _, kind = line[7:].partition(" ")
            assert name == current and fams[name]["type"] is None, f"TYPE must follow its HELP: {line}"
            assert kind in ("counter", "gauge"), line
            fams[name]["type"] = kind
        else:
            m = _SAMPLE.match(line)
            assert m, f"malformed sample line: {line!r}"
            name, labels, value = m.group(1), m.group(2) or "", m.group(3)
            assert name == current and fams[name]["type"], f"sample outside its family: {line}"
            v = float(value.replace("+Inf", "inf").replace("-Inf", "-inf"))
            lbl = {k: val.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
                   for k, val in _LABEL_RE.findall(labels)}
            fams[name]["samples"].append((lbl, v))
    for name, f in fams.items():
        assert name.startswith("netspeedtray_"), name
        assert f["samples"], f"{name}: a family with no samples must not be emitted"
        if f["type"] == "counter":
            assert name.endswith("_total"), f"counter {name} must end in _total"
        keys = [tuple(sorted(lbl.items())) for lbl, _ in f["samples"]]
        assert len(keys) == len(set(keys)), f"{name}: duplicate series"
    return fams


def value(fams, name, **labels):
    for lbl, v in fams[name]["samples"]:
        if all(lbl.get(k) == str(val) for k, val in labels.items()):
            return v
    raise KeyError((name, labels))


def full_snapshot(**over):
    base = dict(
        version="9.9.9", taken_at=1_700_000_000.5, interface_mode="selected",
        aggregate=Rates(rx=1250.0, tx=500.0, measured_rx=1300.0, measured_tx=500.0),
        interfaces=(('Ethernet "2"\\x', Rates(1000.0, 400.0, 1000.0, 400.0)),
                    ("Wi-Fi", Rates(250.0, 100.0, 300.0, 100.0))),
        period_bytes=(5_000_000_000.0, 1_000_000_000.0), lifetime_bytes=(9e10, 2e10),
        period_start=1_699_000_000.0, data_cap_bytes=1e12, data_cap_counts="download",
        latency=(LatencyHop("gateway", 2.5, 0.0), LatencyHop("anchor", 14.0, 0.025)),
        cpu_ratio=0.125, gpu_ratio=0.5, ram_used_bytes=8e9, ram_total_bytes=16e9,
        vram_used_bytes=2e9, vram_total_bytes=8e9)
    base.update(over)
    return MetricsSnapshot(**base)


# --- exposition format --------------------------------------------------------------------------
def test_full_snapshot_renders_valid_exposition_with_conventions():
    fams = parse_exposition(render_metrics(full_snapshot(), scrape_time=1_700_000_001.0))
    assert value(fams, "netspeedtray_info", version="9.9.9") == 1
    assert value(fams, "netspeedtray_scrape_timestamp_seconds") == 1_700_000_001.0
    assert value(fams, "netspeedtray_receive_bytes_per_second", interface_mode="selected") == 1250
    assert value(fams, "netspeedtray_receive_measured_bytes_per_second", interface_mode="selected") == 1300
    # a label value with a quote and a backslash survives escaping
    assert value(fams, "netspeedtray_interface_receive_bytes_per_second", interface='Ethernet "2"\\x') == 1000
    assert value(fams, "netspeedtray_period_receive_bytes_total") == 5e9
    assert value(fams, "netspeedtray_transmit_bytes_total") == 2e10
    assert value(fams, "netspeedtray_data_cap_bytes", counts="download") == 1e12
    assert value(fams, "netspeedtray_latency_rtt_seconds", hop="gateway") == pytest.approx(0.0025)
    assert value(fams, "netspeedtray_packet_loss_ratio", hop="anchor") == pytest.approx(0.025)
    assert value(fams, "netspeedtray_cpu_utilization_ratio") == 0.125
    assert value(fams, "netspeedtray_gpu_memory_total_bytes") == 8e9
    for name in ("netspeedtray_period_receive_bytes_total", "netspeedtray_receive_bytes_total"):
        assert fams[name]["type"] == "counter"
        assert "this pc only" in fams[name]["help"].lower()


def test_empty_snapshot_exports_only_info_and_timestamps():
    """Nothing measured yet = absent series, never fake zeros."""
    fams = parse_exposition(render_metrics(MetricsSnapshot(version="1.0"), scrape_time=5.0))
    assert set(fams) == {"netspeedtray_info", "netspeedtray_scrape_timestamp_seconds"}


def test_nan_and_none_readings_are_absent_not_zero():
    snap = full_snapshot(cpu_ratio=float("nan"), gpu_ratio=None,
                         latency=(LatencyHop("gateway", None, None),))
    fams = parse_exposition(render_metrics(snap))
    for name in ("netspeedtray_cpu_utilization_ratio", "netspeedtray_gpu_utilization_ratio",
                 "netspeedtray_latency_rtt_seconds", "netspeedtray_packet_loss_ratio"):
        assert name not in fams


def test_newline_in_interface_name_cannot_inject_a_line():
    snap = full_snapshot(interfaces=(("evil\nnetspeedtray_fake 1", Rates(1, 1, 1, 1)),))
    fams = parse_exposition(render_metrics(snap))
    assert "netspeedtray_fake" not in fams


# --- snapshot honesty (GUI-side builder) --------------------------------------------------------
class _Windows:
    def __init__(self, gw_loss=0.0, anchor_loss=2.5, gw_ms=3.0, anchor_ms=20.0):
        self.gw_loss, self.anchor_loss = gw_loss, anchor_loss
        self._ms = {"gateway": gw_ms, "anchor": anchor_ms}

    def last_ms(self, hop):
        return self._ms[hop]


def _odometer_state(reset_day=1, period_key="2026-06-01"):
    """The REAL WidgetState odometer methods on a DB-less shell (same pattern as test_usage_counter)."""
    s = types.SimpleNamespace()
    s.config = {"data_cap_reset_day": reset_day}
    s.db_worker = MagicMock()
    s.logger = logging.getLogger("test.metrics.usage")
    s._usage = {"cumulative_up": 0.0, "cumulative_down": 0.0,
                "anchor_up": 0.0, "anchor_down": 0.0, "period_key": period_key}
    s._usage_loaded = True
    s._usage_last_persist = 1e18
    s._usage_live_counters = s._usage_live_sampled_at = s._usage_live_primary = None
    for name in ("_persist_usage_now", "_maybe_reanchor", "add_usage_bytes", "get_usage_this_period",
                 "get_usage_lifetime", "get_usage_period_key"):
        setattr(s, name, getattr(WidgetState, name).__get__(s))
    s._compute_period_key = WidgetState._compute_period_key
    return s


def _fake_widget(config=None, **attrs):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(config or {})
    controller = MagicMock()
    controller.live_rates = ({"Wi-Fi": (100.0, 400.0), "vEthernet": (9.0, 9.0)},
                             {"Wi-Fi": (100.0, 800.0), "vEthernet": (9.0, 9.0)},
                             (100.0, 400.0), (100.0, 800.0))
    controller.monitored_interface_names.return_value = {"Wi-Fi"}
    w = types.SimpleNamespace(config=cfg, controller=controller, widget_state=_odometer_state(),
                              latency_probe=object(), _latency_loss_windows=_Windows(),
                              cpu_usage=37.0, gpu_usage=12.0, ram_used=8.0, ram_total=16.0,
                              vram_used=1.0, vram_total=4.0)
    for k, v in attrs.items():
        setattr(w, k, v)
    return w


def test_rates_are_the_widget_values_and_only_monitored_interfaces():
    snap = build_snapshot(_fake_widget())
    assert snap.aggregate == Rates(rx=400.0, tx=100.0, measured_rx=800.0, measured_tx=100.0)
    assert [n for n, _ in snap.interfaces] == ["Wi-Fi"]   # vEthernet is not what this mode meters


def test_odometer_values_match_widget_state():
    w = _fake_widget()
    w.widget_state.add_usage_bytes(1_000.0, 50_000.0)
    fams = parse_exposition(render_metrics(build_snapshot(w)))
    up, down = w.widget_state.get_usage_this_period()
    assert value(fams, "netspeedtray_period_receive_bytes_total") == down
    assert value(fams, "netspeedtray_period_transmit_bytes_total") == up
    lup, ldown = w.widget_state.get_usage_lifetime()
    assert value(fams, "netspeedtray_receive_bytes_total") == ldown


def test_odometer_counters_are_monotonic_within_a_period(monkeypatch):
    w = _fake_widget()
    # Pin "today" inside the stored period so no rollover happens mid-test.
    monkeypatch.setattr(WidgetState, "_compute_period_key", staticmethod(lambda *_a, **_k: "2026-06-01"))
    w.widget_state._compute_period_key = WidgetState._compute_period_key
    seen = []
    for step in range(6):
        w.widget_state.add_usage_bytes(10.0 * step, 1000.0 * step)
        fams = parse_exposition(render_metrics(build_snapshot(w)))
        seen.append((value(fams, "netspeedtray_period_receive_bytes_total"),
                     value(fams, "netspeedtray_receive_bytes_total")))
    assert seen == sorted(seen) and seen[-1][0] == 15_000.0


def test_period_series_absent_until_the_odometer_loads():
    """get_usage_this_period answers (0, 0) before the DB load; exporting that 0 makes increase()
    count the whole period as new traffic the moment the real total appears."""
    w = _fake_widget()
    ws = w.widget_state
    ws._usage_loaded = False
    ws._try_load_usage = lambda: False
    snap = build_snapshot(w)
    assert snap.period_bytes is None and snap.lifetime_bytes is None and snap.period_start is None
    text = render_metrics(snap)
    assert "netspeedtray_period_" not in text and "netspeedtray_receive_bytes_total" not in text


def test_period_start_is_the_period_key_local_midnight():
    from datetime import date, datetime
    w = _fake_widget()
    snap = build_snapshot(w)
    d = date.fromisoformat(w.widget_state.get_usage_period_key())
    assert snap.period_start == datetime(d.year, d.month, d.day).timestamp()


def test_data_cap_exported_only_when_set():
    assert build_snapshot(_fake_widget()).data_cap_bytes is None
    snap = build_snapshot(_fake_widget({"data_cap_enabled": True, "data_cap_gb": 250, "data_cap_count": "total"}))
    assert snap.data_cap_bytes == 250e9 and snap.data_cap_counts == "total"


def test_latency_anchor_absent_when_public_probe_off():
    fams = parse_exposition(render_metrics(build_snapshot(_fake_widget({"latency_public_enabled": False}))))
    hops = {lbl["hop"] for lbl, _ in fams["netspeedtray_latency_rtt_seconds"]["samples"]}
    assert hops == {"gateway"}
    assert {lbl["hop"] for lbl, _ in fams["netspeedtray_packet_loss_ratio"]["samples"]} == {"gateway"}


def test_latency_anchor_present_when_public_probe_on():
    snap = build_snapshot(_fake_widget({"latency_public_enabled": True}))
    assert [h.hop for h in snap.latency] == ["gateway", "anchor"]
    assert snap.latency[1].loss_ratio == pytest.approx(0.025)


def test_latency_absent_when_latency_monitoring_off():
    assert build_snapshot(_fake_widget({"latency_enabled": False})).latency == ()


def test_hardware_only_while_its_monitor_is_on():
    """The endpoint never forces polling: a value present on the widget (e.g. forced by the open
    Monitor window) is NOT exported while the user's switch is off."""
    off = build_snapshot(_fake_widget({"monitor_cpu_enabled": False, "monitor_gpu_enabled": False}))
    assert (off.cpu_ratio, off.gpu_ratio, off.ram_used_bytes, off.vram_used_bytes) == (None,) * 4
    on = build_snapshot(_fake_widget({"monitor_cpu_enabled": True, "monitor_gpu_enabled": True,
                                      "monitor_ram_enabled": True, "monitor_vram_enabled": True}))
    assert on.cpu_ratio == pytest.approx(0.37) and on.gpu_ratio == pytest.approx(0.12)
    assert on.ram_used_bytes == 8 * 1024 ** 3 and on.vram_total_bytes == 4 * 1024 ** 3
    cpu_only = build_snapshot(_fake_widget({"monitor_cpu_enabled": True, "monitor_ram_enabled": False}))
    assert cpu_only.cpu_ratio is not None and cpu_only.ram_used_bytes is None


def test_monitoring_never_polled_yet_is_absent():
    snap = build_snapshot(_fake_widget({"monitor_cpu_enabled": True}, cpu_usage=float("nan")))
    assert snap.cpu_ratio is None


# --- controller: live_rates mirror the display ---------------------------------------------------
class _IO:
    def __init__(self, s, r):
        self.bytes_sent, self.bytes_recv = s, r


def test_controller_live_rates_match_display_and_zero_on_reprime():
    from unittest.mock import patch
    from netspeedtray.core.controller import StatsController
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"interface_mode": "selected", "selected_interfaces": ["Wi-Fi"]})
    c = StatsController(config=cfg, widget_state=MagicMock(spec=WidgetState))
    shown = []
    c.display_speed_updated.connect(lambda u, d: shown.append((u, d)))
    t0 = 1000.0  # fixed clock: (t0 + 1.0) - t0 must be exactly 1.0 (a real monotonic near 2**k rounds it)
    c.last_check_time = t0
    c.last_interface_counters = {"Wi-Fi": _IO(0, 0), "LAN": _IO(0, 0)}
    with patch("time.monotonic", return_value=t0 + 1.0):
        c._handle_network_counters({"Wi-Fi": _IO(1000, 4000), "LAN": _IO(7, 7)})
    disp, meas, agg, magg = c.live_rates
    assert agg == (1000, 4000) and magg == (1000, 4000)
    assert shown[-1] == pytest.approx((agg[0] * 8 / 1e6, agg[1] * 8 / 1e6))
    assert c.monitored_interface_names(disp) == {"Wi-Fi"}
    c.repriming_needed = 1
    with patch("time.monotonic", return_value=t0 + 2.0):
        c._handle_network_counters({"Wi-Fi": _IO(2000, 8000), "LAN": _IO(7, 7)})
    assert c.live_rates == ({}, {}, (0.0, 0.0), (0.0, 0.0)) and shown[-1] == (0.0, 0.0)


@pytest.mark.parametrize("mode,primary,expected", [
    ("auto", "Wi-Fi", {"Wi-Fi"}), ("auto", None, set()),
    ("selected", None, {"LAN"}), ("all_virtual", None, {"Wi-Fi", "LAN", "vEthernet (WSL)"}),
])
def test_monitored_interface_names_follow_the_mode(mode, primary, expected):
    from netspeedtray.core.controller import StatsController
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"interface_mode": mode, "selected_interfaces": ["LAN"]})
    c = StatsController(config=cfg, widget_state=MagicMock(spec=WidgetState))
    c.primary_interface = primary
    assert c.monitored_interface_names({"Wi-Fi": 0, "LAN": 0, "vEthernet (WSL)": 0}) == expected


# --- the server ----------------------------------------------------------------------------------
def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def endpoint():
    ep = MetricsEndpoint()
    yield ep
    ep.stop()


def get(port, path="/metrics", method="GET", host=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = dict(headers or {})
        if host:
            headers["Host"] = host
        conn.request(method, path, headers=headers)
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode("utf-8")
    finally:
        conn.close()


def test_binds_loopback_only(endpoint):
    port = free_port()
    st = endpoint.apply(True, port)
    assert st.state == M.STATE_RUNNING and st.url == f"http://127.0.0.1:{port}/metrics"
    assert endpoint._server.socket.getsockname() == ("127.0.0.1", port)
    assert endpoint._server.socket.family == socket.AF_INET
    assert M.HOST == "127.0.0.1"


def test_serves_metrics_with_prometheus_content_type(endpoint):
    port = free_port()
    endpoint.apply(True, port)
    endpoint.publish(full_snapshot())
    status, headers, body = get(port)
    assert status == 200
    assert headers["Content-Type"] == "text/plain; version=0.0.4; charset=utf-8"
    fams = parse_exposition(body)
    assert value(fams, "netspeedtray_info", version="9.9.9") == 1
    assert abs(value(fams, "netspeedtray_scrape_timestamp_seconds") - time.time()) < 5


def test_only_get_metrics_is_served(endpoint):
    port = free_port()
    endpoint.apply(True, port)
    assert get(port, "/")[0] == 404
    assert get(port, "/metrics/../etc")[0] == 404
    assert get(port, method="POST")[0] == 405
    assert get(port, method="HEAD")[0] == 200


def test_foreign_host_header_is_refused(endpoint):
    """DNS rebinding: a web page whose own name resolves to 127.0.0.1 sends its own Host."""
    port = free_port()
    endpoint.apply(True, port)
    assert get(port, host=f"evil.example:{port}")[0] == 403
    assert get(port, host=f"localhost:{port}")[0] == 200
    assert get(port, host=f"127.0.0.1:{port}")[0] == 200


def test_responses_carry_nosniff(endpoint):
    port = free_port()
    endpoint.apply(True, port)
    for path in ("/metrics", "/nope"):
        _, headers, _ = get(port, path)
        assert headers.get("X-Content-Type-Options") == "nosniff", path


@pytest.mark.parametrize("hdrs", [
    {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"},
    {"Host": "evil.example"},
])
def test_web_page_requests_are_refused_without_spending_the_rate_limit(endpoint, hdrs):
    """A web page can fire blind no-cors fetches at 127.0.0.1. It can't read the answer (CORS), but
    if those requests counted toward the bucket it could starve a real scraper. Refused first."""
    port = free_port()
    endpoint.apply(True, port)
    for _ in range(M.RATE_LIMIT_BURST * 2):
        assert get(port, headers=hdrs)[0] == 403
    assert get(port)[0] == 200     # the scraper's request still has its whole burst
    assert get(port, headers={"Sec-Fetch-Site": "none"})[0] == 200   # typed in the address bar


def test_overlong_request_line_is_refused(endpoint):
    port = free_port()
    endpoint.apply(True, port)
    assert get(port, "/metrics?" + "a" * 2000)[0] == 414


def test_non_loopback_peer_is_refused():
    srv = me._LoopbackHTTPServer.__new__(me._LoopbackHTTPServer)
    assert srv.verify_request(None, ("127.0.0.1", 5)) is True
    assert srv.verify_request(None, ("::1", 5)) is True
    assert srv.verify_request(None, ("192.168.1.20", 5)) is False
    assert srv.verify_request(None, ("0.0.0.0", 5)) is False


def test_rate_limit_answers_429():
    clock = [0.0]
    bucket = me._TokenBucket(rate=1.0, burst=2, clock=lambda: clock[0])
    assert [bucket.take() for _ in range(3)] == [True, True, False]
    clock[0] = 1.0
    assert bucket.take() is True


def test_off_by_default_no_socket():
    assert constants.config.defaults.DEFAULT_CONFIG["metrics_endpoint_enabled"] is False
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    w = types.SimpleNamespace(config=dict(constants.config.defaults.DEFAULT_CONFIG),
                              metrics_endpoint=None, logger=logging.getLogger("t"))
    w._publish_metrics_snapshot = lambda: None
    NetworkSpeedWidget.apply_metrics_endpoint(w)
    assert w.metrics_endpoint is None   # never enabled: not even the object exists
    port = constants.network.metrics.DEFAULT_PORT
    ep = MetricsEndpoint()
    assert ep.apply(False, port).state == M.STATE_OFF and not ep.running


def test_port_in_use_reports_error_and_keeps_the_other_owner():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    ep = MetricsEndpoint()
    try:
        st = ep.apply(True, port)
        assert st.state == M.STATE_ERROR and st.error == "in_use" and st.port == port
        assert not ep.running
        assert ep.apply(True, port) is st   # a live-preview tick does not retry the same port
    finally:
        ep.stop()
        blocker.close()


def test_start_stop_and_move_on_toggle(endpoint):
    p1, p2 = free_port(), free_port()
    endpoint.apply(True, p1)
    assert get(p1)[0] == 200
    endpoint.apply(True, p2)                      # port changed: moves
    assert get(p2)[0] == 200
    with pytest.raises(OSError):
        get(p1)
    endpoint.apply(False, p2)                     # toggled off: nothing listens
    assert endpoint.status.state == M.STATE_OFF
    with pytest.raises(OSError):
        get(p2)
    endpoint.apply(True, p2)                      # back on
    assert get(p2)[0] == 200


def test_settings_apply_reconciles_the_endpoint():
    from netspeedtray.core.config_controller import ConfigController
    w = MagicMock()
    w.config = {"metrics_endpoint_enabled": True}
    ConfigController(w, MagicMock()).apply_all_settings()
    w.apply_metrics_endpoint.assert_called_once_with()


def test_shutdown_leaves_no_thread():
    before = set(threading.enumerate())
    ep = MetricsEndpoint()
    port = free_port()
    ep.apply(True, port)
    for _ in range(3):
        get(port)
    # a client that connects and never sends: stop() must not wait out its timeout, and no thread
    # may survive stop() - checked straight after it returns, not after a grace period.
    idle = socket.create_connection(("127.0.0.1", port))
    deadline = time.time() + 2
    while time.time() < deadline and not (set(threading.enumerate()) - before - {ep._thread}):
        time.sleep(0.01)   # until the server has accepted it and a handler thread waits on it
    assert set(threading.enumerate()) - before - {ep._thread}
    try:
        t0 = time.monotonic()
        ep.stop()
        assert time.monotonic() - t0 < 1.0, "quitting must not wait out an idle client's timeout"
        leftover = {t for t in set(threading.enumerate()) - before if t.is_alive()}
        assert not leftover, leftover
    finally:
        idle.close()
    with pytest.raises(OSError):
        get(port)


class _Drip:
    """A local client that trickles its request one byte at a time, forever (slowloris)."""

    def __init__(self, port, interval=0.3):
        self.sock = socket.create_connection(("127.0.0.1", port))
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, args=(interval,), daemon=True)
        self._t.start()

    def _run(self, interval):
        payload = b"GET /metrics HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Pad: " + b"a" * 10_000
        for b in payload:
            if self._stop.wait(interval):
                return
            try:
                self.sock.send(bytes([b]))
            except OSError:
                return

    def close(self):
        self._stop.set()
        self.sock.close()


def _settle(ep, n, before, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline and len(set(threading.enumerate()) - before - {ep._thread}) < n:
        time.sleep(0.01)


def test_stop_does_not_wait_for_trickling_clients():
    """Five slowloris clients: stop() (quit / toggle off / port change, on the GUI thread) must still
    return promptly - the request deadline and the stop flag are checked on EVERY read."""
    before = set(threading.enumerate())
    ep = MetricsEndpoint()
    port = free_port()
    ep.apply(True, port)
    drips = [_Drip(port, interval=0.2) for _ in range(5)]
    try:
        _settle(ep, M.MAX_CONCURRENT * 2, before)   # handler threads + the drip threads
        time.sleep(0.5)                             # every handler is now mid-request
        srv = ep._server
        assert len(srv.handler_threads) == M.MAX_CONCURRENT   # the 5th drip got no slot
        t0 = time.monotonic()
        ep.stop()
        assert time.monotonic() - t0 < 1.0
        # ...and the handlers really ended (the stop flag), not merely abandoned as daemons.
        assert not [t for t in srv.handler_threads if t.is_alive()]
    finally:
        for d in drips:
            d.close()


def test_trickling_client_loses_its_slot_at_the_request_deadline(endpoint):
    """A drip holds a concurrency slot for at most REQUEST_TIMEOUT_SEC, not per byte forever."""
    port = free_port()
    endpoint.apply(True, port)
    drips = [_Drip(port, interval=0.2) for _ in range(M.MAX_CONCURRENT)]
    try:
        time.sleep(M.REQUEST_TIMEOUT_SEC + 0.8)
        assert get(port)[0] == 200   # the slots were freed
    finally:
        for d in drips:
            d.close()


def test_running_port_cannot_be_hijacked(endpoint):
    """SO_EXCLUSIVEADDRUSE: while we listen, another socket asking for SO_REUSEADDR cannot bind
    our port and steal the scrapes (Windows-only option; skipped elsewhere)."""
    if not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        pytest.skip("Windows-only socket option")
    port = free_port()
    endpoint.apply(True, port)
    thief = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        thief.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with pytest.raises(OSError):
            thief.bind(("127.0.0.1", port))
    finally:
        thief.close()


def test_server_thread_never_reads_qt_owned_state():
    """The builder reads the widget on the GUI thread; the server thread only reads the frozen store."""
    main = threading.get_ident()
    readers, store_readers = set(), set()

    class Recording(types.SimpleNamespace):
        def __getattribute__(self, name):
            readers.add(threading.get_ident())
            return object.__getattribute__(self, name)

    fake = _fake_widget()
    widget = Recording(**vars(fake))

    class RecordingStore(SnapshotStore):
        def current(self):
            store_readers.add(threading.get_ident())
            return super().current()

    ep = MetricsEndpoint(RecordingStore())
    widget.metrics_endpoint = ep
    port = free_port()
    try:
        ep.apply(True, port)
        from netspeedtray.views.widget.main import NetworkSpeedWidget
        NetworkSpeedWidget._publish_metrics_snapshot(widget)   # the stats_ready slot, on this thread
        for _ in range(3):
            status, _, body = get(port)
            assert status == 200 and "netspeedtray_receive_bytes_per_second" in body
    finally:
        ep.stop()
    assert readers == {main}
    assert store_readers and main not in store_readers


# --- Settings ------------------------------------------------------------------------------------
@pytest.fixture
def page(qt_owned):
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings.pages.advanced import AdvancedPage
    return qt_owned(AdvancedPage(I18nStrings("en_US"), lambda: None))


def test_settings_round_trip_and_url(page):
    page.load_settings({"metrics_endpoint_enabled": True, "metrics_endpoint_port": 9300})
    out = page.get_settings()
    assert out["metrics_endpoint_enabled"] is True and out["metrics_endpoint_port"] == 9300
    assert page.metrics_url.text() == "http://127.0.0.1:9300/metrics"
    assert page.metrics_url.isReadOnly()


def test_settings_hide_port_and_url_while_off(page):
    page.load_settings({})
    assert page.get_settings()["metrics_endpoint_enabled"] is False
    assert page._metrics_port_card.isHidden() and page._metrics_url_card.isHidden()


def test_settings_show_port_in_use(page):
    page.load_settings({"metrics_endpoint_enabled": True, "metrics_endpoint_port": 9300})
    page.set_metrics_endpoint_status(me.EndpointStatus(M.STATE_ERROR, 9300, "in_use"))
    assert page.metrics_status_text() == "Port 9300 is already in use by another app. Choose another port."
    page.set_metrics_endpoint_status(me.EndpointStatus(M.STATE_RUNNING, 9300))
    assert page.metrics_status_text().startswith("Listening")
    page.set_metrics_endpoint_status(me.EndpointStatus(M.STATE_RUNNING, 9184))   # stale port
    assert page.metrics_status_text() == ""


def test_copy_button_copies_the_url(page, q_app):
    from PyQt6.QtWidgets import QApplication
    page.load_settings({"metrics_endpoint_enabled": True, "metrics_endpoint_port": 9301})
    page.metrics_copy_btn.click()
    assert QApplication.clipboard().text() == "http://127.0.0.1:9301/metrics"


def test_config_schema_bounds_the_port():
    rules = constants.config.defaults.VALIDATION_SCHEMA["metrics_endpoint_port"]
    assert rules["min"] == 1024 and rules["max"] == 65535 and rules["default"] == M.DEFAULT_PORT


# --- [::1] squatting and the bind retry (RC checkup) --------------------------------------------

def _has_ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


needs_v6 = pytest.mark.skipif(not _has_ipv6_loopback(), reason="no IPv6 loopback on this machine")


def _free_on_both() -> int:
    for _ in range(50):
        port = free_port()
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
                s.bind(("::1", port))
            return port
        except OSError:
            continue
    pytest.skip("no port free on both loopbacks")


@needs_v6
def test_the_ipv6_loopback_is_held_too_so_nothing_can_squat_it(endpoint):
    port = _free_on_both()
    assert endpoint.apply(True, port).state == M.STATE_RUNNING
    assert endpoint._server6.socket.getsockname()[:2] == ("::1", port)
    squatter = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            squatter.bind(("::1", port))
    finally:
        squatter.close()
    conn = http.client.HTTPConnection("::1", port, timeout=5)
    try:
        conn.request("GET", "/metrics", headers={"Host": f"[::1]:{port}"})
        assert conn.getresponse().status == 200
    finally:
        conn.close()


@needs_v6
def test_a_squatter_on_ipv6_loopback_stops_the_endpoint_from_serving(endpoint):
    port = _free_on_both()
    squatter = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    squatter.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    squatter.bind(("::1", port))
    squatter.listen(1)
    try:
        st = endpoint.apply(True, port)
        assert st.state == M.STATE_ERROR and st.error == "in_use"
        assert not endpoint.running
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", port))      # our 127.0.0.1 socket was released too
        probe.close()
    finally:
        squatter.close()


def test_a_busy_port_is_retried_with_backoff_until_it_frees():
    now = [1000.0]
    ep = MetricsEndpoint(clock=lambda: now[0])
    port = _free_on_both() if _has_ipv6_loopback() else free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        assert ep.apply(True, port).error == "in_use"
        assert ep.retry_if_due() is False                    # not due yet
        now[0] += ep.RETRY_DELAYS_SEC[0]
        assert ep.retry_if_due() is False                    # due, still busy -> next backoff
        assert ep._retry_at == now[0] + ep.RETRY_DELAYS_SEC[1]
        assert ep.apply(True, port).state == M.STATE_ERROR   # a settings tick does not hammer it
    finally:
        blocker.close()
    try:
        now[0] += ep.RETRY_DELAYS_SEC[1]
        assert ep.retry_if_due() is True
        assert ep.status.state == M.STATE_RUNNING
    finally:
        ep.stop()


def test_an_out_of_range_port_is_never_retried():
    ep = MetricsEndpoint(clock=lambda: 1e9)
    assert ep.apply(True, 80).state == M.STATE_ERROR
    assert ep.retry_if_due() is False


def test_the_widget_retries_the_endpoint_on_each_stats_tick():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    ep = MagicMock(running=False)
    ep.retry_if_due.return_value = False
    stub = types.SimpleNamespace(metrics_endpoint=ep, logger=logging.getLogger("t"))
    NetworkSpeedWidget._publish_metrics_snapshot(stub)
    ep.retry_if_due.assert_called_once()
    ep.publish.assert_not_called()


def test_an_ipv6_only_excluded_port_falls_back_to_ipv4_only(endpoint, monkeypatch):
    """WSAEACCES on [::1] (a Hyper-V/WSL IPv6-only excluded range): nobody can listen there, so the
    endpoint runs on 127.0.0.1 like a machine without IPv6, instead of reporting the port dead."""
    real = me._LoopbackHTTPServer

    def fake(port, store, host=M.HOST):
        if host == "::1":
            err = OSError(13, "An attempt was made to access a socket in a way forbidden")
            err.winerror = 10013
            raise err
        return real(port, store, host=host)
    monkeypatch.setattr(me, "_LoopbackHTTPServer", fake)
    port = free_port()
    st = endpoint.apply(True, port)
    assert st.state == M.STATE_RUNNING and endpoint._server6 is None
    assert get(port)[0] == 200
