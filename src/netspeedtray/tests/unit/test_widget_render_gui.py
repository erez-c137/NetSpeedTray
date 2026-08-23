"""
Render-verification tests: drive WidgetRenderer into an offscreen QImage and probe
the ACTUAL painted pixels. This automates the "squint at the widget to check the
colors look right" QA - especially color coding (the #153 banding + the canonical-Mbps
unit fix) - font-independently and deterministically.

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
    # q_app ensures a QApplication exists before we build QFont/QPen/QPainter - without
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


def test_hardware_stats_render_smoke(q_app):
    """draw_hardware_stats renders CPU + GPU rows headless without error (non-blank).
    Covers the hardware display path at the render level (text label style avoids
    icon-asset dependence)."""
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"hardware_label_style": "text", "monitor_cpu_enabled": True,
                "monitor_gpu_enabled": True, "show_hardware_temps": True})
    r = WidgetRenderer(cfg, I18nStrings("en_US"))

    img = QImage(300, 56, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    r.draw_hardware_stats(p, 50.0, 70.0, 300, 56, r.config, cpu_temp=45.0, gpu_temp=60.0)
    p.end()

    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4))
    assert int((arr[:, :, 3] >= 250).sum()) > 30, "hardware stats drew almost nothing"


def test_hw_percent_content_width_stable_across_digits(q_app):
    """Integration proof for #179: the CPU% segment's measured content width is IDENTICAL at 9%,
    10% and 100%. That width is exactly what widget_paint feeds into the side-by-side right-anchor
    (align_dx = widget_width - content_width), so a constant width means the whole block - including
    a network readout drawn to its left - can no longer slide by a digit as CPU crosses 9<->10.
    Guarded to fonts with tabular figures (space==digit), which the default Segoe UI has; CI fallback
    fonts make the padding best-effort, so we skip the exact-equality assertion there rather than fail."""
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"hardware_label_style": "text", "monitor_cpu_enabled": True, "monitor_gpu_enabled": False})
    r = WidgetRenderer(cfg, I18nStrings("en_US"))
    if r.metrics.horizontalAdvance(" ") != r.metrics.horizontalAdvance("0"):
        pytest.skip("test font lacks tabular space==digit metrics; the padding is best-effort there")

    def content_w(cpu):
        img = QImage(320, 56, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        r.draw_hardware_stats(p, float(cpu), None, 320, 56, r.config)
        p.end()
        return r.get_last_text_rect().width()

    w9, w10, w100 = content_w(9), content_w(10), content_w(100)
    assert w9 == w10 == w100, f"CPU% segment width still jitters: 9->{w9}, 10->{w10}, 100->{w100}"


# --------------------------------------------------------------------------- #250: the separator

def _drawn_strings(renderer, **kwargs) -> list:
    """Every string draw_hardware_stats paints, captured in order."""
    from unittest.mock import patch

    img = QImage(360, 52, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    seen = []
    real = QPainter.drawText

    def spy(self, *args):
        if args and isinstance(args[-1], str):
            seen.append(args[-1])
        return real(self, *args)

    with patch.object(QPainter, "drawText", spy):
        renderer.draw_hardware_stats(painter, width=360, height=52, config=renderer.config, **kwargs)
    painter.end()
    return seen


def test_memory_is_not_introduced_by_a_pipe(renderer):
    """#250: the reporter found the ' | ' before the memory value cluttered a readout that is only a
    few characters wide. A gap separates it just as well.

    Asserted on the painted strings rather than pixels, because a glyph's pixels are a fragile thing
    to test and its presence in the draw calls is not.
    """
    renderer.config.hardware_label_style = "icons_colored"
    drawn = _drawn_strings(renderer, cpu_usage=8.0, gpu_usage=3.0,
                           ram_info=(11.8, 15.7), vram_info=(2.1, 8.0), layout_mode="horizontal")

    assert drawn, "nothing was painted - the fixture is not exercising the memory path"
    assert not [d for d in drawn if "|" in d], (
        "the memory value is still introduced by a pipe: %r" % [d for d in drawn if "|" in d])


def test_the_memory_value_is_still_painted(renderer):
    """Guard the obvious regression: removing the separator must not remove the number with it."""
    renderer.config.hardware_label_style = "icons_colored"
    drawn = _drawn_strings(renderer, cpu_usage=8.0, gpu_usage=3.0,
                           ram_info=(11.8, 15.7), vram_info=(2.1, 8.0), layout_mode="horizontal")
    assert any("15.7" in d for d in drawn), "the RAM total vanished along with the separator"


# --------------------------------------------------------------------------- iGPU VRAM

def test_igpu_vram_is_blank_not_zero(renderer):
    """An integrated GPU has no dedicated video memory, and PDH dutifully reports 0.0 GB.

    Painting "0.0G" spends widget width to say nothing, and reads as a measurement rather than an
    absence. The Monitor's Overview tile has always hidden itself in this case; the widget and the
    Hardware telemetry strip had not, which is the drift this closes.
    """
    renderer.config.hardware_label_style = "icons_colored"
    drawn = _drawn_strings(renderer, cpu_usage=8.0, gpu_usage=3.0,
                           ram_info=(9.2, 15.7),      # real RAM: total known
                           vram_info=(0.0, 0.0),      # iGPU: nothing dedicated, no total
                           layout_mode="horizontal")

    assert any("15.7" in d for d in drawn), "RAM must still be painted"
    assert not [d for d in drawn if "0.0G" in d], (
        "the iGPU still paints a 0.0G: %r" % [d for d in drawn if "0.0G" in d])


def test_a_real_gpu_with_vram_still_shows_it(renderer):
    """The guard must not swallow a genuine reading."""
    renderer.config.hardware_label_style = "icons_colored"
    drawn = _drawn_strings(renderer, cpu_usage=8.0, gpu_usage=30.0,
                           ram_info=(9.2, 15.7), vram_info=(2.1, 8.0),
                           layout_mode="horizontal")
    assert any("2.1" in d and "8.0" in d for d in drawn), "a real VRAM reading was hidden"


def test_a_gpu_with_no_total_but_real_usage_still_shows_it(renderer):
    """AMD/Intel dGPUs have no nvidia-smi, so no total - but their usage is still worth showing."""
    renderer.config.hardware_label_style = "icons_colored"
    drawn = _drawn_strings(renderer, cpu_usage=8.0, gpu_usage=30.0,
                           ram_info=(9.2, 15.7), vram_info=(2.5, 0.0),
                           layout_mode="horizontal")
    assert any("2.5G" in d for d in drawn), "usage without a known total was hidden"
