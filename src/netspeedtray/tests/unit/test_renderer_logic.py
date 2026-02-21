import pytest
from unittest.mock import MagicMock
from netspeedtray.views.graph.renderer import GraphRenderer

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
