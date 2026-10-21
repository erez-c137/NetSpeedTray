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

from netspeedtray import constants
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
    power_utils._capacity_retry_at = 0.0
    yield
    power_utils._design_mwh = None
    power_utils._capacity_retry_at = 0.0


# --- power_utils.read_power_state ---------------------------------------------------

def test_state_discharging(monkeypatch):
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=0, flags=0, pct=63))
    assert power_utils.read_power_state() == power_utils.PowerState("discharge", 63)


def test_state_charging_bit_beats_ac_online(monkeypatch):
    # Firmwares can report ACLineStatus online a moment before charging starts;
    # the charging bit (8 in the Win32 BatteryFlag) is the authority.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0x8, pct=42))
    assert power_utils.read_power_state().mode == "charge"


def test_state_on_ac_idle(monkeypatch):
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0, pct=100))
    state = power_utils.read_power_state()
    assert state.mode == "ac" and state.charge_pct == 100


def test_state_no_battery_flag(monkeypatch):
    # 128 = "no system battery" in the Win32 BatteryFlag.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0x80, pct=255))
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


def test_capacity_cached_across_calls(monkeypatch):  # a GOOD value is cached for the process
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


def test_ceiling_units_are_right_sized():
    # IMPLAUSIBLE_MW is milliwatts, and the ceiling is 500 W: above every real laptop
    # discharge and every real adapter's charge output, so only firmware garbage is dropped.
    # (The first draft read "22000" and thus clipped real discharges at 22 W.)
    assert constants.hardware.power.IMPLAUSIBLE_MW == 500_000


def test_ceiling_admits_a_max_class_adapter():
    # A 330 W gaming-class adapter charging the pack at full output is a REAL reading;
    # the single ceiling must not clip it.
    assert constants.hardware.power.IMPLAUSIBLE_MW > 330_000


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
    assert "3h 12m" in texts             # helpers.format_duration_short, like the Monitor
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


def test_state_flag_9_is_high_plus_charging(monkeypatch):
    # IdeaPad 13ARP10 at 72%: BatteryFlag 9 is 1 (high) + 8 (charging) per the Win32
    # docs - not "low + no battery". The first draft read the bits off by one position.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0x9, pct=72))
    assert power_utils.read_power_state() == power_utils.PowerState("charge", 72)


def test_state_desktop_reports_no_battery(monkeypatch):
    # What a desktop really returns (measured on the owner's PC, 2026-10-21): AC online,
    # BatteryFlag 128 "no system battery", percent 255. The section must not appear -
    # it used to read as MODE_AC and paint "On AC" on every desktop with the toggle on.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0x80, pct=255))
    assert power_utils.read_power_state() == power_utils.PowerState("none", None)
    assert power_utils.has_battery() is False


def test_state_critical_discharge_is_not_charging(monkeypatch):
    # 4 = critical (<5%), 2 = low: a nearly empty laptop on battery is DISCHARGING.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=0, flags=0x4 | 0x2, pct=3))
    assert power_utils.read_power_state() == power_utils.PowerState("discharge", 3)


def test_state_unknown_flag_is_not_read_as_bits(monkeypatch):
    # 255 = "unknown status": every bit is set, so it must not read as charging.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=255, flags=0xFF, pct=255))
    assert power_utils.read_power_state().mode == "none"
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=0, flags=0xFF, pct=55))
    assert power_utils.read_power_state() == power_utils.PowerState("discharge", 55)


def test_has_battery_on_a_laptop(monkeypatch):
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=0, flags=0x1, pct=80))
    assert power_utils.has_battery() is True
    monkeypatch.setattr(power_utils, "_power_status_struct", lambda: None)
    assert power_utils.has_battery() is False


# --- the battery section in the card: placement, locale, live refresh (#399/#394 consistency) ---

def _body_order(card):
    """Top-to-bottom text of the card body: one entry per body item, a sub-layout's labels joined."""
    from PyQt6.QtWidgets import QFrame
    body = card.findChild(QFrame, "usageCard").layout()
    out = []
    for i in range(body.count()):
        item = body.itemAt(i)
        if item.widget() is not None:
            w = item.widget()
            out.append(w.text() if isinstance(w, QLabel) else "<sep>")
        elif item.layout() is not None:
            lay = item.layout()
            out.append(" ".join(lay.itemAt(j).widget().text() for j in range(lay.count())
                                if isinstance(lay.itemAt(j).widget(), QLabel)))
    return out


def test_battery_sits_with_the_live_readings_above_hint_and_usage(q_app):
    """Battery is a live reading like the hardware rows (#399): it follows them, ahead of the
    gesture hint and the usage totals - not tacked on under the data-cap bar."""
    card = UsageFlyout(_i18n(), (1e6, 2e6), (3e6, 4e6), hint="Right-click for options",
                       power=_power(), hardware={"cpu": {"usage": 12.0}})
    order = [t for t in _body_order(card) if t != "<sep>"]
    cpu = next(i for i, t in enumerate(order) if t.startswith("CPU"))
    now = next(i for i, t in enumerate(order) if t.startswith("Now"))
    hint = order.index("Right-click for options")
    today = next(i for i, t in enumerate(order) if t.startswith("Today "))
    assert cpu < now < hint < today


def test_battery_values_use_the_locale_decimal_separator(q_app):
    """Same rule as the hardware rows beside it: "8,6 W" in de_DE, never a mixed "8.6 W"."""
    i18n = _i18n()
    i18n.DECIMAL_SEPARATOR = ","
    texts = _texts(UsageFlyout(i18n, power=_power(live_w=8.6, today_avg_w=9.1)))
    assert "8,6 W" in texts and "9,1 W" in texts
    charging = _texts(UsageFlyout(i18n, power=_power(mode="charge", live_w=-38.2, projected_hours=None)))
    assert "+38,2 W" in charging


def test_update_power_refreshes_the_rows_in_place(q_app):
    """The card stays up while the pointer rests on the widget, so the live draw refreshes in
    place (like update_hardware) - no rebuild while the same rows are shown."""
    card = UsageFlyout(None, power=_power(live_w=8.6, projected_hours=3.2, charge_pct=67))
    assert card.has_power
    assert card.update_power(_power(live_w=11.4, projected_hours=2.0, charge_pct=66)) is True
    texts = _texts(card)
    assert "11.4 W" in texts and "8.6 W" not in texts
    assert "2h 0m" in texts
    assert "66% battery" in texts


def test_update_power_asks_for_a_rebuild_when_the_rows_change(q_app):
    """Plugging in mid-hover drops "Projected" and turns "Now" green: a different row set, so
    the card says so (False) and the widget rebuilds it instead of showing stale rows."""
    card = UsageFlyout(None, power=_power())
    assert card.update_power(_power(mode="charge", live_w=-38.2, projected_hours=None)) is False
    assert UsageFlyout(_i18n(), (0, 0), (0, 0)).has_power is False


# --- independent review of 2c053df: state edge cases, Windows' estimate, capacity retry ---------

def test_state_no_battery_flag_wins_over_a_percent(monkeypatch):
    # 128 "no system battery" is honored by itself: a stray percent is not a battery.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0x80, pct=100))
    assert power_utils.read_power_state().mode == "none"


def test_state_unknown_ac_line_with_a_real_battery_is_shown(monkeypatch):
    # ACLineStatus 255 with a battery present: unknown-but-present, so the section still
    # shows (charge level, a dash for the rate) instead of vanishing.
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=255, flags=0x1, pct=60))
    assert power_utils.read_power_state() == power_utils.PowerState("unknown", 60)
    assert power_utils.has_battery() is True
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=255, flags=0xFF, pct=255))
    assert power_utils.read_power_state().mode == "none"


def test_windows_runtime_estimate(monkeypatch):
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=0, flags=0x1, pct=70, life=14400))
    assert power_utils.windows_runtime_hours() == pytest.approx(4.0)
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=0, flags=0x1, pct=70, life=0xFFFFFFFF))
    assert power_utils.windows_runtime_hours() is None      # "unknown"
    monkeypatch.setattr(power_utils, "_power_status_struct",
                        lambda: _status(ac=1, flags=0x1, pct=70, life=14400))
    assert power_utils.windows_runtime_hours() is None      # on AC it is not a runtime
    monkeypatch.setattr(power_utils, "_power_status_struct", lambda: None)
    assert power_utils.windows_runtime_hours() is None


def test_ac_line_status(monkeypatch):
    monkeypatch.setattr(power_utils, "_power_status_struct", lambda: _status(ac=0))
    assert power_utils.ac_line_status() == 0
    monkeypatch.setattr(power_utils, "_power_status_struct", lambda: None)
    assert power_utils.ac_line_status() is None


def test_capacity_sums_every_pack(monkeypatch):
    # Dual-battery laptops (ThinkPad internal + hot-swap): the runtime is the sum of packs.
    monkeypatch.setattr(power_utils.wmi, "WMI",
                        lambda namespace=None: types.SimpleNamespace(query=lambda q: [
                            types.SimpleNamespace(DesignedCapacity=23_000),
                            types.SimpleNamespace(DesignedCapacity=72_000)]))
    assert power_utils.read_design_capacity_mwh() == 95_000


def test_capacity_failure_is_retried_after_a_backoff(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(power_utils.time, "monotonic", lambda: clock["t"])
    calls = {"n": 0}

    def _wmi(namespace=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("RPC server unavailable")          # transient, e.g. right after resume
        return types.SimpleNamespace(query=lambda q: [types.SimpleNamespace(DesignedCapacity=60000)])
    monkeypatch.setattr(power_utils.wmi, "WMI", _wmi)
    assert power_utils.read_design_capacity_mwh() is None
    assert power_utils.read_design_capacity_mwh() is None   # inside the backoff: no new query
    assert calls["n"] == 1
    clock["t"] += constants.hardware.power.CAPACITY_RETRY_SEC + 1
    assert power_utils.read_design_capacity_mwh() == 60000  # not cached as "unavailable" forever
    assert calls["n"] == 2


def test_cached_capacity_never_queries_wmi(monkeypatch):
    """The hover (GUI thread) reads only the cache the monitor thread fills."""
    def _boom(namespace=None):
        raise AssertionError("WMI on the GUI thread")
    monkeypatch.setattr(power_utils.wmi, "WMI", _boom)
    assert power_utils.cached_design_capacity_mwh() is None
    power_utils._design_mwh = 54700
    assert power_utils.cached_design_capacity_mwh() == 54700


# --- the card: theme-aware charging green, a section caption -----------------------------------

def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


def _contrast(a, b):
    def lum(c):
        lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in _rgb(c)]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("dark", [True, False])
def test_charging_green_is_readable_in_both_themes(q_app, monkeypatch, dark):
    """#6CCB5F on the light card is ~2:1; the light theme uses Win11's light success green."""
    import re
    from netspeedtray.utils import styles as su
    monkeypatch.setattr(su, "is_dark_mode", lambda: dark)
    card = UsageFlyout(None, power=_power(mode="charge", live_w=-38.2, projected_hours=None))
    lbl = next(w for w in card.findChildren(QLabel) if w.text() == "+38.2 W")
    color = re.search(r"color:\s*(#[0-9A-Fa-f]{6})", lbl.styleSheet()).group(1)
    assert _contrast(color, su.semantic_colors()["card_bg"]) >= 4.5


def test_battery_section_has_a_caption(q_app):
    """Without a heading, "Now 9.6 W" under "CPU ... 8.5 W" read as another hardware row, and
    "Today avg" sat next to the usage "Today"."""
    i18n = _i18n()
    i18n.POWER_SECTION_TITLE = "Battery"
    card = UsageFlyout(i18n, power=_power(), hardware={"cpu": {"usage": 12.0}})
    order = [t for t in _body_order(card) if t != "<sep>"]
    cap = order.index("Battery")
    now = next(i for i, t in enumerate(order) if t.startswith("Now"))
    cpu = next(i for i, t in enumerate(order) if t.startswith("CPU"))
    assert cpu < cap < now
