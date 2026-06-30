"""
Regression test for the context-menu centering bug: with hardware monitoring on, the widget draws a
network segment AND a CPU/GPU segment side by side. The menu must center over the WHOLE widget, not
the last-drawn segment (which would shove it to the right). WidgetRenderer.get_content_bounds()
returns the union of all segments drawn in a paint; the tray menu uses it instead of the last rect.
"""
import pytest
from PyQt6.QtCore import QRect

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.widget_renderer import WidgetRenderer


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _renderer():
    return WidgetRenderer({}, I18nStrings("en_US"))


def test_content_bounds_unions_all_segments(q_app):
    r = _renderer()
    r.reset_content_bounds()
    # network segment on the left, hardware (CPU/GPU) segment on the right
    r._extend_content_bounds(QRect(0, 5, 100, 20))
    r._last_text_rect = QRect(0, 5, 100, 20)
    r._extend_content_bounds(QRect(140, 5, 60, 20))
    r._last_text_rect = QRect(140, 5, 60, 20)        # last segment = just the hardware part

    cb = r.get_content_bounds()
    assert cb.left() == 0 and cb.right() == 199       # spans BOTH segments
    assert cb.width() == 200
    # The menu centers on cb.center(); over the whole 200px, not the 140-199 hardware segment.
    assert cb.center().x() == 99
    assert cb.center().x() != r.get_last_text_rect().center().x()   # the bug would use the right one


def test_content_bounds_resets_per_paint(q_app):
    r = _renderer()
    r._extend_content_bounds(QRect(0, 0, 300, 20))    # stale from a previous (wide) paint
    r.reset_content_bounds()
    r._extend_content_bounds(QRect(0, 0, 80, 20))     # network-only paint now
    r._last_text_rect = QRect(0, 0, 80, 20)
    assert r.get_content_bounds().width() == 80       # no leftover width from last paint


def test_content_bounds_falls_back_to_last_rect_when_empty(q_app):
    r = _renderer()
    r.reset_content_bounds()
    r._last_text_rect = QRect(10, 0, 50, 20)
    assert r.get_content_bounds() == r._last_text_rect   # nothing accumulated -> last rect


def test_extend_ignores_invalid_rects(q_app):
    r = _renderer()
    r.reset_content_bounds()
    r._extend_content_bounds(QRect())                 # null
    r._extend_content_bounds(QRect(0, 0, 0, 20))      # empty width
    r._extend_content_bounds(QRect(5, 0, 40, 20))     # valid
    assert r.get_content_bounds() == QRect(5, 0, 40, 20)


def test_side_by_side_content_right_aligns(q_app):
    """The side-by-side widget right-aligns its content so the worst-case width reservation's surplus
    sits on the LEFT (toward the app icons), not as dead space between the content and the system tray.
    Rendered at a width deliberately wider than the content, the content's right edge hugs the widget
    edge - and a single-metric (network-only) layout is left unchanged (it fills its width)."""
    from PyQt6.QtGui import QPixmap, QPainter, QColor
    from netspeedtray.utils.widget_paint import render_widget, WidgetMetrics, font_from_config

    m = WidgetMetrics(upload_mbps=0.3, download_mbps=0.1, cpu_usage=14.0, gpu_usage=0.0,
                      cpu_temp=65.0, ram_used=12.0, ram_total=15.7)
    # Render onto a canvas deliberately MUCH wider than the content so the right-align engages
    # regardless of the host's font metrics (CI ships no fonts, so absolute widths aren't portable -
    # but "content narrower than a 600px canvas" holds for every font).
    W, H = 600, 40

    def _render(cfg):
        r = WidgetRenderer(cfg, I18nStrings("en_US"))
        pm = QPixmap(W, H); pm.fill(QColor(0, 0, 0))
        p = QPainter(pm)
        render_widget(p, QRect(0, 0, W, H), r, r.config, m, layout_mode="horizontal",
                      network_width=None, font=font_from_config(cfg))
        p.end()
        return r.get_content_bounds()

    sbs = _render({"widget_display_mode": "side_by_side", "monitor_cpu_enabled": True,
                   "monitor_ram_enabled": True, "show_hardware_temps": True, "background_opacity": 0})
    assert 0 < sbs.width() < W                       # content is narrower than the reserved width
    assert sbs.right() >= W - 12                      # ...and right-aligned (hugs the tray-side edge)
    assert sbs.left() > W // 4                         # the surplus (a wide gap) moved to the LEFT

    net = _render({"widget_display_mode": "network_only", "background_opacity": 0})
    assert net.left() <= 6                            # single-metric layout is NOT right-aligned


def test_layout_mode_maps_horizontal_taskbar_so_right_align_fires(q_app):
    """The right-align probe only fires for layout_mode='horizontal'. The live widget derives that from
    the taskbar edge, and the value MUST match what PreviewWidget uses for the same scenario (a bottom
    taskbar) or the live widget and the Settings/Overview preview diverge. Regression for the inverted
    mapping that left the common TOP/BOTTOM taskbar at 'vertical' (probe skipped) - preview right-aligned
    while the live widget kept the dead-space gap."""
    from netspeedtray import constants
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    m = NetworkSpeedWidget._layout_mode_for_edge
    # A horizontal taskbar (the default) -> 'horizontal' -> probe runs -> content right-aligns.
    assert m(constants.TaskbarEdge.BOTTOM) == "horizontal"
    assert m(constants.TaskbarEdge.TOP) == "horizontal"
    # A side taskbar is docked differently and is left as-is.
    assert m(constants.TaskbarEdge.LEFT) == "vertical"
    assert m(constants.TaskbarEdge.RIGHT) == "vertical"
