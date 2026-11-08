"""
The taskbar widget's fonts are ONE family each, never a fallback list (memory plan item 2.1, 2.2.1).

A QFont with more than one family (``setFamilies([...])``, a QSS ``font-family: 'A','B'``, a registered
substitute) makes Qt resolve its whole fallback chain on the first measurement, and that loads the styles
of every installed font family: ~38 MB on a typical PC (323 families), process-wide and never freed. A
glyph the face lacks costs the same. So the widget picks one family that has every glyph the widget can
paint for the UI language and the config (widget_font_family), draws arrows the main face lacks in a
checked face of their own (widget_arrow_font), and draws a Hebrew Wi-Fi name in Segoe UI (ssid_font).

The suite paints offscreen, where Qt's font database is empty: the logic is tested against a fake glyph
table, and one test re-checks the real fonts on the Windows platform in a subprocess.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtGui import QFont, QFontMetrics

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import widget_renderer as wr

SRC = Path(__file__).resolve().parents[3]
PKG = SRC / "netspeedtray"
D = constants.config.defaults

LATIN = {chr(c) for c in range(0x20, 0x250)} | set("° ")
CYRILLIC = {chr(c) for c in range(0x400, 0x500)}
ARROWS = set("↑↓")
GLYPHS = {   # a fake glyph table: what each face "has"
    "Segoe UI Variable": LATIN | CYRILLIC | ARROWS,
    "Segoe UI": LATIN | CYRILLIC | ARROWS | {chr(c) for c in range(0x590, 0x600)},
    "Yu Gothic UI": LATIN | ARROWS | {"秒"},
    "Malgun Gothic": LATIN | ARROWS | set("초없음"),
    "Microsoft YaHei UI": LATIN | ARROWS | set("不适用"),
    "Microsoft JhengHei UI": LATIN | ARROWS | set("無資料"),
    "Nirmala UI": LATIN | {chr(c) for c in range(0x980, 0xA00)},      # no arrows, like the real one
    "Consolas": LATIN | ARROWS,
}


@pytest.fixture
def fake_fonts(q_app, monkeypatch):
    installed = set(GLYPHS)
    monkeypatch.setattr(wr, "_variable_font_installed", True)
    monkeypatch.setattr(wr, "_family_installed", lambda fam: fam in installed)
    monkeypatch.setattr(wr, "family_covers",
                        lambda fam, text: all(ch in GLYPHS.get(fam, ()) for ch in set(text) if not ch.isspace()))
    return installed


def _cfg(**over):
    cfg = dict(D.DEFAULT_CONFIG)
    cfg.update(over)
    return cfg


BITS = _cfg(widget_display_mode="network_only")
BYTES = _cfg(widget_display_mode="network_only", unit_type="bytes_decimal")
HW = _cfg(widget_display_mode="side_by_side", monitor_cpu_enabled=True)
HW_OFF = _cfg(widget_display_mode="side_by_side")        # the default mode, no CPU/GPU row: no "N/A"


@pytest.mark.parametrize("language,cfg,family", [
    ("en_US", HW, "Segoe UI Variable"),
    ("he_IL", HW, "Segoe UI Variable"),               # every widget string is Latin
    ("ru_RU", BYTES, "Segoe UI Variable"),
    ("ja_JP", BITS, "Segoe UI Variable"),             # "Mbps": nothing Japanese to draw
    ("ja_JP", BYTES, "Yu Gothic UI"),                 # "MB/秒"
    ("ko_KR", BITS, "Segoe UI Variable"),
    ("ko_KR", BYTES, "Malgun Gothic"),
    ("ko_KR", HW, "Malgun Gothic"),                   # "없음" (N/A)
    ("zh_CN", HW_OFF, "Segoe UI Variable"),
    ("zh_CN", HW, "Microsoft YaHei UI"),              # "不适用"
    ("zh_TW", HW, "Microsoft JhengHei UI"),
    ("bn_BD", BITS, "Segoe UI Variable"),
    ("bn_BD", HW, "Nirmala UI"),
])
def test_the_widget_face_is_one_family_with_every_glyph_the_config_shows(fake_fonts, language, cfg, family):
    i18n = I18nStrings(language)
    font = wr.make_widget_font(D.DEFAULT_FONT_FAMILY, 9, 400, i18n, cfg)
    assert font.families() == [family]
    assert wr.family_covers(family, wr.widget_locale_text(i18n, cfg))


def test_the_variable_face_keeps_its_text_optical_size_and_a_script_face_gets_none(fake_fonts):
    opsz = QFont.Tag.fromString("opsz")
    latin = wr.make_widget_font("Segoe UI Variable", 9, 400, I18nStrings("en_US"), BYTES)
    assert latin.variableAxisValue(opsz) == 10.5
    ja = wr.make_widget_font("Segoe UI Variable", 9, 400, I18nStrings("ja_JP"), BYTES)
    assert ja.families() == ["Yu Gothic UI"] and not ja.isVariableAxisSet(opsz)


def test_a_family_the_user_picked_is_kept(fake_fonts):
    """Consolas has no "秒": the widget still draws Consolas (Qt falls back for that glyph alone)."""
    font = wr.make_widget_font("Consolas", 9, 400, I18nStrings("ja_JP"), BYTES)
    assert font.families() == ["Consolas"]


def test_no_script_face_installed_keeps_the_default_face(fake_fonts):
    fake_fonts.discard("Yu Gothic UI")
    font = wr.make_widget_font("Segoe UI Variable", 9, 400, I18nStrings("ja_JP"), BYTES)
    assert font.families() == ["Segoe UI Variable"]


def test_arrows_the_main_face_lacks_get_a_checked_face_without_merging(fake_fonts):
    """Nirmala UI (Bengali) has no arrows: they are drawn in Segoe UI Variable - checked to have them,
    and set to no merging so Qt never resolves a fallback chain for that font."""
    i18n = I18nStrings("bn_BD")
    main = wr.make_widget_font("Segoe UI Variable", 9, 400, i18n, HW)
    arrows = wr.widget_arrow_font(main, ["↑", "↓"], "Segoe UI Variable", 9, 400)
    assert arrows.families() == ["Segoe UI Variable"]
    assert arrows.styleStrategy() & QFont.StyleStrategy.NoFontMerging
    en = wr.make_widget_font("Segoe UI Variable", 9, 400, I18nStrings("en_US"), HW)
    assert wr.widget_arrow_font(en, ["↑", "↓"], "Segoe UI Variable", 9, 400) is en   # shared, as before


def test_the_renderer_and_its_arrow_font_are_one_family_each(fake_fonts):
    renderer = wr.WidgetRenderer(HW, I18nStrings("bn_BD"))
    assert renderer.font.families() == ["Nirmala UI"]
    assert renderer.arrow_font.families() == ["Segoe UI Variable"]


def test_a_hebrew_wifi_name_is_drawn_in_segoe_ui(fake_fonts):
    font = wr.make_widget_font("Segoe UI Variable", 9, 400, I18nStrings("en_US"), BITS)
    assert wr.ssid_font(font, "HomeWiFi") is font
    heb = wr.ssid_font(font, "קפה נת")
    assert heb.families() == ["Segoe UI"] and heb.pointSize() == font.pointSize()


class _SpyMetrics:
    """QFontMetrics stand-in that records every string it measured."""
    def __init__(self, font):
        self._fm, self.seen = QFontMetrics(font), []

    def horizontalAdvance(self, text, *a):
        self.seen.append(text)
        return self._fm.horizontalAdvance(text, *a)

    def __getattr__(self, name):
        return getattr(self._fm, name)


def test_the_layout_measures_shared_arrows_with_the_arrow_font(q_app):
    """The two-row (large taskbar) branch measured the arrows with the MAIN font's metrics; for Bengali
    that is Nirmala UI, which has no arrows - one measurement there made Qt load every installed family.
    Arrows that share the main font are measured with the font that draws them."""
    from netspeedtray.views.widget.layout import WidgetLayoutManager
    from netspeedtray.utils.taskbar_utils import TaskbarInfo
    from PyQt6.QtCore import QRect
    i18n = I18nStrings("bn_BD")
    cfg = _cfg(widget_display_mode="network_only", free_float=False, use_separate_arrow_font=False)
    widget = MagicMock()
    widget.config, widget.i18n = cfg, i18n
    widget._identity_reserve_px.return_value = 0
    widget.ram_total = widget.vram_total = widget.ram_used = widget.vram_used = None
    lm = WidgetLayoutManager(widget)
    lm.metrics, lm.arrow_metrics = _SpyMetrics(QFont("Main", 9)), _SpyMetrics(QFont("Arrows", 9))
    tb = MagicMock(spec=TaskbarInfo)
    tb.rect, tb.hwnd, tb.dpi_scale, tb.height, tb.tasklist_rect = (0, 1032, 1920, 1080), 1, 1.0, 48, None
    tb.get_edge_position.return_value = constants.TaskbarEdge.BOTTOM
    scr = MagicMock()
    scr.geometry.return_value, scr.availableGeometry.return_value = QRect(0, 0, 1920, 1080), QRect(0, 0, 1920, 1032)
    tb.get_screen.return_value = scr
    tb.get_tray_rect.return_value = (1720, 1032, 1920, 1080)
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=tb), \
            patch("netspeedtray.views.widget.layout.is_small_taskbar", return_value=False), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
        lm.resize_widget_for_font()
    glyphs = {i18n.UPLOAD_ARROW, i18n.DOWNLOAD_ARROW}
    assert glyphs & set(lm.arrow_metrics.seen)
    assert not glyphs & set(lm.metrics.seen)


# Modules on the widget's startup / runtime path (not Settings or the Monitor): no QFont in them may
# carry a family list.
_WIDGET_PATH = ["utils/widget_renderer.py", "utils/widget_paint.py", "utils/styles.py", "core/tray_manager.py",
                "views/flyout.py", "views/usage_flyout.py", *sorted(
                    str(p.relative_to(PKG)).replace("\\", "/") for p in (PKG / "views" / "widget").glob("*.py"))]


@pytest.mark.parametrize("module", _WIDGET_PATH)
def test_no_font_family_list_on_the_widget_path(module):
    text = (PKG / module).read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert ".setFamilies(" not in code, f"{module}: a family list loads every installed font family"
    if module != "utils/styles.py":    # its QSS builders style Settings and the Monitor (next PR)
        assert not re.search(r"font-family:\s*'[^']*'\s*,", code), f"{module}: a QSS family list"


def test_the_fluent_icon_font_is_one_installed_family(q_app):
    from netspeedtray.utils import styles
    assert styles.icon_font_family() in constants.styles.ICON_FONT_FAMILIES


_NATIVE_PROBE = r"""
import json, sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import widget_renderer as wr
D = constants.config.defaults
cfgs = {"bits": dict(D.DEFAULT_CONFIG, widget_display_mode="network_only"),
        "bytes": dict(D.DEFAULT_CONFIG, widget_display_mode="network_only", unit_type="bytes_decimal"),
        "hw": dict(D.DEFAULT_CONFIG, widget_display_mode="side_by_side", monitor_cpu_enabled=True,
                   monitor_gpu_enabled=True)}
out = []
for lang in I18nStrings.LANGUAGE_MAP:
    i18n = I18nStrings(lang)
    for name, cfg in cfgs.items():
        r = wr.WidgetRenderer(cfg, i18n)
        fam = r.font.family()
        candidates = constants.fonts.SCRIPT_FAMILIES.get(lang, ())
        any_installed = any(wr._family_installed(c) for c in candidates)
        out.append(dict(lang=lang, cfg=name, families=r.font.families(), arrow=r.arrow_font.families(),
                        covers=wr.family_covers(fam, wr.widget_locale_text(i18n, r.config)),
                        arrows_covered=wr.family_covers(r.arrow_font.family(), "".join(r._arrow_glyphs())),
                        script_needed=bool(candidates), any_installed=any_installed))
print(json.dumps(out))
"""


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows fonts")
def test_every_locale_gets_one_real_face_with_all_its_glyphs():
    """On the real Windows platform, for every UI language and three configs: the widget font and its
    arrow font are one family each, and that family has every glyph the widget can paint - so Qt never
    looks for a fallback (which would load every installed family)."""
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-c", _NATIVE_PROBE], env=env, capture_output=True, text=True,
                         timeout=120)
    rows = json.loads(res.stdout.strip().splitlines()[-1])
    assert len(rows) == 3 * len(I18nStrings.LANGUAGE_MAP)
    for row in rows:
        assert len(row["families"]) == 1 and len(row["arrow"]) == 1, row
        if row["script_needed"] and not row["any_installed"]:
            continue     # a Windows without the script's font (Server): nothing better to pick
        assert row["covers"] and row["arrows_covered"], row
