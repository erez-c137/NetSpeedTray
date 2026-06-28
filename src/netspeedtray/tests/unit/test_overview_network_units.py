"""
The Overview's NetworkHero shows the live download/upload via format_speed, which expects BYTES/SEC —
but the widget stores download_speed/upload_speed in MBPS. The Overview must convert (reusing the
widget's own WidgetMetrics.net_bytes converter, DRY) or the headline number collapses to ~0.0 (most
visibly in "always Mbps" mode) while the byte-rate sparkline still looks fine.

Regression for the "Monitor Overview shows 0.0 up/down while the widget is correct" report.
"""
import pytest

from netspeedtray.utils.widget_paint import WidgetMetrics
from netspeedtray.utils.helpers import format_speed
from netspeedtray.constants.i18n import I18nStrings


def test_net_bytes_converts_mbps_to_bytes_per_second():
    up, down = WidgetMetrics(upload_mbps=8.0, download_mbps=80.0).net_bytes()
    assert up == pytest.approx(1_000_000.0)      # 8 Mbps = 1 MB/s
    assert down == pytest.approx(10_000_000.0)   # 80 Mbps = 10 MB/s


def test_overview_value_roundtrips_to_mbps_through_the_converter():
    """A 100 Mbps download must DISPLAY as ~100 Mbps in always-Mbps mode — not 0.0 — once converted."""
    _up, down_bps = WidgetMetrics(upload_mbps=50.0, download_mbps=100.0).net_bytes()
    text = format_speed(down_bps, I18nStrings("en_US"), force_mega_unit=True, decimal_places=1)
    number = float(text.replace(",", ".").split()[0])
    assert number == pytest.approx(100.0, abs=0.5)   # reads ~100 Mbps, NOT collapsed to ~0.0
