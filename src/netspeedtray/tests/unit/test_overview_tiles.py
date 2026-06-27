"""
OverviewTab tiles — the matplotlib-free glance. Verifies the sparkline stat tiles and the usage
tile read live values defensively, that the refresh timer only runs while visible, and that a
missing data source degrades to a dash instead of crashing.
"""
from datetime import datetime

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.overview.tab import OverviewTab
from netspeedtray.views.monitor.overview.tiles import Sparkline, UsageTile


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _Snap:
    def __init__(self, value):
        self.value = value
        self.timestamp = datetime.now()


class _Agg:
    def __init__(self, up, down):
        self.upload = up
        self.download = down
        self.timestamp = datetime.now()


class _WS:
    def get_aggregated_speed_history(self):
        return [_Agg(1.0e6, 2.0e6), _Agg(1.5e6, 3.0e6)]

    def get_cpu_history(self):
        return [_Snap(10.0), _Snap(42.0)]

    def get_gpu_history(self):
        return []


class _MW:
    """A minimal stand-in for the main widget."""
    cpu_usage = 42.0
    gpu_usage = 0.0
    ram_used = 8.0e9
    ram_total = 16.0e9
    vram_used = None
    vram_total = None
    widget_state = _WS()

    def _hover_usage_totals(self):
        return ((1.0e6, 2.0e6), (3.0e6, 4.0e6))  # (today, month) as (up, down)

    def _hover_cap_info(self):
        return (5.0, 10.0, 50.0)  # used_gb, cap_gb, pct


def _cfg(**over):
    c = {"monitor_cpu_enabled": True, "monitor_gpu_enabled": True, "monitor_ram_enabled": True,
         "unit_type": "bits_decimal", "decimal_places": 1}
    c.update(over)
    return c


def test_tiles_built_per_config(q_app):
    ov = OverviewTab(_MW(), _cfg(monitor_gpu_enabled=False, monitor_ram_enabled=False),
                     I18nStrings("en_US"))
    assert set(ov._tiles.keys()) == {"network", "cpu"}  # only what's enabled (+ network always)


def test_tiles_show_live_values(q_app):
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    ov.show()                       # showEvent ticks immediately
    q_app.processEvents()
    assert ov._tiles["cpu"]._value.text() == "42%"
    assert ov._tiles["ram"]._value.text() == "50%"          # 8/16 GB
    assert "Mbps" in ov._tiles["network"]._value.text()     # bits_decimal -> Mbps
    assert ov._tiles["network"]._sub.isVisible()            # the ↑ sub-line
    # usage tile: cap is set -> progress shows 50%
    assert ov._usage._cap_bar.isVisible()
    assert ov._usage._cap_bar.value() == 50
    ov.hide()


def test_timer_idles_when_hidden(q_app):
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    ov.show()
    q_app.processEvents()
    assert ov._timer.isActive()
    ov.hide()
    q_app.processEvents()
    assert not ov._timer.isActive()
    ov.teardown()
    assert not ov._timer.isActive()


def test_missing_data_source_degrades(q_app):
    """A main widget with no widget_state must not crash the tick (teardown race / early boot)."""
    class _Empty:
        cpu_usage = 0.0
        widget_state = None
    ov = OverviewTab(_Empty(), _cfg(monitor_gpu_enabled=False), I18nStrings("en_US"))
    ov.show()
    q_app.processEvents()
    ov._tick()  # must not raise
    ov.hide()


def test_sparkline_handles_short_series(q_app):
    s = Sparkline("#00BCD4")
    s.set_series([])          # empty
    s.set_series([5.0])       # single point (n < 2)
    s.resize(80, 36)
    s.set_series([1.0, 2.0, 3.0, 100.0], vmax=100.0)  # normal, with fixed scale
    # No exception == pass; paintEvent is exercised on the next event loop spin.


def test_usage_tile_hides_cap_when_unset(q_app):
    u = UsageTile(I18nStrings("en_US"))
    u.set((1.0e6, 2.0e6), (3.0e6, 4.0e6), cap=None)
    assert not u._cap_bar.isVisible()
    assert not u._cap_text.isVisible()
