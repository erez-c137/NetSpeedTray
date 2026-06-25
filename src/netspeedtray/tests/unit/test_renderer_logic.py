import pytest
from unittest.mock import MagicMock
from netspeedtray.views.graph.renderer import GraphRenderer
from netspeedtray.utils.widget_renderer import WidgetRenderer

# 1 Mbps expressed in bytes/sec (1_000_000 bits / 8 bits-per-byte).
MBPS_IN_BYTES = 125_000


def test_speed_band_uses_canonical_mbps():
    """
    Color banding must compare the canonical speed (Mbps) against the thresholds,
    never the on-screen number. Regression guard for the unit-mismatch bug where a
    sub-Mbps speed shown in Kbps (e.g. "500 Kbps") was banded as 'high' because the
    displayed number 500 was compared directly to the 10 (Mbps) high threshold.
    """
    band = WidgetRenderer._speed_band  # static method; no Qt needed
    high, low = 10.0, 1.0  # Mbps

    # Idle and below-low speeds -> default band.
    assert band(0, high, low) == "default"
    assert band(0.5 * MBPS_IN_BYTES, high, low) == "default"  # 0.5 Mbps; old code => 'high'

    # Low band is inclusive at the low threshold, up to (not incl.) high.
    assert band(1 * MBPS_IN_BYTES, high, low) == "low"
    assert band(5 * MBPS_IN_BYTES, high, low) == "low"

    # High band is inclusive at the high threshold and above.
    assert band(10 * MBPS_IN_BYTES, high, low) == "high"
    assert band(100 * MBPS_IN_BYTES, high, low) == "high"

    # Boundary edges (the >= bands are where an off-by-one would hide).
    assert band(0.99 * MBPS_IN_BYTES, high, low) == "default"  # just below low
    assert band(9.99 * MBPS_IN_BYTES, high, low) == "low"      # just below high


def test_speed_band_handles_bad_input():
    """Non-numeric input must fall back to the default band, never raise."""
    assert WidgetRenderer._speed_band(None, 10.0, 1.0) == "default"

def test_peak_label_placement_logic():
    # Mock dependencies for GraphRenderer
    parent_widget = MagicMock()
    i18n = MagicMock()
    
    # We need to mock _init_matplotlib to avoid actual window/canvas creation
    with MagicMock() as mock_init:
        GraphRenderer._init_matplotlib = mock_init
        renderer = GraphRenderer(parent_widget, i18n)
    
    ax = MagicMock()
    
    # CASE 1: Mid-graph peak (No flipping)
    # x: [0, 100], y: [0, 100], peak at (50, 50)
    ax.get_xlim.return_value = (0, 100)
    ax.get_ylim.return_value = (0, 100)
    
    offset, ha, va = renderer._get_peak_label_placement(ax, 50, 50)
    assert ha == 'left'
    assert va == 'bottom'
    assert offset == (8, 8)
    
    # CASE 2: Right-edge peak (Flip horizontal)
    # x: [0, 100], y: [0, 100], peak at (85, 50) -> x_norm = 0.85 >= 0.8
    offset, ha, va = renderer._get_peak_label_placement(ax, 85, 50)
    assert ha == 'right'
    assert va == 'bottom'
    assert offset == (-8, 8)
    
    # CASE 3: Top-edge peak (Flip vertical)
    # x: [0, 100], y: [0, 100], peak at (50, 95) -> y_norm = 0.95 >= 0.9
    offset, ha, va = renderer._get_peak_label_placement(ax, 50, 95)
    assert ha == 'left'
    assert va == 'top'
    assert offset == (8, -8)
    
    # CASE 4: Top-right corner (Flip both)
    # peak at (90, 92)
    offset, ha, va = renderer._get_peak_label_placement(ax, 90, 92)
    assert ha == 'right'
    assert va == 'top'
    assert offset == (-8, -8)

    print("Peak label placement tests passed!")

if __name__ == "__main__":
    test_peak_label_placement_logic()
