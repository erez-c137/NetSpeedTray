"""
Line spacing: the gap between the widget's upload and download rows, -4..+4 px (Settings > Appearance >
Interface layout). The default, 1, is the fixed ``TWO_ROW_GAP_PX`` the widget always painted with, so
an untouched config renders exactly as before.

Every place that sizes or paints the two-row network readout must agree on the gap: the renderer
(``draw_network_speeds``), the docked-height model (``_row_baselines`` / ``_two_row_floor``), the
side-taskbar heights (``side_classic_height``, ``stacked_segment_height``) and the pause glyph, which
merges one readout's two arrow cells into a single glyph.
"""
from unittest.mock import MagicMock

import numpy as np
import pytest
from PyQt6.QtCore import QRect, QRectF
from PyQt6.QtGui import QFont, QFontMetrics

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import widget_paint as wp
from netspeedtray.utils.helpers import line_spacing_px
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager
from netspeedtray.tests.unit import test_pause_cue as pause
from netspeedtray.tests.unit import test_side_taskbar_compact as side

DEFAULT = constants.config.defaults.DEFAULT_LINE_SPACING


# --- The value ------------------------------------------------------------------------------------

def test_default_is_the_old_fixed_gap():
    assert DEFAULT == constants.renderer.TWO_ROW_GAP_PX == 1
    assert constants.config.defaults.DEFAULT_CONFIG["line_spacing"] == DEFAULT
    schema = constants.config.defaults.VALIDATION_SCHEMA["line_spacing"]
    assert (schema["min"], schema["max"], schema["default"]) == (-4, 4, DEFAULT)


@pytest.mark.parametrize("raw,expected", [(-4, -4), (0, 0), (4, 4), (None, DEFAULT), (True, DEFAULT),
                                          (False, DEFAULT), ("3", DEFAULT), (2.5, DEFAULT)])
def test_only_a_plain_int_is_taken(raw, expected):
    """A bool is an int in Python; a hand-edited ``true`` must not become a 1 px gap in the renderer
    while the layout reads the default - both go through ``line_spacing_px``."""
    assert line_spacing_px(raw) == expected
    assert RenderConfig.from_dict({"line_spacing": raw}).line_spacing == expected


def test_missing_key_is_the_default():
    assert RenderConfig.from_dict({}).line_spacing == DEFAULT


# --- The renderer: the rows move apart by exactly the spacing ---------------------------------------

def _ink_rows(cfg, w=220, h=48):
    img, _ = pause._paint(cfg, w, h, paused=False)
    rows = np.nonzero((img[:h, :w, 3] > 2).any(axis=1))[0]     # text only, not the near-clear backdrop
    return int(rows[0]), int(rows[-1])


@pytest.mark.parametrize("spacing", [-4, -1, 0, 2, 4])
def test_rows_painted_spacing_apart(q_app, spacing):
    base = pause._cfg(dict(widget_display_mode="network_only"), "#FFFFFF")
    top0, bottom0 = _ink_rows(base)
    top, bottom = _ink_rows({**base, "line_spacing": spacing})
    assert (bottom - top) - (bottom0 - top0) == spacing - DEFAULT
    assert abs((top + bottom) - (top0 + bottom0)) <= 1          # still centred


# --- The docked-height model --------------------------------------------------------------------

def _lm(spacing=None, font_size=9) -> WidgetLayoutManager:
    widget = MagicMock()
    widget.config = {} if spacing is None else {"line_spacing": spacing}
    lm = WidgetLayoutManager(widget)
    lm.metrics = QFontMetrics(QFont("Segoe UI", font_size))
    return lm


@pytest.mark.parametrize("spacing", [-4, 0, 1, 4])
def test_layout_baselines_follow_the_spacing(q_app, spacing):
    lm, line_h = _lm(spacing), _lm().metrics.height()
    net_top, net_bottom, hw_top, hw_bottom = lm._row_baselines(48)
    assert net_bottom - net_top == line_h + spacing
    assert lm._two_row_height() == 2 * line_h + spacing
    assert (hw_top, hw_bottom) == _lm()._row_baselines(48)[2:]   # CPU/GPU rows have no gap


def test_layout_reads_junk_as_the_default(q_app):
    assert _lm(True)._row_gap() == _lm("4")._row_gap() == _lm()._row_gap() == DEFAULT
    assert WidgetLayoutManager(MagicMock())._row_gap() == DEFAULT     # config is not even a dict


@pytest.mark.parametrize("spacing,extra", [(None, 2), (1, 2), (4, 5), (-4, 2)])
def test_two_row_floor(q_app, spacing, extra):
    """The #221 floor (``2*line_height + 2`` by default) grows with a wider spacing, and a negative one
    never lowers it: the hardware rows are painted without a gap and would crop."""
    lm = _lm(spacing)
    assert lm._two_row_floor(200) == 2 * lm.metrics.height() + extra


# --- Side taskbar ------------------------------------------------------------------------------

def test_stacked_network_segment_height():
    assert wp.stacked_segment_height("network", 16, False, False) == 33               # unchanged default
    assert wp.stacked_segment_height("network", 16, False, False, 4) == 36
    assert wp.stacked_segment_height("network", 16, False, False, -4) == 28
    assert wp.stacked_segment_height("cpu", 16, True, False, 4) == 32                 # no gap there


@pytest.mark.parametrize("mode", ["network_only", "split_graphs", "side_by_side"])
@pytest.mark.parametrize("spacing", [-4, 4])
def test_classic_side_height_moves_with_the_spacing(spacing, mode):
    cfg = side._config(**side.MODES[mode])
    base = RenderConfig.from_dict(cfg)
    moved = RenderConfig.from_dict({**cfg, "line_spacing": spacing})
    panels = 2 if mode == "split_graphs" else 1
    assert (wp.side_classic_height(moved, 16) - wp.side_classic_height(base, 16)
            == panels * (spacing - DEFAULT))


@pytest.mark.parametrize("mode", ["cpu_only", "combined"])
@pytest.mark.parametrize("spacing", [-4, 4])
def test_hardware_only_heights_ignore_the_spacing(q_app, mode, spacing):
    """CPU/GPU rows have no gap, so a hardware-only widget keeps its height (side bar and free float)."""
    cfg = side._config(**side.MODES[mode])
    moved = {**cfg, "line_spacing": spacing}
    assert (wp.side_classic_height(RenderConfig.from_dict(moved), 16)
            == wp.side_classic_height(RenderConfig.from_dict(cfg), 16))
    assert side._size(moved, side._taskbar(side.LEFT, 300))[1] == side._size(cfg, side._taskbar(side.LEFT, 300))[1]


@pytest.mark.parametrize("mode", ["network_only", "network_graph", "split_graphs", "side_by_side"])
@pytest.mark.parametrize("spacing", [-4, 4])
@pytest.mark.parametrize("thickness", [48, 32, 300])
def test_side_bar_never_paints_outside_the_widget(q_app, mode, spacing, thickness):
    """A guard, not the regression test for the side-bar heights (the 8 px padding hides a few px):
    48/32 px bars use the compact ladder (its own fixed gap); a 300 px one (wide enough with the test
    platform's fallback font) the classic two-row readout, sized by ``side_classic_height``."""
    cfg = side._config(**side.MODES[mode], line_spacing=spacing)
    w, h, fit = side._size(cfg, side._taskbar(side.LEFT, thickness))
    if thickness == 300:
        assert fit is None or fit.classic, "precondition: the classic two-row readout"
    ys, xs = side._outside(side._paint(cfg, w, h), w, h)
    assert len(ys) == 0, f"{mode} @ {spacing:+d}: ink outside the {w}x{h} widget"


# --- Pause glyph --------------------------------------------------------------------------------

def test_pause_glyph_merges_rows_at_the_widest_spacing(q_app):
    """At +4 one readout's two arrow cells are 4 px apart: still one glyph across both rows. Split
    panels on a side bar (cells ~12 px apart, a divider between them) still get one each."""
    r = WidgetRenderer(pause._cfg({}, "#FFFFFF"), I18nStrings("en_US"))
    r._pause_slots = [QRectF(9, 4, 8, 16), QRectF(9, 24, 8, 16)]
    assert len(wp._pause_glyph_rects(QRect(0, 0, 48, 72), r, 4)) == 1
    r._pause_slots = [QRectF(9, 4, 8, 16), QRectF(9, 32, 8, 16)]
    assert len(wp._pause_glyph_rects(QRect(0, 0, 48, 72), r, 4)) == 2


@pytest.mark.parametrize("spacing", [-4, 4])
@pytest.mark.parametrize("mode", ["network_only", "side_by_side", "split_graphs"])
def test_pause_glyph_never_overlaps_text(q_app, monkeypatch, mode, spacing):
    overrides, phase, _arrows = pause.HORIZONTAL[mode]
    cfg = pause._cfg({**overrides, "line_spacing": spacing}, "#FFFFFF")
    rects = pause._assert_no_overlap(cfg, 260, 48, monkeypatch, f"{mode} @ {spacing:+d}", cycle_mode=phase)
    panels = 2 if mode == "split_graphs" else 1      # split graphs: one readout per panel, side by side
    assert len(rects) == panels, f"{mode} @ {spacing:+d}: one glyph across both rows of each readout"
