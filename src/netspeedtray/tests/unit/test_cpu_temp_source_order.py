"""
Regression tests for CPU-temperature source ORDER (#216, with #237/#275 for the placeholder side).

On #216 a flat ~27 °C ACPI thermal zone (a motherboard/ambient zone, or a firmware placeholder)
shadowed LibreHardwareMonitor's correct CPU-package reading, because the ACPI zone was polled
first and any in-range value short-circuited the search. LHM/OHM must be asked first; ACPI is only
the fallback for machines with no hardware-monitor tool.

The ACPI zone here is deliberately a value that would pass every placeholder check (it moves, and
it is not a round kelvin), so these tests pin the ORDER itself, not the placeholder rule.
"""

import itertools
import pytest
from unittest.mock import MagicMock, patch

from netspeedtray.core.monitor_thread import StatsMonitorThread


@pytest.fixture
def thread(q_app):
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    # Forward-compatible with the LHM-over-HTTP source (#187 / PR #276): never let a real LHM web
    # server on the developer's machine answer for the sources under test. Unused before then.
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = None
    return t


def _acpi_zone(raw_by_poll):
    """A win32pdh stub with ONE thermal zone whose High Precision Temperature counter (tenths of
    a kelvin) returns the next value of `raw_by_poll` on each poll; the standard counter fails."""
    pdh = MagicMock()
    pdh.OpenQuery.return_value = 7
    pdh.EnumObjectItems.return_value = (None, [r"\_TZ.TZ00"])
    handles = itertools.count(100)
    pdh.AddCounter.side_effect = lambda q, path: next(handles)   # 100 = HP, 101 = standard
    values = iter(raw_by_poll)

    def formatted(handle, fmt):
        if handle == 100:
            return (0, float(next(values)))
        raise OSError("counter unavailable")
    pdh.GetFormattedCounterValue.side_effect = formatted
    return pdh


def _lhm_cpu_package(value):
    s = MagicMock()
    s.Name, s.Identifier, s.Value = "CPU Package", "/intelcpu/0/temperature/0", value
    wmi = MagicMock()
    wmi.ExecQuery.return_value = [s]
    return wmi


# A live ambient zone: 300.2 K -> 27.05 °C, then 300.3 K. Moves, not round: trusted as a sensor.
_AMBIENT_ZONE = [3002.0, 3003.0, 3002.0]


def test_lhm_wins_over_a_live_acpi_zone(thread):
    """#216: LHM reads 61 °C, the ACPI zone reads ~27 °C. The readout must be LHM's, every poll."""
    thread._wmi_ohm = _lhm_cpu_package(61.0)
    with patch.object(thread, "_init_ohm_wmi"), \
         patch("netspeedtray.core.monitor_thread.win32pdh", _acpi_zone(_AMBIENT_ZONE)), \
         patch("netspeedtray.core.monitor_thread.win32com.client", None):
        assert [thread._poll_cpu_temperature() for _ in range(3)] == [61.0] * 3


def test_acpi_is_still_the_fallback_without_lhm(thread):
    """No hardware-monitor tool: the same zone is the best reading available and is used."""
    thread._wmi_ohm = None
    with patch.object(thread, "_init_ohm_wmi"), \
         patch("netspeedtray.core.monitor_thread.win32pdh", _acpi_zone(_AMBIENT_ZONE)), \
         patch("netspeedtray.core.monitor_thread.win32com.client", None):
        assert thread._poll_cpu_temperature() == pytest.approx(27.05)


def test_an_lhm_failure_falls_through_to_acpi(thread):
    """LHM connected but its query throws (tool closed mid-session): fall back, don't go blank."""
    wmi = MagicMock()
    wmi.ExecQuery.side_effect = Exception("RPC server is unavailable")
    thread._wmi_ohm = wmi
    with patch.object(thread, "_init_ohm_wmi"), \
         patch("netspeedtray.core.monitor_thread.win32pdh", _acpi_zone(_AMBIENT_ZONE)), \
         patch("netspeedtray.core.monitor_thread.win32com.client", None):
        assert thread._poll_cpu_temperature() == pytest.approx(27.05)
        assert thread._wmi_ohm is None   # re-probed on the next poll
