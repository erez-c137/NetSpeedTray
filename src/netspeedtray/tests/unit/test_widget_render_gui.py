"""
Render-verification tests: drive WidgetRenderer into an offscreen QImage and probe
the ACTUAL painted pixels. This automates the "squint at the widget to check the
colors look right" QA — especially color coding (the #153 banding + the canonical-Mbps
unit fix) — font-independently and deterministically.

We use distinct pure band colors (red/green/blue) so the band is unambiguous in the
image, count only solid glyph-core pixels (alpha>=250, skipping anti-aliased edges),
and assert the dominant color matches the expected band.
"""
from collections import Counter

import numpy as np
import pytest
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.widget_renderer import WidgetRenderer

# draw_network_speeds takes bytes/sec; 1 Mbps == 125_000 bytes/s.
MBPS_IN_BYTES = 125_000

HIGH = (255, 0, 0)     # high_speed_color
LOW = (0, 255, 0)      # low_speed_color
DEFAULT = (0, 0, 255)  # default_color


@pytest.fixture
def renderer(q_app):
    # q_app ensures a QApplication exists before we build QFont/QPen/QPainter — without
    # one those Qt calls hang under the offscreen platform.
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({
        "color_coding": True,
        "high_speed_threshold": 10.0,
        "low_speed_threshold": 1.0,
        "high_speed_color": "#FF0000",
        "low_speed_color": "#00FF00",
        "default_color": "#0000FF",
        "hide_unit_suffix": False,
    })
    return WidgetRenderer(cfg, I18nStrings("en_US"))


def _solid_rgb_counts(renderer, up_bytes, dw_bytes, w=280, h=52) -> Counter:
    """Render both speeds and count solid (glyph-core) pixels by RGB."""
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))  # transparent background
    painter = QPainter(img)
    renderer.draw_network_speeds(painter, up_bytes, dw_bytes, w, h, renderer.config)
    painter.end()

    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4))
    solid = arr[arr[:, :, 3] >= 250][:, :3]  # only fully-opaque pixels, RGB
    return Counter(map(tuple, solid.tolist()))


def _dominant(counts: Counter):
    return counts.most_common(1)[0][0] if counts else None


def test_high_band_paints_high_color(renderer):
    # 50 Mbps is above the 10 Mbps high threshold -> high color (red).
    counts = _solid_rgb_counts(renderer, 50 * MBPS_IN_BYTES, 50 * MBPS_IN_BYTES)
    assert _dominant(counts) == HIGH, f"expected red-dominant; top: {counts.most_common(3)}"


def test_low_band_paints_low_color(renderer):
    # 5 Mbps is between low(1) and high(10) -> low color (green).
    counts = _solid_rgb_counts(renderer, 5 * MBPS_IN_BYTES, 5 * MBPS_IN_BYTES)
    assert _dominant(counts) == LOW, f"expected green-dominant; top: {counts.most_common(3)}"


def test_default_band_paints_default_color(renderer):
    # 0.5 Mbps is below the 1 Mbps low threshold -> default color (blue). This is the
    # exact case the unit-bug fix corrected: displayed as "500 Kbps" but banded by the
    # canonical Mbps speed, not the on-screen number.
    counts = _solid_rgb_counts(renderer, 0.5 * MBPS_IN_BYTES, 0.5 * MBPS_IN_BYTES)
    assert _dominant(counts) == DEFAULT, f"expected blue-dominant; top: {counts.most_common(3)}"


def test_exactly_at_high_threshold_is_high(renderer):
    # The bands are inclusive at the top (>=), so exactly 10 Mbps is 'high'.
    counts = _solid_rgb_counts(renderer, 10 * MBPS_IN_BYTES, 10 * MBPS_IN_BYTES)
    assert _dominant(counts) == HIGH, f"expected red at the threshold; top: {counts.most_common(3)}"


def test_color_coding_off_uses_default_color(q_app):
    # With color coding disabled, even a fast speed paints in the default color, not high.
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({
        "color_coding": False,
        "high_speed_color": "#FF0000", "low_speed_color": "#00FF00", "default_color": "#0000FF",
    })
    r = WidgetRenderer(cfg, I18nStrings("en_US"))
    counts = _solid_rgb_counts(r, 50 * MBPS_IN_BYTES, 50 * MBPS_IN_BYTES)
    assert _dominant(counts) == DEFAULT, f"expected default (blue) with coding off; top: {counts.most_common(3)}"


def test_something_actually_rendered(renderer):
    # Guard against "nothing drew" / blank-widget regressions.
    counts = _solid_rgb_counts(renderer, 50 * MBPS_IN_BYTES, 50 * MBPS_IN_BYTES)
    assert sum(counts.values()) > 50, "almost no glyph pixels were painted"


# --- hardware suffix: the display side of the stale-temp/power fix ------------

def test_hw_suffix_na_when_sensor_missing(renderer):
    # temp/power None while enabled -> "(N/A)" (so a dropped sensor clears the stale
    # reading instead of freezing the last value).
    na = f"({renderer.i18n.DEFAULT_TEXT})"
    assert renderer._build_hw_suffix(None, None, show_temps=True, show_power=True) == na
    assert renderer._build_hw_suffix(None, None, show_temps=True, show_power=False) == na


def test_hw_suffix_shows_values_when_present(renderer):
    assert renderer._build_hw_suffix(43.0, None, show_temps=True, show_power=False) == "(43°C)"
    assert renderer._build_hw_suffix(43.0, 7.8, show_temps=True, show_power=True) == "(43°C, 7.8W)"


def test_hw_suffix_empty_when_disabled(renderer):
    assert renderer._build_hw_suffix(43.0, 7.8, show_temps=False, show_power=False) == ""
