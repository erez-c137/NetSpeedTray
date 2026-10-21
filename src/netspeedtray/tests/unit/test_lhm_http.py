"""LibreHardwareMonitor web-server source (#187, PR #276).

Parsing tests feed data.json trees straight to ``parse_snapshot``. Client tests run against real
loopback servers started here (never port 8085), because the failure modes that matter - nothing
listening, a server that never answers, one that trickles bytes, something that is not LHM on the
port - live in the socket layer and are invisible to a mocked ``urlopen``.
"""
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

import pytest

from netspeedtray import constants
from netspeedtray.core.lhm_http import (
    LhmHttpClient,
    parse_lhm_value,
    parse_snapshot,
)


def hardware(name, hardware_id, *sensors):
    return {"Text": name, "HardwareId": hardware_id, "Children": list(sensors)}


def sensor(name, sensor_id, sensor_type, value):
    return {
        "Text": name, "SensorId": sensor_id, "Type": sensor_type,
        "Value": value, "Children": [],
    }


def tree(*children):
    return {"Text": "Sensor", "Children": list(children)}


_CPU_POWER_TREE = tree(hardware("AMD CPU", "/amdcpu/0",
                                sensor("Package", "/amdcpu/0/power/0", "Power", "42 W")))


class TestLhmHttp(unittest.TestCase):
    def test_default_endpoint_uses_numeric_loopback(self):
        client = LhmHttpClient()
        self.assertEqual(client.url, "http://127.0.0.1:8085/data.json")
        self.assertEqual(constants.lhm.HTTP_HOST, "127.0.0.1")

    def test_decimal_comma_and_point(self):
        self.assertEqual(parse_lhm_value("58,9 °C"), 58.9)
        self.assertEqual(parse_lhm_value("45,0 °C"), 45.0)
        self.assertEqual(parse_lhm_value("56.8 W"), 56.8)
        self.assertEqual(parse_lhm_value(" 61 °C "), 61.0)
        self.assertEqual(parse_lhm_value(47.5), 47.5)
        self.assertIsNone(parse_lhm_value("-"))
        self.assertIsNone(parse_lhm_value(""))
        self.assertIsNone(parse_lhm_value(None))
        self.assertIsNone(parse_lhm_value(True))
        self.assertIsNone(parse_lhm_value(float("nan")))

    def test_fahrenheit_display_is_converted_to_celsius(self):
        """LHM can be set to show Fahrenheit, and data.json carries the display string."""
        snapshot = parse_snapshot(tree(
            hardware("Intel Core", "/intelcpu/0",
                sensor("CPU Package", "/intelcpu/0/temperature/0", "Temperature", "131,0 °F"))))
        self.assertAlmostEqual(snapshot.cpu_temp, 55.0)

    def test_ryzen_and_real_multi_gpu_case(self):
        data = tree(
            hardware("Nuvoton NCT6686D", "/lpc/nct6686d/0",
                sensor("CPU Socket", "/lpc/nct6686d/0/temperature/4", "Temperature", "84,0 °C")),
            hardware("AMD Ryzen 7 9700X", "/amdcpu/0",
                sensor("CCD1 (Tdie)", "/amdcpu/0/temperature/0", "Temperature", "48,3 °C"),
                sensor("Core (Tctl/Tdie)", "/amdcpu/0/temperature/2", "Temperature", "58,9 °C"),
                sensor("Package", "/amdcpu/0/power/0", "Power", "56,8 W")),
            hardware("AMD Radeon(TM) Graphics", "/gpu-amd/0",
                sensor("GPU Core", "/gpu-amd/0/temperature/0", "Temperature", "44,0 °C"),
                sensor("GPU Core", "/gpu-amd/0/power/0", "Power", "21,0 W")),
            hardware("NVIDIA GeForce RTX 5060 Ti", "/gpu-nvidia/0",
                sensor("GPU Core", "/gpu-nvidia/0/temperature/0", "Temperature", "50,3 °C"),
                sensor("GPU Package", "/gpu-nvidia/0/power/0", "Power", "24,2 W")),
        )
        snapshot = parse_snapshot(data)
        self.assertEqual(snapshot.cpu_temp, 58.9)
        self.assertEqual(snapshot.cpu_power, 56.8)
        self.assertEqual(snapshot.cpu_temp_sensor, "Core (Tctl/Tdie)")
        self.assertEqual(snapshot.preferred_gpu.hardware_id, "/gpu-nvidia/0")
        self.assertEqual(snapshot.preferred_gpu.temperature, 50.3)
        self.assertEqual(snapshot.preferred_gpu.power, 24.2)

    def test_intel_cpu_package(self):
        snapshot = parse_snapshot(tree(
            hardware("Intel Core", "/intelcpu/0",
                sensor("CPU Package", "/intelcpu/0/temperature/0", "Temperature", "61.5 °C"),
                sensor("CPU Package", "/intelcpu/0/power/0", "Power", "72,4 W"))))
        self.assertEqual(snapshot.cpu_temp, 61.5)
        self.assertEqual(snapshot.cpu_power, 72.4)

    def test_cpu_without_a_temperature_sensor_reports_none(self):
        """#237: Core Ultra under LHM shows load and voltage only - no CPU temperature to invent."""
        snapshot = parse_snapshot(tree(
            hardware("Intel Core Ultra X7 358H", "/intelcpu/0",
                sensor("CPU Total", "/intelcpu/0/load/0", "Load", "12,0 %"),
                sensor("Core #1", "/intelcpu/0/voltage/1", "Voltage", "0,9 V")),
            hardware("NVIDIA GPU", "/gpu-nvidia/0",
                sensor("GPU Core", "/gpu-nvidia/0/temperature/0", "Temperature", "46,0 °C"))))
        self.assertIsNone(snapshot.cpu_temp)
        self.assertEqual(snapshot.preferred_gpu.temperature, 46.0)

    def test_gpu_core_cannot_become_cpu_sensor(self):
        snapshot = parse_snapshot(tree(
            hardware("NVIDIA GPU", "/gpu-nvidia/0",
                sensor("GPU Core", "/gpu-nvidia/0/temperature/0", "Temperature", "77 °C"))))
        self.assertIsNone(snapshot.cpu_temp)
        self.assertEqual(snapshot.preferred_gpu.temperature, 77.0)

    def test_sensor_id_is_authoritative_even_without_hardware_id(self):
        snapshot = parse_snapshot(tree(
            sensor("CPU Package", "/lpc/nct6686d/0/temperature/0", "Temperature", "84 °C"),
            sensor("GPU Core", "/gpu-nvidia/0/temperature/0", "Temperature", "50 °C"),
            sensor("Package", "/amdcpu/0/power/0", "Power", "56 W")))
        self.assertIsNone(snapshot.cpu_temp)
        self.assertEqual(snapshot.cpu_power, 56.0)

    def test_malformed_tree_shapes_are_ignored(self):
        snapshot = parse_snapshot({"Children": [None, 5, "x", {"Children": "nope"}]})
        self.assertIsNone(snapshot.cpu_temp)
        self.assertEqual(snapshot.gpus, ())
        self.assertIsNone(parse_snapshot([1, 2]).cpu_temp)


# --------------------------------------------------------------------------------------------
# Real loopback servers
# --------------------------------------------------------------------------------------------

def _http_ok(body: bytes) -> bytes:
    return (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)


class _Server:
    """One-shot-per-connection TCP server on an ephemeral loopback port; ``handler(conn)`` runs per
    accepted connection on a background thread."""

    def __init__(self, handler):
        self.handler = handler
        self.connections = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self.sock.settimeout(0.05)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            self.connections += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            conn.settimeout(3.0)
            _read_request(conn)
            self.handler(conn, self._stop)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._stop.set()
        self._thread.join(1.0)
        self.sock.close()


def _read_request(conn):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


@pytest.fixture
def serve():
    servers = []

    def start(handler):
        server = _Server(handler)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def _client(port, **kwargs):
    kwargs.setdefault("failure_backoff_seconds", 30.0)
    return LhmHttpClient(port=port, **kwargs)


def _timed(client):
    started = time.monotonic()
    result = client.get_snapshot()
    return result, time.monotonic() - started


# Generous slack over the 0.5 s deadline for a loaded CI runner; the regressions these guard
# against took seconds (4.2 s for the trickle case on the owner's machine).
_DEADLINE_BOUND = constants.lhm.TOTAL_DEADLINE_SEC + 0.6


def test_reads_a_real_response_and_shares_it_within_the_cache_window(serve):
    body = json.dumps(_CPU_POWER_TREE).encode()
    server = serve(lambda conn, _stop: conn.sendall(_http_ok(body)))
    client = _client(server.port, cache_seconds=10)
    assert client.get_snapshot().cpu_power == 42.0
    assert client.get_snapshot().cpu_power == 42.0
    assert server.connections == 1


def test_utf8_bom_and_chunked_transfer_are_accepted(serve):
    body = b"\xef\xbb\xbf" + json.dumps(_CPU_POWER_TREE).encode()

    def chunked(conn, _stop):
        conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
        for i in range(0, len(body), 50):
            part = body[i:i + 50]
            conn.sendall(f"{len(part):x}\r\n".encode() + part + b"\r\n")
        conn.sendall(b"0\r\n\r\n")

    server = serve(chunked)
    assert _client(server.port).get_snapshot().cpu_power == 42.0


def test_connection_refused_returns_none_and_backs_off():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()   # nothing listens here now
    client = _client(port)
    result, elapsed = _timed(client)
    assert result is None
    assert elapsed < _DEADLINE_BOUND
    with patch.object(client, "_fetch_json") as fetch:
        assert client.get_snapshot() is None
        fetch.assert_not_called()


def test_a_server_that_never_answers_costs_one_bounded_attempt(serve):
    server = serve(lambda conn, stop: stop.wait(5.0))
    client = _client(server.port)
    result, elapsed = _timed(client)
    assert result is None
    assert elapsed < _DEADLINE_BOUND
    result, elapsed = _timed(client)   # inside the backoff window: no attempt at all
    assert result is None
    assert elapsed < 0.05
    assert server.connections == 1


def test_a_trickling_server_is_cut_off_at_the_total_deadline(serve):
    """Review point 2: a per-read timeout let one byte every 0.2 s hold a poll for 4.2 s."""
    def trickle(conn, stop):
        for byte in _http_ok(json.dumps(_CPU_POWER_TREE).encode()):
            if stop.wait(0.1):
                return
            conn.sendall(bytes([byte]))

    server = serve(trickle)
    result, elapsed = _timed(_client(server.port))
    assert result is None
    assert elapsed < _DEADLINE_BOUND


def test_a_trickling_header_is_cut_off_too(serve):
    def slow_headers(conn, stop):
        conn.sendall(b"HTTP/1.1 200 OK\r\n")
        while not stop.wait(0.1):
            conn.sendall(b"X")

    server = serve(slow_headers)
    result, elapsed = _timed(_client(server.port))
    assert result is None
    assert elapsed < _DEADLINE_BOUND


@pytest.mark.parametrize("reply", [
    b"NOT HTTP AT ALL\r\n\r\n",                                                     # BadStatusLine
    b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\nConnection: close\r\n\r\n{\"Text\"",  # truncated body
    b"",                                                                              # RemoteDisconnected
    b"HTTP/1.1 200 OK\r\n" + b"X-Long: " + b"a" * 70000 + b"\r\n\r\n",                # LineTooLong
], ids=["bad-status-line", "truncated-body", "remote-disconnected", "line-too-long"])
def test_something_else_on_the_port_never_escapes_and_backs_off(serve, reply):
    """Review point 1: http.client.HTTPException escaped get_snapshot(); the CPU-temp call is not
    wrapped in the poll loop, so every tick's stats (network speeds included) were dropped."""
    server = serve(lambda conn, _stop: conn.sendall(reply))
    client = _client(server.port)
    assert client.get_snapshot() is None
    assert client.get_snapshot() is None
    assert server.connections == 1


@pytest.mark.parametrize("reply", [
    b"HTTP/1.1 404 Not Found\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}",
    _http_ok(b"{broken"),
    _http_ok(b"\xff\xfe\x00garbage"),
], ids=["not-found", "bad-json", "bad-encoding"])
def test_non_lhm_responses_return_none_and_back_off(serve, reply):
    server = serve(lambda conn, _stop: conn.sendall(reply))
    client = _client(server.port)
    assert client.get_snapshot() is None
    assert client.get_snapshot() is None
    assert server.connections == 1


def test_an_oversized_response_is_refused(serve):
    body = json.dumps(_CPU_POWER_TREE).encode()
    server = serve(lambda conn, _stop: conn.sendall(_http_ok(body)))
    assert _client(server.port, max_response_bytes=len(body) - 1).get_snapshot() is None


def test_unexpected_errors_never_escape():
    client = _client(1)
    with patch.object(client, "_fetch_json", side_effect=RuntimeError("anything")):
        assert client.get_snapshot() is None
    with patch.object(client, "_fetch_json", return_value={"Children": []}), \
         patch("netspeedtray.core.lhm_http.parse_snapshot", side_effect=RecursionError):
        client.reset_backoff()
        assert client.get_snapshot() is None


def test_a_system_proxy_is_never_used(serve, monkeypatch):
    """Review point 3: urlopen honoured the Windows/env proxy even for 127.0.0.1."""
    for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")   # discard port: a proxied request fails
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    body = json.dumps(_CPU_POWER_TREE).encode()
    server = serve(lambda conn, _stop: conn.sendall(_http_ok(body)))
    with patch("urllib.request.getproxies", return_value={"http": "http://127.0.0.1:9"}):
        assert _client(server.port).get_snapshot().cpu_power == 42.0


@patch("netspeedtray.core.lhm_http.time.monotonic", side_effect=(100.0, 101.0, 131.0, 131.0))
def test_retried_only_after_the_backoff_window(_monotonic):
    client = _client(1, failure_backoff_seconds=30.0)
    with patch.object(client, "_fetch_json", side_effect=OSError("offline")) as fetch:
        assert client.get_snapshot() is None   # t=100: attempt, fail
        assert client.get_snapshot() is None   # t=101: inside the window
        assert client.get_snapshot() is None   # t=131: retried
    assert fetch.call_count == 2


def test_an_lhm_started_later_is_found_after_the_backoff(serve):
    client = _client(1, failure_backoff_seconds=0.05)
    with patch.object(client, "_fetch_json", side_effect=ConnectionRefusedError):
        assert client.get_snapshot() is None
    time.sleep(0.06)
    with patch.object(client, "_fetch_json", return_value=_CPU_POWER_TREE):
        assert client.get_snapshot().cpu_power == 42.0


def test_reset_backoff_retries_immediately():
    client = _client(1)
    with patch.object(client, "_fetch_json", side_effect=OSError("offline")):
        assert client.get_snapshot() is None
    client.reset_backoff()
    with patch.object(client, "_fetch_json", return_value=_CPU_POWER_TREE) as fetch:
        assert client.get_snapshot().cpu_power == 42.0
    fetch.assert_called_once()


def test_a_failure_drops_the_last_good_reading():
    """A stale snapshot must not keep answering for an LHM that has since gone away."""
    client = _client(1, cache_seconds=0.0)
    with patch.object(client, "_fetch_json", return_value=_CPU_POWER_TREE):
        assert client.get_snapshot().cpu_power == 42.0
    with patch.object(client, "_fetch_json", side_effect=OSError("closed")):
        assert client.get_snapshot() is None
