"""
GraphHoverTooltip — the Monitor graph's lightweight hover readout (ported feature: the one thing the
old Graph window had that the Monitor lacked). The mouse-snap path needs a live matplotlib canvas, so
that's covered by a standalone smoke script; here we cover the canvas-free formatting: the network graph
formats values as speed, the hardware graphs as percent, and the timestamp + series name are shown.
"""
from datetime import datetime
from types import SimpleNamespace

import matplotlib.dates as mdates

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.graph_hover import GraphHoverTooltip


def _host(stat):
    return SimpleNamespace(_current_stat=stat,
                           config={"unit_type": "bits_decimal", "decimal_places": 1},
                           i18n=I18nStrings("en_US"))


def test_network_hover_formats_speed():
    t = GraphHoverTooltip(_host("network"))
    x = mdates.date2num(datetime(2026, 6, 28, 14, 30, 0))
    html = t._format(x, [("Download", 4.0e6, "#42B883")])
    assert "14:30:00" in html
    assert "Download" in html and "Mbps" in html      # bytes/sec -> bits/sec speed
    assert "32.0" in html                              # 4 MB/s == 32 Mbps


def test_hardware_hover_formats_percent():
    t = GraphHoverTooltip(_host("hwcombined"))
    x = mdates.date2num(datetime(2026, 6, 28, 9, 5, 0))
    html = t._format(x, [("CPU", 42.0, "#00BCD4"), ("RAM", 67.0, "#4CAF50")])
    assert "09:05:00" in html
    assert "CPU" in html and "42%" in html
    assert "RAM" in html and "67%" in html             # multiple series at the cursor time
