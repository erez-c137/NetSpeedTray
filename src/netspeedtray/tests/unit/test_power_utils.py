"""
Tests for the hover card's battery section: utils/power_utils (state parsing,
capacity sanity, runtime projection, phantom guard constant) and the
UsageFlyout rows built from the summary dict.

The state reads are pure functions over a filled _SYSTEM_POWER_STATUS, so the
tests monkeypatch _power_status_struct rather than shelling out to the kernel.
"""
import types

import pytest
from PyQt6.QtWidgets import QApplication, QLabel

from netspeedtray.utils import power_utils
from netspeedtray.views.usage_flyout import UsageFlyout


def _status(ac=1, flags=0, pct=80, life=14400, full=18000):
    st = power_utils._SYSTEM_POWER_STATUS()
    st.ACLineStatus = ac
    st.BatteryFlag = flags
    st.BatteryLifePercent = pct
    st.BatteryLifeTime = life
    st.BatteryFullLifeTime = full
    return st


@pytest.fixture(autouse=True)
def _reset_capacity_cache():
    power_utils._design_mwh = None      # the process-wide cache must not leak across tests
    yield
    power_utils._design_mwh = None


# --- power_utils.read_power_state ---------------------------------------------------

def test_state_discharging(monkeypatch):
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=0, flags=0, pct=63))
    assert power_utils.read_power_state() == power_utils.PowerState("discharge", 63)


def test_state_charging_bit_beats_ac_online(monkeypatch):
    # Firmwares can report ACLineStatus online a moment before charging starts;
    # the charging bit is the authority.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0x4, pct=42))
    assert power_utils.read_power_state().mode == "charge"


def test_state_on_ac_idle(monkeypatch):
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0, pct=100))
    state = power_utils.read_power_state()
    assert state.mode == "ac" and state.charge_pct == 100


def test_state_no_battery_flag(monkeypatch):
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0x8, pct=255))
    assert power_utils.read_power_state() == power_utils.PowerState("none", None)


def test_state_unknown_percent_still_reports_mode(monkeypatch):
    # 255 = "unknown" charge; the mode is still worth showing.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=0, flags=0, pct=255))
    state = power_utils.read_power_state()
    assert state.mode == "discharge" and state.charge_pct is None


def test_state_api_failure_is_none(monkeypatch):
    monkeypatch.setattr(power_utils, "_power_status_struct", lambda: None)
    assert power_utils.read_power_state() == power_utils.PowerState("none", None)


# --- power_utils.read_design_capacity_mwh --------------------------------------------

def test_capacity_sane_value_accepted(monkeypatch):
    class _Batt:
        DesignedCapacity = 54700
    monkeypatch.setattr(power_utils.wmi, "WMI",
                        lambda namespace=None: types.SimpleNamespace(
                            query=lambda q: [_Batt()]))
    assert power_utils.read_design_capacity_mwh() == 54700


def test_capacity_nonsense_rejected(monkeypatch):
    class _Batt:
        DesignedCapacity = 5  # mWh - not a pack; a bare sensor misreporting
    monkeypatch.setattr(power_utils.wmi, "WMI",
                        lambda namespace=None: types.SimpleNamespace(
                            query=lambda q: [_Batt()]))
    assert power_utils.read_design_capacity_mwh() is None


def test_capacity_cached_across_calls(monkeypatch):
    calls = {"n": 0}

    def _wmi(namespace=None):
        calls["n"] += 1
        return types.SimpleNamespace(query=lambda q: [types.SimpleNamespace(DesignedCapacity=60000)])
    monkeypatch.setattr(power_utils.wmi, "WMI", _wmi)
    assert power_utils.read_design_capacity_mwh() == 60000
    assert power_utils.read_design_capacity_mwh() == 60000
    assert calls["n"] == 1


def test_capacity_wmi_failure_returns_none(monkeypatch):
    def _boom(namespace=None):
        raise OSError("WMI unavailable")
    monkeypatch.setattr(power_utils.wmi, "WMI", _boom)
    assert power_utils.read_design_capacity_mwh() is None


# --- power_utils.projected_runtime_hours ----------------------------------------------

def test_projection_math():
    # 50% of a 60 Wh pack at 10 W = 3 hours.
    assert power_utils.projected_runtime_hours(10.0, 50, 60000) == pytest.approx(3.0)


def test_projection_needs_all_inputs():
    assert power_utils.projected_runtime_hours(None, 50, 60000) is None
    assert power_utils.projected_runtime_hours(10.0, None, 60000) is None
    assert power_utils.projected_runtime_hours(10.0, 50, None) is None
    assert power_utils.projected_runtime_hours(0.0, 50, 60000) is None
    assert power_utils.projected_runtime_hours(10.0, 0, 60000) is None


def test_phantom_ceiling_is_sane():
    # 22 kW is above any laptop pack's discharge; below the observed 33-36 kW
    # AC-transition phantoms the guard exists to drop.
    assert 0 < power_utils.IMPLAUSIBLE_MW < 33000


# --- UsageFlyout battery section -----------------------------------------------------

@pytest.fixture(scope="session")
def q_app():
    return QApplication.instance() or QApplication([])


def _i18n():
    ns = types.SimpleNamespace()
    for k, v in {"BYTES_UNIT": "B", "KB_UNIT": "KB", "MB_UNIT": "MB",
                 "GB_UNIT": "GB", "TB_UNIT": "TB", "PB_UNIT": "PB"}.items():
        setattr(ns, k, v)
    return ns


def _texts(card):
    return [w.text() for w in card.findChildren(QLabel)]


def _power(mode="discharge", live_w=8.6, today_avg_w=9.1, projected_hours=3.2, charge_pct=67):
    return {"mode": mode, "live_w": live_w, "today_avg_w": today_avg_w,
            "projected_hours": projected_hours, "charge_pct": charge_pct}


def test_battery_rows_render(q_app):
    card = UsageFlyout(None, power=_power())
    texts = _texts(card)
    assert "Now" in texts and "8.6 W" in texts
    assert "3 h 12 m" in texts
    assert "67% battery" in texts


def test_battery_today_avg_omitted_when_no_history(q_app):
    summary = _power(today_avg_w=None)
    texts = _texts(UsageFlyout(None, power=summary))
    assert "Today avg" not in texts          # no history yet -> row absent, not dashed


def test_battery_charging_shows_plus_and_no_projection(q_app):
    summary = _power(mode="charge", live_w=38.2, projected_hours=None)
    texts = _texts(UsageFlyout(None, power=summary))
    assert "+38.2 W" in texts
    assert "Projected" not in texts


def test_battery_on_ac_value(q_app):
    summary = _power(mode="ac", live_w=None, projected_hours=None, today_avg_w=None)
    texts = _texts(UsageFlyout(None, power=summary))
    assert "On AC" in texts


def test_battery_discharging_without_reading_yet(q_app):
    # First seconds after launch: mode known, watts not stashed yet.
    summary = _power(mode="discharge", live_w=None, projected_hours=None)
    texts = _texts(UsageFlyout(None, power=summary))
    assert "—" in texts
    assert "Projected" not in texts          # no watts -> no projection
    assert "67% battery" in texts


def test_battery_section_absent_without_dict(q_app):
    texts = _texts(UsageFlyout(_i18n(), (0, 0), (0, 0)))
    assert not any("Now" in t for t in texts)


def test_state_flag_lies_about_no_battery(monkeypatch):
    # Real firmware quirk (IdeaPad 13ARP10): flag 9 = "low + no battery" while
    # charging at 72%. A valid percent is proof of a battery; the state must
    # fall through to the AC line, not vanish.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0x9, pct=72))
    state = power_utils.read_power_state()
    assert state.mode == "ac" and state.charge_pct == 72


def test_charge_ceiling_admits_real_adapter_rates():
    # A 65 W adapter charging the pack at 28.7 kW is a REAL reading (observed);
    # the discharge phantom ceiling (22 kW) must not apply to charge rates.
    assert power_utils.IMPLAUSIBLE_CHARGE_MW > 28000
    assert power_utils.IMPLAUSIBLE_CHARGE_MW > power_utils.IMPLAUSIBLE_MW
