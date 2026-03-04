"""
Unit tests for the AppActivityWorker.
"""

from types import SimpleNamespace
from unittest.mock import patch

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
    assert "93.184.216.34:443" in first_row["endpoint_preview"]

    assert second_payload["total_down_bps"] == pytest.approx(900.0)
    assert second_payload["total_up_bps"] == pytest.approx(700.0)


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
