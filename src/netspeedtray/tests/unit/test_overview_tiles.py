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

    def get_ram_history(self):
        return [_Snap(50.0)]

    def get_speed_history(self, start, end, iface, resolution='auto'):
        return [(datetime.now(), 1.0e6, 2.0e6), (datetime.now(), 1.5e6, 3.0e6)]   # (ts, up, dn)

    def get_hardware_history(self, stat, start, end):
        return [(datetime.now(), 42.0)]

    def summarize_network(self, direction, start, end, iface, poll):
        from netspeedtray.utils.summaries import summarize_raw
        return summarize_raw([2.0e6, 3.0e6] if direction == "download" else [1.0e6, 1.5e6])

    def summarize_hardware(self, stat, start, end, poll):
        from netspeedtray.utils.summaries import summarize_raw
        return summarize_raw([10.0, 42.0])


class _MW:
    """A minimal stand-in for the main widget."""
    cpu_usage = 42.0
    gpu_usage = 0.0
    gpu_present = True
    download_speed = 2.0e6
    upload_speed = 1.0e6
    ram_used = 8.0e9
    ram_total = 16.0e9
    vram_used = None
    vram_total = None
    session_start_time = None
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


def test_hardware_tiles_always_built(q_app):
    # The Monitor forces hardware collection while open, so the four hardware tiles always exist
    # regardless of the widget's config flags. Network is now the hero, not a tile.
    ov = OverviewTab(_MW(), _cfg(monitor_gpu_enabled=False, monitor_ram_enabled=False),
                     I18nStrings("en_US"))
    assert set(ov._tiles.keys()) == {"cpu", "gpu", "ram", "vram"}


def test_latency_pill_classification(q_app):
    from types import SimpleNamespace
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    L = lambda gw, an, ls: ov._latency_html(SimpleNamespace(latency_gw=gw, latency_anchor=an, latency_loss=ls))
    assert "Good" in L(12.0, None, 0.0) and "12 ms" in L(12.0, None, 0.0)
    assert "OK" in L(80.0, None, 0.0)
    assert "Slow" in L(None, None, 10.0) and "loss" in L(None, None, 10.0)   # all timeouts
    assert "Slow" in L(220.0, None, 0.0)
    assert "45 ms" in L(2.0, 45.0, 0.0)                                        # internet anchor wins
    assert L(None, None, 0.0) == ""                                           # no data -> empty


def test_syspower_breakdown_and_true_system(q_app):
    from types import SimpleNamespace
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    # CPU+GPU breakdown when no true system source (honest — not relabelled as "system").
    assert ov._syspower_text(SimpleNamespace(cpu_power=42.0, gpu_power=65.0, system_power=None)) \
        == "CPU 42 W  ·  GPU 65 W"
    # A real PSYS/battery reading shows a distinct "System N W" first, then the breakdown.
    txt = ov._syspower_text(SimpleNamespace(cpu_power=42.0, gpu_power=65.0, system_power=95.0))
    assert txt.startswith("System 95 W") and "CPU 42 W" in txt and "GPU 65 W" in txt
    # No power counters at all -> empty (no "0 W").
    assert ov._syspower_text(SimpleNamespace(cpu_power=None, gpu_power=None, system_power=None)) == ""


def test_hw_sub_omits_power_that_rounds_to_zero(q_app):
    # Regression for the GPU-tile flicker: a flaky iGPU power of 0.3 W rounds to "0 W" and would make
    # the sub-line appear/vanish; only show power that displays as >= 1 W. (temp >= 1 likewise.)
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    assert ov._hw_sub(None, 0.3) == ""
    assert ov._hw_sub(None, 0.0) == ""
    assert ov._hw_sub(0.0, None) == ""
    assert ov._hw_sub(55.0, 65.0) == "55°C  ·  65 W"
    assert ov._hw_sub(62.0, None) == "62°C"


def test_stat_tile_reserves_sub_line(q_app):
    # The sub-line keeps its height even when empty, so a coming-and-going reading never reflows the
    # tile (the cause of the title/% jumping up and down).
    from netspeedtray.views.monitor.overview.tiles import StatTile
    t = StatTile("iGPU", "#FF9800")
    assert t._sub.minimumHeight() > 0
    t.set("23%", [1.0, 2.0], vmax=100.0, sub_text="")
    assert t._sub.text() == " "                 # blank line reserved, not hidden
    t.set("23%", [1.0, 2.0], vmax=100.0, sub_text="55°C")
    assert t._sub.text() == "55°C"


def test_gpu_tile_hides_on_no_gpu(q_app):
    # Review fix: a confirmed no-GPU box (gpu_present False) hides the GPU tile instead of 0%.
    mw = _MW()
    mw.gpu_present = False
    ov = OverviewTab(mw, _cfg(), I18nStrings("en_US"))
    ov.show()
    q_app.processEvents()
    assert not ov._tiles["gpu"].isVisibleTo(ov)
    ov.hide()


def test_tiles_show_live_values(q_app):
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    ov.show()                       # showEvent ticks immediately
    q_app.processEvents()
    assert ov._tiles["cpu"]._value.text() == "42%"
    assert ov._tiles["ram"]._value.text() == "50%"          # 8/16
    # network hero shows both directions in Mbps (bits_decimal)
    assert "Mbps" in ov._hero._down_v.text()
    assert "Mbps" in ov._hero._up_v.text()
    # VRAM has no reading (vram_used None) -> its tile hides itself
    assert not ov._tiles["vram"].isVisibleTo(ov)
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


def test_cap_bar_warns_by_threshold(q_app):
    """The cap bar stays calm under 80%, goes amber 80-99%, red at/over 100% (and clamps to 100)."""
    u = UsageTile(I18nStrings("en_US"))
    u.set((1e6, 2e6), (3e6, 4e6), cap=(4.0, 10.0, 40.0))
    ss = u._cap_bar.styleSheet()
    assert "#FFB900" not in ss and "#E81123" not in ss      # accent
    u.set((1e6, 2e6), (3e6, 4e6), cap=(8.5, 10.0, 85.0))
    assert "#FFB900" in u._cap_bar.styleSheet()             # amber
    u.set((1e6, 2e6), (3e6, 4e6), cap=(11.0, 10.0, 110.0))
    assert "#E81123" in u._cap_bar.styleSheet()             # red
    assert u._cap_bar.value() == 100                        # clamped


def test_network_hero_dual_sparkline(q_app):
    """The hero feeds BOTH download and upload series into one dual-trace sparkline."""
    from netspeedtray.views.monitor.overview.tiles import NetworkHero
    h = NetworkHero(I18nStrings("en_US"), "#4DA3FF", "#F0883E")
    h.set("12.4 Mbps", "0.5 Mbps", [1.0, 2.0, 3.0], [0.2, 0.5, 0.3], sub_text="Peak ↓ 3 ↑ 0.5")
    assert h._down_v.text() == "12.4 Mbps"
    assert h._up_v.text() == "0.5 Mbps"
    assert h._spark._series == [1.0, 2.0, 3.0]
    assert h._spark._series2 == [0.2, 0.5, 0.3]   # upload trace present
    assert h._sub.text() == "Peak ↓ 3 ↑ 0.5"


def test_sparkline_paints_baseline_when_sparse(q_app):
    """With <2 points the sparkline draws a faint baseline (keeps tile weight) instead of returning."""
    s = Sparkline("#4CAF50")
    s.resize(80, 36)
    s.set_series([])     # zero points -> baseline path, must not raise
    s.set_series([42.0], vmax=100.0)  # one point -> flat line at height, must not raise
    s.repaint()
