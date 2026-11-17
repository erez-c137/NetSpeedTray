"""
"Always show megabytes" with light traffic (#556): a non-zero rate that rounds to zero at the chosen precision
reads "<0.1" (the smallest step), never a false-looking "0.0". True zero stays "0.0", auto mode never gets the
floor, no extra digits are added (#303), and the floor never needs more width than the widget reserved.
"""
import pytest
from PyQt6.QtGui import QFont, QFontMetrics

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.helpers import format_speed, get_reference_value_string

UNIT_TYPES = ("bits_decimal", "bits_binary", "bytes_decimal", "bytes_binary")
FLOORS = {0: "<1", 1: "<0.1", 2: "<0.01"}


@pytest.fixture(scope="module")
def en():
    return I18nStrings("en_US")


@pytest.fixture(scope="module")
def de():
    return I18nStrings("de_DE")


@pytest.mark.parametrize("unit_type", UNIT_TYPES)
@pytest.mark.parametrize("decimal_places", [0, 1, 2])
@pytest.mark.parametrize("speed", [0.01, 1, 3, 40, 120])   # bytes/s: well under 0.01 of any mega unit
def test_tiny_traffic_reads_below_the_smallest_step(en, unit_type, decimal_places, speed):
    val, _ = format_speed(speed, en, force_mega_unit=True, unit_type=unit_type,
                          decimal_places=decimal_places, split_unit=True)
    assert val == FLOORS[decimal_places]


@pytest.mark.parametrize("unit_type", UNIT_TYPES)
@pytest.mark.parametrize("decimal_places", [0, 1, 2])
def test_true_zero_still_reads_zero(en, unit_type, decimal_places):
    val, _ = format_speed(0, en, force_mega_unit=True, unit_type=unit_type,
                          decimal_places=decimal_places, split_unit=True)
    assert val == ("0" if decimal_places == 0 else "0." + "0" * decimal_places)


@pytest.mark.parametrize("speed", [float("nan"), float("inf"), -5.0])
def test_non_finite_or_negative_is_zero_not_floor(en, speed):
    val, _ = format_speed(speed, en, force_mega_unit=True, split_unit=True)
    assert val == "0.0"


@pytest.mark.parametrize("unit_type", UNIT_TYPES)
@pytest.mark.parametrize("decimal_places", [0, 1, 2])
@pytest.mark.parametrize("speed", [0.01, 0.4, 1, 3, 40, 999, 1_000, 3_000, 40_000, 124_999])
def test_auto_mode_never_shows_the_floor(en, unit_type, decimal_places, speed):
    val, _ = format_speed(speed, en, unit_type=unit_type, decimal_places=decimal_places, split_unit=True)
    assert constants.network.units.BELOW_STEP_PREFIX not in val


def test_values_that_show_a_digit_keep_it(en):
    # 0.024 Mbps at 2 decimals is a real "0.02", and 0.064 at 1 decimal a real "0.1": no floor there.
    assert format_speed(3_000, en, force_mega_unit=True, decimal_places=2, split_unit=True)[0] == "0.02"
    assert format_speed(8_000, en, force_mega_unit=True, decimal_places=1, split_unit=True)[0] == "0.1"


def test_floor_takes_the_locale_separator(de):
    assert format_speed(3, de, force_mega_unit=True, decimal_places=1, split_unit=True)[0] == "<0,1"
    assert format_speed(3, de, force_mega_unit=True, decimal_places=2, split_unit=True)[0] == "<0,01"


@pytest.mark.parametrize("decimal_places", [0, 1, 2])
@pytest.mark.parametrize("sep", [".", ","])
def test_floor_fits_the_reserved_width_in_the_widget_font(q_app, decimal_places, sep):
    # The widget reserves its number column from the reference string once; the floor must never need more.
    # Checked against the AUTO reference ("888.8"), the narrower of the two, so it holds in either mode.
    fm = QFontMetrics(QFont(constants.config.defaults.DEFAULT_FONT_FAMILY, constants.config.defaults.DEFAULT_FONT_SIZE))
    floor = FLOORS[decimal_places].replace(".", sep)
    ref = get_reference_value_string(False, decimal_places).replace(".", sep)
    assert fm.horizontalAdvance(floor) <= fm.horizontalAdvance(ref), (floor, ref)


def test_rounding_edge_pair(en):
    # 6249 B/s = 0.049992 Mbps rounds to 0.0 -> floor; 6250 B/s = 0.05 Mbps rounds to "0.1" -> a real digit.
    assert format_speed(6_249, en, force_mega_unit=True, split_unit=True)[0] == "<0.1"
    assert format_speed(6_250, en, force_mega_unit=True, split_unit=True)[0] == "0.1"


def test_monitor_graph_hover_keeps_a_floor_value_and_the_rows_after_it(q_app):
    # The hover label is rich text; an unescaped "<0.1 Mbps<br>..." opened a tag and Qt dropped the value and
    # every row after it (review of #578). Parse it the way the QLabel does.
    from types import SimpleNamespace

    import matplotlib.dates as mdates
    from datetime import datetime
    from PyQt6.QtGui import QTextDocument

    from netspeedtray.views.monitor.graph_hover import GraphHoverTooltip

    host = SimpleNamespace(_current_stat="network", i18n=I18nStrings("en_US"),
                           config={"unit_type": "bits_decimal", "decimal_places": 1,
                                   "speed_display_mode": "always_mbps"})
    x = mdates.date2num(datetime(2026, 6, 28, 14, 30, 0))
    rich = GraphHoverTooltip(host)._format(x, [("Download", 0.01, "#42B883"), ("Upload", 0.3, "#E91E63")])
    doc = QTextDocument()
    doc.setHtml(rich)
    text = doc.toPlainText()
    assert "Download <0.1 Mbps" in text, text
    assert "Upload 0.3 Mbps" in text, text


def _draw_frame(direction, spy):
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QColor, QImage, QPainter

    from netspeedtray.utils import widget_renderer as wr
    from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget

    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(widget_display_mode="network_only", graph_enabled=False, default_color="#FFFFFF")
    assert cfg["speed_display_mode"] == "always_mbps"
    img = QImage(220, 40, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 255))
    renderer = wr.WidgetRenderer(cfg, I18nStrings("he_IL"))
    p = QPainter(img)
    p.setLayoutDirection(direction)
    orig = wr.draw_text
    wr.draw_text = lambda painter, x, y, text, user=False: (spy.append((text, painter.layoutDirection())),
                                                           orig(painter, x, y, text, user))
    try:
        render_widget(p, QRect(0, 0, 220, 40), renderer, renderer.config,
                      WidgetMetrics(upload_mbps=0.008, download_mbps=0.024),   # both read "<0.1 Mbps"
                      layout_mode="horizontal", cycle_mode="network_only", network_width=None,
                      font=font_from_config(cfg))
    finally:
        wr.draw_text = orig
    after = p.layoutDirection()
    p.end()
    return after


def test_hebrew_widget_draws_the_floor_left_to_right(q_app):
    # In the RTL (he_IL) app the widget's painter lays text out right to left, and Qt mirrors the
    # direction-neutral "<" of "<0.1" into "0.1>" (seen on the native platform; the offscreen test platform's
    # fallback face draws < and > alike, so this pins the mechanism, not pixels): the value must be drawn
    # with the painter LTR, and the painter's own direction must come back afterwards.
    from PyQt6.QtCore import Qt

    spy = []
    after = _draw_frame(Qt.LayoutDirection.RightToLeft, spy)
    values = [d for text, d in spy if text == "<0.1"]
    assert len(values) == 2, spy
    assert all(d == Qt.LayoutDirection.LeftToRight for d in values), spy
    assert after == Qt.LayoutDirection.RightToLeft
