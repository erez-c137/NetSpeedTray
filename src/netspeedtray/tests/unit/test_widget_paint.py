"""
Shared paint path (C1 keystone) - parity & smoke tests.

`render_widget` is the single draw path for both the live taskbar widget and every preview.
These tests drive it into an offscreen QImage and assert:
  - it paints *something* in every display mode (no silent blank),
  - it is deterministic (same inputs → identical pixels - a preview must be stable), and
  - it reflects config (a different arrow glyph yields different pixels - the preview is live).
Plus a construction/smoke test for PreviewWidget itself.
"""
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter

import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.utils.widget_paint import (
    WidgetMetrics, render_widget, demo_metrics, font_from_config,
)

W, H = 360, 44

MODES = ["network_only", "combined", "side_by_side", "cpu_only", "gpu_only"]


def _base_config(**overrides):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({
        "monitor_cpu_enabled": True,
        "monitor_gpu_enabled": True,
        "show_hardware_temps": True,
    })
    cfg.update(overrides)
    return cfg


def _render_to_bytes(cfg, metrics, *, cycle_mode="network_only") -> bytes:
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    renderer = WidgetRenderer(cfg, I18nStrings("en_US"))
    painter = QPainter(img)
    render_widget(painter, QRect(0, 0, W, H), renderer, renderer.config, metrics,
                  layout_mode="horizontal", cycle_mode=cycle_mode, network_width=None,
                  font=font_from_config(cfg))
    painter.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits(); ptr.setsize(img.sizeInBytes())
    return bytes(ptr)


def _solid_pixels(raw: bytes) -> int:
    # Count only solid glyph-core pixels (alpha == 255). The background fill is alpha==1,
    # so this isolates the actual drawn text/stats from the near-transparent backdrop.
    return sum(1 for i in range(3, len(raw), 4) if raw[i] >= 250)


@pytest.mark.parametrize("mode", MODES)
def test_render_widget_paints_in_every_mode(q_app, mode):
    cfg = _base_config(widget_display_mode=mode)
    raw = _render_to_bytes(cfg, demo_metrics())
    assert _solid_pixels(raw) > 20, f"mode {mode} drew (almost) no foreground glyphs"


def test_render_widget_is_deterministic(q_app):
    cfg = _base_config(widget_display_mode="network_only")
    a = _render_to_bytes(cfg, demo_metrics())
    b = _render_to_bytes(cfg, demo_metrics())
    assert a == b, "same config + metrics must produce identical pixels (preview stability)"


def test_render_widget_reflects_config(q_app):
    # A robust, font-substitution-proof check that config actually drives the output:
    # a larger font paints more solid glyph pixels. (Arrow-glyph swaps are visible on real
    # Windows but the offscreen test font substitutes ↑/▲ to the same shape.)
    small = _render_to_bytes(_base_config(widget_display_mode="network_only", font_size=10),
                             demo_metrics())
    large = _render_to_bytes(_base_config(widget_display_mode="network_only", font_size=20),
                             demo_metrics())
    assert small != large, "changing font size must change the rendered output"
    assert _solid_pixels(large) > _solid_pixels(small), "larger font should paint more glyph pixels"


def test_render_widget_handles_empty_metrics(q_app):
    cfg = _base_config(widget_display_mode="combined")
    # All-default/empty metrics must not raise (cold-start frame before any data).
    raw = _render_to_bytes(cfg, WidgetMetrics())
    assert isinstance(raw, bytes) and len(raw) == W * H * 4


def test_preview_widget_constructs_and_updates(q_app):
    from netspeedtray.views.widget.preview import PreviewWidget
    pv = PreviewWidget(_base_config(widget_display_mode="network_only"), I18nStrings("en_US"))
    assert pv.config["widget_display_mode"] == "network_only"
    pv.set_config(_base_config(widget_display_mode="combined"))
    assert pv.config["widget_display_mode"] == "combined"
    assert pv._renderer.config.widget_display_mode == "combined"
    pv.set_metrics(WidgetMetrics(upload_mbps=1.0, download_mbps=2.0))  # must not raise
