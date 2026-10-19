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
