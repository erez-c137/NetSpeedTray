"""
CJK graph-font tests (#161).

The matplotlib graph must not render tofu boxes (missing-glyph rectangles) for
the axis / series labels under Japanese, Korean, or Chinese locales. These tests
verify that the font we select actually contains a glyph for every character in
the *real* localized labels (via FreeType's char->glyph index; index 0 is the
".notdef"/tofu glyph). No screen or QApplication needed.

On a machine without the relevant CJK system font (e.g. a barebones CI runner),
the coverage tests skip rather than fail - matching the runtime behavior, where
`configure_cjk_font` is a safe no-op if no CJK font is installed.
"""
import json
from pathlib import Path

import pytest

from netspeedtray.utils.mpl_fonts import (
    pick_cjk_font,
    configure_cjk_font,
    _script_for_language,
)

_LOCALES_DIR = Path(__file__).resolve().parents[2] / "constants" / "locales"
_GRAPH_LABEL_KEYS = ["DOWNLOAD_LABEL", "UPLOAD_LABEL", "ORDER_TYPE_CPU", "ORDER_TYPE_GPU"]


def _load_labels(locale: str, keys):
    data = json.loads((_LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    return {k: data[k] for k in keys if k in data and isinstance(data[k], str)}


def _missing_glyphs(font_name: str, text: str):
    """Chars in `text` that the named font has NO glyph for (would render as tofu)."""
    import matplotlib.font_manager as fm
    from matplotlib.ft2font import FT2Font

    face = FT2Font(fm.findfont(fm.FontProperties(family=font_name)))
    return [c for c in text if not c.isspace() and face.get_char_index(ord(c)) == 0]


class TestScriptMapping:
    def test_cjk_locales_map_to_scripts(self):
        assert _script_for_language("ja_JP") == "ja"
        assert _script_for_language("ko_KR") == "ko"
        assert _script_for_language("zh_CN") == "zh"
        assert _script_for_language("zh-TW") == "zh"

    def test_non_cjk_and_empty_map_to_none(self):
        for loc in ("en_US", "de_DE", "fr_FR", "ru_RU", "sl_SI", "", None):
            assert _script_for_language(loc) is None


class TestPickAndConfigure:
    def test_non_cjk_is_a_noop(self):
        for loc in ("en_US", "de_DE", "", None):
            assert pick_cjk_font(loc) is None
            assert configure_cjk_font(loc) is None

    @pytest.mark.parametrize(
        "locale, sample",
        [("ja_JP", "使用率"), ("ko_KR", "사용량")],
    )
    def test_real_labels_have_no_tofu(self, locale, sample):
        font = pick_cjk_font(locale)
        if font is None:
            pytest.skip(f"no {locale} system font installed (e.g. barebones CI runner)")

        # A representative script sample must be fully covered.
        assert not _missing_glyphs(font, sample), f"'{font}' lacks glyphs for {sample!r}"

        # The ACTUAL shipped graph labels must be fully covered - this is the fix.
        labels = _load_labels(locale, _GRAPH_LABEL_KEYS)
        assert labels, f"no graph-label keys found in {locale}.json"
        for key, text in labels.items():
            missing = _missing_glyphs(font, text)
            assert not missing, f"'{font}' lacks glyphs {missing} for {key}={text!r} ({locale})"

    def test_configure_sets_rcparams(self):
        import matplotlib as mpl

        font = pick_cjk_font("ja_JP")
        if font is None:
            pytest.skip("no Japanese system font installed")

        saved = {k: mpl.rcParams[k] for k in ("font.sans-serif", "font.family", "axes.unicode_minus")}
        try:
            chosen = configure_cjk_font("ja_JP")
            assert chosen == font
            assert mpl.rcParams["font.sans-serif"][0] == font, "CJK font must be first in the fallback list"
            assert mpl.rcParams["axes.unicode_minus"] is False, "unicode minus disabled for CJK-font safety"
        finally:
            for k, v in saved.items():
                mpl.rcParams[k] = v


def test_shape_rtl_hebrew_and_passthrough():
    """shape_rtl reshapes Hebrew (RTL) for matplotlib but leaves LTR text untouched (item 6)."""
    from netspeedtray.utils.mpl_fonts import shape_rtl, _script_for_language
    assert _script_for_language("he_IL") == "he"     # Hebrew now maps to a font script
    assert shape_rtl("Download", "en_US") == "Download"   # LTR passthrough
    assert shape_rtl("", "he_IL") == ""                    # empty passthrough
    from netspeedtray.utils.mpl_fonts import matplotlib_does_bidi
    from bidi.algorithm import get_display
    heb = "הורדה"
    shaped = shape_rtl(heb, "he_IL")
    # matplotlib < 3.11: reshaped to visual order; 3.11+ (libraqm) reorders itself, so logical order
    assert shaped == (heb if matplotlib_does_bidi() else get_display(heb))
    # a non-RTL locale never reshapes even Hebrew text
    assert shape_rtl(heb, "ja_JP") == heb


def _mpl_visual_glyphs(path, text):
    """Glyph ids of `text` as matplotlib's Agg text layout places them, left to right."""
    from matplotlib import _text_helpers
    from matplotlib.ft2font import FT2Font
    font = FT2Font(path)
    font.set_size(24, 72)
    try:
        items = list(_text_helpers.layout(text, font))                   # 3.11+
    except TypeError:
        items = list(_text_helpers.layout(text, font, kern_mode=None))   # 3.10 (kerning-only layout)
    gid = (lambda i: i.glyph_index) if items and hasattr(items[0], "glyph_index") else (lambda i: i.glyph_idx)
    return [int(gid(i)) for i in sorted(items, key=lambda i: i.x)]


def _qt_visual_glyphs(path, text):
    """The same through Qt's text layout (HarfBuzz + Qt's bidi) - what every Qt label shows."""
    from PyQt6.QtGui import QFont, QFontDatabase, QTextLayout
    fid = QFontDatabase.addApplicationFont(path)
    if fid < 0:
        pytest.skip(f"Qt cannot load {path}")
    font = QFont(QFontDatabase.applicationFontFamilies(fid)[0])
    font.setPixelSize(24)
    lay = QTextLayout(text, font)
    lay.beginLayout()
    line = lay.createLine()
    lay.endLayout()
    pairs = [(p.x(), int(g)) for run in line.glyphRuns() for g, p in zip(run.glyphIndexes(), run.positions())]
    return [g for _, g in sorted(pairs)]


@pytest.mark.parametrize("text", ["מהירות הורדה", " ↑ אתחול", "שיא 12.3 Mbps"])
def test_hebrew_graph_text_reads_in_the_same_order_as_qt(q_app, text):
    """What matplotlib DRAWS for shape_rtl(text) must be what Qt draws for the logical text. String
    checks alone passed while matplotlib 3.11 (which does its own bidi) drew every Hebrew graph label
    reversed: shape_rtl's python-bidi display order was reordered a second time."""
    import matplotlib.font_manager as fm
    from netspeedtray.utils.mpl_fonts import shape_rtl
    try:
        path = fm.findfont(fm.FontProperties(family="Segoe UI"), fallback_to_default=False)
    except Exception:
        pytest.skip("Segoe UI not installed")
    assert _mpl_visual_glyphs(path, shape_rtl(text, "he_IL")) == _qt_visual_glyphs(path, text)
