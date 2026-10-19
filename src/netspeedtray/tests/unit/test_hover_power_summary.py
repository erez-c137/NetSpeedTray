"""Regression: _hover_power_summary must return a summary, not die silently.

The function referenced an undefined local (``mode`` instead of ``state.mode``), so
every call raised NameError, the except swallowed it (debug log only), and the hover
card's battery section never rendered - with the usage rows off, no card at all.

Runs against a stub self so the whole real function body executes without the live
widget; skipped on machines that report no battery. Also guards the data source: the
summary carries NetSpeedTray's own readings only - no external data sources.
"""
import logging

import pytest

from netspeedtray.views.widget.main import NetworkSpeedWidget


class _StubWidget:
    """Just the attributes _hover_power_summary reads on self."""

    def __init__(self, live_w):
        self.logger = logging.getLogger("test")
        self.battery_power = None
        self.system_power = live_w

    def _hover_battery_today_avg(self):  # the 30s-TTL DB summary - not under test here
        return None


def test_power_summary_returns_dict():
    from netspeedtray.utils import power_utils
    state = power_utils.read_power_state()
    if state.mode == power_utils.MODE_NONE:
        pytest.skip("no battery on this machine")

    stub = _StubWidget(live_w=8.8)
    summary = NetworkSpeedWidget._hover_power_summary(stub)

    assert summary is not None          # the NameError regression: used to be None
    # The watts-authoritative rule: a positive live reading always reads as discharge,
    # whatever the API flags say (CI runners and AC-idle desktops report 'ac' with no
    # battery, and the stub's 8.8 W must win over that).
    assert summary["mode"] == power_utils.MODE_DISCHARGE
    assert summary["live_w"] == 8.8
    assert summary["charge_pct"] == state.charge_pct


def test_power_summary_has_no_external_data_sources():
    """The card reads nothing outside the app's own poll and stats DB: no budget/tier/
    heat-style fields from any external optimizer may appear in the summary."""
    from netspeedtray.utils import power_utils
    state = power_utils.read_power_state()
    if state.mode == power_utils.MODE_NONE:
        pytest.skip("no battery on this machine")

    summary = NetworkSpeedWidget._hover_power_summary(_StubWidget(live_w=8.8))
    assert summary is not None
    assert not ({"budget_w", "budget_hours", "tier", "heat"} & set(summary))
