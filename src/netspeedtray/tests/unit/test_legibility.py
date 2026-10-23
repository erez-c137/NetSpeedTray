"""
The widget legibility package "L" (2.2 rc.2; Docs/releases/v2.2/widget-legibility-options.md, "DECIDED
2026-10-23"): the erasing halo, the Detailed blend, the widget graph's scale floor, High Contrast, the
accent-coloured taskbar, the 1.4 config step, the font / arrow / side-bar polish. The band palette's
numbers are pinned in test_color_scheme (test_band_palette_*).
"""
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QFont, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.widget_state import AggregatedSpeedData
from netspeedtray.tests.color_helpers import themed
from netspeedtray.utils import color_scheme, taskbar_appearance
from netspeedtray.utils.config import ConfigManager
from netspeedtray.utils.taskbar_appearance import TaskbarAppearance
from netspeedtray.utils import widget_paint
from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer, make_widget_font

D = constants.config.defaults
EN = I18nStrings("en_US")
MBPS = 125_000.0
T0 = datetime(2026, 10, 23, 12, 0, 0)


def _history(up_mbps, down_mbps, n=120):
    return [AggregatedSpeedData(upload=up_mbps * MBPS, download=down_mbps * MBPS,
                                timestamp=T0 + timedelta(seconds=i)) for i in range(n)]


def _cfg(**over):
    cfg = dict(D.DEFAULT_CONFIG, widget_display_mode="network_only", graph_enabled=True,
               graph_style="detailed", graph_opacity=100, background_opacity=0)
    cfg.update(over)
    return themed(cfg)


def _paint(cfg, metrics=None, w=200, h=48, dpr=1.0, renderer=None):
    """The frame as an (h*dpr, w*dpr, 4) uint8 RGBA array (premultiplied), transparent around it."""
    renderer = renderer or WidgetRenderer(cfg, EN)
    metrics = metrics or WidgetMetrics(upload_mbps=40.0, download_mbps=40.0, net_history=_history(40, 40))
    img = QImage(round(w * dpr), round(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        render_widget(p, QRect(0, 0, w, h), renderer, renderer.config, metrics, font=font_from_config(cfg))
    finally:
        p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return np.frombuffer(bytes(ptr), dtype=np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[
        :, :img.width()].astype(int)


def _dilate(mask, k):
    out = mask.copy()
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            out |= np.roll(np.roll(mask, dy, 0), dx, 1)
    return out


def _magentaish(arr):
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    return (a > 30) & (r > g + 25) & (b > g + 25)


def _text_core(arr):
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    return (a >= 250) & (r >= 245) & (g >= 245) & (b >= 245)


# --------------------------------------------------------------------------- 1. the erasing halo

GRAPH_MAGENTA = dict(graph_upload_color="#FF00FF", graph_download_color="#FF00FF", default_color="#FFFFFF")


@pytest.mark.parametrize("style", ["detailed", "trend"])
@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_the_graph_is_erased_around_every_glyph(q_app, style, dpr):
    """A steady 40 Mbps fill sits behind both text rows. Within the halo radius (1.5 px x the pixel
    ratio) of any glyph pixel there is no graph colour left; away from the text the graph is drawn."""
    arr = _paint(_cfg(graph_style=style, **GRAPH_MAGENTA), dpr=dpr)
    core = _text_core(arr)
    assert core.sum() > 100
    near = _dilate(core, max(1, int(constants.renderer.HALO_RADIUS_PX * dpr) - 1))   # square: diagonals reach further
    graph = _magentaish(arr)
    assert graph[near].sum() == 0, f"{graph[near].sum()} graph pixels inside the halo"
    assert graph[~_dilate(core, int(4 * dpr))].sum() > 50, "the graph itself vanished"


def test_without_the_halo_the_graph_runs_into_the_glyphs(q_app, monkeypatch):
    """The control for the test above: with the layers switched off the same frame has graph colour
    right against the glyphs (what 2.2 rc.1 painted)."""
    monkeypatch.setattr(widget_paint, "_halo_wanted", lambda config: False)
    arr = _paint(_cfg(**GRAPH_MAGENTA))
    assert _magentaish(arr)[_dilate(_text_core(arr), 1)].sum() > 20


def test_the_widget_background_is_never_erased(q_app):
    """An opaque background: every pixel of the frame stays opaque - the halo only cuts the graph."""
    arr = _paint(_cfg(background_color="#303030", background_opacity=100, **GRAPH_MAGENTA))
    assert (arr[..., 3] == 255).all()
    near = _dilate(_text_core(arr), 1) & ~_text_core(arr)
    grey = (np.abs(arr[..., 0] - 0x30) <= 2) & (np.abs(arr[..., 1] - 0x30) <= 2)
    assert grey[near].sum() > 0.3 * near.sum()        # the background, not the graph, shows by the text
    assert _magentaish(arr)[_dilate(_text_core(arr), 1)].sum() == 0


def test_a_partly_transparent_background_survives_the_halo(q_app):
    arr = _paint(_cfg(background_color="#000000", background_opacity=40, **GRAPH_MAGENTA))
    assert (arr[..., 3] >= round(0.4 * 255) - 1).all()


def test_the_halo_layers_are_only_used_with_a_graph(q_app, monkeypatch):
    seen = []
    real = widget_paint._HaloLayers.begin
    monkeypatch.setattr(widget_paint._HaloLayers, "begin",
                        classmethod(lambda cls, p, r: seen.append(1) or real.__func__(cls, p, r)))
    _paint(_cfg(graph_enabled=False))
    assert not seen
    _paint(_cfg())
    assert seen


def test_the_split_view_and_the_side_by_side_probe_draw_the_graph_once(q_app):
    """The right-align probe paints the foreground once more to measure it; it must not paint the graph
    into the live layer a second time (it would double the fill's alpha)."""
    cfg = _cfg(widget_display_mode="side_by_side", graph_split_directions=True, **GRAPH_MAGENTA)
    r = WidgetRenderer(cfg, EN)
    calls = []
    real = r._draw_mini_graph
    r._draw_mini_graph = lambda p, *a, **k: calls.append(p) or real(p, *a, **k)
    _paint(cfg, renderer=r, w=300)
    from collections import Counter
    # Once per panel on each painter: the probe's throwaway image, and the live graph layer - never the
    # live layer twice.
    assert sorted(Counter(id(p) for p in calls).values()) == [2, 2]


def test_halo_mask_grows_the_text_by_the_radius(q_app):
    """The mask is full strength out to HALO_RADIUS_PX and fades to ~0 by radius + feather."""
    img = QImage(40, 20, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    for y in range(20):
        img.setPixelColor(20, y, QColor(255, 255, 255, 255))     # a 1 px vertical stroke at x = 20
    mask = widget_paint.halo_mask(img)
    alpha = [mask.pixelColor(x, 10).alpha() for x in range(40)]
    assert alpha[20] == 255 and alpha[21] == 255 and alpha[19] == 255     # within 1.5 px: erased
    assert 0 < alpha[23] < 255                                             # the feather
    assert alpha[26] == 0 and alpha[14] == 0                              # past radius + feather


def test_the_mirror_paints_through_the_shared_path():
    """The paint-only mirrors of #434 call the same render_widget, so they get the halo too."""
    from netspeedtray.views.widget import mirror
    assert mirror.render_widget is widget_paint.render_widget


def test_the_halo_costs_little(q_app):
    """Budget: a 1 Hz repaint must stay cheap. Generous bound (CI machines are slow)."""
    import time
    cfg = _cfg()
    r = WidgetRenderer(cfg, EN)
    _paint(cfg, renderer=r, dpr=1.5)
    t0 = time.perf_counter()
    for _ in range(10):
        _paint(cfg, renderer=r, dpr=1.5)
    assert (time.perf_counter() - t0) / 10 < 0.05


# --------------------------------------------------------------------------- 2a / 3a: the graph itself

def test_the_detailed_fill_starts_at_alpha_80():
    assert constants.renderer.DETAILED_FILL_ALPHA == 80
    assert D.DEFAULT_GRAPH_OPACITY == 66          # unchanged, on purpose


@pytest.mark.parametrize("chatter_bps", [0.0, 2_000.0, 9_000.0, 100_000.0])
def test_idle_chatter_stays_low_on_the_widget_graph(chatter_bps):
    """3a: the auto-fit never tops out below 1 Mbps, so 1-9 KB/s of chatter is a sliver, not a half-full
    graph under a readout of 0.0."""
    hist = [AggregatedSpeedData(upload=chatter_bps, download=chatter_bps, timestamp=T0 + timedelta(seconds=i))
            for i in range(60)]
    top, ceiling = WidgetRenderer._network_graph_scale(hist, RenderConfig.from_dict(dict(D.DEFAULT_CONFIG)))
    assert constants.renderer.MIN_Y_SCALE == 125_000
    assert top == 125_000 and ceiling is None
    assert chatter_bps / top < 0.1 or chatter_bps >= 100_000


def test_busy_traffic_still_fills_the_graph():
    hist = _history(40, 40, 60)
    top, _ = WidgetRenderer._network_graph_scale(hist, RenderConfig.from_dict(dict(D.DEFAULT_CONFIG)))
    assert top == pytest.approx(40 * MBPS * constants.renderer.GRAPH_Y_AXIS_PADDING_FACTOR)


def test_the_monitor_window_does_not_use_the_widget_floor():
    import inspect
    from netspeedtray.views.graph import renderer as monitor_renderer
    assert "MIN_Y_SCALE" not in inspect.getsource(monitor_renderer)


# --------------------------------------------------------------------------- 4a: High Contrast

HC_DESERT = TaskbarAppearance(high_contrast=True, hc_text="#3D3D3D", hc_background="#FFFAEF")


@pytest.fixture
def appearance(monkeypatch):
    def set_(app):
        monkeypatch.setattr(color_scheme, "read_taskbar_appearance", lambda shell_light=False: app)
        monkeypatch.setattr(color_scheme, "_appearance", None)
    return set_


def test_high_contrast_paints_in_the_system_text_colour(appearance):
    appearance(HC_DESERT)
    cfg = _cfg(color_coding=True, background_opacity=80, background_color="#000000",
               hardware_label_color="brand", use_custom_arrow_colors=True)
    snapshot = dict(cfg)
    rc = RenderConfig.from_dict(cfg)
    assert rc.high_contrast and not rc.color_coding
    for stem in D.THEMED_COLOR_STEMS:
        assert getattr(rc, stem) == "#3D3D3D", stem
    assert rc.background_opacity == 0.0 and rc.hardware_label_color == "match_text"
    assert rc.graph_enabled is True               # the layout keeps its shape...
    assert cfg == snapshot                        # ...and the config is untouched


def test_high_contrast_hides_the_graph(q_app, appearance):
    appearance(HC_DESERT)
    arr = _paint(_cfg(**GRAPH_MAGENTA))
    assert _magentaish(arr).sum() == 0
    ink = arr[arr[..., 3] >= 250][:, :3]
    assert len(ink) and all(abs(c - 0x3D) <= 2 for c in ink[0])


def test_high_contrast_off_paints_as_before(appearance):
    appearance(taskbar_appearance.PLAIN)
    rc = RenderConfig.from_dict(_cfg(color_coding=True))
    assert not rc.high_contrast and rc.color_coding and rc.default_color == "#FFFFFF"


def test_reading_high_contrast():
    assert taskbar_appearance._colorref_hex(0x00EFFAFF) == "#FFFAEF"     # COLORREF is 0x00BBGGRR
    on, text, back = taskbar_appearance.read_high_contrast()
    assert isinstance(on, bool)                  # the real call works on this machine (either state)
    if not on:
        assert (text, back) == (None, None)


# --------------------------------------------------------------------------- 5a: accent taskbar

GREEN_BAR = TaskbarAppearance(accent_on=True, accent_bar="#0B5A2B")


def test_a_pen_too_close_to_the_accent_bar_becomes_the_text_colour(appearance):
    appearance(GREEN_BAR)
    cfg = _cfg(high_speed_color="#1E7A3C", low_speed_color="#FFA500", graph_download_color="#1A6B35")
    rc = RenderConfig.from_dict(cfg)
    assert color_scheme.contrast_ratio("#1E7A3C", "#0B5A2B") < 3
    assert rc.high_speed_color == "#FFFFFF" and rc.graph_download_color == "#FFFFFF"
    assert rc.low_speed_color == "#FFA500"        # reads there: kept
    assert rc.pen_backdrop == "#0B5A2B"
    assert cfg["high_speed_color_dark"] == "#1E7A3C"   # config untouched


def test_every_default_pen_reads_on_green_and_orange_accent_bars(appearance):
    """The two accent bars of the live check: every pen the widget paints reaches 3:1 there."""
    for bar in ("#0B5A2B", "#8A3A00", "#4A2400"):
        appearance(TaskbarAppearance(accent_on=True, accent_bar=bar))
        rc = RenderConfig.from_dict(_cfg(color_coding=True, medium_speed_threshold=5.0))
        for stem in D.THEMED_COLOR_STEMS:
            assert color_scheme.contrast_ratio(getattr(rc, stem), bar) >= 3.0, (bar, stem, getattr(rc, stem))
        for c in WidgetRenderer.hardware_label_colors(rc):
            assert color_scheme.contrast_ratio(QColor(c).name(), bar) >= 3.0, (bar, c)


def test_an_unreadable_accent_shade_turns_coding_off(appearance):
    """5b fallback: the bar is accent-coloured but its shade can't be read."""
    appearance(TaskbarAppearance(accent_on=True, accent_bar=None))
    rc = RenderConfig.from_dict(_cfg(color_coding=True))
    assert rc.color_coding is False and rc.high_speed_color == D.THEMED_COLOR_DEFAULTS["high_speed_color"][0]


def test_the_backdrop_half_uses_the_real_bar_colour(appearance, monkeypatch):
    """A half-opaque light background over a dark accent bar is judged against that bar."""
    monkeypatch.setattr(color_scheme, "_shell_light", False)
    appearance(TaskbarAppearance(accent_on=True, accent_bar="#004400"))
    assert color_scheme.taskbar_color(False) == "#004400"
    assert color_scheme.seen_backdrop(dict(background_color="#FFFFFF", background_opacity=50), False) == "#80A280"
    appearance(taskbar_appearance.PLAIN)
    assert color_scheme.taskbar_color(False) == constants.color.TASKBAR_REFERENCE_DARK


def test_reading_the_accent_palette(monkeypatch):
    palette = bytes.fromhex("99ebff004cc2ff000091f8000078d4000067c000003e9200001a6800f7630c00")
    fake = MagicMock()
    fake.OpenKey.return_value = "key"
    fake.QueryValueEx.side_effect = lambda key, name: (
        (palette, 3) if name == "AccentPalette" else (1, 4))
    fake.REG_BINARY = 3
    fake.HKEY_CURRENT_USER = 0
    monkeypatch.setattr(taskbar_appearance, "winreg", fake)
    assert taskbar_appearance.read_accent_palette()[3] == "#0078D4"
    on, bar = taskbar_appearance.read_accent_taskbar(shell_light=False)
    assert on and bar == taskbar_appearance.read_accent_palette()[
        constants.taskbar.taskbar.ACCENT_TASKBAR_PALETTE_INDEX]
    # Windows 11 paints the accent bar in the light mode too (build 26300, VM); Windows 10 does not.
    monkeypatch.setattr(taskbar_appearance, "_windows_build", lambda: 26300)
    assert taskbar_appearance.read_accent_taskbar(shell_light=True) == (True, bar)
    monkeypatch.setattr(taskbar_appearance, "_windows_build", lambda: 19045)
    assert taskbar_appearance.read_accent_taskbar(shell_light=True) == (False, None)


@pytest.mark.parametrize("shell_light", [False, True])
def test_an_accent_bar_picks_the_text_half_by_its_own_shade(appearance, monkeypatch, shell_light):
    """In the light Windows mode the accent bar is still a dark shade (Windows draws white clock text on
    it): the widget's text follows the bar, not the mode."""
    monkeypatch.setattr(color_scheme, "_shell_light", shell_light)
    appearance(TaskbarAppearance(accent_on=True, accent_bar="#B85A00"))
    rc = RenderConfig.from_dict(dict(D.DEFAULT_CONFIG))
    assert rc.default_color == "#FFFFFF"
    assert color_scheme.contrast_ratio(rc.default_color, "#B85A00") >= 4.5


def test_a_contrast_or_accent_change_is_a_theme_change(q_app, monkeypatch):
    """SystemEventHandler re-checks on the broadcast and emits theme_changed for a High Contrast / accent
    change even when Light/Dark did not flip."""
    from netspeedtray.core.system_events import SystemEventHandler
    with patch("netspeedtray.core.system_events.WinEventHook"):
        h = SystemEventHandler()
    seen = MagicMock()
    h.theme_changed.connect(seen)
    state = {"app": taskbar_appearance.PLAIN}
    monkeypatch.setattr(color_scheme, "read_taskbar_appearance", lambda shell_light=False: state["app"])
    with patch("netspeedtray.core.system_events.read_system_uses_light_theme", return_value=0):
        h.check_shell_theme()                     # baseline
        h.check_shell_theme()
        assert seen.call_count == 0
        state["app"] = HC_DESERT
        h.check_shell_theme()
        assert seen.call_count == 1
        h.check_shell_theme()                     # deduped
        assert seen.call_count == 1
        state["app"] = GREEN_BAR
        h.check_shell_theme()
        assert seen.call_count == 2


@pytest.mark.parametrize("message,wparam", [(0x0015, 0), (0x0320, 0), (0x001A, 0x0043)])
def test_the_native_filter_wakes_the_check_for_contrast_and_accent_messages(message, wparam):
    import ctypes
    from netspeedtray.core.system_events import _ImmersiveColorSetFilter, _NativeMSG
    calls = []
    f = _ImmersiveColorSetFilter(lambda: calls.append(1))
    msg = _NativeMSG(message=message, wParam=wparam, lParam=0)
    f.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
    assert calls == [1]


# --------------------------------------------------------------------------- the 1.4 config step

def _file_2_1_7(**over):
    """A 2.1.7 / rc.1 (schema 1.2) file as that build writes it: every key, factory values."""
    cfg = {k: v for k, v in D.DEFAULT_CONFIG.items()
           if not k.endswith(("_dark", "_light")) and k != "medium_speed_threshold"}
    cfg.update(config_version="1.2", default_color="#FFFFFF", color_is_automatic=True,
               high_speed_color="#00FF00", low_speed_color="#FFA500", arrow_up_color="#00FF00",
               arrow_down_color="#FFFFFF", font_family="Segoe UI", arrow_font_family="Segoe UI",
               use_separate_arrow_font=True, arrow_font_size=10, arrow_font_weight=600)
    cfg.update(over)
    return cfg


def _load(tmp_path, cfg):
    path = tmp_path / "NetSpeedTray_Config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return ConfigManager(path).load(), path


def test_a_2_1_7_user_on_defaults_gets_every_new_default(tmp_path):
    out, path = _load(tmp_path, _file_2_1_7())
    assert (out["high_speed_color_dark"], out["high_speed_color_light"]) == ("#8CF2C3", "#145252")
    assert (out["low_speed_color_dark"], out["low_speed_color_light"]) == ("#FFA500", "#57190F")
    assert (out["medium_speed_color_dark"], out["medium_speed_color_light"]) == ("#FFFF80", "#764B0A")
    assert out["default_color_light"] == "#1B1B1B"
    assert out["font_family"] == "Segoe UI Variable"
    assert out["use_separate_arrow_font"] is False
    assert out["config_version"] == "1.4"
    assert path.with_name(path.name + ".bak.v1.2").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["high_speed_color_dark"] == "#8CF2C3"


def test_a_2_1_7_user_with_chosen_colours_and_fonts_keeps_them(tmp_path):
    out, _ = _load(tmp_path, _file_2_1_7(high_speed_color="#33FF99", font_family="Consolas",
                                         arrow_font_family="Segoe UI", arrow_font_size=11))
    assert (out["high_speed_color_dark"], out["high_speed_color_light"]) == ("#33FF99", "#33FF99")
    assert out["font_family"] == "Consolas"
    assert out["use_separate_arrow_font"] is True and out["arrow_font_size"] == 11


@pytest.mark.parametrize("old", ["#00FF00", "#00ff00"])
def test_a_1_3_file_on_the_old_green_moves_once(tmp_path, old):
    cfg = dict(D.DEFAULT_CONFIG, config_version="1.3", high_speed_color_dark=old,
               high_speed_color_light="#007A00", font_family="Segoe UI")
    out, path = _load(tmp_path, cfg)
    assert out["high_speed_color_dark"] == "#8CF2C3"
    assert out["high_speed_color_light"] == "#007A00"       # an unshipped 1.3 value: left alone
    assert out["font_family"] == "Segoe UI Variable"
    assert path.with_name(path.name + ".bak.v1.3").exists()


def test_a_1_4_file_keeps_a_deliberate_green(tmp_path):
    """Once on 1.4, #00FF00 / Segoe UI are the user's picks: never moved again."""
    cfg = dict(D.DEFAULT_CONFIG, high_speed_color_dark="#00FF00", font_family="Segoe UI",
               use_separate_arrow_font=True, arrow_font_family="Segoe UI")
    out, _ = _load(tmp_path, cfg)
    assert out["high_speed_color_dark"] == "#00FF00" and out["font_family"] == "Segoe UI"
    assert out["use_separate_arrow_font"] is True


def test_the_1_4_defaults_are_the_decided_ones():
    assert D.CONFIG_SCHEMA_VERSION == "1.4"
    assert D.DEFAULT_FONT_FAMILY == D.DEFAULT_ARROW_FONT_FAMILY == "Segoe UI Variable"
    assert D.DEFAULT_USE_SEPARATE_ARROW_FONT is False
    assert D.THEMED_COLOR_DEFAULTS["graph_upload_color"] == ("#6F86A6", "#6A7FA3")
    assert D.THEMED_COLOR_DEFAULTS["default_color"] == ("#FFFFFF", "#1B1B1B")


# --------------------------------------------------------------------------- 7a / 7b fonts

@pytest.mark.parametrize("name", ["Segoe UI Variable", "Segoe UI Variable Text"])
def test_the_default_font_is_segoe_ui_variable_at_its_text_size(q_app, name):
    """The stored family is the one Qt resolves; the Text optical size (opsz 10.5) is set explicitly, so
    Qt 6.9 (shipped) and 6.10 (tests) measure alike - left alone, 6.10 drew the Small size, 4 px wider
    on "888.8 Mbps". A pre-release "... Text" name resolves the same way."""
    from PyQt6.QtGui import QFontDatabase, QFontInfo, QFontMetrics
    f = make_widget_font(name, 9, 400)
    if "Segoe UI Variable" not in QFontDatabase.families():
        assert f.family() == "Segoe UI"
        return
    assert f.family() == "Segoe UI Variable" and QFontInfo(f).family() == "Segoe UI Variable"
    assert f.families() == ["Segoe UI Variable", "Segoe UI"]
    assert f.variableAxisValue(QFont.Tag.fromString("opsz")) == 10.5
    small = QFont("Segoe UI Variable", 9, 400)
    small.setVariableAxis(QFont.Tag.fromString("opsz"), 8.0)
    text_w, small_w = (QFontMetrics(x).horizontalAdvance("888.8 Mbps") for x in (f, small))
    assert text_w < small_w, "the Text optical size did not apply"


def test_hebrew_falls_back_to_segoe_ui_not_an_arbitrary_face(q_app):
    """Segoe UI Variable has no Hebrew. A Hebrew Wi-Fi name in the identity badge takes its letters from
    Segoe UI - the second family of the request, the Hebrew UI's face (#194) - so it measures like Segoe
    UI (an arbitrary fallback drew some letters at a mismatched size)."""
    from PyQt6.QtGui import QFontDatabase, QFontMetrics, QRawFont
    if "Segoe UI Variable" not in QFontDatabase.families():
        pytest.skip("Windows 10: the widget draws Segoe UI outright")
    heb = "\u05e7\u05e4\u05d4\u05e0\u05ea\u05d1\u05d9\u05ea"
    f = make_widget_font("Segoe UI Variable", 9, 400)
    assert not QRawFont.fromFont(f).supportsCharacter(heb[0])        # the variable face itself has none
    seg = QFont("Segoe UI", 9, 400)
    assert QFontMetrics(f).horizontalAdvance(heb) == QFontMetrics(seg).horizontalAdvance(heb)


_NATIVE_FONT_PROBE = r"""
import json, sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QFontDatabase, QFontInfo, QFontMetrics, QRawFont
app = QApplication(sys.argv[:1])
from netspeedtray.utils.widget_renderer import make_widget_font
out = {"installed": "Segoe UI Variable" in QFontDatabase.families()}
if out["installed"]:
    heb = "קפהנתבית"
    f = make_widget_font("Segoe UI Variable", 9, 400)
    small = QFont("Segoe UI Variable", 9, 400); small.setVariableAxis(QFont.Tag.fromString("opsz"), 8.0)
    out.update(resolved=QFontInfo(f).family(),
               text_w=QFontMetrics(f).horizontalAdvance("888.8 Mbps"),
               small_w=QFontMetrics(small).horizontalAdvance("888.8 Mbps"),
               face_has_hebrew=QRawFont.fromFont(f).supportsCharacter(heb[0]),
               heb_w=QFontMetrics(f).horizontalAdvance(heb),
               heb_segoe_w=QFontMetrics(QFont("Segoe UI", 9, 400)).horizontalAdvance(heb))
print(json.dumps(out))
"""


@pytest.mark.skipif(not __import__("sys").platform.startswith("win"), reason="Windows fonts")
def test_the_variable_font_on_the_native_platform():
    """The suite paints offscreen, where Qt's font database has no "Segoe UI Variable" (the widget then
    draws Segoe UI). On the real Windows platform: the Text optical size applies (narrower than Small),
    and Hebrew - which the variable face lacks - measures exactly as Segoe UI."""
    import os
    import subprocess
    import sys
    src = str(Path(__file__).resolve().parents[3])
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=src, PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-c", _NATIVE_FONT_PROBE], env=env, capture_output=True,
                         text=True, timeout=60)
    out = json.loads(res.stdout.strip().splitlines()[-1])
    if not out["installed"]:
        pytest.skip("no Segoe UI Variable on this machine (Windows 10 / Server)")
    assert out["resolved"] == "Segoe UI Variable"
    assert out["text_w"] < out["small_w"], out
    assert out["face_has_hebrew"] is False and out["heb_w"] == out["heb_segoe_w"], out


def test_without_the_variable_font_the_default_falls_back_to_segoe_ui(q_app, monkeypatch):
    from netspeedtray.utils import widget_renderer
    monkeypatch.setattr(widget_renderer, "_variable_font_installed", False)
    assert make_widget_font("Segoe UI Variable", 9, 400).family() == "Segoe UI"
    assert make_widget_font("Segoe UI Variable Text", 9, 400).family() == "Segoe UI"
    assert make_widget_font("Consolas", 9, 400).family() == "Consolas"   # any other family: unchanged


@pytest.mark.parametrize("key", ["font_family", "arrow_font_family"])
@pytest.mark.parametrize("version", ["1.2", "1.3", "1.4"])
def test_a_prerelease_optical_size_name_is_read_as_the_family(tmp_path, key, version):
    """rc.2 pre-release builds stored "Segoe UI Variable Text" (and stamped 1.4): read it as the family
    at any version, since 2.1.7 after a rollback resolved that name to Tahoma."""
    cfg = dict(D.DEFAULT_CONFIG, config_version=version, **{key: "Segoe UI Variable Text"})
    out, path = _load(tmp_path, cfg)
    assert out[key] == "Segoe UI Variable"
    assert json.loads(path.read_text(encoding="utf-8"))[key] == "Segoe UI Variable"


def test_the_arrows_use_the_text_font_by_default(q_app):
    r = WidgetRenderer(dict(D.DEFAULT_CONFIG), EN)
    assert r.arrow_font is r.font


def test_settings_keeps_the_weight_of_the_default_variable_font(q_app, monkeypatch):
    """Settings > Appearance asked QFontDatabase for the styles of "Segoe UI Variable Text" - an optical
    size, not a family, so none came back: the weight slider went dead and every Apply saved weight 400
    over the user's (a 2.1.7 "Segoe UI" at DemiBold, migrated to the new default, lost its bold)."""
    from PyQt6.QtGui import QFontDatabase
    from netspeedtray.utils import widget_renderer
    from netspeedtray.views.settings.pages.appearance import AppearancePage
    monkeypatch.setattr(widget_renderer, "_variable_font_installed", True)   # Windows 11
    styles = {"Segoe UI Variable": {"Text": 400, "Text Semibold": 600, "Text Bold": 700}}
    monkeypatch.setattr(QFontDatabase, "styles", staticmethod(lambda fam: list(styles.get(fam, {}))))
    monkeypatch.setattr(QFontDatabase, "weight", staticmethod(lambda fam, s: styles.get(fam, {}).get(s, -1)))
    page = AppearancePage(EN, MagicMock(), MagicMock(), MagicMock())
    for name in ("Segoe UI Variable", "Segoe UI Variable Text"):     # the default; a pre-release name
        page.load_settings(dict(D.DEFAULT_CONFIG, font_family=name, font_weight=600))
        assert page.allowed_font_weights == [400, 600, 700]
        out = page.get_settings()
        assert out["font_family"] == name and out["font_weight"] == 600
        assert page.current_font.family() == "Segoe UI Variable"    # what the font dialog opens on


def test_the_settings_font_picker_round_trips_the_default(q_app):
    """Picking the default face in the font dialog stores exactly the default family name."""
    from netspeedtray.views.settings.pages.appearance import AppearancePage
    page = AppearancePage(EN, MagicMock(), MagicMock(), MagicMock())
    page.load_settings(dict(D.DEFAULT_CONFIG))
    page.set_font_family(QFont("Segoe UI Variable", 9))       # what QFontDialog hands back
    assert page.get_settings()["font_family"] == D.DEFAULT_FONT_FAMILY == "Segoe UI Variable"


def test_light_text_falls_back_to_black_on_a_mid_grey_background(monkeypatch):
    """#1B1B1B on 50 % black over a light bar is 3.9:1; pure black is 4.7:1 - the factory text takes it."""
    monkeypatch.setattr(color_scheme, "_shell_light", True)
    rc = RenderConfig.from_dict(dict(D.DEFAULT_CONFIG, background_color="#000000", background_opacity=50))
    assert rc.default_color == "#000000"
    rc = RenderConfig.from_dict(dict(D.DEFAULT_CONFIG))
    assert rc.default_color == "#1B1B1B"


# --------------------------------------------------------------------------- 7e side bars

def test_the_side_bar_unit_is_unambiguous(q_app):
    """On a 48 px side bar the compact readout's unit row says "Mbps", not "Mb" (megabits or
    megabytes?), and the arrow keeps a 3 px gap from the number."""
    from netspeedtray.utils.widget_paint import side_bar_fit
    cfg = dict(D.DEFAULT_CONFIG, widget_display_mode="network_only", unit_type="bits_decimal")
    r = WidgetRenderer(cfg, EN)
    fit = side_bar_fit(r.config, r.metrics, r.arrow_metrics, EN, 48)
    assert not fit.classic and fit.unit_short is False
    drawn = []
    painter = MagicMock()
    painter.drawText.side_effect = lambda *a: drawn.append(a[-1])
    r.draw_network_compact(painter, 4.2 * MBPS, 48.7 * MBPS, 48, 60, r.config, fit)
    assert EN.MBITS_LABEL in drawn and EN.MBITS_SHORT_LABEL not in drawn
    assert constants.renderer.COMPACT_ARROW_GAP_PX == 3
