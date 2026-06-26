"""
Unit tests for the AppActivityWorker.
"""

from types import SimpleNamespace
from unittest.mock import patch

import psutil
import pytest
import socket

from netspeedtray.views.app_activity.worker import AppActivityWorker


def _conn(pid: int, local_ip: str, local_port: int, remote_ip: str, remote_port: int, status: str):
    return SimpleNamespace(
        pid=pid,
        type=socket.SOCK_STREAM,
        laddr=SimpleNamespace(ip=local_ip, port=local_port),
        raddr=SimpleNamespace(ip=remote_ip, port=remote_port),
        status=status,
    )


def test_sample_groups_by_pid_and_calculates_deltas():
    worker = AppActivityWorker()
    payloads = []
    worker.data_ready.connect(payloads.append)

    sample_index = {"value": 0}
    process_names = {111: "chrome.exe", 222: "updater.exe"}
    process_counters = {
        111: [(1000, 2000), (1500, 2600)],  # down +500 B/s, up +600 B/s
        222: [(5000, 7000), (5400, 7100)],  # down +400 B/s, up +100 B/s
    }

    def fake_net_connections(kind: str):
        assert kind == "inet"
        sample_index["value"] += 1
        return [
            _conn(111, "127.0.0.1", 51000, "93.184.216.34", 443, "ESTABLISHED"),
            _conn(111, "127.0.0.1", 51001, "142.250.74.14", 443, "ESTABLISHED"),
            _conn(222, "127.0.0.1", 52000, "151.101.1.69", 443, "ESTABLISHED"),
        ]

    class FakeProcess:
        def __init__(self, pid: int):
            self.pid = pid

        def name(self) -> str:
            return process_names[self.pid]

        def io_counters(self):
            step = max(0, sample_index["value"] - 1)
            read_bytes, write_bytes = process_counters[self.pid][step]
            return SimpleNamespace(read_bytes=read_bytes, write_bytes=write_bytes)

    with (
        patch("netspeedtray.views.app_activity.worker.psutil.net_connections", side_effect=fake_net_connections),
        patch("netspeedtray.views.app_activity.worker.psutil.Process", side_effect=lambda pid: FakeProcess(pid)),
        patch("netspeedtray.views.app_activity.worker.time.monotonic", side_effect=[100.0, 101.0]),
    ):
        worker.sample()  # baseline
        worker.sample()  # delta sample

    assert len(payloads) == 2
    second_payload = payloads[1]
    rows = second_payload["rows"]
    assert len(rows) == 2

    first_row = rows[0]
    assert first_row["pid"] == 111
    assert first_row["process_name"] == "chrome.exe"
    assert first_row["download_bps"] == pytest.approx(500.0)
    assert first_row["upload_bps"] == pytest.approx(600.0)
    assert first_row["connection_count"] == 2
    assert any("93.184.216.34:443" in endpoint for endpoint in first_row["endpoints"])

    assert second_payload["total_down_bps"] == pytest.approx(900.0)
    assert second_payload["total_up_bps"] == pytest.approx(700.0)
    assert second_payload["access_limited"] is False


def test_sample_handles_empty_connections():
    worker = AppActivityWorker()
    payloads = []
    worker.data_ready.connect(payloads.append)

    with (
        patch("netspeedtray.views.app_activity.worker.psutil.net_connections", return_value=[]),
        patch("netspeedtray.views.app_activity.worker.time.monotonic", return_value=100.0),
    ):
        worker.sample()

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["rows"] == []
    assert payload["total_down_bps"] == 0.0
    assert payload["total_up_bps"] == 0.0
    assert payload["access_limited"] is False


def test_sample_falls_back_when_global_connections_are_denied():
    worker = AppActivityWorker()
    payloads = []
    worker.data_ready.connect(payloads.append)

    class FallbackProc:
        def __init__(self, pid: int, allow: bool):
            self.pid = pid
            self._allow = allow

        def net_connections(self, kind: str):
            assert kind == "inet"
            if not self._allow:
                raise psutil.AccessDenied(pid=self.pid)
            return [_conn(self.pid, "127.0.0.1", 53000, "1.1.1.1", 443, "ESTABLISHED")]

    class FakeProcess:
        def __init__(self, pid: int):
            self.pid = pid

        def name(self) -> str:
            return "allowed.exe"

        def io_counters(self):
            return SimpleNamespace(read_bytes=1000, write_bytes=2000)

    with (
        patch("netspeedtray.views.app_activity.worker.psutil.net_connections", side_effect=psutil.AccessDenied()),
        patch(
            "netspeedtray.views.app_activity.worker.psutil.process_iter",
            return_value=[FallbackProc(777, True), FallbackProc(888, False)],
        ),
        patch("netspeedtray.views.app_activity.worker.psutil.Process", side_effect=lambda pid: FakeProcess(pid)),
        patch("netspeedtray.views.app_activity.worker.time.monotonic", return_value=100.0),
    ):
        worker.sample()

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["access_limited"] is True
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["pid"] == 777


# --- pure formatters ---------------------------------------------------------

def test_get_protocol_name():
    assert AppActivityWorker._get_protocol_name(socket.SOCK_STREAM) == "TCP"
    assert AppActivityWorker._get_protocol_name(socket.SOCK_DGRAM) == "UDP"
    assert AppActivityWorker._get_protocol_name(0) == "IP"  # unknown -> generic IP


def test_format_address_variants():
    f = AppActivityWorker._format_address
    assert f(SimpleNamespace(ip="1.2.3.4", port=443)) == "1.2.3.4:443"  # addr object
    assert f(("1.2.3.4", 443)) == "1.2.3.4:443"                         # tuple form
    assert f(SimpleNamespace(ip="1.2.3.4", port=None)) == "1.2.3.4"     # host only
    assert f(None) == "-"                                               # missing
    assert f(()) == "-"                                                 # empty


def test_format_connection_includes_status_but_omits_none():
    w = AppActivityWorker()
    est = _conn(1, "127.0.0.1", 5000, "1.1.1.1", 443, "ESTABLISHED")
    assert w._format_connection(est) == "TCP 127.0.0.1:5000 -> 1.1.1.1:443 ESTABLISHED"
    # A 'NONE'/blank status (common for UDP) is omitted, not printed literally.
    none_status = _conn(1, "127.0.0.1", 5000, "1.1.1.1", 443, "NONE")
    assert w._format_connection(none_status) == "TCP 127.0.0.1:5000 -> 1.1.1.1:443"


# --- rate edge cases ---------------------------------------------------------

def _single_pid_worker(io_sequence):
    """A worker whose one process returns io_counters from io_sequence per sample."""
    worker = AppActivityWorker()
    payloads = []
    worker.data_ready.connect(payloads.append)
    idx = {"value": 0}

    def fake_net_connections(kind):
        idx["value"] += 1
        return [_conn(1, "127.0.0.1", 5000, "1.1.1.1", 443, "ESTABLISHED")]

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "app.exe"

        def io_counters(self):
            step = max(0, idx["value"] - 1)
            rb, wb = io_sequence[step]
            return SimpleNamespace(read_bytes=rb, write_bytes=wb)

    return worker, payloads, fake_net_connections, FakeProcess


def test_first_sample_reports_zero_rates():
    """The baseline sample has no previous counters, so every rate is 0 (not garbage)."""
    worker, payloads, fake_nc, FakeProcess = _single_pid_worker([(9999, 8888)])
    with (
        patch("netspeedtray.views.app_activity.worker.psutil.net_connections", side_effect=fake_nc),
        patch("netspeedtray.views.app_activity.worker.psutil.Process", side_effect=lambda pid: FakeProcess(pid)),
        patch("netspeedtray.views.app_activity.worker.time.monotonic", return_value=100.0),
    ):
        worker.sample()
    row = payloads[0]["rows"][0]
    assert row["download_bps"] == 0.0
    assert row["upload_bps"] == 0.0


def test_counter_reset_clamps_rate_to_zero():
    """If counters DROP between samples (process restart / counter wrap), the rate is
    clamped to 0 rather than going negative."""
    worker, payloads, fake_nc, FakeProcess = _single_pid_worker([(10000, 20000), (500, 600)])
    with (
        patch("netspeedtray.views.app_activity.worker.psutil.net_connections", side_effect=fake_nc),
        patch("netspeedtray.views.app_activity.worker.psutil.Process", side_effect=lambda pid: FakeProcess(pid)),
        patch("netspeedtray.views.app_activity.worker.time.monotonic", side_effect=[100.0, 101.0]),
    ):
        worker.sample()
        worker.sample()
    row = payloads[1]["rows"][0]
    assert row["download_bps"] == 0.0  # clamped, never negative
    assert row["upload_bps"] == 0.0


def test_duplicate_endpoints_are_deduplicated():
    """Two identical connections for one PID collapse to a single endpoint + count of 1."""
    worker = AppActivityWorker()
    payloads = []
    worker.data_ready.connect(payloads.append)
    c1 = _conn(1, "127.0.0.1", 5000, "1.1.1.1", 443, "ESTABLISHED")
    c2 = _conn(1, "127.0.0.1", 5000, "1.1.1.1", 443, "ESTABLISHED")  # identical

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "app.exe"

        def io_counters(self):
            return SimpleNamespace(read_bytes=1, write_bytes=1)

    with (
        patch("netspeedtray.views.app_activity.worker.psutil.net_connections", return_value=[c1, c2]),
        patch("netspeedtray.views.app_activity.worker.psutil.Process", side_effect=lambda pid: FakeProcess(pid)),
        patch("netspeedtray.views.app_activity.worker.time.monotonic", return_value=100.0),
    ):
        worker.sample()
    row = payloads[0]["rows"][0]
    assert row["connection_count"] == 1
    assert len(row["endpoints"]) == 1
