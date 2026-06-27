"""
AppActivityWorker v2 — the HONEST connection-count model.

The worker no longer estimates per-app speed from disk I/O (the old lie). These tests pin the
honest contract: roll up PIDs by app identity, count live sockets / distinct hosts / TCP-UDP
exactly, the active-first sort, and — as a regression guard — that NO byte/speed keys leak back in.
"""
import socket
from types import SimpleNamespace
from unittest.mock import patch

import psutil
import pytest

from netspeedtray.views.app_activity.worker import AppActivityWorker


def _conn(pid, remote_ip, remote_port=443, status="ESTABLISHED", proto=socket.SOCK_STREAM,
          local_ip="192.168.1.5", local_port=50000):
    return SimpleNamespace(
        pid=pid, type=proto,
        laddr=SimpleNamespace(ip=local_ip, port=local_port),
        raddr=SimpleNamespace(ip=remote_ip, port=remote_port) if remote_ip else None,
        status=status,
    )


def _run(conns, names):
    """Run one sample() with mocked net_connections + process names; return the payload."""
    worker = AppActivityWorker()
    payloads = []
    worker.data_ready.connect(payloads.append)

    def fake_process(pid):
        return SimpleNamespace(name=lambda: names.get(pid, f"proc{pid}"))

    with (
        patch("netspeedtray.views.app_activity.worker.psutil.net_connections", return_value=conns),
        patch("netspeedtray.views.app_activity.worker.psutil.Process", side_effect=fake_process),
    ):
        worker.sample()
    assert len(payloads) == 1
    return payloads[0]


def _row(payload, name):
    return next(r for r in payload["rows"] if r["display_name"] == name)


def test_pids_roll_up_to_one_identity():
    conns = [_conn(111, "93.184.216.34"), _conn(222, "142.250.74.14"), _conn(333, "151.101.1.69")]
    names = {111: "chrome.exe", 222: "chrome.exe", 333: "chrome.exe"}
    payload = _run(conns, names)
    assert payload["app_count"] == 1
    row = payload["rows"][0]
    assert row["display_name"] == "chrome.exe"
    assert row["pids"] == [111, 222, 333]
    assert row["conn_count"] == 3


def test_conn_count_is_exact_socket_count():
    conns = [_conn(1, f"10.0.0.{i}") for i in range(1, 6)]  # 5 sockets
    payload = _run(conns, {1: "app.exe"})
    assert _row(payload, "app.exe")["conn_count"] == 5


def test_distinct_hosts_dedup_and_loopback_excluded():
    conns = [
        _conn(1, "8.8.8.8"), _conn(1, "8.8.8.8"),      # duplicate remote
        _conn(1, "127.0.0.1"), _conn(1, "169.254.1.1"),  # loopback / link-local excluded
        _conn(1, None, status="LISTEN"),                  # no remote
    ]
    payload = _run(conns, {1: "app.exe"})
    row = _row(payload, "app.exe")
    assert row["distinct_hosts"] == ["8.8.8.8"]
    assert row["host_count"] == 1
    assert row["conn_count"] == 5  # every socket still counted


def test_tcp_udp_classification():
    conns = [
        _conn(1, "8.8.8.8", proto=socket.SOCK_STREAM),
        _conn(1, "8.8.4.4", proto=socket.SOCK_DGRAM),
        _conn(1, "1.1.1.1", proto=socket.SOCK_DGRAM),
    ]
    row = _row(_run(conns, {1: "app.exe"}), "app.exe")
    assert row["tcp_count"] == 1
    assert row["udp_count"] == 2


def test_idle_flag_when_no_established():
    conns = [_conn(1, "8.8.8.8", status="LISTEN"), _conn(1, "8.8.4.4", status="TIME_WAIT")]
    row = _row(_run(conns, {1: "app.exe"}), "app.exe")
    assert row["established_count"] == 0
    assert row["is_idle"] is True


def test_sort_active_first_then_count_then_name():
    conns = [
        _conn(1, "8.8.8.8", status="LISTEN"), _conn(1, "8.8.4.4", status="LISTEN"),
        _conn(1, "1.1.1.1", status="LISTEN"),                                   # idle, 3 conns
        _conn(2, "9.9.9.9", status="ESTABLISHED"),                              # active, 1 conn
    ]
    payload = _run(conns, {1: "idle.exe", 2: "active.exe"})
    # active app sorts above the idle one even though idle has more connections
    assert payload["rows"][0]["display_name"] == "active.exe"
    assert payload["rows"][1]["display_name"] == "idle.exe"


def test_no_byte_or_speed_keys_leak_back():
    """Regression guard: the dishonest download/upload/rate keys must never reappear."""
    payload = _run([_conn(1, "8.8.8.8")], {1: "app.exe"})
    forbidden = {"download_bps", "upload_bps", "total_down_bps", "total_up_bps"}
    assert not (set(payload) & forbidden)
    for row in payload["rows"]:
        assert not (set(row) & forbidden)


def test_empty_connections():
    payload = _run([], {})
    assert payload["rows"] == []
    assert payload["app_count"] == 0
    assert payload["total_conn_count"] == 0
    assert payload["access_limited"] is False


def test_access_denied_falls_back_and_sets_limited():
    worker = AppActivityWorker()
    payloads = []
    worker.data_ready.connect(payloads.append)

    class FallbackProc:
        def __init__(self, pid, allow):
            self.pid = pid; self._allow = allow
        def net_connections(self, kind):
            if not self._allow:
                raise psutil.AccessDenied(pid=self.pid)
            return [_conn(self.pid, "1.1.1.1")]

    with (
        patch("netspeedtray.views.app_activity.worker.psutil.net_connections", side_effect=psutil.AccessDenied()),
        patch("netspeedtray.views.app_activity.worker.psutil.process_iter",
              return_value=[FallbackProc(777, True), FallbackProc(888, False)]),
        patch("netspeedtray.views.app_activity.worker.psutil.Process",
              side_effect=lambda pid: SimpleNamespace(name=lambda: "allowed.exe")),
    ):
        worker.sample()

    payload = payloads[0]
    assert payload["access_limited"] is True
    assert payload["app_count"] == 1
    assert payload["rows"][0]["pids"] == [777]


# --- pure formatters (unchanged) --------------------------------------------

def test_get_protocol_name():
    assert AppActivityWorker._get_protocol_name(socket.SOCK_STREAM) == "TCP"
    assert AppActivityWorker._get_protocol_name(socket.SOCK_DGRAM) == "UDP"
    assert AppActivityWorker._get_protocol_name(0) == "IP"


def test_format_address_variants():
    f = AppActivityWorker._format_address
    assert f(SimpleNamespace(ip="1.2.3.4", port=443)) == "1.2.3.4:443"
    assert f(("1.2.3.4", 443)) == "1.2.3.4:443"
    assert f(SimpleNamespace(ip="1.2.3.4", port=None)) == "1.2.3.4"
    assert f(None) == "-"


def test_format_connection_includes_status_but_omits_none():
    w = AppActivityWorker()
    est = _conn(1, "1.1.1.1", status="ESTABLISHED")
    assert w._format_connection(est) == "TCP 192.168.1.5:50000 -> 1.1.1.1:443 ESTABLISHED"
    none_status = _conn(1, "1.1.1.1", status="NONE")
    assert w._format_connection(none_status) == "TCP 192.168.1.5:50000 -> 1.1.1.1:443"


def test_remote_host_excludes_non_routable():
    h = AppActivityWorker._remote_host
    assert h(_conn(1, "8.8.8.8")) == "8.8.8.8"
    assert h(_conn(1, "127.0.0.1")) is None
    assert h(_conn(1, "169.254.5.5")) is None
    assert h(_conn(1, None)) is None
