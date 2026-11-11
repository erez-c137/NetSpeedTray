"""
Bengali in the Monitor graphs (2.2.1). matplotlib 3.11 lays text out with libraqm (HarfBuzz), which
shapes Bengali - conjuncts and vowel signs as Qt draws them. With such a matplotlib the bn_BD graph
draws its own text in Nirmala UI; with an older one (3.10, what 2.2.0 ships) it stays English
(test_graph_text_unshaped_scripts.py), exactly as before.
"""
import re
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import mpl_fonts

_BENGALI = re.compile("[ঀ-৿]")
END = datetime(2026, 10, 19, 18, 0, 0)
MBPS = 125_000.0


@pytest.fixture
def rc_params():
    """configure_cjk_font changes matplotlib's global font list: put it back."""
    import matplotlib as mpl
    saved = {k: mpl.rcParams[k] for k in ("font.sans-serif", "font.family", "axes.unicode_minus")}
    yield
    for k, v in saved.items():
        mpl.rcParams[k] = v


@pytest.fixture
def shaping(monkeypatch, rc_params):
    monkeypatch.setattr(mpl_fonts, "_MPL_SHAPES", True)


def _nirmala_path():
    from matplotlib import font_manager as fm
    try:
        return fm.findfont(fm.FontProperties(family="Nirmala UI"), fallback_to_default=False)
    except Exception:
        return None


def test_shaping_detection_matches_matplotlibs_own_layout():
    """matplotlib_shapes_text() is True exactly when matplotlib lays "ক্ষ" (ka + virama + ssa) out as
    the one conjunct glyph; unshaped it is three glyphs side by side. Holds on 3.10 and 3.11 alike."""
    from matplotlib import ft2font
    path = _nirmala_path()
    if not path:
        pytest.skip("Nirmala UI is not installed here")
    font = ft2font.FT2Font(path)
    font.set_size(12, 72)
    font.set_text("ক্ষ")
    assert (font.get_num_glyphs() == 1) == mpl_fonts.matplotlib_shapes_text()


def test_bengali_draws_its_own_graph_text_where_matplotlib_shapes(shaping):
    bn = I18nStrings("bn_BD")
    assert not mpl_fonts.graph_text_is_english("bn_BD")
    assert mpl_fonts.mpl_unshaped_locales() == frozenset()
    assert mpl_fonts.graph_i18n(bn) is bn
    assert mpl_fonts._script_for_language("bn_BD") == "bn"


def test_bengali_stays_english_where_matplotlib_does_not_shape(monkeypatch):
    monkeypatch.setattr(mpl_fonts, "_MPL_SHAPES", False)
    assert mpl_fonts.graph_text_is_english("bn_BD")
    assert mpl_fonts._script_for_language("bn_BD") is None   # the graph font is untouched (DejaVu Sans)
    assert mpl_fonts.graph_i18n(I18nStrings("bn_BD")).language == "en_US"


@pytest.mark.parametrize("fmt,bn,en", [
    ("%a %d", "শনি ১৭", "Sat 17"),
    ("%b %Y", "অক্টো ২০২৬", "Oct 2026"),
    ("%Y-%m-%d", "২০২৬-১০-১৭", "2026-10-17"),
    ("%a %H:%M", "শনি 14:05", "Sat 14:05"),     # a clock time stays Western
])
def test_date_ticks_write_calendar_numbers_in_bengali_digits(q_app, fmt, bn, en):
    """The time-axis formatter gets the GRAPH's language: bn_BD only where the graph is Bengali, en_US
    on the English fallback - which keeps every digit Western."""
    from netspeedtray.views.graph.time_axis import _calendar_digits, localized_strftime
    dt = datetime(2026, 10, 17, 14, 5)
    assert localized_strftime(dt, _calendar_digits(dt, fmt, "bn_BD"), "bn_BD") == bn
    assert localized_strftime(dt, _calendar_digits(dt, fmt, "en_US"), "en_US") == en


def test_bengali_graph_font_is_nirmala_ui(shaping):
    import matplotlib as mpl
    font = mpl_fonts.pick_cjk_font("bn_BD")
    if font is None:
        pytest.skip("no Bengali system font installed")
    assert mpl_fonts.configure_cjk_font("bn_BD") == font == "Nirmala UI"
    assert mpl.rcParams["font.sans-serif"][0] == "Nirmala UI"


def test_rendered_bengali_graph_draws_bengali_text(monkeypatch, shaping):
    """Through the real GraphRenderer onto an Agg canvas: the axis labels are the Bengali strings, in
    glyphs Nirmala UI has (no tofu); the numbers stay Western (live values)."""
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.text import Text
    from netspeedtray.views.graph.renderer import GraphRenderer

    def _agg_init(self):
        self.figure = Figure(figsize=(9.3, 4.3), dpi=100)
        self.canvas = FigureCanvasAgg(self.figure)
        self._setup_standard_axes()

    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _agg_init)
    rng = np.random.default_rng(7)
    n = 24 * 7
    start = END - timedelta(hours=n)
    data = [(start + timedelta(hours=i), 2.0 * MBPS, float(v) * MBPS)
            for i, v in enumerate(rng.uniform(5.0, 25.0, n))]
    r = GraphRenderer(MagicMock(), I18nStrings("bn_BD"))
    assert r.i18n is r.ui_i18n
    r.render(data, start, END, "TIMELINE_WEEK", stat_type="network")
    r.figure.canvas.draw()
    texts = [t.get_text() for t in r.figure.findobj(Text) if t.get_visible() and t.get_text().strip()]
    bn = I18nStrings("bn_BD")
    assert any(bn.DOWNLOAD_LABEL in t for t in texts), texts
    assert any(_BENGALI.search(t) for t in texts)
    numeric = [t for t in texts if re.fullmatch(r"[0-9.,\s]+", t)]
    assert numeric, texts                                       # the y-axis ticks: Western digits
    # The date ticks are calendar labels: the day numbers in Bengali digits ("শনি ১৭").
    dates = [t.get_text() for t in r.ax_upload.get_xticklabels() if t.get_text().strip()]
    assert dates and all(re.search("[০-৯]", t) and not re.search("[0-9]", t) for t in dates), dates
    path = _nirmala_path()
    if path:
        from matplotlib import ft2font
        face = ft2font.FT2Font(path)
        missing = sorted({c for t in texts for c in t if not c.isspace() and face.get_char_index(ord(c)) == 0})
        assert not missing, missing


def test_history_starts_note_writes_its_date_like_the_ticks(monkeypatch, shaping):
    """The graph's other calendar date - the "History starts <date>" note on a range before the first
    record - follows the ticks' digits: a Bengali graph wrote "১৭ অক্টো" on its axis and "17 অক্টো
    2026" in its note. The English graph's note is unchanged."""
    from matplotlib.figure import Figure
    from netspeedtray.constants import styles as style_constants
    from netspeedtray.views.graph.renderer import GraphRenderer

    def _bare_init(self):
        self.figure = Figure(figsize=(8, 6), dpi=100)
        self.canvas = MagicMock()
        self.ax_download = self.figure.add_subplot(2, 1, 1)
        self.ax_upload = self.figure.add_subplot(2, 1, 2)
        self.axes = [self.ax_download, self.ax_upload]
        self._is_dark_mode = False
        self._current_text_color = style_constants.LIGHT_MODE_TEXT_COLOR
        self._current_grid_color = style_constants.GRID_COLOR_LIGHT

    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _bare_init)
    end = datetime(2025, 12, 10, 12)
    start = end - timedelta(days=7)
    pad = [(start, 0.0, 0.0), (end, 0.0, 0.0)]
    earliest = {"earliest": datetime(2026, 10, 17, 8)}   # after the range, in another year
    notes = {}
    for lang in ("bn_BD", "en_US"):
        r = GraphRenderer(MagicMock(), I18nStrings(lang))
        r.render(pad, start, end, "TIMELINE_WEEK", stat_type="network", hw_styles=earliest)
        notes[lang] = [t.get_text() for ax in r.figure.axes for t in ax.texts if t.get_text().strip()]
    bn_note = [t for t in notes["bn_BD"] if "১৭" in t]
    assert bn_note and "২০২৬" in bn_note[0] and not re.search("[0-9]", bn_note[0]), notes["bn_BD"]
    assert any(t.startswith("History starts") and "Oct 17 2026" in t for t in notes["en_US"]), notes["en_US"]
