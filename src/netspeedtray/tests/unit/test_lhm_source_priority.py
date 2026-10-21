"""Where the LibreHardwareMonitor web server (#187, PR #276) sits among the sensor sources.

Order for CPU temperature: LHM HTTP > LHM v0.9.4/OHM WMI > ACPI thermal zones (with the #237/#275
placeholder rejection still applying to ACPI). For CPU power: LHM HTTP > RAPL > WMI. The HTTP read
must cost nothing when temperatures and power are off, and a hostile or absent endpoint must never
take a poll down with it.
"""
import socket
import threading
import time

import pytest
from unittest.mock import MagicMock, patch

from netspeedtray.core.lhm_http import LhmGpu, LhmHttpClient, LhmSnapshot
from netspeedtray.core.monitor_thread import StatsMonitorThread

from netspeedtray.tests.unit.test_cpu_temp_source_order import _acpi_zone, _lhm_cpu_package, _AMBIENT_ZONE


def _lhm(snapshot):
    client = MagicMock()
    client.get_snapshot.return_value = snapshot
    return client


@pytest.fixture
def thread(q_app):
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    return t


# --- CPU temperature ------------------------------------------------------------------------

def test_http_beats_wmi_and_acpi(thread):
    thread._lhm_http = _lhm(LhmSnapshot(cpu_temp=58.9, cpu_temp_sensor="Core (Tctl/Tdie)"))
    thread._wmi_ohm = _lhm_cpu_package(61.0)
    with patch.object(thread, "_init_ohm_wmi") as init_wmi, \
         patch("netspeedtray.core.monitor_thread.win32pdh", _acpi_zone(_AMBIENT_ZONE)):
        assert thread._poll_cpu_temperature() == 58.9
    init_wmi.assert_not_called()   # WMI is not even probed while HTTP answers


def test_http_without_a_cpu_sensor_falls_back_to_wmi(thread):
    """#237 hardware: LHM is up but publishes no CPU temperature - WMI/ACPI still get their turn."""
    thread._lhm_http = _lhm(LhmSnapshot(gpus=(LhmGpu("/gpu-nvidia/0", "RTX", temperature=46.0),)))
    thread._wmi_ohm = _lhm_cpu_package(61.0)
    with patch.object(thread, "_init_ohm_wmi"), \
         patch("netspeedtray.core.monitor_thread.win32pdh", _acpi_zone(_AMBIENT_ZONE)), \
         patch("netspeedtray.core.monitor_thread.win32com.client", None):
        assert thread._poll_cpu_temperature() == 61.0


def test_http_down_falls_back_to_acpi(thread):
    thread._lhm_http = _lhm(None)
    thread._wmi_ohm = None
    with patch.object(thread, "_init_ohm_wmi"), \
         patch("netspeedtray.core.monitor_thread.win32pdh", _acpi_zone(_AMBIENT_ZONE)), \
         patch("netspeedtray.core.monitor_thread.win32com.client", None):
        assert thread._poll_cpu_temperature() == pytest.approx(27.05)


def test_acpi_placeholder_is_still_rejected_behind_http(thread):
    """#237/#275: with HTTP and WMI both empty, a frozen round-kelvin ACPI zone is still no reading."""
    thread._lhm_http = _lhm(None)
    thread._wmi_ohm = None
    with patch.object(thread, "_init_ohm_wmi"), \
         patch("netspeedtray.core.monitor_thread.win32pdh", _acpi_zone([3000.0] * 6)), \
         patch("netspeedtray.core.monitor_thread.win32com.client", None):
        readings = [thread._poll_cpu_temperature() for _ in range(6)]
    assert readings[-1] is None


# --- CPU power ------------------------------------------------------------------------------

def test_http_power_beats_rapl(thread):
    thread._lhm_http = _lhm(LhmSnapshot(cpu_power=56.8))
    with patch.object(thread, "_init_power_query") as init_rapl:
        assert thread._poll_cpu_power() == 56.8
    init_rapl.assert_not_called()


def test_no_http_power_falls_back_to_rapl(thread):
    thread._lhm_http = _lhm(LhmSnapshot(cpu_temp=50.0))
    thread._power_query = 123
    thread._power_pkg_counter = 456
    thread._wmi_ohm = False
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterValue", return_value=(0, 15000.0)):
        assert thread._poll_cpu_power() == pytest.approx(15.0)


# --- Cost and robustness --------------------------------------------------------------------

def test_gpu_poll_does_not_touch_http_with_temps_and_power_off(thread):
    thread._lhm_http = _lhm(LhmSnapshot(cpu_temp=50.0))
    thread._gpu_query = None
    thread._gpu_query_retry_after = float("inf")
    thread._poll_gpu_hybrid(include_temp=False, include_power=False)
    thread._lhm_http.get_snapshot.assert_not_called()


def test_the_poll_loop_never_reads_http_with_temps_and_power_off(q_app):
    """No new polling cost for the majority who leave temperatures and power off."""
    config = {"monitor_cpu_enabled": True, "monitor_gpu_enabled": False,
              "show_hardware_temps": False, "show_hardware_power": False}
    mt = StatsMonitorThread(interval=0.01, config=config)
    mt._lhm_http = _lhm(LhmSnapshot(cpu_temp=50.0))
    cpu_percent = MagicMock(return_value=5.0)
    with patch("netspeedtray.core.monitor_thread.constants.timers.MINIMUM_INTERVAL_MS", 10),          patch("netspeedtray.core.monitor_thread.psutil.cpu_percent", cpu_percent):
        mt.start()
        deadline = time.monotonic() + 3.0
        while cpu_percent.call_count < 4 and time.monotonic() < deadline:   # prime + 3 ticks
            time.sleep(0.01)
        mt.stop()
    assert cpu_percent.call_count >= 4, "the CPU branch of the poll loop never ran"
    mt._lhm_http.get_snapshot.assert_not_called()


def test_gpu_presence_from_lhm_is_latched_through_a_backoff(thread):
    """With PDH unavailable, LHM's GPU is the presence evidence. During an LHM backoff window (or a
    snapshot with the GPU asleep) the Monitor's GPU tiles must not flap out and back in."""
    gpu = LhmSnapshot(gpus=(LhmGpu("/gpu-nvidia/0", "RTX", temperature=50.0),))
    thread._gpu_query = None
    thread._gpu_query_retry_after = float("inf")
    thread._lhm_http = MagicMock()
    thread._lhm_http.get_snapshot.side_effect = [gpu, None, LhmSnapshot()]
    thread._nvidia_smi_path = None
    thread._wmi_ohm = None
    with patch.object(thread, "_init_ohm_wmi"):
        results = [thread._poll_gpu_hybrid(include_temp=True, include_power=False) for _ in range(3)]
    assert [r.present for r in results] == [True, True, True]
    assert results[1].temp is None   # latched presence, never a latched reading


def test_turning_temps_on_retries_http_at_once(thread):
    """A config change re-arms the hardware sources; the HTTP backoff is cleared with them."""
    client = LhmHttpClient(port=1)
    client._retry_after = time.monotonic() + 1000
    thread._lhm_http = client
    thread._hw_queries_dirty = True
    thread._is_running = True

    def stop_after_one_tick(*_a, **_k):
        thread._is_running = False
        return {}

    with patch("netspeedtray.core.monitor_thread.psutil.net_io_counters", side_effect=stop_after_one_tick), \
         patch.object(thread, "_init_com"):
        thread.run()
    assert client._retry_after == 0.0


@pytest.fixture
def garbage_server():
    """Something that is not LHM listening on the port: answers every request with junk."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(4)
    sock.settimeout(0.05)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            try:
                conn.recv(4096)
                conn.sendall(b"SSH-2.0-OpenSSH_9.6\r\n")
            except OSError:
                pass
            finally:
                conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield sock.getsockname()[1]
    stop.set()
    t.join(1.0)
    sock.close()


def test_a_foreign_service_on_the_port_does_not_break_the_temp_poll(thread, garbage_server):
    """Review point 1, end to end: BadStatusLine from a non-LHM service used to escape
    _poll_cpu_temperature (unwrapped in the loop) and drop the whole tick's stats."""
    thread._lhm_http = LhmHttpClient(port=garbage_server)
    thread._wmi_ohm = None
    with patch.object(thread, "_init_ohm_wmi"), \
         patch("netspeedtray.core.monitor_thread.win32pdh", _acpi_zone(_AMBIENT_ZONE)), \
         patch("netspeedtray.core.monitor_thread.win32com.client", None):
        assert thread._poll_cpu_temperature() == pytest.approx(27.05)
        started = time.monotonic()
        thread._poll_cpu_power()
        assert time.monotonic() - started < 0.05   # backed off: no second attempt this window
