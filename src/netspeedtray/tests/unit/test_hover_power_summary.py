"""Regression: _hover_power_summary must return a summary, not die silently.

The function referenced an undefined local (``mode`` instead of ``state.mode``), so
every call raised NameError, the except swallowed it (debug log only), and the hover
card's battery section never rendered - with the usage rows off, no card at all.

Deterministic: read_power_state is stubbed, so these run identically on machines
with or without a battery. Also guards the data source: the summary carries
NetSpeedTray's own readings only - no external data sources.
"""
import logging

from netspeedtray.utils import power_utils
from netspeedtray.views.widget.main import NetworkSpeedWidget


class _StubWidget:
    """Just the attributes _hover_power_summary reads on self."""

    def __init__(self, live_w, signed_w=None):
        self.logger = logging.getLogger("test")
        self.battery_power = signed_w
        self.system_power = live_w

    def _hover_battery_today_avg(self):  # the 30s-TTL DB summary - not under test here
        return None


def _forced_state_mode(monkeypatch, mode):
    monkeypatch.setattr(power_utils, "read_power_state",
                        lambda: power_utils.PowerState(mode, 63))


def test_power_summary_ac_with_system_power_shows_no_drain(monkeypatch):
    """On AC a whole-system reading (e.g. RAPL PSYS) must NOT read as battery drain."""
    _forced_state_mode(monkeypatch, power_utils.MODE_AC)
    summary = NetworkSpeedWidget._hover_power_summary(_StubWidget(live_w=8.8))
    assert summary is not None          # the NameError regression: used to be None
    assert summary["mode"] == power_utils.MODE_AC
    assert summary["live_w"] is None    # not 8.8 W posing as discharge
    assert summary["charge_pct"] == 63


def test_power_summary_discharging_uses_system_power(monkeypatch):
    _forced_state_mode(monkeypatch, power_utils.MODE_DISCHARGE)
    summary = NetworkSpeedWidget._hover_power_summary(_StubWidget(live_w=8.8))

    assert summary is not None
    assert summary["mode"] == power_utils.MODE_DISCHARGE
    assert summary["live_w"] == 8.8


def test_power_summary_charging_signed_value_wins(monkeypatch):
    """The signed battery poll outranks the ctypes mode (flag=9 firmware quirk)."""
    _forced_state_mode(monkeypatch, power_utils.MODE_AC)
    summary = NetworkSpeedWidget._hover_power_summary(_StubWidget(8.8, signed_w=-38.2))

    assert summary is not None
    assert summary["mode"] == power_utils.MODE_CHARGE   # negative watts = charging
    assert summary["live_w"] == -38.2


def test_power_summary_charging_system_power_not_drain(monkeypatch):
    _forced_state_mode(monkeypatch, power_utils.MODE_CHARGE)
    summary = NetworkSpeedWidget._hover_power_summary(_StubWidget(live_w=8.8))

    assert summary is not None
    assert summary["mode"] == power_utils.MODE_CHARGE
    assert summary["live_w"] is None


def test_power_summary_no_battery_omits_the_section(monkeypatch):
    """Desktops (and failed state reads) never see the battery section at all."""
    _forced_state_mode(monkeypatch, power_utils.MODE_NONE)
    assert NetworkSpeedWidget._hover_power_summary(_StubWidget(live_w=8.8)) is None


def test_power_summary_has_no_external_data_sources(monkeypatch):
    """The card reads nothing outside the app's own poll and stats DB: no budget/tier/
    heat-style fields from any external optimizer may appear in the summary."""
    _forced_state_mode(monkeypatch, power_utils.MODE_CHARGE)
    summary = NetworkSpeedWidget._hover_power_summary(_StubWidget(live_w=8.8))
    assert summary is not None
    assert not ({"budget_w", "budget_hours", "tier", "heat"} & set(summary))
