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
    heb = "הורדה"
    shaped = shape_rtl(heb, "he_IL")
    assert shaped != heb and len(shaped) == len(heb)       # reshaped to visual order, same glyphs
    # a non-RTL locale never reshapes even Hebrew text
    assert shape_rtl(heb, "ja_JP") == heb
