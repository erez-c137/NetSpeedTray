"""
The paint ``layout_mode`` follows the widget's OWN taskbar, not the primary (#229 design §3.7).

``NetworkSpeedWidget._refresh_cached_layout_mode`` read a bare ``get_taskbar_info()`` - always the
PRIMARY taskbar - while every other dock path (sizing, positioning, the refresh loop) honours
``preferred_monitor``. Harmless while every bar shares one edge (native Win11), wrong as soon as a
Preferred-Monitor user's bar sits on another edge (ExplorerPatcher / Windhawk side bars): the widget
was sized for a side bar but painted with the horizontal layout. The mirror widgets of "Show on all
taskbars" make the per-bar edge the normal case, so main gets the same fix.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.views.widget import main as widget_main


def _widget(preferred):
    w = MagicMock()
    # free_float off: the #188 free-float shortcut is not under test (it would enumerate screens).
    w.config = {"preferred_monitor": preferred, "free_float": False}
    w._cached_layout_mode = "horizontal"
    w._layout_mode_for_edge = widget_main.NetworkSpeedWidget._layout_mode_for_edge
    return w


@pytest.fixture
def two_bars(monkeypatch):
    """Primary bar on the BOTTOM edge, the secondary ("DISPLAY2") bar on the LEFT edge."""
    seen = []

    def fake_get_taskbar_info(preferred_screen_name=None):
        seen.append(preferred_screen_name)
        edge = constants.TaskbarEdge.LEFT if preferred_screen_name == "DISPLAY2" else constants.TaskbarEdge.BOTTOM
        return SimpleNamespace(hwnd=1, get_edge_position=lambda: edge)

    monkeypatch.setattr(widget_main, "get_taskbar_info", fake_get_taskbar_info)
    return seen


def test_layout_mode_reads_the_preferred_monitors_taskbar(two_bars):
    w = _widget("DISPLAY2")
    widget_main.NetworkSpeedWidget._refresh_cached_layout_mode(w)
    assert two_bars == ["DISPLAY2"]
    assert w._cached_layout_mode == "vertical"


def test_layout_mode_without_preference_is_the_primary(two_bars):
    w = _widget(None)
    widget_main.NetworkSpeedWidget._refresh_cached_layout_mode(w)
    assert two_bars == [None]
    assert w._cached_layout_mode == "horizontal"
