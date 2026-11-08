"""
Only the font files a UI language needs are loaded (memory plan, rc.7 - the owner's requirement).

Qt resolves a QFont's whole fallback chain - and so loads the styles of EVERY installed family, ~850
font files and ~38 MB on a typical PC, process-wide and never freed - for a family list, a family name
it can't match, or a glyph the face lacks. So the app draws its UI in ONE face per language
(utils/styles.ui_family) and the widget in its own face (utils/font_runs). DirectWrite maps each face's
files into the process as Qt loads it, which makes the loaded set observable: psutil's memory_maps().

Per UI language, in a clean process on the real Windows platform, after painting the widget, building
and drawing the tray menu, the hover card and a notice flyout, the mapped font files must be EXACTLY:
  - Latin/Cyrillic/Greek UIs: Segoe UI Variable (widget + UI) and Segoe Fluent Icons (menu icons);
  - he_IL: + Segoe UI (#194: Segoe UI Variable has no Hebrew);
  - ja_JP + Yu Gothic UI, ko_KR + Malgun Gothic, zh_CN + Microsoft YaHei UI, zh_TW + Microsoft
    JhengHei UI, bn_BD + Nirmala UI.
(Windows 10: Segoe UI and Segoe MDL2 Assets in place of the first two.) Until rc.7 the menu alone
loaded all of Segoe UI in every language, and every family in the CJK and Bengali ones.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from netspeedtray.constants.i18n import I18nStrings

SRC = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows fonts (DirectWrite)")

# The UI face each language adds to the base pair (widget face + icon face).
LANGUAGE_FACE = {"he_IL": "Segoe UI", "ja_JP": "Yu Gothic UI", "ko_KR": "Malgun Gothic",
                 "zh_CN": "Microsoft YaHei UI", "zh_TW": "Microsoft JhengHei UI", "bn_BD": "Nirmala UI"}

_PROBE = r"""
import json, os, sys, psutil
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import QImage, QPainter, QColor
from PyQt6.QtCore import Qt, QRect
app = QApplication(sys.argv[:1])
lang = sys.argv[1]
from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import styles as su
from netspeedtray.utils import font_runs as fr
i18n = I18nStrings(lang)
app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl else Qt.LayoutDirection.LeftToRight)
if hasattr(su, "apply_ui_language"):
    su.apply_ui_language(app, i18n.language)                   # what monitor.main() does
else:                                                           # (2.2.0: the pin fails there, as it should)
    su.apply_script_font_fallbacks(i18n.language)

from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.utils.widget_paint import render_widget, WidgetMetrics, font_from_config
cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
cfg["language"] = lang
r = WidgetRenderer(cfg, i18n)
img = QImage(320, 40, QImage.Format.Format_ARGB32)
img.fill(QColor(0, 0, 0, 0))
p = QPainter(img)
render_widget(p, QRect(0, 0, 320, 40), r, r.config, WidgetMetrics(upload_mbps=12.3, download_mbps=456.7),
              font=font_from_config(cfg))
p.end()

from netspeedtray.core.tray_manager import TrayIconManager
host = QWidget()
tm = TrayIconManager(host, i18n)
tm.initialize()
tm._apply_menu_style()
tm.context_menu.adjustSize()
tm.context_menu.grab()                                          # the tray menu, drawn

from netspeedtray.views.usage_flyout import UsageFlyout
card = UsageFlyout(i18n, (1.2e9, 3.4e9), (5.6e10, 7.8e10), hint=i18n.WIDGET_HOVER_TOOLTIP,
                   cap=(42.0, 100.0, 42.0), pin_hint=i18n.HOVER_CARD_PIN_HINT)
card.adjustSize()
card.grab()                                                     # the hover card, drawn

from netspeedtray.views.flyout import Flyout
fl = Flyout(i18n.DB_UPGRADE_DONE_FLYOUT_TITLE, i18n.DB_UPGRADE_DONE_FLYOUT_BODY,
            action_text=i18n.STATS_DETAIL_OPEN_FOLDER, auto_dismiss_ms=0)
fl.adjustSize()
fl.grab()                                                       # a notice flyout, drawn

for t in sys.argv[2:]:                                          # Wi-Fi names, drawn as the widget draws them
    p = QPainter(img); p.setFont(r.font); fr.draw_text(p, 4, 20, t, user=True); p.end()

files = sorted({os.path.basename(m.path) for m in psutil.Process().memory_maps()
                if m.path.lower().endswith((".ttf", ".ttc", ".otf"))})
print(json.dumps({"files": files, "ui": su.ui_family() if hasattr(su, "ui_family") else app.font().family(), "widget": r.font.family(), "icons": su.icon_font_family(),
                  "installed": {f: fr.resolve_family(f) is not None for f in
                                ["Segoe UI Variable", "Segoe Fluent Icons"] + list(set(%r.values()))}}))
""" % LANGUAGE_FACE

_STYLE_WORDS = {"regular", "bold", "italic", "oblique", "light", "semilight", "semibold", "medium", "black",
                "thin", "extralight", "extrabold", "heavy", "demibold", "condensed", "narrow", "book"}


def _faces_by_file() -> dict:
    """font file name (lower case) -> the face names Windows registers it under."""
    import winreg
    out = {}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    name, data, _t = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                faces = [f.strip() for f in name.split(" (")[0].split("&") if f.strip()]
                out.setdefault(os.path.basename(str(data)).lower(), []).extend(faces)
    return out


def _is_face(registered: str, family: str) -> bool:
    """``registered`` ("Yu Gothic UI Semilight") is a style of ``family`` ("Yu Gothic UI")."""
    low, fam = registered.lower(), family.lower()
    return low == fam or (low.startswith(fam + " ") and set(low[len(fam):].split()) <= _STYLE_WORDS)


def _probe(lang: str, *ssids: str) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    res = subprocess.run([sys.executable, "-c", _PROBE, lang, *ssids], env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=180)
    assert res.returncode == 0, res.stderr[-2000:]
    return json.loads(res.stdout.strip().splitlines()[-1])


def _families_of(files, families) -> dict:
    """file -> the expected family it belongs to (None: it belongs to none of them)."""
    registered = _faces_by_file()
    return {f: next((fam for fam in families for face in registered.get(f.lower(), []) if _is_face(face, fam)),
                    None) for f in files}


@pytest.mark.parametrize("lang", sorted(I18nStrings.LANGUAGE_MAP))
def test_only_the_languages_font_files_are_loaded(lang):
    out = _probe(lang)
    installed = out["installed"]
    base = ["Segoe UI Variable" if installed["Segoe UI Variable"] else "Segoe UI",
            "Segoe Fluent Icons" if installed["Segoe Fluent Icons"] else "Segoe MDL2 Assets"]
    extra = LANGUAGE_FACE.get(lang)
    if extra and not installed.get(extra, True):
        pytest.skip(f"{extra} is not installed here")
    expected = set(base) | ({extra} if extra else set())
    assert len(out["files"]) < 40, f"{lang}: {len(out['files'])} font files mapped - Qt loaded every family"
    owners = _families_of(out["files"], sorted(expected, key=len, reverse=True))
    stray = sorted(f for f, fam in owners.items() if fam is None)
    assert not stray, f"{lang}: font files of faces it doesn't need: {stray}"
    assert set(owners.values()) == expected, f"{lang}: {sorted(set(owners.values()))} != {sorted(expected)}"
    assert out["ui"] == (extra or base[0])
    assert out["widget"] == base[0] and out["icons"] == base[1]


# Left-to-right Wi-Fi names a known face covers cluster by cluster take the widget's known-face runs:
# only their faces load, not every family (until rc.7 any name the widget face lacked took Qt's path).
_SSIDS = {"บ้าน WiFi": "Leelawadee UI", "我的网络 5G": "Microsoft YaHei UI",
          "우리집": "Malgun Gothic", "自宅のWi-Fi": "Yu Gothic UI",
          "বাড়ি \U0001f3e0": "Nirmala UI", "Home \U0001f3e0 ❤️": "Segoe UI Emoji"}


@pytest.mark.parametrize("ssid", sorted(_SSIDS))
def test_a_covered_wifi_name_loads_only_its_faces(ssid):
    out = _probe("en_US", ssid)
    if not out["installed"]["Segoe UI Variable"]:
        pytest.skip("Segoe UI Variable is not installed here")
    expected = {"Segoe UI Variable", "Segoe Fluent Icons", _SSIDS[ssid]}
    if "\U0001f3e0" in ssid:
        expected.add("Segoe UI Emoji")
    assert len(out["files"]) < 40, f"{ascii(ssid)}: {len(out['files'])} font files mapped - Qt loaded every family"
    owners = _families_of(out["files"], sorted(expected, key=len, reverse=True))
    stray = sorted(f for f, fam in owners.items() if fam is None)
    assert not stray, f"{ascii(ssid)}: font files of faces it doesn't need: {stray}"
