"""
Pause froze the numbers with no visual cue, so a paused widget looked hung - the very failure the
non-persisted pause was meant to avoid. A paused frame is now drawn dimmed under a small pause badge,
in the same rect (the widget never resizes for it), and the live widget passes its pause state in.
"""
import inspect

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.utils.widget_paint import WidgetMetrics, render_widget, font_from_config

W, H = 150, 48


def _render(paused, color="#FFFFFF"):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg["default_color"] = color
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    renderer = WidgetRenderer(cfg, I18nStrings("en_US"))
    p = QPainter(img)
    render_widget(p, QRect(0, 0, W, H), renderer, renderer.config,
                  WidgetMetrics(upload_mbps=1.4, download_mbps=48.7), layout_mode="horizontal",
                  font=font_from_config(cfg), paused=paused)
    p.end()
    return img


def _solid(img):
    return sum(1 for y in range(H) for x in range(W) if img.pixelColor(x, y).alpha() >= 250)


def test_paused_frame_is_dimmed_with_a_badge(q_app):
    live, paused = _render(False), _render(True)
    assert paused.size() == live.size()
    # The readout no longer paints at full strength; what is left solid is just the small badge.
    assert _solid(paused) < _solid(live) * 0.6
    assert _solid(paused) > 20


def test_badge_contrasts_on_both_themes(q_app):
    for text_color in ("#FFFFFF", "#1A1A1A"):
        img = _render(True, text_color)
        solid = [img.pixelColor(x, y) for y in range(H) for x in range(W) if img.pixelColor(x, y).alpha() >= 250]
        tones = {c.lightness() > 127 for c in solid}
        assert tones == {True, False}     # disc in the text color, bars in the opposite tone


def test_unpaused_is_unchanged_by_default(q_app):
    a, b = _render(False), _render(False)
    assert a == b


def test_live_widget_passes_its_pause_state():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    assert "paused=self.is_paused" in inspect.getsource(NetworkSpeedWidget.paintEvent)
