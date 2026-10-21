"""
Side (LEFT/RIGHT) taskbar, Phase A (#322 follow-up; Docs/reference/side-taskbar-design.md).

Win11 25H2 docks the native taskbar on the left or right at 48 px (Default) or 32 px (Small). Live
(2026-10-20) the widget was 50 px wide in a 48 px bar (the #88 floor), and its ~83 px readout was
clipped to "0.1 M". Now:

1. The width is the bar's strip, never more (``vertical_taskbar_strip``: inset capped by the window,
   the window when there is no inset - auto-hide), and position / drag / dock_signature use the same.
2. A "fits" ladder (``widget_paint.side_bar_fit``) picks the layout from measured font metrics:
   today's layout when it fits (wide third-party bars - unchanged), else a compact readout with ONE
   shared unit (``format_speed_compact``), whole numbers, no arrows ... down to a logged clip.
3. Hardware the bar can't hold goes to the hover card, which has its own gate and opens beside a side
   bar.

Text metrics: the suite's offscreen platform has only Qt's fallback font, so in-process tests assert
"what is painted stays inside the widget" for whatever tier the measured font gets. The tiers the
owner sees (Segoe UI 9 pt: T2 at 48, T3 at 32) are checked with real fonts in a subprocess on Qt's
native platform (QImages only - no window is shown).
"""
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QFontMetrics, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import taskbar_utils
from netspeedtray.utils.helpers import compact_speed_reference_strings, format_speed_compact
from netspeedtray.utils.taskbar_utils import TaskbarInfo, dock_signature, vertical_taskbar_strip
from netspeedtray.utils.widget_paint import (
    TIER_CLASSIC, TIER_CLIP, TIER_UNIT_ROW, TIER_WHOLE, WidgetMetrics, font_from_config, render_widget,
    side_bar_fit, side_bar_height,
)
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager

SRC_DIR = Path(__file__).resolve().parents[3]
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
LEFT, RIGHT, BOTTOM = constants.TaskbarEdge.LEFT, constants.TaskbarEdge.RIGHT, constants.TaskbarEdge.BOTTOM
EN = I18nStrings("en_US")
MBIT = 1e6 / 8          # bytes/sec in one Mbps

METRICS = WidgetMetrics(upload_mbps=12.4, download_mbps=88.6, cpu_usage=37.0, gpu_usage=52.0,
                        cpu_temp=58.0, gpu_temp=64.0, ram_used=9.2, ram_total=16.0, vram_used=3.1,
                        vram_total=8.0)

HW = dict(monitor_cpu_enabled=True, monitor_gpu_enabled=True, monitor_ram_enabled=True)
MODES = {
    "network_only": dict(widget_display_mode="network_only"),
    "network_graph": dict(widget_display_mode="network_only", graph_enabled=True),
    "split_graphs": dict(widget_display_mode="network_only", graph_enabled=True, graph_split_directions=True),
    "side_by_side": dict(widget_display_mode="side_by_side", graph_enabled=True, **HW),
    "side_by_stack": dict(widget_display_mode="side_by_side", stack_hardware_stats=True, **HW),
    "cycle": dict(widget_display_mode="cycle", **HW),
    "cpu_only": dict(widget_display_mode="cpu_only", **HW),
    "combined": dict(widget_display_mode="combined", **HW),
    "hide_units": dict(widget_display_mode="network_only", hide_unit_suffix=True),
    "bytes_long_units": dict(widget_display_mode="network_only", unit_type="bytes_binary"),
}


def _config(**overrides):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"free_float": False, "graph_enabled": False, "background_opacity": 0,
                "hardware_label_style": "icons"})
    cfg.update(overrides)
    return cfg


def _taskbar(edge, thickness, *, inset=None, dpi=1.0, screen_w=1920, screen_h=1080, slide=0):
    """A side taskbar ``thickness`` logical px wide. ``inset`` overrides the work-area inset (0 =
    auto-hide / #221: no inset); ``slide`` moves the window rect only (auto-hide animating)."""
    inset = thickness if inset is None else inset
    full = QRect(0, 0, screen_w, screen_h)
    phys = round(thickness * dpi)
    if edge == RIGHT:
        avail = QRect(0, 0, screen_w - inset, screen_h)
        rect = (round(screen_w * dpi) - phys + slide, 0, round(screen_w * dpi) + slide, round(screen_h * dpi))
    else:
        avail = QRect(inset, 0, screen_w - inset, screen_h)
        rect = (-slide, 0, phys - slide, round(screen_h * dpi))
    screen = MagicMock()
    screen.geometry.return_value = full
    screen.availableGeometry.return_value = avail
    tb = MagicMock(spec=TaskbarInfo)
    tb.rect, tb.hwnd, tb.dpi_scale = rect, 12345, dpi
    tb.height = rect[2] - rect[0]
    tb.tasklist_rect = None
    tb.get_edge_position.return_value = edge
    tb.get_screen.return_value = screen
    tb.get_tray_rect.return_value = (rect[0], round(908 * dpi), rect[2], round(screen_h * dpi))
    return tb


def _size(cfg, tb, lang="en_US"):
    """(width, height, side fit) the real WidgetLayoutManager gives the widget on taskbar ``tb``."""
    widget = MagicMock()
    widget.config = cfg
    widget.i18n = I18nStrings(lang)
    widget._identity_reserve_px.return_value = 0
    widget.ram_total, widget.vram_total = 16.0, 8.0
    lm = WidgetLayoutManager(widget)
    lm.metrics = QFontMetrics(font_from_config(cfg))
    lm.arrow_metrics = lm.metrics
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=tb), \
            patch("netspeedtray.views.widget.layout.is_small_taskbar", return_value=False), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
        lm.resize_widget_for_font()
    (w, h), _ = widget.setFixedSize.call_args
    return w, h, lm._side_fit


def _paint(cfg, width, height, *, lang="en_US", cycle_mode="network_only", metrics=METRICS):
    """Paint a width x height side-bar widget onto a 3x larger transparent canvas; RGBA array."""
    renderer = WidgetRenderer(cfg, I18nStrings(lang))
    img = QImage(width * 3, height * 3, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    render_widget(p, QRect(0, 0, width, height), renderer, renderer.config, metrics,
                  layout_mode="vertical", cycle_mode=cycle_mode, network_width=None,
                  font=font_from_config(cfg))
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    return np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4)).copy()


def _outside(arr, width, height):
    out = arr[:, :, 3] > 0
    out[:height, :width] = False
    return np.nonzero(out)


def _fit(cfg, width, lang="en_US"):
    rc = RenderConfig.from_dict(cfg)
    fm = QFontMetrics(font_from_config(cfg))
    return side_bar_fit(rc, fm, fm, I18nStrings(lang), width, mem_totals=(16.0, 8.0))


# --- 1. Width never exceeds the bar ---------------------------------------------------------------

@pytest.mark.parametrize("edge", [LEFT, RIGHT])
@pytest.mark.parametrize("thickness", [48, 32])
def test_widget_is_exactly_as_wide_as_the_bar(q_app, edge, thickness):
    w, _, _ = _size(_config(), _taskbar(edge, thickness))
    assert w == thickness            # was max(thickness, 50): 2 px over Default, 18 px over Small


@pytest.mark.parametrize("edge", [LEFT, RIGHT])
def test_auto_hide_side_bar_without_inset_uses_the_window_width(q_app, edge):
    tb = _taskbar(edge, 48, inset=0)
    assert vertical_taskbar_strip(tb, edge, 1.0)[1] == 48
    assert _size(_config(), tb)[0] == 48
    assert dock_signature(tb)[1] == 48           # was 0 (inset only), so auto-hide never re-sized
    # Mid-slide: same strip, same signature (no resize while the bar animates, #135).
    assert dock_signature(_taskbar(edge, 48, inset=0, slide=20)) == dock_signature(tb)


def test_inset_wider_than_the_taskbar_window_is_capped(q_app):
    """A second appbar on the same edge reserves more work area than the taskbar covers."""
    tb = _taskbar(RIGHT, 48, inset=80)
    assert vertical_taskbar_strip(tb, RIGHT, 1.0) == (1920 - 48, 48)
    assert _size(_config(), tb)[0] == 48


def test_fractional_dpi_strip_is_floored(q_app):
    tb = _taskbar(RIGHT, 38.4, inset=38, dpi=1.25)     # 48 physical px at 125%
    w, _, _ = _size(_config(), tb)
    assert w <= vertical_taskbar_strip(tb, RIGHT, 1.25)[1]


@pytest.mark.parametrize("edge", [LEFT, RIGHT])
@pytest.mark.parametrize("inset", [48, 0])
def test_position_and_drag_keep_the_widget_inside_the_strip(q_app, edge, inset):
    from PyQt6.QtCore import QPoint, QSize
    from netspeedtray.core.position_manager import PositionCalculator
    tb = _taskbar(edge, 48, inset=inset)
    w, h, _ = _size(_config(), tb)
    x0, th = vertical_taskbar_strip(tb, edge, 1.0)
    calc = PositionCalculator()
    pos = calc.calculate_position(tb, (w, h), {"tray_offset_y": 3})
    assert x0 <= pos.x and pos.x + w <= x0 + th, (pos, x0, th)
    dragged = calc.constrain_drag_position(QPoint(500, 400), tb, QSize(w, h))
    assert dragged.x() == pos.x                     # drag uses the same strip as the placement


# --- 2. The compact number formatter --------------------------------------------------------------

def test_compact_shares_one_unit_and_keeps_three_digits():
    assert format_speed_compact(12.4 * MBIT, 88.6 * MBIT, EN) == ("12.4", "88.6", "Mbps")
    # The LARGER value picks the unit for both: 0.5 Mbps up reads in Mbps next to 120 Mbps down.
    assert format_speed_compact(0.5 * MBIT, 120.4 * MBIT, EN) == ("0.5", "120", "Mbps")
    assert format_speed_compact(120.4 * MBIT, 0.5 * MBIT, EN) == ("120", "0.5", "Mbps")   # either side
    assert format_speed_compact(5 * MBIT, 999.4 * MBIT, EN) == ("5.0", "999", "Mbps")
    # 999.5 would round to four digits: promote both to the next unit.
    assert format_speed_compact(5 * MBIT, 999.6 * MBIT, EN) == ("0.0", "1.0", "Gbps")
    assert format_speed_compact(300e3 / 8, 40e3 / 8, EN) == ("300", "40.0", "Kbps")


def test_compact_whole_numbers_from_ten_and_decimal_places():
    assert format_speed_compact(12.4 * MBIT, 9.4 * MBIT, EN, whole_from_ten=True) == ("12", "9.4", "Mbps")
    assert format_speed_compact(9.96 * MBIT, 0, EN, whole_from_ten=True)[0] == "10"   # rounds into 10+
    assert format_speed_compact(9.96 * MBIT, 0, EN)[0] == "10.0"
    assert format_speed_compact(12.4 * MBIT, 0, EN, decimal_places=0)[0] == "12"
    assert format_speed_compact(12.44 * MBIT, 0, EN, decimal_places=2)[0] == "12.4"   # 2 -> 1


def test_compact_honours_locale_units_and_always_mbps():
    de = I18nStrings("de_DE")
    up, dw, unit = format_speed_compact(12.4 * MBIT, 88.6 * MBIT, de)
    assert (up, dw) == ("12,4", "88,6") and unit == de.MBITS_LABEL
    assert format_speed_compact(12.4 * MBIT, 88.6 * MBIT, EN, short_labels=True)[2] == EN.MBITS_SHORT_LABEL
    # always_mbps keeps the mega floor for small speeds, and still promotes to giga.
    assert format_speed_compact(0.2 * MBIT, 0.4 * MBIT, EN, force_mega_unit=True) == ("0.2", "0.4", "Mbps")
    assert format_speed_compact(0, 1250 * MBIT, EN, force_mega_unit=True)[2] == "Gbps"
    assert format_speed_compact(10e6, 0, EN, unit_type="bytes_decimal")[2] == "MB/s"


def test_compact_values_never_exceed_the_reference_width(q_app):
    fm = QFontMetrics(font_from_config(_config()))
    rng = np.random.default_rng(7)
    for whole in (False, True):
        ref_w = max(fm.horizontalAdvance(s) for s in compact_speed_reference_strings(1, whole))
        for up, dw in (10 ** rng.uniform(0, 11, size=(400, 2))) / 8:
            for text in format_speed_compact(up, dw, EN, whole_from_ten=whole)[:2]:
                assert len(text.replace(".", "")) <= 3, (up, dw, text)
                assert fm.horizontalAdvance(text) <= ref_w, (up, dw, text)


# --- 3. The tier ladder ------------------------------------------------------------------------

@pytest.mark.parametrize("mode", MODES.keys())
def test_a_wide_side_bar_keeps_todays_layout(q_app, mode):
    """A 400 px third-party side bar fits today's layout: classic, nothing moved to the card."""
    fit = _fit(_config(**MODES[mode]), 400)
    assert fit.classic and fit.tier == TIER_CLASSIC and not fit.hides_hardware


@pytest.mark.parametrize("mode", MODES.keys())
@pytest.mark.parametrize("thickness", [48, 32])
@pytest.mark.parametrize("edge", [LEFT, RIGHT])
def test_every_mode_paints_inside_a_native_side_bar(q_app, mode, thickness, edge):
    cfg = _config(**MODES[mode])
    w, h, fit = _size(cfg, _taskbar(edge, thickness))
    assert w == thickness and fit is not None
    for phase in (["network_only", "cpu_only", "gpu_only"] if mode == "cycle" else ["network_only"]):
        arr = _paint(cfg, w, h, cycle_mode=phase)
        ys, xs = _outside(arr, w, h)
        assert not len(xs), f"{mode}/{phase} painted outside the {w}x{h} widget at x {xs.min()}..{xs.max()}"
        assert (arr[:h, :w, 3] >= 250).sum() > 20, f"{mode}/{phase}: nothing readable painted"


@pytest.mark.parametrize("thickness", [48, 32])
def test_the_chosen_tier_measurably_fits(q_app, thickness):
    """Not a pixel constant: whatever tier the measured font gets, its widest row fits the bar."""
    cfg = _config()
    fit = _fit(cfg, thickness)
    fm = QFontMetrics(font_from_config(cfg))
    inner = thickness - 2 * constants.renderer.TEXT_MARGIN
    arrow = max(fm.horizontalAdvance(EN.UPLOAD_ARROW), fm.horizontalAdvance(EN.DOWNLOAD_ARROW))
    whole = fit.tier >= TIER_WHOLE
    num = max(fm.horizontalAdvance(s) for s in compact_speed_reference_strings(1, whole))
    if fit.tier < TIER_CLIP:
        row = num + (arrow + constants.renderer.COMPACT_ARROW_GAP_PX if fit.tier < 4 else 0)
        assert row <= inner, (fit, row, inner)
    # ... and the tier above it does not (the ladder took the first fit, top down).
    if TIER_UNIT_ROW <= fit.tier < TIER_CLIP:
        assert _fit(cfg, 400).tier < fit.tier


def test_the_ladder_only_steps_down_as_the_bar_narrows(q_app):
    cfg = _config()
    tiers = [_fit(cfg, w).tier for w in (400, 120, 64, 48, 40, 32, 26, 20, 12)]
    assert tiers == sorted(tiers), tiers


def test_side_by_side_moves_hardware_to_the_card_on_a_native_bar(q_app):
    cfg = _config(**MODES["side_by_side"])
    w, h, fit = _size(cfg, _taskbar(RIGHT, 48))
    assert fit.hides_hardware and not fit.classic
    # Only the network readout (+ graph) is on the bar: as tall as network_only, not the 5-row stack.
    assert h == _size(_config(**MODES["network_graph"]), _taskbar(RIGHT, 48))[1]


def test_cycle_is_sized_to_its_tallest_phase(q_app):
    cfg = _config(**MODES["cycle"])
    w, h, fit = _size(cfg, _taskbar(RIGHT, 32))
    for phase in ("network_only", "cpu_only", "gpu_only"):
        arr = _paint(cfg, w, h, cycle_mode=phase)
        ys = np.nonzero((arr[:, :, 3] >= 250).any(axis=1))[0]
        assert ys.max() < h, (phase, ys.max(), h)


def test_layout_height_is_the_shared_side_bar_height(q_app):
    """Layout and Settings preview size from the same function - no drift between them."""
    for mode, over in MODES.items():
        cfg = _config(**over)
        for th in (400, 48, 32):
            w, h, fit = _size(cfg, _taskbar(LEFT, th))
            fm = QFontMetrics(font_from_config(cfg))
            assert h == max(20, side_bar_height(RenderConfig.from_dict(cfg), fit, fm.height())), (mode, th)


def test_horizontal_bars_never_use_the_ladder(q_app):
    """The ladder is side-bar only: a TOP/BOTTOM widget is sized and painted exactly as before."""
    cfg = _config(**MODES["side_by_side"])
    r = WidgetRenderer(cfg, EN)
    imgs = []
    for _ in range(2):
        img = QImage(300, 40, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        render_widget(p, QRect(0, 0, 300, 40), r, r.config, METRICS, layout_mode="horizontal",
                      font=font_from_config(cfg))
        p.end()
        imgs.append(img)
    assert imgs[0] == imgs[1]
    with patch("netspeedtray.utils.widget_paint.side_bar_fit", side_effect=AssertionError("called")):
        img = QImage(300, 40, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        render_widget(p, QRect(0, 0, 300, 40), r, r.config, METRICS, layout_mode="horizontal",
                      font=font_from_config(cfg))
        p.end()
    assert img == imgs[0]


def test_compact_clip_is_logged_once(q_app, caplog):
    cfg = _config(font_size=20)
    r = WidgetRenderer(cfg, EN)
    for _ in range(3):
        img = QImage(12, 80, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        render_widget(p, QRect(0, 0, 12, 80), r, r.config, METRICS, layout_mode="vertical",
                      font=font_from_config(cfg))
        p.end()
    assert sum("too narrow" in rec.message for rec in caplog.records) == 1


# --- 4. Settings preview parity -------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["network_only", "side_by_side", "split_graphs", "cycle"])
@pytest.mark.parametrize("thickness", [48, 32, 400])
def test_preview_matches_the_live_side_bar_widget(q_app, mode, thickness):
    from netspeedtray.utils.widget_paint import demo_metrics
    from netspeedtray.views.widget.preview import PreviewWidget
    cfg = _config(**MODES[mode])
    w, h, _ = _size(cfg, _taskbar(RIGHT, thickness))
    pv = PreviewWidget(cfg, EN, width=300, height=40)
    pv.set_bar_width(thickness)
    assert (pv.width(), pv.height()) == (w, h)
    # Same pixels as the live widget's paint path for the same snapshot.
    live = _paint(cfg, w, h, metrics=demo_metrics())[:h, :w]
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    pv.render(img)
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(h * w * 4)
    shown = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
    assert np.array_equal(live[:, :, 3] > 0, shown[:, :, 3] > 0)
    pv.set_bar_width(None)
    assert pv.height() == 40 and pv.side_fit is None


# --- 5. Hover card: hardware section, placement, gate ---------------------------------------------

def _card(**kw):
    from netspeedtray.views.usage_flyout import UsageFlyout
    return UsageFlyout(EN, **kw)


HW_SNAPSHOT = {"cpu": {"usage": 37.0, "temp": 58.0}, "gpu": {"usage": 52.0, "temp": None},
               "ram": (9.2, 16.0), "vram": (3.1, 8.0)}


def test_card_shows_hardware_and_updates_it_live_without_resizing(q_app):
    from PyQt6.QtWidgets import QLabel
    card = _card(hardware=HW_SNAPSHOT)
    texts = [lbl.text() for lbl in card.findChildren(QLabel)]
    for t in ("CPU", "GPU", "RAM", "VRAM", "37%", "58°C", "52%", "9.2 / 16.0 GB"):
        assert t in texts, (t, texts)
    assert "—" in texts                               # the GPU has no temperature reading
    card.adjustSize()
    before = card.sizeHint()
    card.update_hardware({**HW_SNAPSHOT, "cpu": {"usage": 100.0, "temp": 99.0}, "ram": (15.9, 16.0)})
    texts = [lbl.text() for lbl in card.findChildren(QLabel)]
    assert "100%" in texts and "15.9 / 16.0 GB" in texts
    assert card.sizeHint() == before                  # fixed-width columns: the card never jumps
    assert _card(today=(1, 2), month=(3, 4)).has_hardware is False


@pytest.mark.parametrize("edge", [RIGHT, LEFT, constants.TaskbarEdge.TOP, BOTTOM, None])
def test_card_opens_away_from_the_bar_and_inside_the_work_area(q_app, edge):
    screen_w, screen_h = 1920, 1080
    if edge == RIGHT:
        avail, widget = QRect(0, 0, screen_w - 48, screen_h), QRect(screen_w - 48, 800, 48, 58)
    elif edge == LEFT:
        avail, widget = QRect(48, 0, screen_w - 48, screen_h), QRect(0, 800, 48, 58)
    elif edge == constants.TaskbarEdge.TOP:
        avail, widget = QRect(0, 48, screen_w, screen_h - 48), QRect(1600, 0, 85, 48)
    else:
        avail, widget = QRect(0, 0, screen_w, screen_h - 48), QRect(1600, 1032, 85, 48)
    card = _card(hardware=HW_SNAPSHOT)
    try:
        card.show_for(widget, avail, edge=edge)
        g = card.geometry()
        assert avail.contains(g), (g, avail)
        assert not g.intersects(widget)
        if edge == RIGHT:
            assert 8 <= avail.right() - g.right() <= 9 and abs(g.center().y() - widget.center().y()) <= 1
        elif edge == LEFT:
            assert g.left() == avail.left() + 8 and abs(g.center().y() - widget.center().y()) <= 1
        elif edge == constants.TaskbarEdge.TOP:
            assert g.top() > widget.bottom()
        else:
            assert g.bottom() < widget.top()
    finally:
        card.hide()
        card.deleteLater()


def _hover_fake(fit, **config):
    from netspeedtray.views.widget.main import NetworkSpeedWidget as W
    cfg = {"show_usage_on_hover": False, "show_hover_tips": False, **config}
    fake = types.SimpleNamespace(
        config=cfg, _cached_layout_mode="vertical", layout_manager=types.SimpleNamespace(_side_fit=fit),
        cpu_usage=37.0, gpu_usage=52.0, cpu_temp=58.0, gpu_temp=None, cpu_power=None, gpu_power=None,
        ram_used=9.2, ram_total=16.0, vram_used=3.1, vram_total=8.0, network_identity=None,
        logger=MagicMock(), _hover_card=None, _HOVER_HW_REFRESH_SEC=W._HOVER_HW_REFRESH_SEC)
    for name in ("_side_bar_fit", "_hover_hardware_wanted", "_hover_hardware_snapshot",
                 "_identity_presentation", "_identity_ssid_text", "_refresh_hover_hardware"):
        setattr(fake, name, types.MethodType(getattr(W, name), fake))
    return fake


def test_hardware_gate_is_independent_of_the_usage_and_tips_toggles(q_app):
    hiding = _fit(_config(**MODES["side_by_side"]), 48)
    fake = _hover_fake(hiding, **HW)
    assert fake._hover_hardware_wanted()                       # both usage toggles are OFF
    snap = fake._hover_hardware_snapshot()
    assert set(snap) == {"cpu", "gpu", "ram"} and snap["cpu"]["usage"] == 37.0
    assert "temp" not in snap["cpu"]                           # temps are off in the config

    assert not _hover_fake(_fit(_config(**MODES["side_by_side"]), 400), **HW)._hover_hardware_wanted()
    assert not _hover_fake(_fit(_config(), 48))._hover_hardware_wanted()   # network_only: nothing hidden
    horizontal = _hover_fake(hiding, **HW)
    horizontal._cached_layout_mode = "horizontal"
    assert not horizontal._hover_hardware_wanted()             # phase C, not now


def test_open_card_refreshes_its_hardware_about_once_a_second(q_app):
    fake = _hover_fake(_fit(_config(**MODES["side_by_side"]), 48), **HW)
    fake._hover_card = MagicMock(has_hardware=True)
    fake._hover_hw_refreshed = 0.0
    fake._refresh_hover_hardware()
    fake._refresh_hover_hardware()                            # same tick: throttled
    assert fake._hover_card.update_hardware.call_count == 1


# --- 6. Widgets-overlap nudge compares y on a side bar --------------------------------------------

def test_overlap_on_a_side_bar_is_decided_by_y():
    from netspeedtray.utils.taskbar_utils import rect_overlaps_y
    widgets = (1872, 0, 1920, 120)                       # the Widgets button at the top of a right bar
    assert rect_overlaps_y(800, 858, widgets, 1.0) is False
    assert rect_overlaps_y(100, 158, widgets, 1.0) is True
    assert rect_overlaps_y(100, 158, widgets, 2.0) is False  # physical 0..120 -> logical 0..60


@pytest.mark.parametrize("y,expect", [(800, False), (40, True)])
def test_side_bar_widget_is_nudged_only_when_it_really_overlaps(y, expect):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake = types.SimpleNamespace(
        _widgets_nudge_shown=False, logger=MagicMock(), config={}, update_config=MagicMock(),
        x=lambda: 1872, y=lambda: y, width=lambda: 48, height=lambda: 58, i18n=EN)
    tb = types.SimpleNamespace(widgets_rect=(1872, 0, 1920, 120), dpi_scale=1.0,
                               get_edge_position=lambda: RIGHT)
    with patch("netspeedtray.views.widget.main.QTimer.singleShot"), \
            patch.object(taskbar_utils, "set_widgets_probe_enabled"):
        NetworkSpeedWidget._maybe_nudge_widgets_overlap(fake, tb)
    assert fake._widgets_nudge_shown is expect


# --- 7. Real fonts: the tiers the owner will see ---------------------------------------------------

_PROBE = r"""
import importlib, json, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
t = importlib.import_module("netspeedtray.tests.unit.test_side_taskbar_compact")
rows = []
for name in ("network_only", "side_by_side", "cycle", "split_graphs"):
    for th in (48, 32):
        for lang in ("en_US", "ru_RU", "de_DE"):
            cfg = t._config(hardware_label_style="icons_colored", **t.MODES[name])
            w, h, fit = t._size(cfg, t._taskbar(t.RIGHT, th), lang)
            arr = t._paint(cfg, w, h, lang=lang)
            ys, xs = t._outside(arr, w, h)
            rows.append([name, th, lang, w, h, fit.tier, bool(fit.classic), bool(fit.hides_hardware),
                         int(len(xs))])
print("RESULT" + json.dumps(rows), flush=True)
"""


@pytest.mark.skipif(sys.platform != "win32" or not FONT_DIR.is_dir(),
                    reason="needs Windows and its fonts for real text metrics")
def test_native_side_bars_with_real_fonts(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORM", "QT_QPA_FONTDIR")}
    env.update({"QT_QPA_PLATFORM": "windows", "QT_SCALE_FACTOR": "1", "APPDATA": str(tmp_path),
                "PYTHONPATH": str(SRC_DIR), "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.run([sys.executable, "-c", _PROBE], env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=180)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
    assert lines, f"probe failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    for name, th, lang, w, h, tier, classic, hides_hw, outside in json.loads(lines[-1][len("RESULT"):]):
        where = (name, th, lang, w, h, tier)
        assert w == th and outside == 0, where
        assert not classic, where                              # today's ~83 px readout never fits
        # Segoe UI 9 pt: arrow + "99.9" per row with the unit below on Default, whole numbers on Small.
        assert tier == (TIER_UNIT_ROW if th == 48 else TIER_WHOLE), where
        assert hides_hw == (name in ("side_by_side", "cycle")), where


# --- 8. Review follow-ups -------------------------------------------------------------------------

def test_centring_never_rounds_past_a_fractional_strip():
    """Exhaustive: a widget that fits the strip's whole pixels is never rounded 1 px past either edge
    (why _centre_in_strip needs no clamp)."""
    import math
    from netspeedtray.core.position_manager import PositionCalculator
    for x10 in range(0, 100):
        for s10 in range(300, 500, 3):
            x0, strip = 1880 + x10 / 10, s10 / 10
            left, right = math.ceil(x0), math.floor(x0 + strip)
            for w in range(right - left - 3, right - left + 1):
                x = PositionCalculator._centre_in_strip(w, x0, strip)
                assert left <= x and x + w <= right, (x0, strip, w, x)


def test_strip_follows_the_taskbar_window_when_another_appbar_shares_the_edge(q_app):
    """Another appbar between the taskbar and the screen edge: the reserved band (80 px) is wider
    than the taskbar (48 px) and the taskbar is NOT the part touching the edge."""
    tb = _taskbar(RIGHT, 48, inset=80)
    tb.rect = (1920 - 80, 0, 1920 - 32, 1080)            # taskbar 1840..1888, appbar 1888..1920
    assert vertical_taskbar_strip(tb, RIGHT, 1.0) == (1840, 48)
    tb.rect = (round((1920 - 80) * 1.5), 0, round((1920 - 32) * 1.5), 1620)
    tb.dpi_scale = 1.5
    assert vertical_taskbar_strip(tb, RIGHT, 1.5) == (1840, 48)


@pytest.mark.parametrize("lang,unit_type", [("ru_RU", "bytes_binary"), ("ru_RU", "bits_binary"),
                                            ("de_DE", "bytes_binary")])
@pytest.mark.parametrize("font_size", [11, 13, 20])
def test_a_unit_wider_than_a_small_bar_is_clipped_not_overhung(q_app, lang, unit_type, font_size):
    cfg = _config(widget_display_mode="network_only", unit_type=unit_type, font_size=font_size)
    w, h, _ = _size(cfg, _taskbar(RIGHT, 32), lang)
    for speeds in ((12.4, 88.6), (0.0, 0.0), (900.0, 1200.0)):
        m = WidgetMetrics(upload_mbps=speeds[0], download_mbps=speeds[1])
        ys, xs = _outside(_paint(cfg, w, h, lang=lang, metrics=m), w, h)
        assert not len(xs), (lang, unit_type, font_size, speeds, xs.min(), xs.max())


def test_compact_readout_never_paints_outside_its_band(q_app):
    """Fonts differ (CI's fallback font is narrower than Segoe UI with Cyrillic units), so drive the
    painter directly: a T2 readout whose unit row ("МиБ/с" short form) can't fit a narrow band is
    clipped to it, never painted over the neighbouring screen."""
    from netspeedtray.utils.widget_paint import SideBarFit
    cfg = _config(unit_type="bytes_binary", font_size=13)
    ru = I18nStrings("ru_RU")
    r = WidgetRenderer(cfg, ru)
    fit = SideBarFit(TIER_UNIT_ROW, False, True, True, False, False, False)
    width, height = 16, 60
    img = QImage(width * 3, height * 3, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.translate(width, height)
    r.draw_network_compact(p, 12.4 * MBIT, 88.6 * MBIT, width, height, r.config, fit)
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4)).copy()
    inside = arr[height:2 * height, width:2 * width, 3] > 0
    assert inside.any()
    assert (arr[:, :, 3] > 0).sum() == inside.sum(), "painted outside the band"


def test_card_lists_only_the_stats_the_mode_shows(q_app):
    cfg = _config(widget_display_mode="cpu_only", monitor_vram_enabled=True, show_hardware_temps=True, **HW)
    keep = ("monitor_", "widget_", "show_hardware", "stack_")
    fake = _hover_fake(_fit(cfg, 32), **{k: v for k, v in cfg.items() if k.startswith(keep)})
    assert set(fake._hover_hardware_snapshot()) == {"cpu", "ram"}      # no GPU / VRAM in cpu_only
    order = _config(**{**MODES["side_by_side"], "widget_display_order": ["network", "cpu", "none"],
                       "stack_hardware_stats": False})
    fake = _hover_fake(_fit(order, 48), **{k: v for k, v in order.items() if k.startswith(keep)})
    assert set(fake._hover_hardware_snapshot()) == {"cpu", "ram"}      # GPU dropped from the order


def test_open_card_closes_when_its_hardware_goes_away(q_app):
    fake = _hover_fake(_fit(_config(**MODES["side_by_side"]), 48), **HW)
    card = MagicMock(has_hardware=True)
    fake._hover_card = card
    fake._hover_hw_refreshed = 0.0
    fake._hide_usage_hover_card = MagicMock()
    fake.config.update(monitor_cpu_enabled=False, monitor_gpu_enabled=False, monitor_ram_enabled=False)
    fake._refresh_hover_hardware()
    fake._hide_usage_hover_card.assert_called_once()
    card.update_hardware.assert_not_called()


def test_a_resize_closes_an_open_card(q_app):
    cfg = _config()
    widget = MagicMock()
    widget.config, widget.i18n = cfg, EN
    widget._identity_reserve_px.return_value = 0
    widget._hover_card = object()
    lm = WidgetLayoutManager(widget)
    lm.metrics = lm.arrow_metrics = QFontMetrics(font_from_config(cfg))
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=_taskbar(RIGHT, 48)), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
        lm.resize_widget_for_font()
    widget._hide_usage_hover_card.assert_called_once()


def test_hover_arms_the_card_for_hardware_with_usage_and_tips_off(q_app):
    """The ARM gate itself (enterEvent), not just the helper: usage + tips off and the side bar hides
    the hardware -> the card is armed; on a bar that fits everything it is not."""
    from PyQt6.QtCore import QPointF, QTimer
    from PyQt6.QtGui import QEnterEvent
    from PyQt6.QtWidgets import QWidget
    from netspeedtray.views.widget.main import NetworkSpeedWidget

    class _Armable(NetworkSpeedWidget):
        def __init__(self, fit):           # only the state enterEvent reads - not the whole app
            QWidget.__init__(self)
            self.config = {"show_usage_on_hover": False, "show_hover_tips": False, **HW,
                           "widget_display_mode": "side_by_side"}
            self._cached_layout_mode = "vertical"
            self.layout_manager = types.SimpleNamespace(_side_fit=fit)
            self._hover_card = None
            self._hover_card_timer = QTimer()
            self._hover_card_timer.setSingleShot(True)
            self.logger = MagicMock()

    event = QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1))
    hiding = _Armable(_fit(_config(**MODES["side_by_side"]), 48))
    hiding.enterEvent(event)
    fits = _Armable(_fit(_config(**MODES["side_by_side"]), 400))
    fits.enterEvent(event)
    try:
        assert hiding._hover_card_timer.isActive()
        assert not fits._hover_card_timer.isActive()
    finally:
        for w in (hiding, fits):
            w._hover_card_timer.stop()
            w.deleteLater()


def test_preview_does_not_re_measure_when_the_bar_is_unchanged(q_app):
    from netspeedtray.views.widget.preview import PreviewWidget
    pv = PreviewWidget(_config(), EN, width=300, height=40)
    with patch.object(PreviewWidget, "_fit_to_content") as fit:
        pv.set_bar_width(None)                       # a horizontal bar: called on every preview update
        pv.set_bar_width(None)
        assert fit.call_count == 0
        pv.set_bar_width(48)
        pv.set_bar_width(48)
        assert fit.call_count == 1
