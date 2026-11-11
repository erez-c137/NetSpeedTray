"""
The Monitor graph draws its text with matplotlib, which does no complex text shaping. For Bengali
(#457) that is worse than missing glyphs: with Nirmala UI the letters appear, but conjuncts come out
as letter + hasant + letter and the pre-base vowel signs land on the wrong side - text that looks
like Bengali and reads as nonsense. So for the locales in `mpl_fonts.MPL_UNSHAPED_LOCALES` every
string matplotlib draws (axis labels, legend, peak caption, empty-state text, tick day/month names)
comes from en_US, through one choke point (`graph_i18n`, taken by the renderer). Everything Qt draws
around the graph - including the hover readout, which reads the lines' labels - stays in the UI
language.

These render through the real GraphRenderer onto an Agg canvas and compare every text artist in the
figure, so a new draw site that bypasses `self.i18n` shows up as a Bengali string here.
"""
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.text import Text

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.mpl_fonts import MPL_UNSHAPED_LOCALES, graph_i18n, graph_text_is_english
from netspeedtray.views.graph.renderer import GraphRenderer
from netspeedtray.views.monitor.graph_hover import GraphHoverTooltip

END = datetime(2026, 10, 19, 18, 0, 0)
MBPS = 125_000.0
_BENGALI = re.compile("[ঀ-৿]")


def _agg_init(self):
    self.figure = Figure(figsize=(9.3, 4.3), dpi=100)
    self.canvas = FigureCanvasAgg(self.figure)
    self._setup_standard_axes()


@pytest.fixture
def agg(monkeypatch):
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _agg_init)


def _month_data(n=720, seed=7):
    rng = np.random.default_rng(seed)
    down = rng.uniform(5.0, 25.0, n)
    down[500] = 950.0                              # a spike, so the clipped axis draws a peak caption
    start = END - timedelta(hours=n)
    return [(start + timedelta(hours=i), 2.0 * MBPS, float(down[i]) * MBPS) for i in range(n)], start


def _render(lang, data=None, period="TIMELINE_MONTH"):
    r = GraphRenderer(MagicMock(), I18nStrings(lang))
    if data is None:
        data, start = _month_data()
    else:
        start = END - timedelta(days=30)
    r.render(data, start, END, period, stat_type="network")
    r.figure.canvas.draw()                          # resolves tick labels
    return r


def _figure_texts(fig):
    """Every non-empty string matplotlib would paint: titles, axis labels, tick labels, legend, text."""
    return sorted(t.get_text() for t in fig.findobj(Text) if t.get_visible() and t.get_text().strip())


# --------------------------------------------------------------------------- the choke point

def test_bengali_is_the_unshaped_locale():
    assert "bn_BD" in MPL_UNSHAPED_LOCALES
    assert graph_text_is_english("bn_BD")
    for lang in ("en_US", "de_DE", "ja_JP", "he_IL", None, ""):
        assert not graph_text_is_english(lang)


def test_graph_i18n_is_english_for_bengali_and_identity_otherwise():
    bn = I18nStrings("bn_BD")
    g = graph_i18n(bn)
    assert g is not bn and g.language == "en_US"
    assert g.DOWNLOAD_LABEL == "Download"
    for lang in ("de_DE", "ja_JP", "he_IL", "en_US"):
        i18n = I18nStrings(lang)
        assert graph_i18n(i18n) is i18n


# --------------------------------------------------------------------------- the rendered figure

def test_bengali_graph_draws_exactly_the_english_text(agg):
    en = _figure_texts(_render("en_US").figure)
    bn = _figure_texts(_render("bn_BD").figure)
    assert any("Download" in t for t in en)                 # the comparison has teeth
    assert any(re.search(r"[A-Z][a-z]{2} \d", t) for t in en), en   # month-name ticks were drawn
    assert bn == en
    assert not any(_BENGALI.search(t) for t in bn)


def test_bengali_empty_graph_message_is_english(agg):
    en = _figure_texts(_render("en_US", data=[]).figure)
    bn = _figure_texts(_render("bn_BD", data=[]).figure)
    assert bn == en
    assert not any(_BENGALI.search(t) for t in bn)


@pytest.mark.parametrize("lang", ["de_DE", "ja_JP", "he_IL"])
def test_other_locales_keep_their_own_graph_text(agg, lang):
    r = _render(lang)
    assert r.i18n is r.ui_i18n and r.i18n.language == lang
    texts = _figure_texts(r.figure)
    assert texts != _figure_texts(_render("en_US").figure)


# --------------------------------------------------------------------------- Qt text stays Bengali

def test_hover_readout_names_series_in_the_ui_language(agg):
    r = _render("bn_BD")
    bn = I18nStrings("bn_BD")
    assert r.ui_series_label("Download") == bn.DOWNLOAD_LABEL
    assert r.ui_series_label("CPU") == bn.ORDER_TYPE_CPU
    assert r.ui_series_label("_child3") == "_child3"
    host = SimpleNamespace(_current_stat="network", renderer=r, i18n=bn,
                           config={"unit_type": "bits_decimal", "decimal_places": 1})
    html = GraphHoverTooltip(host)._format(r.ax_download.get_xlim()[0], [("Download", 40.0, "#42B883")])
    assert bn.DOWNLOAD_LABEL in html and ">Download<" not in html


def test_hover_readout_unchanged_where_graph_text_is_localized(agg):
    r = _render("de_DE")
    assert r.ui_series_label(r.i18n.DOWNLOAD_LABEL) == r.i18n.DOWNLOAD_LABEL


# --------------------------------------------------------------------------- Qt script fallback

def test_bengali_registers_nirmala_as_a_lazy_script_fallback(q_app):
    """Nirmala UI is Qt's application fallback for Bengali and script-neutral text (the danda after
    Latin), NOT a substitute of the Segoe families: a substitute makes every Segoe font resolve its
    fallback chain on its first measurement - every installed family's styles loaded at startup, ~38 MB."""
    from PyQt6.QtCore import QChar
    from PyQt6.QtGui import QFont, QFontDatabase
    from netspeedtray.constants import styles as sc
    from netspeedtray.utils.styles import apply_script_font_fallbacks
    scripts = (QChar.Script.Script_Bengali, QChar.Script.Script_Common)
    before = {s: list(QFontDatabase.applicationFallbackFontFamilies(s)) for s in scripts}
    subs_before = {f: list(QFont.substitutes(f)) for f in (sc.FONT_FAMILY_VARIABLE, sc.FONT_FALLBACK)}
    try:
        assert apply_script_font_fallbacks("de_DE") == []
        assert apply_script_font_fallbacks(None) == []
        assert {s: list(QFontDatabase.applicationFallbackFontFamilies(s)) for s in scripts} == before
        assert apply_script_font_fallbacks("bn_BD") == ["Script_Bengali", "Script_Common"]
        apply_script_font_fallbacks("bn_BD")                    # idempotent
        for s in scripts:
            assert [x.lower() for x in QFontDatabase.applicationFallbackFontFamilies(s)].count("nirmala ui") == 1
        assert {f: list(QFont.substitutes(f)) for f in subs_before} == subs_before    # no substitutes
    finally:
        for s in scripts:
            QFontDatabase.setApplicationFallbackFontFamilies(s, before[s])


# --------------------------------------------------------------------------- the Hardware graph

class _HwCanvas(FigureCanvasAgg):
    """Agg canvas with the two Qt calls the hardware layouts make (apply_theme's palette)."""

    def palette(self):
        return MagicMock()

    def setPalette(self, _pal):
        pass


def _hw_init(self):
    from netspeedtray.constants import styles as style_constants
    self.figure = Figure(figsize=(9.3, 4.3), dpi=100)
    self.canvas = _HwCanvas(self.figure)
    self._is_dark_mode = False
    self._current_text_color = style_constants.LIGHT_MODE_TEXT_COLOR
    self._current_grid_color = style_constants.GRID_COLOR_LIGHT
    self._setup_standard_axes()


@pytest.fixture
def agg_hw(monkeypatch):
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _hw_init)


def _hw_series(seed, n=24 * 30):
    rng = np.random.default_rng(seed)
    start = END - timedelta(hours=n)
    return [((start + timedelta(hours=i)).timestamp(), float(v)) for i, v in enumerate(rng.uniform(5, 90, n))]


def _render_hw(lang, stat, empty=False):
    r = GraphRenderer(MagicMock(), I18nStrings(lang))
    if stat in ("cpu", "gpu"):                       # the Monitor's toggle layout: one series
        data = [] if empty else _hw_series(1 if stat == "cpu" else 2)
    else:
        data = ({"cpu": [], "gpu": [], "ram": []} if empty
                else {"cpu": _hw_series(1), "gpu": _hw_series(2), "ram": _hw_series(3)})
    r.render(data, END - timedelta(days=30), END, "TIMELINE_MONTH", stat_type=stat,
             hw_styles={"history_off": {"cpu": None, "gpu": None}})
    r.figure.canvas.draw()
    return _figure_texts(r.figure)


HW_CASES = [(stat, empty) for stat in ("hwcombined", "hwseparate", "cpu", "gpu") for empty in (False, True)]


@pytest.mark.parametrize("stat,empty", HW_CASES)
def test_bengali_hardware_graph_draws_exactly_the_english_text(agg_hw, stat, empty):
    en = _render_hw("en_US", stat, empty)
    bn = _render_hw("bn_BD", stat, empty)
    assert any(re.search("[A-Za-z]{3}", t) for t in en), en         # words were drawn at all
    assert bn == en
    assert not any(_BENGALI.search(t) for t in bn)


@pytest.mark.parametrize("stat,empty", HW_CASES)
def test_german_hardware_graph_stays_german(agg_hw, stat, empty):
    assert _render_hw("de_DE", stat, empty) != _render_hw("en_US", stat, empty)
