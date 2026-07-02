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


def test_hw_percent_is_plain_text():
    """The percent is plain text now; the fixed percent COLUMN in draw_hardware_stats provides the
    constant width (right-aligned when memory is inline, left-aligned above a memory row), so the
    value reads naturally and still lines up. Regression guard against re-padding the string."""
    f = WidgetRenderer._fmt_hw_percent
    assert f(9) == "9%"
    assert f(10) == "10%"
    assert f(100) == "100%"
    assert f(0) == "0%"
    assert f(7.8) == "7%"            # truncates toward zero

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


def test_peak_label_is_localized():
    """The peak marker label must use the locale decimal separator AND the locale's
    speed unit, not a hardcoded '.'/'Mbps'. Regression guard for the graph-i18n fix:
    a German user's graph showed '12.3 Mbps' while the widget showed '12,3 Mbit/s'.
    Drives the real _add_peak_markers path with a real de_DE i18n."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from netspeedtray.constants.i18n import I18nStrings

    i18n = I18nStrings("de_DE")               # sep=',', MBITS_UNIT='Mbit/s'
    GraphRenderer._init_matplotlib = MagicMock()
    renderer = GraphRenderer(MagicMock(), i18n)

    fig, ax = plt.subplots()
    ax.set_xlim(0, 5)
    ax.set_ylim(0, 20)
    x_data = [0, 1, 2, 3, 4]
    y_data = [0.3, 0.6, 0.4, 12.34, 5.2]      # peak = 12.34 Mbps at index 3
    renderer._add_peak_markers(ax, x_data, y_data, "#4aa3ff", "Download", "download")
    text = renderer._peak_artists_download["label"].get_text()
    plt.close(fig)

    assert "12,3" in text, f"expected comma decimal separator, got {text!r}"
    assert "Mbit/s" in text, f"expected localized unit, got {text!r}"
    assert "Mbps" not in text and "12.3" not in text, f"leaked English unit/decimal: {text!r}"


# --- 6.2b hardware-graph shaping (smoothing + fixed/auto axis) -------------------

def test_smooth_series_preserves_length_and_range():
    """The Hann moving-average must keep the sample count and stay within the data envelope (a
    smoother can't introduce a spike above the max or below the min)."""
    import numpy as np
    ys = np.array([0, 100, 0, 100, 0, 100, 0, 100, 0, 100], dtype=float)
    out = GraphRenderer._smooth_series(ys, 5)
    assert len(out) == len(ys)
    assert out.min() >= ys.min() - 1e-9 and out.max() <= ys.max() + 1e-9
    # A real average pulls the alternating spikes toward the middle.
    assert out.max() < ys.max()


def test_smooth_series_noop_when_too_short():
    """Below the window length there's nothing to smooth - return the series untouched."""
    import numpy as np
    ys = np.array([10.0, 20.0, 30.0])
    out = GraphRenderer._smooth_series(ys, 5)
    assert list(out) == [10.0, 20.0, 30.0]


def test_apply_hw_ylim_fixed_vs_auto():
    """Fixed pins 0-100; auto scales to the data with a 10% floor (so an idle line isn't a sliver)."""
    from unittest.mock import MagicMock
    ax = MagicMock()
    GraphRenderer._apply_hw_ylim(None, ax, True, 12.0)
    ax.set_ylim.assert_called_with(0, 100)

    ax = MagicMock()
    GraphRenderer._apply_hw_ylim(None, ax, False, 40.0)     # 40 * 1.2 = 48
    ax.set_ylim.assert_called_with(0, 48.0)

    ax = MagicMock()
    GraphRenderer._apply_hw_ylim(None, ax, False, 2.0)      # below the floor -> 10
    ax.set_ylim.assert_called_with(0, 10.0)


# --- 6.2b review fix: toggle (single-stat) mode must honour hw_styles -------------
# Regression guard for the adversarial-review finding: the Monitor's "toggle" layout renders one
# CPU/GPU line via render(stat_type="cpu"/"gpu", hw_styles=...). That path must route through the
# hw-aware _render_hwsingle (configured colour + Smooth + fixed/auto axis), while the standalone
# GraphWindow (hw_styles=None) must keep the legacy _plot_high_res path. Mock-based to avoid a real
# canvas.draw() (which hangs under pytest-qt); the visual integration is covered by the render smoke.

def _mock_renderer():
    from unittest.mock import MagicMock
    GraphRenderer._init_matplotlib = MagicMock()
    r = GraphRenderer(MagicMock(), MagicMock())
    r.figure = MagicMock()
    r.canvas = MagicMock()
    r._current_text_color = "white"
    r._current_grid_color = "#444"
    r._render_hwsingle = MagicMock()
    r._plot_high_res = MagicMock(return_value=(None, None, None))
    r._configure_hardware_axes = MagicMock()
    return r


def _hw_list():
    import time
    now = time.time()
    data = [(now - 60 + i, 10.0 + i, 0.0) for i in range(10)]
    return data, now


def test_toggle_mode_dispatches_to_hwsingle_with_styles():
    from datetime import datetime
    r = _mock_renderer()
    data, now = _hw_list()
    hw = {"cpu": ("#ff8800", "-"), "smoothing": True, "fixed_axis": False}
    r.render(data, datetime.fromtimestamp(now - 60), datetime.fromtimestamp(now),
             "TIMELINE_60_MINUTES", stat_type="cpu", hw_styles=hw, force_rebuild=True)
    r._render_hwsingle.assert_called_once()
    assert r._render_hwsingle.call_args[0][-1] is hw     # the colour/smooth/axis styles reach the renderer
    r._plot_high_res.assert_not_called()                 # not the legacy path


def test_standalone_single_stat_uses_legacy_path():
    from datetime import datetime
    r = _mock_renderer()
    data, now = _hw_list()
    # hw_styles=None => standalone GraphWindow: legacy _plot_high_res + unconditional 0-100 axis.
    r.render(data, datetime.fromtimestamp(now - 60), datetime.fromtimestamp(now),
             "TIMELINE_60_MINUTES", stat_type="cpu", hw_styles=None, force_rebuild=True)
    r._render_hwsingle.assert_not_called()
    r._plot_high_res.assert_called_once()


if __name__ == "__main__":
    test_peak_label_placement_logic()
