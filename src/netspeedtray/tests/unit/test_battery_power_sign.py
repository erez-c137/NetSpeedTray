"""The battery poll's sign contract (#323 review regressions).

``StatsMonitorThread._poll_battery_power`` reports the battery flow SIGNED:
discharge positive, charge negative. ``system_power`` must never carry a
charge flow - the first draft let the (now signed) battery fallback leak
negative watts into the stats DB, which its own docstring forbids.
"""
import sys
import types

import pytest

from netspeedtray.core.monitor_thread import StatsMonitorThread


def _battery(discharging=False, rate=0, charging=False, charge_rate=0):
    """One Win32_BatteryStatus-shaped firmware report, in milliwatts."""
    return types.SimpleNamespace(Discharging=discharging, DischargeRate=rate,
                                 Charging=charging, ChargeRate=charge_rate)


def _fake_wmi(monkeypatch, samples):
    """Install a fake ``wmi`` module whose BatteryStatus() yields `samples`."""
    fake = types.ModuleType("wmi")
    fake.WMI = lambda namespace=None: types.SimpleNamespace(
        BatteryStatus=lambda: samples)
    monkeypatch.setitem(sys.modules, "wmi", fake)


@pytest.fixture(autouse=True)
def _a_laptop(monkeypatch):
    """Deterministic on any machine: the split tests below model a laptop (the thread asks
    has_battery() before its hover polls); the no-battery tests override it."""
    from netspeedtray.utils import power_utils
    monkeypatch.setattr(power_utils, "has_battery", lambda: True)


def _thread():
    # Never started as a QThread: only the poll methods are under test.
    return StatsMonitorThread(interval=1.0, config={})


# --- _poll_battery_power: the value's sign and sanity -------------------------------

def test_discharge_is_positive_watts(monkeypatch):
    _fake_wmi(monkeypatch, [_battery(discharging=True, rate=43_500)])
    assert _thread()._poll_battery_power() == pytest.approx(43.5)


def test_charge_is_negative_watts(monkeypatch):
    _fake_wmi(monkeypatch, [_battery(charging=True, charge_rate=38_200)])
    assert _thread()._poll_battery_power() == pytest.approx(-38.2)


def test_garbage_rate_is_dropped(monkeypatch):
    _fake_wmi(monkeypatch, [_battery(discharging=True, rate=600_000)])
    assert _thread()._poll_battery_power() is None


def test_garbage_charge_rate_is_dropped(monkeypatch):
    _fake_wmi(monkeypatch, [_battery(charging=True, charge_rate=600_000)])
    assert _thread()._poll_battery_power() is None


def test_idle_on_ac_is_none(monkeypatch):
    _fake_wmi(monkeypatch, [_battery()])
    assert _thread()._poll_battery_power() is None


# --- system_power keeps the sign out ------------------------------------------------

def test_charge_flow_never_reaches_system_power(monkeypatch):
    """THE #323 leak: a signed -38.2 W from the battery fallback must yield None,
    not a negative ``system_power`` sample for the stats history."""
    t = _thread()
    monkeypatch.setattr(t, "_poll_battery_power", lambda: -38.2)
    assert t._poll_system_power() is None


def test_discharge_still_serves_the_battery_fallback(monkeypatch):
    """The clamp must not break the legitimate case: no PSYS + discharging."""
    t = _thread()
    monkeypatch.setattr(t, "_poll_battery_power", lambda: 9.4)
    assert t._poll_system_power() == pytest.approx(9.4)


# --- _collect_power_stats: the split between the two power features -----------------

def test_charge_rate_reaches_battery_power_with_power_tiles_on(monkeypatch):
    """#323 review 3: with show_hardware_power on, a charging machine used to leave the
    hover's charge rate (+X W) blank forever - _poll_system_power is discharge-only, so
    the signed reading never reach battery_power. It rides along under its own switch."""
    t = _thread()
    t.config = {"show_hardware_power": True, "power_display_enabled": True}
    monkeypatch.setattr(t, "_poll_system_power", lambda: None)   # no PSYS; charge flow → None
    monkeypatch.setattr(t, "_poll_battery_power", lambda: -38.2)
    stats = {}
    t._collect_power_stats(stats, force_hw=False)
    assert stats["battery_power"] == pytest.approx(-38.2)
    assert "system_power" not in stats


def test_discharge_with_power_tiles_on_uses_system_power_only(monkeypatch):
    """Discharging under the power tiles: the battery fallback already lives in
    system_power; the duplicate battery poll must NOT add a battery_power flow."""
    t = _thread()
    t.config = {"show_hardware_power": True, "power_display_enabled": True}
    monkeypatch.setattr(t, "_poll_system_power", lambda: 43.5)
    monkeypatch.setattr(t, "_poll_battery_power", lambda: 43.5)   # cache: same positive reading
    stats = {}
    t._collect_power_stats(stats, force_hw=False)
    assert stats["system_power"] == pytest.approx(43.5)
    assert "battery_power" not in stats


def test_charge_rate_poll_skipped_when_power_display_off(monkeypatch):
    """The hover's consent switch governs its own extra poll: with the power tiles on but
    the hover off, no battery_power (and nothing) is written from a charge flow."""
    t = _thread()
    t.config = {"show_hardware_power": True}
    monkeypatch.setattr(t, "_poll_system_power", lambda: None)
    monkeypatch.setattr(t, "_poll_battery_power", lambda: -38.2)
    stats = {}
    t._collect_power_stats(stats, force_hw=False)
    assert stats == {}


def test_battery_only_split_discharge_to_system_charge_to_battery(monkeypatch):
    """The hover-only branch (power tiles off) keeps its contract: discharge is a
    system_power sample, charge rides battery_power."""
    t = _thread()
    t.config = {"power_display_enabled": True}
    monkeypatch.setattr(t, "_poll_battery_power", lambda: 12.7)
    stats = {}
    t._collect_power_stats(stats, force_hw=False)
    assert stats["system_power"] == pytest.approx(12.7)

    t2 = _thread()
    t2.config = {"power_display_enabled": True}
    monkeypatch.setattr(t2, "_poll_battery_power", lambda: -38.2)
    stats2 = {}
    t2._collect_power_stats(stats2, force_hw=False)
    assert stats2["battery_power"] == pytest.approx(-38.2)
    assert "system_power" not in stats2


# --- no battery: the hover's switch costs nothing ------------------------------------

def _no_battery(monkeypatch):
    from netspeedtray.utils import power_utils
    monkeypatch.setattr(power_utils, "has_battery", lambda: False)


def _wmi_must_not_run(t, monkeypatch):
    def _boom():
        raise AssertionError("the WMI battery poll ran on a machine with no battery")
    monkeypatch.setattr(t, "_poll_battery_power", _boom)


def test_no_battery_skips_the_wmi_poll_hover_only(monkeypatch):
    """A desktop with the battery switch on must not pay for a WMI BatteryStatus query every
    8 s for a section that can never show (power tiles off)."""
    _no_battery(monkeypatch)
    t = _thread()
    t.config = {"power_display_enabled": True}
    _wmi_must_not_run(t, monkeypatch)
    stats = {}
    t._collect_power_stats(stats, force_hw=False)
    assert stats == {}


def test_no_battery_skips_the_extra_charge_poll_with_power_tiles_on(monkeypatch):
    """With the power tiles on, the existing system_power path runs as before; only the
    hover's extra signed poll is skipped."""
    _no_battery(monkeypatch)
    t = _thread()
    t.config = {"show_hardware_power": True, "power_display_enabled": True}
    monkeypatch.setattr(t, "_poll_system_power", lambda: 61.0)   # a desktop RAPL PSYS reading
    _wmi_must_not_run(t, monkeypatch)
    stats = {}
    t._collect_power_stats(stats, force_hw=False)
    assert stats == {"system_power": pytest.approx(61.0)}
