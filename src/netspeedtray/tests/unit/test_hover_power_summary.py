"""Regression: _hover_power_summary must return a summary, not die silently.

The function referenced an undefined local (``mode`` instead of ``state.mode``), so
every call raised NameError, the except swallowed it (debug log only), and the hover
card's battery section never rendered - with the usage rows off, no card at all.

Deterministic: read_power_state is stubbed, so these run identically on machines
with or without a battery. Also guards the data source: the summary carries
NetSpeedTray's own readings only - no external data sources.
"""
import logging

import pytest

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
    # No real WMI or kernel reads in unit tests: the projection inputs are stubbed too.
    monkeypatch.setattr(power_utils, "windows_runtime_hours", lambda: None)
    monkeypatch.setattr(power_utils, "cached_design_capacity_mwh", lambda: None)
    monkeypatch.setattr(power_utils, "read_design_capacity_mwh",
                        lambda: (_ for _ in ()).throw(AssertionError("WMI on the GUI thread")))


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
    """The signed battery poll outranks the API's mode (its charging bit can lag the flow)."""
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


# --- live refresh of an open card -----------------------------------------------------

class _FakeCard:
    def __init__(self, accept=True):
        self.has_power = True
        self.accept = accept
        self.updates = []

    def update_power(self, power):
        self.updates.append(power)
        return self.accept


class _RefreshStub:
    _HOVER_HW_REFRESH_SEC = NetworkSpeedWidget._HOVER_HW_REFRESH_SEC

    def __init__(self, card, summary, enabled=True):
        self.logger = logging.getLogger("test")
        self.config = {"power_display_enabled": enabled}
        self._hover_card = card
        self._hover_power_refreshed = 0.0
        self.summary = summary
        self.rebuilt = 0

    def _hover_power_summary(self):
        return self.summary

    def _hide_usage_hover_card(self):
        self.hidden = getattr(self, "hidden", 0) + 1
        self._hover_card = None

    def _show_usage_hover_card(self):
        # The real one builds the new card and shows it BEFORE the old one goes, so the
        # pointer never sees a frame without a card (no hide first).
        assert getattr(self, "hidden", 0) == 0, "the old card was hidden before the rebuild"
        self.rebuilt += 1
        if self.summary is not None:
            self._hover_card = _FakeCard()


def test_refresh_pushes_fresh_values_into_the_open_card():
    card = _FakeCard()
    stub = _RefreshStub(card, {"mode": "discharge", "live_w": 9.0})
    NetworkSpeedWidget.refresh_hover_power(stub)
    assert card.updates == [{"mode": "discharge", "live_w": 9.0}]
    assert stub.rebuilt == 0
    # Throttled like the hardware rows: an immediate second poll does nothing.
    NetworkSpeedWidget.refresh_hover_power(stub)
    assert len(card.updates) == 1


def test_refresh_rebuilds_when_the_rows_change_or_the_section_goes():
    stub = _RefreshStub(_FakeCard(accept=False), {"mode": "charge", "live_w": -38.2})
    NetworkSpeedWidget.refresh_hover_power(stub)
    assert stub.rebuilt == 1
    assert getattr(stub, "hidden", 0) == 0     # swapped, not closed
    gone = _RefreshStub(_FakeCard(), None)     # switched off in Settings, or no battery anymore
    NetworkSpeedWidget.refresh_hover_power(gone)
    assert gone.rebuilt == 1 and gone.hidden == 1 and gone._hover_card is None


def test_refresh_is_a_no_op_without_a_battery_card():
    stub = _RefreshStub(None, {"mode": "ac"})
    NetworkSpeedWidget.refresh_hover_power(stub)        # no card open
    card = _FakeCard()
    card.has_power = False
    stub2 = _RefreshStub(card, {"mode": "ac"})
    NetworkSpeedWidget.refresh_hover_power(stub2)       # card without a battery section
    assert card.updates == [] and stub.rebuilt == 0 and stub2.rebuilt == 0


# --- projection sources (independent review of 2c053df) ------------------------------------

def test_projection_prefers_windows_estimate(monkeypatch):
    _forced_state_mode(monkeypatch, power_utils.MODE_DISCHARGE)
    monkeypatch.setattr(power_utils, "windows_runtime_hours", lambda: 3.5)
    monkeypatch.setattr(power_utils, "cached_design_capacity_mwh", lambda: 60000)
    summary = NetworkSpeedWidget._hover_power_summary(_StubWidget(live_w=8.8))
    assert summary["projected_hours"] == 3.5


def test_projection_falls_back_to_the_prefetched_capacity(monkeypatch):
    _forced_state_mode(monkeypatch, power_utils.MODE_DISCHARGE)
    monkeypatch.setattr(power_utils, "cached_design_capacity_mwh", lambda: 60000)
    summary = NetworkSpeedWidget._hover_power_summary(_StubWidget(live_w=8.8))
    assert summary["projected_hours"] == pytest.approx(60000 * 0.63 / 8.8 / 1000.0)


def test_unknown_ac_line_keeps_the_section(monkeypatch):
    """Battery present, AC line unknown: show the charge level, never a guessed drain."""
    _forced_state_mode(monkeypatch, power_utils.MODE_UNKNOWN)
    summary = NetworkSpeedWidget._hover_power_summary(_StubWidget(live_w=8.8))
    assert summary is not None and summary["live_w"] is None and summary["charge_pct"] == 63
