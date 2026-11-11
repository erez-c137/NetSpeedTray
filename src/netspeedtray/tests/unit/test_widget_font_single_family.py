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
def test_right_to_left_text_goes_to_qt_as_in_rc6(fake_fonts, base):
    """Bidi is Qt's: text with right-to-left letters is drawn whole through rc6_font, as 2.2.0 drew it."""
    for text in ("קפה נת", "Café קפה", "בית 🏠", "רשת [5G] בית"):
        assert _runs(base, text) == ((fr.QT_PATH, text),)


def test_a_cluster_no_known_face_has_goes_to_qt(fake_fonts):
    """No box and no guess: the whole string is drawn as 2.2.0 drew it (and pays the font load)."""
    assert _runs("Segoe UI Variable", "a\u2603") == ((fr.QT_PATH, "a\u2603"),)


@pytest.mark.parametrize("text,user,runs", [
    ("HomeWiFi", True, ((None, "HomeWiFi"),)),                 # the widget's face has it all: as before
    ("Home 😀", True, ((None, "Home "), ("Segoe UI Emoji", "😀"))),   # a covered Wi-Fi name: known faces
    ("บ้าน", True, (("Leelawadee UI", "บ้าน"),)),
    ("Home 😀", False, ((None, "Home "), ("Segoe UI Emoji", "😀"))),   # an own string: the same
    ("Home‮😀", True, ((fr.QT_PATH, "Home‮😀"),)),     # a bidi override: Qt lays it out
    ("Home⁧😀", True, ((fr.QT_PATH, "Home⁧😀"),)),     # an isolate
    ("Home‏😀", True, ((fr.QT_PATH, "Home‏😀"),)),     # an RLM
    ("a\x07😀", True, ((fr.QT_PATH, "a\x07😀"),)),            # a control character
])
def test_user_text_takes_known_faces_unless_only_qt_lays_it_out(fake_fonts, text, user, runs):
    fr.set_text_language("")
    assert fr.text_runs(QFont("Segoe UI Variable", 9), text, user) == runs


def test_user_text_in_a_right_to_left_ui_is_qt_laid_out(fake_fonts, q_app):
    """In a right-to-left UI the paragraph is right to left: Qt's bidi moves a Wi-Fi name's trailing emoji
    to its left end, which only Qt's own layout reproduces - so such a name is drawn as 2.2.0 drew it."""
    from PyQt6.QtCore import Qt
    fr.set_text_language("he_IL")
    q_app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    try:
        assert fr.text_runs(QFont("Segoe UI Variable", 9), "Home 😀", True) == (
            (fr.QT_PATH, "Home 😀"),)
        assert fr.text_runs(QFont("Segoe UI Variable", 9), "HomeWiFi", True) == ((None, "HomeWiFi"),)
        assert fr.text_runs(QFont("Segoe UI Variable", 9), "Home 😀", False) == (
            (None, "Home "), ("Segoe UI Emoji", "😀"))                   # own strings: unchanged
    finally:
        q_app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)


def test_a_word_is_drawn_in_one_face(fake_fonts, monkeypatch):
    """A word the first Han face lacks one letter of goes whole to a face that has it all - never two
    faces inside one word."""
    monkeypatch.setitem(GLYPHS, "Microsoft YaHei UI", GLYPHS["Microsoft YaHei UI"] | set("咖啡网"))
    assert _runs("Segoe UI Variable", "咖啡网 5G") == (
        ("Microsoft YaHei UI", "咖啡网 "), (None, "5G"))


def test_rc6_font_is_the_220_widget_font(q_app):
    f = make_widget_font("Segoe UI Variable", 9, 400)
    rc6 = fr.rc6_font(f)
    if f.family() == "Segoe UI Variable":
        assert rc6.families() == ["Segoe UI Variable", "Segoe UI"]
    assert fr.rc6_font(QFont("Consolas", 9)).families() == ["Consolas"]


@pytest.mark.parametrize("text,runs", [
    # Qt 6.10's QTextBoundaryFinder reports a boundary at every UTF-16 unit once Indic text appears,
    # inside an emoji's surrogate pair too: rc.7 drafts drew these as boxes / "wifi" in the emoji face.
    ("বাড়ি 🏠 ওয়াইফাই", (("Nirmala UI", "বাড়ি "), ("Segoe UI Emoji", "🏠 "), ("Nirmala UI", "ওয়াইফাই"))),
    ("নেট🙂wifi", (("Nirmala UI", "নেট"), ("Segoe UI Emoji", "🙂"), (None, "wifi"))),
    ("घर 🏠 नेट", (("Nirmala UI", "घर "), ("Segoe UI Emoji", "🏠 "), ("Nirmala UI", "नेट"))),
])
def test_indic_next_to_emoji_keeps_every_cluster_whole(fake_fonts, monkeypatch, text, runs):
    monkeypatch.setitem(GLYPHS, "Nirmala UI", GLYPHS["Nirmala UI"] | {chr(c) for c in range(0x900, 0x980)})
    monkeypatch.setitem(GLYPHS, "Segoe UI Emoji", GLYPHS["Segoe UI Emoji"] | {"🙂"})
    assert _runs("Segoe UI Variable", text, "bn_BD") == runs


def test_custom_emoji_arrows_stay_colour_emoji_as_in_rc6(fake_fonts, monkeypatch):
    """⬆ ⬇ (U+2B06/2B07) were drawn by Qt's emoji segmentation in Segoe UI Emoji until 2.2.0."""
    monkeypatch.setitem(GLYPHS, "Segoe UI Emoji", GLYPHS["Segoe UI Emoji"] | {"⬆", "⬇"})
    monkeypatch.setitem(GLYPHS, "Segoe UI Symbol", GLYPHS["Segoe UI Symbol"] | {"⬆", "⬇"})
    assert _runs("Segoe UI Variable", "⬆") == (("Segoe UI Emoji", "⬆"),)
    assert _runs("Segoe UI Variable", "▴") == (("MS UI Gothic", "▴"),)     # not an emoji: as before


def _cluster_boundaries(text):
    pos, out = 0, {0}
    for cl in fr.clusters(text):
        pos += len(cl)
        out.add(pos)
    return out


def test_fuzz_runs_rejoin_and_never_split_a_cluster(fake_fonts):
    """Random mixed Indic + emoji + RTL + CJK + marks + controls: the runs rejoin to the text, every run
    boundary is a cluster boundary, no cluster is split, and no cluster splits a surrogate pair."""
    import random
    pool = (list("aZ09 ().-_/…'") + list("שלוםבית") + list("مرحبا") + ["ক", "্", "ষ", "র", "\u200d", "\u200c",
            "া"] + list("घर्") + list("中文家秒") + list("한글") + list("ไทย") + ["👨", "👩", "👧", "\ufe0f", "\ufe0e",
            "\u20e3", "🏽", "🇮", "🇱", "🏠", "☕", "❤", "✓", "★", "▴", "⇑", "\u0301", "\u0308", "\U000e0067",
            "\U000e007f", "🏴", "\u200f", "\u200e", "\u202e", "\u2066", "\U00020000", "\U0001d400", " ", "\t"])
    rng = random.Random(487)
    for _ in range(1500):
        text = "".join(rng.choice(pool) for _ in range(rng.randint(1, 20)))
        runs = _runs("Segoe UI Variable", text, rng.choice(["en_US", "bn_BD", "he_IL", "ja_JP"]))
        assert "".join(run for _f, run in runs) == text, (ascii(text), runs)
        bounds, pos = _cluster_boundaries(text), 0
        for _face, run in runs:
            pos += len(run)
            assert pos in bounds, (ascii(text), runs)
        assert "".join(fr.clusters(text)) == text


def test_the_metrics_measure_runs_as_they_are_drawn(fake_fonts):
    font = QFont("Segoe UI Variable", 9)
    fm = fr.WidgetFontMetrics(font)
    assert fm.horizontalAdvance("456.7 Mbps") == fr.QFontMetrics(font).horizontalAdvance("456.7 Mbps")
    fr.set_text_language("ja_JP")
    runs = fr.text_runs(font, "MB/秒")
    assert fm.horizontalAdvance("MB/秒") == round(fr._placed(font, runs)[1])
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


# Every module of the app (the widget, the tray menu, flyouts, Settings, the Monitor): no QFont in them
# may carry a family list, no stylesheet a font-family list, and the renderer draws no text past draw_text.
_APP_MODULES = sorted(str(p.relative_to(PKG)).replace("\\", "/") for p in PKG.rglob("*.py")
                      if "tests" not in p.relative_to(PKG).parts)


@pytest.mark.parametrize("module", _APP_MODULES)
def test_no_font_family_list_anywhere_in_the_app(module):
    text = (PKG / module).read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    allowed = 1 if module == "utils/font_runs.py" else 0      # rc6_font: 2.2.0's font, on purpose
    assert code.count(".setFamilies(") == allowed, f"{module}: a family list loads every installed font family"
    assert "insertSubstitution" not in code, f"{module}: a substitute loads every installed font family"
    assert not re.search(r"font-family:\s*['\"][^'\"]*['\"]\s*,", code), f"{module}: a QSS family list"
    assert not re.search(r"font-family:\s*['\"]Segoe UI Variable (Text|Display|Small)", code), \
        f"{module}: an optical size is not a family Qt can match (asking for it loads every family)"


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
from PyQt6.QtGui import QTextLayout, QTextOption
font = make_widget_font("Segoe UI Variable", 9, 400)


def run_layouts(font, text):
    # the known-face runs of a string laid out exactly as draw_text draws them (one layout per run)
    layouts = []
    for f, run, _x in fr._placed(font, fr.text_runs(font, text))[0]:
        layout = QTextLayout(run, f)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        layout.setTextOption(option)
        layout.beginLayout()
        layout.createLine()
        layout.endLayout()
        layouts.append(layout)
    return layouts


extra = ["\u09b0\u200d\u09cd\u09af", "\u0e1a\u0e49\u0e32\u0e19", "1\ufe0f\u20e3", "\u05d1\u05d9\u05ea \U0001f3e0",
         "\u2764\ufe0f Home", "Fam \U0001f468\u200d\U0001f469\u200d\U0001f467", "\u0995\u09cd\u09b7",
         "a\u200cb", "\u2615", "\U0001f1ee\U0001f1f1",
         "\u09ac\u09be\u09a1\u09bc\u09bf \U0001f3e0 \u0993\u09af\u09bc\u09be\u0987\u09ab\u09be\u0987",
         "\u09a8\u09c7\u099f\U0001f642wifi", "\u0918\u0930 \U0001f3e0 \u0928\u0947\u091f",
         "\u2b06", "\u2b07"]
bad = []
for lang in I18nStrings.LANGUAGE_MAP:
    fr.set_text_language(lang)
    data = json.loads((get_locales_path() / f"{lang}.json").read_text(encoding="utf-8"))
    for text in [v for v in data.values() if isinstance(v, str) and v] + extra:
        if fr.text_runs(font, text)[0][0] == fr.QT_PATH:
            if not fr._is_rtl(text):
                bad.append([lang, ascii(text[:40]), "qt path", ""])
            continue
        for layout in run_layouts(font, text):     # exactly what draw_text draws
          for gr in layout.glyphRuns():
            raw, idx = gr.rawFont(), list(gr.glyphIndexes())
            circle = raw.glyphIndexesForString("\u25cc")
            if 0 in idx or raw.familyName().lower() != layout.font().family().lower() or (
                    circle and circle[0] and circle[0] in idx and "\u25cc" not in text):
                bad.append([lang, ascii(text[:40]), raw.familyName(), layout.font().family()])
print(json.dumps(bad))
"""


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows fonts")
def test_every_locale_string_shapes_without_a_box_or_a_dotted_circle():
    """On the real Windows platform, every string of every locale file (and conjuncts, ZWJ/ZWNJ, emoji
    sequences, a keycap, a flag, Thai, the ⬆ arrows): every left-to-right one splits into known-face
    runs, and laid out as draw_text lays them out no glyph is a missing-glyph box or a dotted circle
    (a mark cut from its base) and every glyph comes from its run's face - Qt never fell back."""
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-c", _SHAPE_PROBE], env=env, capture_output=True, text=True,
                         timeout=300)
    bad = json.loads(res.stdout.strip().splitlines()[-1])
    assert not bad, bad[:10]


_MEMORY_PROBE = r"""
import json, sys, psutil
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtCore import Qt
app = QApplication(sys.argv[:1])
from netspeedtray.constants.arrows import ARROW_PRESETS
from netspeedtray.constants.i18n import I18nStrings, get_locales_path
from netspeedtray.utils import font_runs as fr
from netspeedtray.utils.widget_renderer import make_widget_font
uss = lambda: psutil.Process().memory_full_info().uss / 2**20
font = make_widget_font("Segoe UI Variable", 9, 400)
fm = fr.WidgetFontMetrics(font)
img = QImage(600, 40, QImage.Format.Format_ARGB32)
def draw(t, d=Qt.LayoutDirection.LeftToRight):
    p = QPainter(img); p.setLayoutDirection(d); p.setFont(font); fr.draw_text(p, 100, 25, t); p.end()
draw("12.3 Mbps"); fm.horizontalAdvance("888.8")
u0 = uss()
for lang in I18nStrings.LANGUAGE_MAP:
    fr.set_text_language(lang)
    data = json.loads((get_locales_path() / f"{lang}.json").read_text(encoding="utf-8"))
    texts = [v for v in data.values() if isinstance(v, str) and v and not fr._is_rtl(v)]
    texts += [g for _n, up, dw in ARROW_PRESETS for g in (up, dw)] + ["\u2b06", "\u2b07", "\u2764\ufe0f", "1\ufe0f\u20e3"]
    for t in texts:
        fm.horizontalAdvance(t)
        draw(t)
        draw(t, Qt.LayoutDirection.RightToLeft)
print(round(uss() - u0, 1))
"""


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows fonts")
def test_no_own_string_makes_qt_load_every_font_family():
    """Measure and draw every left-to-right string of every locale file, the arrow presets and emoji
    arrows, on the real Windows platform: the process grows by a few MB at most. Loading every installed
    family's styles - the bug this module exists for - costs ~38 MB in one step on a typical PC."""
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-c", _MEMORY_PROBE], env=env, capture_output=True, text=True,
                         timeout=600)
    grew = float(res.stdout.strip().splitlines()[-1])
    assert grew < 20, f"USS grew {grew} MB: something made Qt load every font family"


_RC6_PROBE = r"""
import json, sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QImage, QPainter, QColor
from PyQt6.QtCore import Qt, QRectF
app = QApplication(sys.argv[:1])
from netspeedtray.utils import font_runs as fr
from netspeedtray.utils.widget_renderer import make_widget_font
font = make_widget_font("Segoe UI Variable", 9, 400)
old = QFont(font)
if font.family() == "Segoe UI Variable":
    old.setFamilies(["Segoe UI Variable", "Segoe UI"])        # the widget font until 2.2.0
# Qt-path names (right-to-left letters, scripts no known face has): identical in both directions. The
# covered left-to-right names (Thai, emoji, Bengali + emoji) take known-face runs in a left-to-right UI
# (test_covered_user_text_*), but in a right-to-left UI they too are Qt's - identical to 2.2.0.
qt_texts = ["\u05e8\u05e9\u05ea [5G] \u05d1\u05d9\u05ea", "\u05d1\u05d9\u05ea (2)",
         "Home \u05d1\u05d9\u05ea 2.4 \U0001f468\u200d\U0001f469\u200d\U0001f467", "\u1b05\u1b13", "\u1b83\u1b8a",
         "\u1a20\u1a21", "\u1c00\u1c01", "\u1901\u1902",
         "\u05e7\u05e4\u05d4 \u05e0\u05ea", "Caf\u00e9 \u05e7\u05e4\u05d4",
         "\u05e9 \u0995\u2605", "\u2605\u0915\u05e9", "(\u05d1\u05d9\u05ea)", "\u0645\u0631\u062d\u0628\u0627 5G"]
covered = ["\u0e1a\u0e49\u0e32\u0e19 WiFi", "Home \U0001f3e0 \u2764\ufe0f", "\u09ac\u09be\u09a1\u09bc\u09bf \U0001f3e0"]
bad = []
for d in (Qt.LayoutDirection.LeftToRight, Qt.LayoutDirection.RightToLeft):
    app.setLayoutDirection(d)              # the app's direction, as the widget's painter and metrics see it
    fr.reset_caches()
    for t in qt_texts + (covered if d == Qt.LayoutDirection.RightToLeft else []):
        imgs = []
        for which in ("old", "new"):
            img = QImage(420, 30, QImage.Format.Format_ARGB32); img.fill(QColor("white"))
            p = QPainter(img); p.setLayoutDirection(d); p.setPen(QColor("black"))
            if which == "old":
                p.setFont(old); p.drawText(20, 20, t); p.drawText(QRectF(200, 0, 200, 30), Qt.AlignmentFlag.AlignCenter, t)
            else:
                p.setFont(font); fr.draw_text(p, 20, 20, t, user=True); fr.draw_text_centered(p, QRectF(200, 0, 200, 30), t, user=True)
            p.end(); imgs.append(img)
        w_old = __import__("PyQt6.QtGui", fromlist=["QFontMetrics"]).QFontMetrics(old).horizontalAdvance(t)
        if imgs[0] != imgs[1] or fr.user_text_advance(fr.WidgetFontMetrics(font), t) != w_old:
            bad.append([d.name, ascii(t)])
print(json.dumps(bad))
"""


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows fonts")
def test_user_text_draws_and_measures_exactly_as_rc6():
    """Wi-Fi names that go to Qt - Hebrew with brackets and digits, ZWJ emoji next to Hebrew, Balinese,
    Sundanese, Tai Tham, Lepcha, Limbu, Arabic - in a left-to-right and a right-to-left UI, and every
    name in a right-to-left UI: pixel-identical to 2.2.0, and the same width."""
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-c", _RC6_PROBE], env=env, capture_output=True, text=True,
                         timeout=300)
    bad = json.loads(res.stdout.strip().splitlines()[-1])
    assert not bad, bad
