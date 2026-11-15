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
import functools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from netspeedtray.constants.i18n import I18nStrings

SRC = Path(__file__).resolve().parents[3]
# Shows the real Monitor, Settings and font picker on the native platform: the serial native lane.
pytestmark = [pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows fonts (DirectWrite)"),
              pytest.mark.native_desktop]

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
                                ["Segoe UI Variable", "Segoe Fluent Icons", "Leelawadee UI", "Segoe UI Emoji",
                                 "MS UI Gothic", "Microsoft YaHei UI"] + list(set(%r.values()))}}))
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


@functools.lru_cache(maxsize=None)
def _installed_faces() -> tuple:
    """Which of the probed faces this PC has (en_US probe). Installed fonts don't change during a run,
    so one probe per test process answers every _assert_only_shown_faces call (it was one each)."""
    return tuple(sorted(_probe("en_US")["installed"].items()))


def _families_of(files, families) -> dict:
    """file -> the expected family it belongs to (None: it belongs to none of them)."""
    registered = _faces_by_file()

    def owner(f):
        faces = registered.get(f.lower())
        if faces is None:   # not in the Fonts key (Cascadia Mono): its file name ("CascadiaMonoItalic.ttf")
            stem = f.lower().replace(" ", "")
            return next((fam for fam in families if stem.startswith(fam.lower().replace(" ", ""))), None)
        return next((fam for fam in families for face in faces if _is_face(face, fam)), None)

    return {f: owner(f) for f in files}


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


# The Monitor window (every tab) adds no face. Settings adds two, and only these, while it is open (its
# font picker adds the faces of the rows it draws); once Settings is closed and destroyed, Qt's font
# database is reset and all of them are unmapped again:
#  - Nirmala UI: the Language list names each language in its own script; laying the list out measures
#    its last row, "বাংলা" (the other native names' faces load only when the list is opened);
#  - MS UI Gothic: the arrow-style buttons show ▴▾ △▽ ▵▿ ⇑⇓, which neither Segoe UI Variable nor
#    Segoe Fluent Icons has, in the face the widget draws those presets in.
SETTINGS_FACES = {"Nirmala UI", "MS UI Gothic"}

_APP_PROBE = r"""
import json, os, sys, psutil
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer, QEventLoop
app = QApplication(sys.argv[:1])
lang = sys.argv[1]
names = json.loads(sys.argv[2])                               # the adapter names (never this PC's own)
system_text = json.loads(sys.argv[3])                         # other system / user text, or {}
from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.utils.config import ConfigManager
i18n = constants.i18n.get_i18n(lang)
if system_text.get("windows"):
    su._windows_language = system_text["windows"]             # the Windows display language
app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl else Qt.LayoutDirection.LeftToRight)
if hasattr(su, "apply_ui_language"):
    su.apply_ui_language(app, i18n.language)
else:
    su.apply_script_font_fallbacks(i18n.language)
from netspeedtray.core import startup_manager as _sm          # never touch the Run key
_sm.StartupManager._set_startup_registry = lambda self, enable: None
_sm._clear_startup_approved = lambda name: None
from netspeedtray.core import shutdown_channel as _sc
_sc.arm_window = lambda hwnd: True
from netspeedtray.views.widget import NetworkSpeedWidget
NetworkSpeedWidget.get_unified_interface_list = lambda self: list(names)
app.setQuitOnLastWindowClosed(False)
cfg = ConfigManager().load()
cfg["language"] = lang
cfg["check_for_updates"] = False
cfg["preferred_monitor"] = chr(92) * 2 + "." + chr(92) + "DISPLAY97"   # a saved monitor, not connected
if system_text.get("host"):
    cfg["latency_public_host"] = system_text["host"]          # Check connection's tick + Settings' host box
if system_text.get("gpu"):
    cfg["gpu_adapter"] = system_text["gpu"]                   # a saved GPU: listed and selected in Settings
w = NetworkSpeedWidget(taskbar_height=40, config=cfg, i18n=i18n)


def pump(s):
    loop = QEventLoop(); QTimer.singleShot(int(s * 1000), loop.quit); loop.exec()


def files():
    return sorted({os.path.basename(m.path) for m in psutil.Process().memory_maps()
                   if m.path.lower().endswith((".ttf", ".ttc", ".otf"))})


out = {}
w.open_monitor_window(); pump(1.5)
for tab in range(3):
    try:
        w.monitor_window._tab_bar.setCurrentIndex(tab); pump(0.6)
    except Exception:
        pass
# The Check connection sheet mid-run ("12 / 20" per hop) - no pings: the state is set by hand.
from netspeedtray.views.monitor import connection_check as ccv
sheet = ccv.ConnectionCheckSheet(cfg, i18n, gateway_ip="192.168.1.1", autostart=False)
sheet._echoes = {ccv.CC.HOP_GATEWAY: [1.0, 2.5, None, 3.0]}
sheet._state = "running"
sheet._refresh()
sheet.adjustSize()
sheet.grab()
if system_text:
    # The Monitor's other system text, drawn: the adapter menu + its button showing a picked adapter,
    # a process name in the Network and Hardware lists and the detail panel, an adapter driver's name in
    # Check connection's details, and a Wi-Fi name in the hover card.
    from netspeedtray.views.monitor.network.header import NetworkHeader
    from netspeedtray.views.monitor.network.app_list import AppRow
    from netspeedtray.views.monitor.network.detail import ConnectionDetailPanel
    from netspeedtray.views.monitor.hardware.list import HardwareRow
    from netspeedtray.views.usage_flyout import UsageFlyout
    from types import SimpleNamespace
    head = NetworkHeader(i18n, "TIMELINE_24_HOURS")
    head.show(); head.set_interfaces(names); head._select_iface(names[0], names[0]); pump(0.2)
    head.grab(); head._iface_menu.adjustSize(); head._iface_menu.grab(); head.hide()
    proc = system_text["process"]
    row = {"identity_key": proc, "display_name": proc, "conn_count": 3, "established_count": 2,
           "host_count": 1, "pids": [42], "cpu_pct": 12.0, "rss_bytes": 1e8, "gpu_pct": 0.0}
    for widget in (AppRow(i18n), HardwareRow(i18n)):
        widget.update_row(row, 5, True) if isinstance(widget, HardwareRow) else widget.update_row(row, 5)
        widget.adjustSize(); widget.grab()
    panel = ConnectionDetailPanel(i18n); panel.set_row(row); panel._title.adjustSize(); panel._title.grab()
    sheet._details_card.show_details(SimpleNamespace(adapter=system_text["adapter"])); sheet._details_card.setVisible(True)
    sheet.adjustSize(); sheet.grab()
    card = UsageFlyout(i18n, hardware={"cpu": {"usage": 5.0}, "identity": system_text["wifi"]})
    card.adjustSize(); card.grab()
    # The hover card's GPU list (#236): a Han adapter name beside a Latin one, the picked one marked.
    gpus = [{"key": (0, 1), "name": system_text["gpu"], "usage": 3.0, "temp": 26.0, "on_taskbar": True},
            {"key": (0, 2), "name": "Intel UHD Graphics 770", "usage": 0.0, "temp": None,
             "vram": (0.7, 7.8), "vram_shared": True}]
    card = UsageFlyout(i18n, hardware={"gpus": gpus})
    card.adjustSize(); card.grab()
out["monitor"] = files()
w.monitor_window.close(); pump(0.5)
w.show_settings(); pump(1)
for r in range(w.settings_dialog.sidebar.count()):
    w.settings_dialog.sidebar.setCurrentRow(r); pump(0.3)
combo = w.settings_dialog.widget_page.preferred_monitor_combo
out["not_connected_item"] = any("DISPLAY97" in combo.itemText(i) for i in range(combo.count()))
combo.view().grab()                                           # its items, drawn
out["settings"] = files()
# The font picker maps the faces of the rows it draws, only while it is open.
picker = w.settings_dialog._make_font_picker(w.settings_dialog.appearance_page.current_font, "main")
picker.show(); pump(0.5); picker.list.grab()
out["picker"] = files()
picker.reject(); picker.deleteLater()
# Settings closed: once it is destroyed and Qt's font database reset (utils/font_release), every face
# it or the picker mapped is unmapped again - back to the Monitor-closed set.
from netspeedtray import constants as _c
from netspeedtray.utils import font_release as _fr
_released = []                                              # the scheduled release, once it has run
_orig_release = _fr.release_font_database
_fr.release_font_database = lambda: (_released.append(1), _orig_release())[1]
w.settings_dialog.close()
# Wait for that release (plus a settle for the unmapping), never longer than the fixed wait this
# replaced: a release that never comes reads exactly as before.
import time as _t
_deadline = _t.monotonic() + _c.timeouts.FONT_RELEASE_DELAY_MS / 1000.0 + 1.5
while _t.monotonic() < _deadline and not _released:
    pump(0.05)
if _released:
    pump(max(0.0, min(0.5, _deadline - _t.monotonic())))
out["settings_closed"] = files()
print(json.dumps(out), flush=True)
os._exit(0)
"""


# The adapter names Settings and the Monitor list - fixed, never this PC's own (a CJK adapter name on
# the machine running the suite changed what loaded until rc.7's fix).
_ASCII_ADAPTERS = ["Ethernet", "Wi-Fi", "Local Area Connection* 1", "Bluetooth Network Connection"]
# A Chinese Windows' adapters, and the other text that comes from the system or the user, in Chinese:
# each must draw in the ONE face that has it (Microsoft YaHei UI), never through Qt's search of every
# installed family (852 font files before the fix: just building Settings' Network rows was enough).
_CJK_ADAPTERS = ["以太网", "WLAN", "本地连接* 1", "蓝牙网络连接"]
# On a Chinese Windows (its display language picks the face: every Han name in the same one).
_CJK_SYSTEM_TEXT = {"windows": "zh_CN", "gpu": "显示适配器", "process": "微信.exe",
                    "adapter": "网络适配器", "wifi": "我的网络"}
CJK_FACE = "Microsoft YaHei UI"
# A typed host in Han only in a Latin UI: Check connection's "Also check the internet (sends pings to
# {host})" puts it in a Bengali / Hebrew sentence there, which no one face has - Qt's path, as before.
_CJK_HOST = "例子.测试"


def _app_probe(lang, tmp_path, names, system_text) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8",
               APPDATA=str(tmp_path))     # an isolated config / database, never the user's
    res = subprocess.run([sys.executable, "-c", _APP_PROBE, lang, json.dumps(names), json.dumps(system_text)],
                         env=env, capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert res.returncode == 0, res.stderr[-2000:]
    return json.loads(res.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("lang", sorted(I18nStrings.LANGUAGE_MAP))
def test_the_monitor_and_settings_load_only_the_faces_they_show(lang, tmp_path):
    _assert_only_shown_faces(lang, _app_probe(lang, tmp_path, _ASCII_ADAPTERS, {}), set())


@pytest.mark.parametrize("lang", ["bn_BD", "en_US", "he_IL"])
def test_system_text_in_another_script_loads_only_its_face(lang, tmp_path):
    """Chinese adapter names (Settings' Network rows, the Monitor's adapter menu), process names, a
    Wi-Fi name, a GPU name, an adapter driver's name and a typed host, in a UI whose face lacks Han: one
    face more - Microsoft YaHei UI - in the Monitor and in Settings, and gone again once Settings closes."""
    text = dict(_CJK_SYSTEM_TEXT, **({"host": _CJK_HOST} if lang == "en_US" else {}))
    # The connection detail panel (its title: the process name) draws its body in the mono face in a
    # Latin-script UI - by design, not this text: allowed, not required.
    from netspeedtray.constants import fonts as font_constants
    _assert_only_shown_faces(lang, _app_probe(lang, tmp_path, _CJK_ADAPTERS, text), {CJK_FACE},
                             optional=set(font_constants.MONO_FAMILIES))


def _assert_only_shown_faces(lang, out, more, optional=frozenset()) -> None:
    installed = dict(_installed_faces())
    if not installed["Segoe UI Variable"] or not installed["Segoe Fluent Icons"]:
        pytest.skip("the Windows 11 fonts are not installed here")
    extra = LANGUAGE_FACE.get(lang)
    missing = sorted(f for f in ({extra} if extra else set()) | SETTINGS_FACES | set(more)
                     if not installed.get(f, False))
    if missing:
        pytest.skip(f"not installed here: {missing}")
    assert out["not_connected_item"], "the disconnected saved monitor was not listed"
    base = {"Segoe UI Variable", "Segoe Fluent Icons"} | ({extra} if extra else set())
    for stage, expected in (("monitor", base | more), ("settings", base | SETTINGS_FACES | more)):
        found = out[stage]
        assert len(found) < 40, f"{lang} {stage}: {len(found)} font files mapped - Qt loaded every family"
        owners = _families_of(found, sorted(expected | set(optional), key=len, reverse=True))
        stray = sorted(f for f, fam in owners.items() if fam is None)
        assert not stray, f"{lang} {stage}: font files of faces it doesn't need: {stray}"
        assert set(owners.values()) - set(optional) == expected, f"{lang} {stage}: {sorted(set(owners.values()))}"
    assert len(out["picker"]) < 80, f"{lang} picker: {len(out['picker'])} font files - Qt loaded every family"
    # After Settings (and the picker) closed and the font database was reset: nothing they mapped stays.
    closed = _families_of(out["settings_closed"], sorted(base | set(optional), key=len, reverse=True))
    stray = sorted(f for f, fam in closed.items() if fam is None)
    assert not stray, f"{lang} after Settings closed: font files still mapped: {stray}"


# Left-to-right Wi-Fi names a known face covers cluster by cluster take the widget's known-face runs:
# only their faces load, not every family (until rc.7 any name the widget face lacked took Qt's path).
_SSIDS = {"บ้าน WiFi": "Leelawadee UI", "我的网络 5G": "Microsoft YaHei UI",
          "우리집": "Malgun Gothic", "自宅のWi-Fi": "Yu Gothic UI",
          "বাড়ি \U0001f3e0": "Nirmala UI", "Home \U0001f3e0 ❤️": "Segoe UI Emoji"}


@pytest.mark.parametrize("ssid", sorted(_SSIDS))
def test_a_covered_wifi_name_loads_only_its_faces(ssid):
    out = _probe("en_US", ssid)
    expected = {"Segoe UI Variable", "Segoe Fluent Icons", _SSIDS[ssid]}
    if "\U0001f3e0" in ssid:
        expected.add("Segoe UI Emoji")
    missing = sorted(f for f in expected if not out["installed"].get(f, False))
    if missing:
        pytest.skip(f"not installed here: {missing}")
    assert len(out["files"]) < 40, f"{ascii(ssid)}: {len(out['files'])} font files mapped - Qt loaded every family"
    owners = _families_of(out["files"], sorted(expected, key=len, reverse=True))
    stray = sorted(f for f, fam in owners.items() if fam is None)
    assert not stray, f"{ascii(ssid)}: font files of faces it doesn't need: {stray}"
