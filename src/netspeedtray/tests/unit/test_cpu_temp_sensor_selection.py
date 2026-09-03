"""
Regression tests for CPU-temperature sensor selection (#237).

`_poll_cpu_temperature` falls back to matching sensor *names* when no sensor is called exactly
"CPU Package", because some boards label the die sensor differently - AMD Ryzen exposes
"Core (Tctl/Tdie)", and desktop super-I/O chips report a CPU temperature under an /lpc/ identifier.
That fallback matched on the keyword "CORE", and NVIDIA's sensor is named literally **"GPU Core"**.

On a hybrid laptop whose CPU exposes no die sensor at all, the only temperature LHM could see was
the GPU's - so we matched it, took the max, and displayed the GPU's temperature as the CPU's. The
reporter's log: `CPU temperature: 46.0°C via LHM/OHM (sensor: GPU Core)`, from a machine whose
LibreHardwareMonitor report contains zero `/intelcpu/0/temperature/*` entries.

Showing another component's number under a CPU label is exactly the kind of confidently-wrong
reading this project refuses to ship, so the identifier now vetoes the name keywords.
"""

import pytest
from unittest.mock import MagicMock, patch

from netspeedtray.core.monitor_thread import StatsMonitorThread


def _sensor(name, identifier, value):
    s = MagicMock()
    s.Name = name
    s.Identifier = identifier
    s.Value = value
    return s


def _wmi_returning(sensors):
    """An LHM WMI stub. _poll_cpu_temperature queries twice: exact 'CPU Package', then broad."""
    wmi = MagicMock()

    def exec_query(query):
        if "Name='CPU Package'" in query:
            return [s for s in sensors if s.Name == "CPU Package"]
        return sensors

    wmi.ExecQuery.side_effect = exec_query
    return wmi


@pytest.fixture
def thread(q_app):
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    return t


def _no_other_sources():
    """Silence the two fallback temperature sources so a test sees only the LHM/OHM decision.

    `win32com.client` must be neutralised as well as `win32pdh`: source 3 calls
    `win32com.client.GetObject("winmgmts:...")`, which builds a *real* COM object. Besides making
    the test depend on the host's WMI, those objects are finalised later by the garbage collector -
    sometimes on a worker thread that never called CoInitialize, which raises CO_E_NOTINITIALIZED
    and prints a Windows fatal-exception dump into an otherwise green suite.
    """
    return (
        patch("netspeedtray.core.monitor_thread.win32pdh", None),
        patch("netspeedtray.core.monitor_thread.win32com.client", None),
    )


class TestGpuSensorsAreNeverReportedAsCpu:

    def test_gpu_core_alone_is_not_used_as_the_cpu_temperature(self, thread):
        """The #237 machine: an Intel Core Ultra with no CPU die sensor, next to an NVIDIA GPU.
        'GPU Core' contains 'CORE', so it used to match and be reported as the CPU."""
        thread._wmi_ohm = _wmi_returning([
            _sensor("GPU Core", "/gpu-nvidia/0/temperature/0", 46.0),
            _sensor("GPU Hot Spot", "/gpu-nvidia/0/temperature/2", 46.9),
        ])
        pdh, com = _no_other_sources()
        with patch.object(thread, "_init_ohm_wmi"), pdh, com:
            assert thread._poll_cpu_temperature() is None

    def test_a_real_cpu_sensor_still_wins_beside_a_hotter_gpu(self, thread):
        """The GPU runs hotter here. Taking the max across 'CPU-ish' names would return the GPU."""
        thread._wmi_ohm = _wmi_returning([
            _sensor("GPU Core", "/gpu-nvidia/0/temperature/0", 82.0),
            _sensor("Core (Tctl/Tdie)", "/amdcpu/0/temperature/0", 61.0),
        ])
        with patch.object(thread, "_init_ohm_wmi"):
            assert thread._poll_cpu_temperature() == 61.0

    @pytest.mark.parametrize("name,identifier", [
        ("GPU Core", "/gpu-nvidia/0/temperature/0"),
        ("GPU Core", "/gpu-amd/0/temperature/0"),
        ("GPU Core", "/gpu-intel/0/temperature/0"),
        ("Temperature", "/nvme/0/temperature/0"),
        ("Temperature", "/hdd/0/temperature/0"),
        ("Core Temperature", "/ssd/0/temperature/0"),
    ])
    def test_non_cpu_devices_are_rejected_whatever_they_are_named(self, thread, name, identifier):
        thread._wmi_ohm = _wmi_returning([_sensor(name, identifier, 55.0)])
        pdh, com = _no_other_sources()
        with patch.object(thread, "_init_ohm_wmi"), pdh, com:
            assert thread._poll_cpu_temperature() is None


class TestLegitimateCpuSensorsStillResolve:
    """The name fallback exists for real reasons (#148); it must keep working."""

    def test_exact_cpu_package_is_preferred(self, thread):
        thread._wmi_ohm = _wmi_returning([
            _sensor("GPU Core", "/gpu-nvidia/0/temperature/0", 90.0),
            _sensor("CPU Package", "/intelcpu/0/temperature/0", 58.0),
        ])
        with patch.object(thread, "_init_ohm_wmi"):
            assert thread._poll_cpu_temperature() == 58.0

    @pytest.mark.parametrize("name,identifier,expected", [
        ("Core (Tctl/Tdie)", "/amdcpu/0/temperature/0", 61.0),   # Ryzen (#148)
        ("CPU Core #1", "/intelcpu/0/temperature/1", 61.0),
        ("Core Max", "/intelcpu/0/temperature/5", 61.0),
        ("CPU", "/lpc/nct6797d/temperature/0", 61.0),            # desktop super-I/O chip
        ("CPU Core", "/lpc/it8728f/temperature/1", 61.0),
    ])
    def test_cpu_sensors_across_vendors_and_chipsets(self, thread, name, identifier, expected):
        thread._wmi_ohm = _wmi_returning([_sensor(name, identifier, expected)])
        with patch.object(thread, "_init_ohm_wmi"):
            assert thread._poll_cpu_temperature() == expected

    def test_the_hottest_cpu_sensor_wins(self, thread):
        """Per-core sensors: the package/hottest core is the useful number."""
        thread._wmi_ohm = _wmi_returning([
            _sensor("CPU Core #1", "/intelcpu/0/temperature/1", 51.0),
            _sensor("CPU Core #2", "/intelcpu/0/temperature/2", 59.0),
            _sensor("CPU Core #3", "/intelcpu/0/temperature/3", 54.0),
        ])
        with patch.object(thread, "_init_ohm_wmi"):
            assert thread._poll_cpu_temperature() == 59.0

    def test_out_of_range_values_are_ignored(self, thread):
        thread._wmi_ohm = _wmi_returning([
            _sensor("CPU Package", "/intelcpu/0/temperature/0", 0.0),      # exact-name query
            _sensor("CPU Core #1", "/intelcpu/0/temperature/1", 255.0),    # bogus
            _sensor("CPU Core #2", "/intelcpu/0/temperature/2", 57.0),
        ])
        with patch.object(thread, "_init_ohm_wmi"):
            assert thread._poll_cpu_temperature() == 57.0

# ---------------------------------------------------------------------------------------------
# ACPI thermal zones that are placeholders, not sensors (#237, #275)
# ---------------------------------------------------------------------------------------------

def _pdh_returning(raw_by_poll):
    """A win32pdh stub exposing ONE thermal zone whose High Precision Temperature counter returns
    the next value in `raw_by_poll` (tenths of a kelvin) on each poll. The standard Temperature
    counter is left unreadable so the high-precision path is the only source."""
    import itertools
    pdh = MagicMock()
    pdh.OpenQuery.return_value = 7
    pdh.EnumObjectItems.return_value = (None, ["\_TZ.TZ00"])
    handles = itertools.count(100)
    pdh.AddCounter.side_effect = lambda q, path: next(handles)   # 100 = HP, 101 = standard
    values = iter(raw_by_poll)

    def formatted(handle, fmt):
        if handle == 100:
            return (0, float(next(values)))
        raise OSError("counter unavailable")
    pdh.GetFormattedCounterValue.side_effect = formatted
    return pdh


class TestAcpiPlaceholderZonesAreNotSensors:
    """#275's board reports exactly 290.0 K on every poll and shows as 17 °C; #237's reports 300.0 K
    and shows as 27 °C. Real zones report tenths of a kelvin and drift. A zone that only ever
    returns one round-kelvin value is firmware filling in a field, and the honest readout is none."""

    def _only_pdh(self, thread, pdh):
        thread._init_ohm_wmi = lambda: None            # no LHM/OHM
        thread._wmi_ohm = None
        return (
            patch("netspeedtray.core.monitor_thread.win32pdh", pdh),
            patch("netspeedtray.core.monitor_thread.win32com.client", None),
        )

    def test_a_fixed_round_kelvin_zone_is_ignored(self, thread):
        p1, p2 = self._only_pdh(thread, _pdh_returning([2900.0] * 5))     # 290.0 K forever (#275)
        with p1, p2:
            assert [thread._poll_cpu_temperature() for _ in range(5)] == [None] * 5

    def test_three_hundred_kelvin_forever_is_the_237_case(self, thread):
        p1, p2 = self._only_pdh(thread, _pdh_returning([3000.0] * 3))
        with p1, p2:
            assert [thread._poll_cpu_temperature() for _ in range(3)] == [None] * 3

    def test_a_real_zone_is_read_normally(self, thread):
        p1, p2 = self._only_pdh(thread, _pdh_returning([3284.0, 3291.0]))  # 55.25, 55.95 °C
        with p1, p2:
            assert thread._poll_cpu_temperature() == pytest.approx(55.25)
            assert thread._poll_cpu_temperature() == pytest.approx(55.95)

    def test_a_zone_that_moves_is_trusted_even_on_a_round_value(self, thread):
        # Starts on 300.0 K (looks like #237), then moves - a real, coarse sensor. From then on it
        # is trusted, including when it lands back on the round value.
        p1, p2 = self._only_pdh(thread, _pdh_returning([3000.0, 3010.0, 3000.0]))
        with p1, p2:
            assert thread._poll_cpu_temperature() is None
            assert thread._poll_cpu_temperature() == pytest.approx(27.85)
            assert thread._poll_cpu_temperature() == pytest.approx(26.85)

    def test_the_placeholder_is_logged_once_not_per_poll(self, thread):
        p1, p2 = self._only_pdh(thread, _pdh_returning([2900.0] * 4))
        with p1, p2:
            for _ in range(4):
                thread._poll_cpu_temperature()
        placeholder_logs = [c for c in thread.logger.info.call_args_list
                            if "placeholder" in str(c.args[0]).lower()]
        assert len(placeholder_logs) == 1

    def test_the_wmi_fallback_applies_the_same_rule(self, thread):
        """Source 3 (MSAcpi_ThermalZoneTemperature) is the same zone through a different door."""
        zone = MagicMock(); zone.CurrentTemperature = 2900
        wmi = MagicMock(); wmi.ExecQuery.return_value = [zone]
        thread._init_ohm_wmi = lambda: None
        thread._wmi_ohm = None
        thread._wmi = wmi
        with patch("netspeedtray.core.monitor_thread.win32pdh", None):
            assert thread._poll_cpu_temperature() is None
            zone.CurrentTemperature = 3284
            assert thread._poll_cpu_temperature() == pytest.approx(55.25)

