"""
Per-direction arrow colors (#168).

The arrows share the speed text's pen by design: `_draw_speed_line` sets ONE pen - the color-coding
band, or the default - and that same pen paints the arrow, the number and the unit. So with color
coding on, the whole line lights up together as a single signal. That is a defensible design, not an
oversight, which is why this ships opt-in and defaults to the old behavior.

The trap these tests exist for: overriding the pen to draw the arrow and forgetting to restore it
leaves the NUMBER wearing the arrow's color, which reads as a color-coding regression (cf. #153).
"""

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.utils.widget_paint import WidgetMetrics, render_widget, font_from_config

W, H = 360, 44
MBPS = 125_000


def _cfg(**over):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"widget_display_mode": "network_only", "hide_arrows": False})
    cfg.update(over)
    return cfg


def _render(cfg, up_mbps=5.0, down_mbps=5.0) -> set:
    """Render one frame and return the set of distinct opaque RGB colors drawn."""
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    renderer = WidgetRenderer(cfg, I18nStrings("en_US"))
    painter = QPainter(img)
    render_widget(painter, QRect(0, 0, W, H), renderer, renderer.config,
                  WidgetMetrics(upload_mbps=up_mbps, download_mbps=down_mbps),
                  layout_mode="horizontal", cycle_mode="network_only",
                  network_width=None, font=font_from_config(cfg))
    painter.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    raw = img.constBits(); raw.setsize(img.sizeInBytes())
    raw = bytes(raw)
    return {raw[i:i + 3] for i in range(0, len(raw), 4) if raw[i + 3] >= 250}


def _rgb(hex_code: str) -> bytes:
    c = QColor(hex_code)
    return bytes((c.red(), c.green(), c.blue()))


class TestDefaultIsUnchanged:

    def test_the_feature_is_off_by_default(self):
        assert constants.config.defaults.DEFAULT_CONFIG["use_custom_arrow_colors"] is False

    def test_disabled_renders_identically_to_before(self, q_app):
        """Nobody's widget may shift on upgrade. With the toggle off, the arrow color keys must
        make no difference at all - even when they hold wild values."""
        base = _render(_cfg())
        with_unused_colors = _render(_cfg(arrow_up_color="#FF00FF", arrow_down_color="#00FFFF"))
        assert base == with_unused_colors


class TestEnabledColorsTheArrowsOnly:

    def test_both_arrow_colors_are_painted(self, q_app):
        colors = _render(_cfg(use_custom_arrow_colors=True,
                              arrow_up_color="#FF0000", arrow_down_color="#0000FF"))
        assert _rgb("#FF0000") in colors, "upload arrow color was not painted"
        assert _rgb("#0000FF") in colors, "download arrow color was not painted"

    def test_the_number_keeps_the_default_pen(self, q_app):
        """THE trap. If the arrow's pen is not restored, the number inherits it and the arrow color
        silently becomes the whole line's color."""
        default_color = constants.config.defaults.DEFAULT_CONFIG["default_color"]
        colors = _render(_cfg(use_custom_arrow_colors=True, color_coding=False,
                              arrow_up_color="#FF0000", arrow_down_color="#0000FF"))
        assert _rgb(default_color) in colors, \
            "the speed text lost its own color - the arrow pen was not restored"

    def test_the_number_keeps_its_color_coding_band(self, q_app):
        """Same trap, with color coding on: the number must still band by speed while the arrows
        hold their fixed colors."""
        cfg = _cfg(use_custom_arrow_colors=True, color_coding=True,
                   arrow_up_color="#FF0000", arrow_down_color="#0000FF",
                   high_speed_threshold=1.0, low_speed_threshold=0.1,
                   high_speed_color="#00FF00")
        colors = _render(cfg, up_mbps=500.0, down_mbps=500.0)
        assert _rgb("#00FF00") in colors, "the number lost its high-speed band color"
        assert _rgb("#FF0000") in colors and _rgb("#0000FF") in colors

    def test_hidden_arrows_are_unaffected(self, q_app):
        """hide_arrows wins - the override must not resurrect a glyph the user turned off."""
        colors = _render(_cfg(use_custom_arrow_colors=True, hide_arrows=True,
                              arrow_up_color="#FF0000", arrow_down_color="#0000FF"))
        assert _rgb("#FF0000") not in colors
        assert _rgb("#0000FF") not in colors


class TestConfigPlumbing:

    def test_render_config_carries_the_keys(self):
        rc = WidgetRenderer.RenderConfig.from_dict(
            _cfg(use_custom_arrow_colors=True, arrow_up_color="#123456", arrow_down_color="#654321")
        ) if hasattr(WidgetRenderer, "RenderConfig") else None
        if rc is None:                              # RenderConfig is module-level, not nested
            from netspeedtray.utils.widget_renderer import RenderConfig
            rc = RenderConfig.from_dict(
                _cfg(use_custom_arrow_colors=True, arrow_up_color="#123456", arrow_down_color="#654321"))
        assert rc.use_custom_arrow_colors is True
        assert rc.arrow_up_color == "#123456"
        assert rc.arrow_down_color == "#654321"

    @pytest.mark.parametrize("key", ["use_custom_arrow_colors", "arrow_up_color", "arrow_down_color"])
    def test_keys_are_in_both_config_structures(self, key):
        """ConfigConstants.validate() raises on any mismatch, so a key missing from either dict
        stops the app from starting."""
        assert key in constants.config.defaults.DEFAULT_CONFIG
        assert key in constants.config.defaults.VALIDATION_SCHEMA

    @pytest.mark.parametrize("bad", ["", "red", "#FFF", "#GGGGGG", "1234567"])
    def test_invalid_colors_are_rejected_by_the_schema(self, bad):
        """The hex regex is the same one background_color uses. An empty-string 'follow the text'
        sentinel would be silently reset on every load, which is why the feature is gated by a bool
        rather than by a magic empty value."""
        import re
        schema = constants.config.defaults.VALIDATION_SCHEMA["arrow_up_color"]
        assert re.fullmatch(schema["regex"], bad) is None
