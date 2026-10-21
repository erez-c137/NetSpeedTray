"""
The compact readout's shared unit row never paints outside a side bar, at any locale or size.

ru_RU bytes_binary on a Small (32 px) bar at 11 pt: even the short unit "МиБ" is wider than the bar,
so it was clipped by 1 px (rc check). No shorter label is honest (dropping the "и" would say decimal
MB), so the unit row alone steps its font down until the unit fits.

Sized by the real WidgetLayoutManager on a mocked RIGHT taskbar, painted by the real render_widget
with the clip disabled onto a larger canvas: nothing may land outside the widget. Cases where the
numbers themselves don't fit (TIER_CLIP, clipped by design and logged) are skipped.
"""
import os
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.taskbar_utils import TaskbarInfo
from netspeedtray.utils.widget_paint import TIER_CLIP, WidgetMetrics, font_from_config, render_widget
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager

_LOCALE_DIR = os.path.join(os.path.dirname(constants.__file__), "locales")
LOCALES = sorted(f[:-5] for f in os.listdir(_LOCALE_DIR) if f.endswith(".json"))
UNIT_TYPES = ["bytes_binary", "bits_binary", "bytes_decimal", "bits_decimal"]
_I18N = {}


def _i18n(lang):
    if lang not in _I18N:
        _I18N[lang] = I18nStrings(lang)
    return _I18N[lang]


def _right_taskbar(thickness):
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    screen.availableGeometry.return_value = QRect(0, 0, 1920 - thickness, 1080)
    tb = MagicMock(spec=TaskbarInfo)
    tb.rect, tb.hwnd, tb.dpi_scale, tb.height = (1920 - thickness, 0, 1920, 1080), 1, 1.0, thickness
    tb.tasklist_rect = tb.widgets_rect = None
    tb.get_edge_position.return_value = constants.TaskbarEdge.RIGHT
    tb.get_screen.return_value = screen
    tb.get_tray_rect.return_value = (1920 - thickness, 908, 1920, 1080)
    return tb


def _size(cfg, lang, thickness):
    widget = MagicMock()
    widget.config = cfg
    widget.i18n = _i18n(lang)
    widget._identity_reserve_px.return_value = 0
    widget._hover_card = None
    widget.ram_total = widget.vram_total = 0.0
    widget.ram_used = widget.vram_used = None
    lm = WidgetLayoutManager(widget)
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=_right_taskbar(thickness)), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
        lm.set_font(resize=False)
        lm.resize_widget_for_font()
    (w, h), _ = widget.setFixedSize.call_args
    return w, h, lm


def _ink_outside(cfg, lang, w, h, metrics):
    renderer = WidgetRenderer(cfg, _i18n(lang))
    img = QImage(3 * w, 3 * h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.translate(w, h)
    with patch.object(QPainter, "setClipRect", lambda *a, **k: None):
        render_widget(p, QRect(0, 0, w, h), renderer, renderer.config, metrics, layout_mode="vertical",
                      cycle_mode="network_only", network_width=None, font=font_from_config(cfg))
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    a = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4))[:, :, 3].copy()
    a[h:2 * h, w:2 * w] = 0
    xs = np.nonzero(a > 24)[1]
    return None if not len(xs) else (int(xs.min()) - w, int(xs.max()) - w)


# Speeds that land the shared unit on kilo, mega and giga.
SPEEDS = [WidgetMetrics(upload_mbps=0.4, download_mbps=0.8), WidgetMetrics(upload_mbps=12.4, download_mbps=88.6),
          WidgetMetrics(upload_mbps=2400.0, download_mbps=4800.0)]


@pytest.mark.parametrize("thickness", [32, 48])
@pytest.mark.parametrize("unit_type", UNIT_TYPES)
@pytest.mark.parametrize("lang", LOCALES)
def test_unit_row_stays_inside_the_bar(q_app, lang, unit_type, thickness):
    checked = 0
    for font_size in range(constants.fonts.FONT_SIZE_MIN, 13):
        cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
        cfg.update({"free_float": False, "background_opacity": 0, "graph_enabled": False, "language": lang,
                    "widget_display_mode": "network_only", "unit_type": unit_type, "font_size": font_size})
        w, h, lm = _size(cfg, lang, thickness)
        if lm._side_fit is None or lm._side_fit.tier >= TIER_CLIP:
            continue
        checked += 1
        for m in SPEEDS:
            assert _ink_outside(cfg, lang, w, h, m) is None, (lang, unit_type, thickness, font_size, m)
    if not checked:
        pytest.skip("no font size leaves the numbers room on this bar")


def test_fit_font_steps_down_until_the_text_fits(q_app):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, font_size=11)
    r = WidgetRenderer(cfg, _i18n("ru_RU"))
    font, width = r._fit_font("МиБ", 20)
    assert width <= 20 or font.pointSizeF() == WidgetRenderer._MIN_UNIT_POINT_SIZE
    assert font.pointSizeF() < r.font.pointSizeF()


@pytest.mark.parametrize("mode", ["auto", "always_mbps"])
@pytest.mark.parametrize("lang", ["ru_RU", "en_US"])
@pytest.mark.parametrize("unit_type", ["bytes_binary", "bits_binary"])
@pytest.mark.parametrize("font_size", [9, 10, 11])
def test_unit_row_keeps_one_font_size_as_traffic_crosses_units(q_app, font_size, unit_type, lang, mode):
    """The step-down is sized from the widest unit of the family, not the live one: КиБ / МиБ / ГиБ
    (different widths) all draw at the same size, so the row doesn't shrink and grow with traffic."""
    from netspeedtray.utils.helpers import get_unit_labels_for_type
    from netspeedtray.utils.widget_paint import SideBarFit, TIER_UNIT_ROW
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"language": lang, "unit_type": unit_type, "font_size": font_size,
                "short_unit_labels": True, "speed_display_mode": mode})
    renderer = WidgetRenderer(cfg, _i18n(lang))
    fm = renderer.metrics
    family = get_unit_labels_for_type(_i18n(lang), unit_type, True)
    if mode == "always_mbps":
        family = family[2:]
    widths = sorted(fm.horizontalAdvance(u) for u in family)
    if widths[-1] == widths[0]:
        pytest.skip("this font gives every unit of the family one width")
    # 1 px short of the widest unit: it needs a step down, the narrowest does not.
    w = 2 * constants.renderer.TEXT_MARGIN + widths[-1] - 1
    fit = SideBarFit(TIER_UNIT_ROW, True, True, True, False, False, False)
    sizes = {}
    for bps in (5e3, 5e6, 5e9, 500.0):   # K, M, G, base
        painter = MagicMock()
        fonts = []
        painter.setFont.side_effect = lambda f: fonts.append(f.pointSizeF())
        drawn = []
        painter.drawText.side_effect = lambda *a: drawn.append(a[-1])
        renderer.draw_network_compact(painter, bps, bps, w, 80, renderer.config, fit, unit_only=True)
        assert drawn, "the unit row was not drawn"
        sizes[drawn[-1]] = fonts[-1]
    assert len(set(sizes.values())) == 1, sizes
    assert next(iter(sizes.values())) < renderer.font.pointSizeF()


def _ink_right(font, text):
    """x of the last inked column + 1 when ``text`` is drawn at x = 0."""
    from PyQt6.QtGui import QFontMetrics
    fm = QFontMetrics(font)
    img = QImage(fm.horizontalAdvance(text) * 2 + 20, fm.height() * 2, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setFont(font)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(0, fm.ascent(), text)
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    a = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4))[:, :, 3]
    return int(np.nonzero(a > 24)[1].max()) + 1


@pytest.mark.parametrize("lang,unit_type,unit", [("ru_RU", "bytes_binary", "МиБ"), ("en_US", "bits_decimal", "Mb")])
@pytest.mark.parametrize("font_size", [9, 11])
def test_a_unit_wider_than_the_bar_is_drawn_smaller_not_clipped(q_app, font_size, lang, unit_type, unit):
    """Font-independent: a bar too narrow for the short unit at this font (en_US too, since
    CI's offscreen fallback font may have no Cyrillic glyphs to paint). The unit row (drawn alone)
    must still land inside it."""
    from netspeedtray.utils.widget_paint import SideBarFit, TIER_UNIT_ROW
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"language": lang, "unit_type": unit_type, "font_size": font_size,
                "short_unit_labels": True, "background_opacity": 0})
    renderer = WidgetRenderer(cfg, _i18n(lang))
    margin = constants.renderer.TEXT_MARGIN
    # The unit's INK, drawn from the left margin, ends 2 px past the bar. Measured by painting it:
    # font metrics overstate the ink by several px in some fonts (CI's offscreen fallback).
    w = margin + _ink_right(renderer.font, unit) - 2
    h = 3 * renderer.metrics.height()
    fit = SideBarFit(TIER_UNIT_ROW, True, True, True, False, False, False)
    img = QImage(3 * w, 3 * h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.translate(w, h)
    with patch.object(QPainter, "setClipRect", lambda *a, **k: None):
        renderer.draw_network_compact(p, 12.4e6 / 8, 88.6e6 / 8, w, h, renderer.config, fit, unit_only=True)
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    a = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4))[:, :, 3].copy()
    assert (a[h:2 * h, w:2 * w] > 24).any(), "the unit row was not painted"
    a[h:2 * h, w:2 * w] = 0
    xs = np.nonzero(a > 24)[1]
    assert not len(xs), (font_size, w, int(xs.min()) - w, int(xs.max()) - w)
