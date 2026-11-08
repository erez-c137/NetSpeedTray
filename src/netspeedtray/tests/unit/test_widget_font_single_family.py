"""
The taskbar widget's fonts are ONE family each, never a fallback list, and no glyph is left for Qt to
find (memory plan item 2.1, 2.2.1).

A QFont with more than one family (``setFamilies([...])``, a QSS ``font-family: 'A','B'``, a registered
substitute) makes Qt resolve its whole fallback chain on the first measurement, and so does a glyph the
face lacks: that loads the styles of every installed font family, ~38 MB on a typical PC, process-wide
and never freed. So the widget draws in its one face, and only the glyphs that face lacks - a Japanese
"秒", a Chinese "N/A", the ▴ △ ⇑ arrow presets, a Hebrew Wi-Fi name - are drawn run by run in a named
face that has them (utils/font_runs). Digits, separators, arrows and Latin text never change face.

The suite paints offscreen, where Qt's font database is empty: the logic is tested against a fake glyph
table, and one test re-checks the real fonts on the Windows platform in a subprocess.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from PyQt6.QtGui import QFont, QFontMetricsF

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import font_runs as fr
from netspeedtray.utils.widget_renderer import WidgetRenderer, make_widget_font

SRC = Path(__file__).resolve().parents[3]
PKG = SRC / "netspeedtray"
D = constants.config.defaults

LATIN = {chr(c) for c in range(0x20, 0x250)} | set("° ")
ARROWS = set("↑↓")
PRESETS = set("▲▼▴▾△▽▵▿⇑⇓")
EMOJI = set("😀👨👩👧❤☕1⃣🏠")
GLYPHS = {   # a fake glyph table: what each face "has"
    "Segoe UI Variable": LATIN | ARROWS | {chr(c) for c in range(0x400, 0x500)} | {"ת"},  # one stray letter
    "Segoe UI": LATIN | ARROWS | {chr(c) for c in range(0x590, 0x600)},                  # + Hebrew
    "Consolas": LATIN | ARROWS,
    "Yu Gothic UI": LATIN | ARROWS | {"秒", "咖", "啡"},
    "Malgun Gothic": LATIN | ARROWS | set("초없음"),
    "Microsoft YaHei UI": LATIN | ARROWS | set("不适用"),
    "Microsoft JhengHei UI": LATIN | ARROWS | set("無資料"),
    "Nirmala UI": LATIN | {chr(c) for c in range(0x980, 0xA00)},
    "Leelawadee UI": LATIN | {chr(c) for c in range(0xE00, 0xE80)},
    "MS UI Gothic": LATIN | ARROWS | PRESETS | {"❤", "★"},
    "Segoe UI Symbol": LATIN | ARROWS | PRESETS | {"❤", "★"},
    "Segoe UI Emoji": EMOJI | set("0123456789"),
}


@pytest.fixture
def fake_fonts(q_app, monkeypatch):
    installed = set(GLYPHS)
    fr.reset_caches()
    monkeypatch.setattr(fr, "resolve_family", lambda name: name if name in installed else None)
    monkeypatch.setattr(fr, "family_covers", lambda fam, text: fam in installed and all(
        ch in GLYPHS[fam] for ch in set(text) if not ch.isspace() and not fr._ignorable(ch)))
    yield installed
    fr.set_text_language("")
    fr.reset_caches()


def _runs(family, text, language=""):
    fr.set_text_language(language)
    return fr.text_runs(QFont(family, 9), text)


@pytest.mark.parametrize("name", ["Segoe UI Variable", "Segoe UI Variable Text", "Segoe UI", "Consolas"])
def test_the_widget_font_is_always_one_family(q_app, name):
    assert len(make_widget_font(name, 9, 400).families()) == 1


@pytest.mark.parametrize("language,text,runs", [
    ("en_US", "456.7 Mbps", ((None, "456.7 Mbps"),)),                     # the common case: one run
    ("ja_JP", "MB/秒", ((None, "MB/"), ("Yu Gothic UI", "秒"))),           # only the script glyph moves
    ("ko_KR", "KB/초", ((None, "KB/"), ("Malgun Gothic", "초"))),
    ("zh_CN", "(不适用)", ((None, "("), ("Microsoft YaHei UI", "不适用"), (None, ")"))),
    ("zh_TW", "無資料", (("Microsoft JhengHei UI", "無資料"),)),
    ("bn_BD", "(নেই)", ((None, "("), ("Nirmala UI", "নেই"), (None, ")"))),
    ("en_US", "△", (("MS UI Gothic", "△"),)),                             # the face rc.6's fallback used
    ("en_US", "⇓", (("MS UI Gothic", "⇓"),)),
    ("en_US", "Hi 😀", ((None, "Hi "), ("Segoe UI Emoji", "😀"))),
    ("en_US", "บ้าน", (("Leelawadee UI", "บ้าน"),)),                       # a Thai Wi-Fi name
])
def test_only_the_clusters_the_face_lacks_get_another_face(fake_fonts, language, text, runs):
    assert _runs("Segoe UI Variable", text, language) == runs


@pytest.mark.parametrize("text,face", [
    ("র‍্য", "Nirmala UI"),          # bn_BD "RAM": ZWJ + virama stay in the conjunct's face
    ("ক্ষ", "Nirmala UI"),                 # a conjunct
    ("স্ত্রী", "Nirmala UI"),
    ("👨‍👩‍👧", "Segoe UI Emoji"),  # an emoji ZWJ sequence: one cluster, one face
    ("❤️", "Segoe UI Emoji"),         # VS16: colour emoji, not the monochrome symbol face
    ("☕", "Segoe UI Emoji"),              # emoji presentation by default
    ("1️⃣", "Segoe UI Emoji"),   # a keycap: its digit goes with it
])
def test_a_cluster_is_never_split(fake_fonts, text, face):
    assert fr.clusters(text) == [text]
    assert _runs("Segoe UI Variable", text) == ((face, text),)


def test_vs15_asks_for_the_text_symbol(fake_fonts):
    assert _runs("Segoe UI Variable", "❤︎") == (("MS UI Gothic", "❤︎"),)


def test_zwnj_stays_with_its_cluster(fake_fonts):
    assert fr.clusters("a‌b") == ["a‌", "b"]
    assert _runs("Segoe UI Variable", "a‌b") == ((None, "a‌b"),)


def test_the_language_picks_the_han_face(fake_fonts, monkeypatch):
    """Japanese and Chinese share Han code points: the UI language's own face draws them."""
    monkeypatch.setitem(GLYPHS, "Microsoft YaHei UI", GLYPHS["Microsoft YaHei UI"] | {"秒"})
    assert _runs("Segoe UI Variable", "秒", "zh_CN") == (("Microsoft YaHei UI", "秒"),)
    assert _runs("Segoe UI Variable", "秒", "ja_JP") == (("Yu Gothic UI", "秒"),)


def test_arrow_presets_fall_to_segoe_ui_symbol_without_ms_ui_gothic(fake_fonts):
    fake_fonts.discard("MS UI Gothic")
    assert _runs("Segoe UI Variable", "▴") == (("Segoe UI Symbol", "▴"),)


@pytest.mark.parametrize("base", ["Segoe UI Variable", "Consolas", "Yu Gothic UI"])
def test_hebrew_is_drawn_in_segoe_ui_whatever_the_widget_face(fake_fonts, base):
    """Every Hebrew letter in Segoe UI (#194) - also the one the variable face happens to have (ת) and
    also next to an emoji or a Latin word; one QTextLayout keeps the bidi order."""
    assert _runs(base, "קפה נת") == (("Segoe UI", "קפה נת"),)
    assert _runs(base, "Café קפה") == ((None, "Café "), ("Segoe UI", "קפה"))
    assert _runs(base, "בית 🏠") == (("Segoe UI", "בית "), ("Segoe UI Emoji", "🏠"))


def test_a_cluster_no_installed_face_has_gets_a_box_not_a_search(fake_fonts):
    """Drawn in the widget's face with font merging OFF: a missing-glyph box, never Qt's fallback
    search (the 38 MB)."""
    font = QFont("Segoe UI Variable", 9)
    assert _runs("Segoe UI Variable", "a☃") == ((None, "a"), (fr.NO_FACE, "☃"))
    box = fr.run_font(font, fr.NO_FACE)
    assert box.family() == font.family() and box.styleStrategy() & QFont.StyleStrategy.NoFontMerging


def test_layout_ranges_count_utf16_units(fake_fonts):
    """Qt counts UTF-16 units: an emoji is two. Python-length ranges cut one in half (a box)."""
    font = QFont("Segoe UI Variable", 9)
    text = "Fam 👨‍👩‍👧!"
    layout, _line = fr._layout(font, text, _runs("Segoe UI Variable", text))
    (rng,) = layout.formats()
    assert (rng.start, rng.length) == (4, 8)


def test_the_metrics_measure_runs_as_they_are_drawn(fake_fonts):
    font = QFont("Segoe UI Variable", 9)
    fm = fr.WidgetFontMetrics(font)
    assert fm.horizontalAdvance("456.7 Mbps") == fr.QFontMetrics(font).horizontalAdvance("456.7 Mbps")
    fr.set_text_language("ja_JP")
    runs = fr.text_runs(font, "MB/秒")
    assert fm.horizontalAdvance("MB/秒") == round(fr._advance(font, "MB/秒", runs))
    assert fr.run_font(font, "Yu Gothic UI").styleStrategy() & QFont.StyleStrategy.NoFontMerging


def test_a_localized_windows_resolves_english_names(q_app, monkeypatch):
    """On a Korean Windows Qt lists "맑은 고딕" and keeps "Malgun Gothic" only as an alias: the English
    name still resolves - through GDI to the listed name, or through the registry's English font
    names (the same on every UI language), which Qt matches as the alias."""
    fr.reset_caches()
    monkeypatch.setattr(fr, "_listed_families", lambda: {"맑은 고딕": "맑은 고딕", "segoe ui": "Segoe UI"})
    monkeypatch.setattr(fr, "_gdi_face_names", lambda name: ["맑은 고딕"] if name == "Malgun Gothic" else [])
    monkeypatch.setattr(fr, "_registry_faces", lambda: {"yu gothic medium", "yu gothic ui regular",
                                                       "ms gothic", "ms ui gothic", "ms pgothic"})
    try:
        assert fr.resolve_family("Malgun Gothic") == "맑은 고딕"
        assert fr.resolve_family("Segoe UI") == "Segoe UI"
        assert fr.resolve_family("Yu Gothic UI") == "Yu Gothic UI"      # registry: "Yu Gothic UI Regular"
        assert fr.resolve_family("MS UI Gothic") == "MS UI Gothic"
        assert fr.resolve_family("Leelawadee UI") is None
    finally:
        fr.reset_caches()


def test_the_registry_names_parse(q_app, monkeypatch):
    import winreg
    values = ["Malgun Gothic Bold (TrueType)", "MS Gothic & MS UI Gothic & MS PGothic (TrueType)",
              "Nirmala UI & Nirmala Text (TrueType)"]

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(winreg, "OpenKey", lambda hive, path: _Key())

    def _enum(_key, i):
        if i >= len(values):
            raise OSError
        return values[i], "", 1
    monkeypatch.setattr(winreg, "EnumValue", _enum)
    fr.reset_caches()
    try:
        faces = fr._registry_faces()
        assert {"malgun gothic bold", "ms ui gothic", "nirmala ui"} <= faces
        assert fr._in_registry("Malgun Gothic") and not fr._in_registry("Malgun")
    finally:
        fr.reset_caches()


def test_the_renderer_and_layout_measure_with_widget_metrics(q_app):
    from unittest.mock import MagicMock
    from netspeedtray.views.widget.layout import WidgetLayoutManager
    r = WidgetRenderer(dict(D.DEFAULT_CONFIG), I18nStrings("ja_JP"))
    assert isinstance(r.metrics, fr.WidgetFontMetrics) and isinstance(r.arrow_metrics, fr.WidgetFontMetrics)
    widget = MagicMock()
    widget.config, widget.i18n = dict(D.DEFAULT_CONFIG), I18nStrings("ja_JP")
    lm = WidgetLayoutManager(widget)
    lm.set_font(resize=False)
    assert isinstance(lm.metrics, fr.WidgetFontMetrics) and isinstance(lm.arrow_metrics, fr.WidgetFontMetrics)
    assert len(lm._font.families()) == 1
    fr.set_text_language("")


# Modules on the widget's startup / runtime path (not Settings or the Monitor): no QFont in them may
# carry a family list, and the renderer draws no text past draw_text.
_WIDGET_PATH = ["utils/widget_renderer.py", "utils/widget_paint.py", "utils/styles.py", "utils/font_runs.py",
                "core/tray_manager.py", "views/flyout.py", "views/usage_flyout.py", *sorted(
                    str(p.relative_to(PKG)).replace("\\", "/") for p in (PKG / "views" / "widget").glob("*.py"))]


@pytest.mark.parametrize("module", _WIDGET_PATH)
def test_no_font_family_list_on_the_widget_path(module):
    text = (PKG / module).read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert ".setFamilies(" not in code, f"{module}: a family list loads every installed font family"
    assert "insertSubstitution" not in code, f"{module}: a substitute loads every installed font family"
    if module != "utils/styles.py":    # its QSS builders style Settings and the Monitor (next PR)
        assert not re.search(r"font-family:\s*'[^']*'\s*,", code), f"{module}: a QSS family list"


def test_the_renderer_draws_text_only_through_draw_text():
    code = (PKG / "utils" / "widget_renderer.py").read_text(encoding="utf-8")
    loose = [ln for ln in code.splitlines() if "painter.drawText(" in ln and '"G")' not in ln]
    assert not loose, loose


def test_the_fluent_icon_font_is_one_installed_family(q_app):
    from netspeedtray.utils import styles
    assert styles.icon_font_family() in constants.styles.ICON_FONT_FAMILIES


_NATIVE_PROBE = r"""
import json, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
from netspeedtray import constants
from netspeedtray.constants.arrows import ARROW_PRESETS
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import font_runs as fr
from netspeedtray.utils.helpers import get_all_possible_unit_labels
from netspeedtray.utils.widget_renderer import WidgetRenderer
D = constants.config.defaults
out = []
for lang in I18nStrings.LANGUAGE_MAP:
    i18n = I18nStrings(lang)
    r = WidgetRenderer(dict(D.DEFAULT_CONFIG), i18n)
    texts = get_all_possible_unit_labels(i18n) + [i18n.DEFAULT_TEXT, "(" + i18n.DEFAULT_TEXT + ")",
             i18n.UPLOAD_ARROW, i18n.DOWNLOAD_ARROW, "888.8", "CPU 37%", "(62°C, 12.5W)", "RAM 9.2/16"]
    texts += [g for _n, up, dw in ARROW_PRESETS for g in (up, dw)]
    bad = []
    for t in texts:
        for fam, run in fr.text_runs(r.font, t):
            if not fr.family_covers(fam or r.font.family(), run):
                bad.append([t, fam, run])
    out.append(dict(lang=lang, families=r.font.families(), bad=bad))
print(json.dumps(out))
"""


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows fonts")
def test_every_locale_string_is_drawn_by_faces_that_have_its_glyphs():
    """On the real Windows platform, for every UI language: the widget font is one family, and every
    string the widget can paint (all unit labels, "N/A", arrows and arrow presets, hardware rows) splits
    into runs whose faces have all their glyphs - so Qt never looks for a fallback (which would load every
    installed family)."""
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-c", _NATIVE_PROBE], env=env, capture_output=True, text=True,
                         timeout=120)
    rows = json.loads(res.stdout.strip().splitlines()[-1])
    assert len(rows) == len(I18nStrings.LANGUAGE_MAP)
    for row in rows:
        assert len(row["families"]) == 1, row
        assert not row["bad"], row


_SHAPE_PROBE = r"""
import json, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
from netspeedtray.constants.i18n import I18nStrings, get_locales_path
from netspeedtray.utils import font_runs as fr
from netspeedtray.utils.widget_renderer import make_widget_font
font = make_widget_font("Segoe UI Variable", 9, 400)
extra = ["\u09b0\u200d\u09cd\u09af", "\u0e1a\u0e49\u0e32\u0e19", "1\ufe0f\u20e3", "\u05d1\u05d9\u05ea \U0001f3e0",
         "\u2764\ufe0f Home", "Fam \U0001f468\u200d\U0001f469\u200d\U0001f467", "\u0995\u09cd\u09b7",
         "a\u200cb", "\u2615", "\U0001f1ee\U0001f1f1"]
bad = []
for lang in I18nStrings.LANGUAGE_MAP:
    fr.set_text_language(lang)
    data = json.loads((get_locales_path() / f"{lang}.json").read_text(encoding="utf-8"))
    for text in [v for v in data.values() if isinstance(v, str) and v] + extra:
        runs = fr.text_runs(font, text)
        faces = {fr.run_font(font, f).family() for f, _ in runs}
        if fr._is_rtl(text):     # as draw_text draws it: one layout, or each run on its own
            layouts = [fr._layout(font, text, fr._bidi_runs(runs))[0]]
        else:
            layouts = [fr._layout(fr.run_font(font, f), run, ((None, run),))[0] for f, run in runs]
        for gr in [g for layout in layouts for g in layout.glyphRuns()]:
            raw, idx = gr.rawFont(), list(gr.glyphIndexes())
            circle = raw.glyphIndexesForString("\u25cc")
            if 0 in idx or raw.familyName() not in faces or (
                    circle and circle[0] and circle[0] in idx and "\u25cc" not in text):
                bad.append([lang, ascii(text[:40]), raw.familyName(), sorted(faces)])
print(json.dumps(bad))
"""


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows fonts")
def test_every_locale_string_shapes_without_a_box_or_a_dotted_circle():
    """On the real Windows platform, every string of every locale file (and conjuncts, ZWJ/ZWNJ, emoji
    sequences, a keycap, a flag, Thai): laid out as draw_text lays it out, no glyph is a missing-glyph
    box or a dotted circle (a mark cut from its base), and every glyph comes from the face its cluster
    was given - so Qt never fell back to another face (the 38 MB search)."""
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-c", _SHAPE_PROBE], env=env, capture_output=True, text=True,
                         timeout=300)
    bad = json.loads(res.stdout.strip().splitlines()[-1])
    assert not bad, bad[:10]
